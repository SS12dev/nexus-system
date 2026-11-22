"""Other Gmail accounts Money reads bank and card alerts from (MoneyMailbox).

Many people sign in with one Google account and get bank alerts at another. This lets the
owner connect that second mailbox with read-only mail access (gmail.readonly), without a second
Nexus account. Tokens are encrypted like the sign-in grant's (nexus_common.google); the address
is sealed with Money's key, and only a masked form ("ba•••@gmail.com") is ever shown or logged.
"""

import base64
import hashlib
import logging
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import google, money
from nexus_common.models import MoneyMailbox, utcnow
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CONNECT_SCOPE = f"openid email {SCOPE}"
MAX_MAILBOXES = 3
REFUSED = "Google no longer accepts this mailbox's sign-in. Connect it again."


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def mask_email(email: str) -> str:
    """ "bank.alerts@gmail.com" -> "ba•••@gmail.com"."""
    local, _, domain = email.strip().lower().partition("@")
    return f"{local[:2]}•••@{domain}"[:80]


def view(box: MoneyMailbox) -> dict:
    return {
        "id": str(box.id),
        "email_masked": box.email_masked,
        "synced_at": box.synced_at,
        "last_error": box.last_error,
        "created_at": box.created_at,
    }


async def mailboxes(db: AsyncSession, user_id: uuid.UUID) -> list[MoneyMailbox]:
    return list(
        (
            await db.scalars(
                select(MoneyMailbox)
                .where(MoneyMailbox.user_id == user_id)
                .order_by(MoneyMailbox.created_at)
            )
        ).all()
    )


async def save(
    db: AsyncSession, settings: Settings, user_id: uuid.UUID, email: str, token: dict
) -> MoneyMailbox | None:
    """Store (or refresh) a connected mailbox from Google's token response. None when Google
    sent no refresh token for a new one, or read access wasn't granted."""
    if SCOPE not in (token.get("scope") or "").split():
        return None
    key = email_hash(email)
    box = await db.scalar(
        select(MoneyMailbox).where(MoneyMailbox.user_id == user_id, MoneyMailbox.email_hash == key)
    )
    refresh = token.get("refresh_token")
    if box is None:
        if not refresh:
            return None
        box = MoneyMailbox(
            user_id=user_id,
            email_hash=key,
            email_enc=money.seal(settings, email.strip().lower()),
            email_masked=mask_email(email),
            created_at=utcnow(),
        )
        db.add(box)
    if refresh:
        box.refresh_token = google.encrypt(settings, refresh)
    _set_access(settings, box, token)
    box.last_error = None
    await db.flush()
    return box


def _set_access(settings: Settings, box: MoneyMailbox, token: dict) -> None:
    access = token.get("access_token")
    box.access_token = google.encrypt(settings, access) if access else None
    expires_in = token.get("expires_in")
    box.access_expires_at = (
        utcnow() + timedelta(seconds=int(expires_in)) if access and expires_in else None
    )


async def access_token(
    db: AsyncSession, settings: Settings, box: MoneyMailbox, http: httpx.AsyncClient | None = None
) -> str:
    """A live access token for the mailbox, refreshed when needed. Raises GoogleNotConnected
    (and notes why on the mailbox) when Google refuses it. Commits nothing."""
    if box.access_token and box.access_expires_at:
        if box.access_expires_at - google.EXPIRY_MARGIN > utcnow():
            access = google.decrypt(settings, box.access_token)
            if access:
                return access
    refresh = google.decrypt(settings, box.refresh_token)
    if refresh is None:
        box.last_error = REFUSED
        raise google.GoogleNotConnected("gmail")
    data = {
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }
    client = http or httpx.AsyncClient(timeout=20)
    try:
        resp = await client.post(google.TOKEN_URL, data=data)
    finally:
        if http is None:
            await client.aclose()
    if resp.status_code in (400, 401):
        log.info("money.mailbox_refused", extra={"mailbox_id": str(box.id)})
        box.last_error = REFUSED
        await db.flush()
        raise google.GoogleNotConnected("gmail")
    resp.raise_for_status()
    token = resp.json()
    _set_access(settings, box, token)
    box.last_error = None
    await db.flush()
    return token["access_token"]


async def remove(
    db: AsyncSession, settings: Settings, box: MoneyMailbox, http: httpx.AsyncClient | None = None
) -> None:
    """Revoke at Google (best effort) and forget the mailbox. Transactions read from it stay."""
    refresh = google.decrypt(settings, box.refresh_token)
    if refresh:
        client = http or httpx.AsyncClient(timeout=20)
        try:
            await client.post(google.REVOKE_URL, data={"token": refresh})
        except httpx.HTTPError as exc:
            log.warning("money.mailbox_revoke_failed", extra={"error": type(exc).__name__})
        finally:
            if http is None:
                await client.aclose()
    await db.delete(box)
    await db.flush()


GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


class StatementGone(Exception):
    """The statement email or its PDF can't be fetched any more (deleted, or access ended)."""


def _pdf_part(payload: dict) -> dict | None:
    stack = [payload]
    while stack:
        part = stack.pop()
        name = (part.get("filename") or "").lower()
        if name.endswith(".pdf") and (part.get("body") or {}).get("attachmentId"):
            return part
        stack += part.get("parts") or []
    return None


async def fetch_statement_pdf(
    db: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    mailbox_id: uuid.UUID | None,
    message_id: str,
    http: httpx.AsyncClient | None = None,
) -> tuple[str, bytes]:
    """The PDF attached to a statement email, from the mailbox it came to: (file name, bytes).
    Read here and dropped by the caller; never stored. Raises StatementGone."""
    try:
        if mailbox_id:
            box = await db.get(MoneyMailbox, mailbox_id)
            if box is None or box.user_id != user_id:
                raise StatementGone("That mailbox isn't connected any more.")
            token = await access_token(db, settings, box, http)
        else:
            token = await google.access_token(db, settings, user_id, "gmail", http)
    except google.GoogleNotConnected:
        raise StatementGone(
            "Google no longer lets Money read that mailbox. Connect it again."
        ) from None
    client = http or httpx.AsyncClient(timeout=30)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = await client.get(
            f"{GMAIL_API}/messages/{message_id}", params={"format": "full"}, headers=headers
        )
        if resp.status_code == 404:
            raise StatementGone("That email isn't in the mailbox any more.")
        resp.raise_for_status()
        part = _pdf_part(resp.json().get("payload") or {})
        if part is None:
            raise StatementGone("That email has no PDF attached.")
        att = await client.get(
            f"{GMAIL_API}/messages/{message_id}/attachments/{part['body']['attachmentId']}",
            headers=headers,
        )
        att.raise_for_status()
    except httpx.HTTPError as exc:
        raise StatementGone(f"Couldn't fetch it from Gmail ({type(exc).__name__}).") from exc
    finally:
        if http is None:
            await client.aclose()
    data = att.json().get("data", "")
    return part.get("filename") or "statement.pdf", base64.urlsafe_b64decode(
        data + "=" * (-len(data) % 4)
    )
