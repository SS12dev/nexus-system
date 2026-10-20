"""Google services a user grants at sign-in (Gmail, Calendar, ...), and their tokens.

Sign-in asks for the services in GOOGLE_SERVICES on top of openid/email/profile, with offline
access, so Google returns a refresh token. Google's consent screen lets people untick any of
them; whatever they leave ticked is stored here, per user, encrypted at rest. Callers get a
fresh access token with `access_token`, which refreshes it when it is about to expire.
"""

import base64
import hashlib
import logging
import uuid
from datetime import timedelta

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_common.models import GoogleGrant, utcnow
from jarvis_common.settings import Settings

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# The narrowest scope that still lets agents do useful work in each service.
SERVICE_SCOPES: dict[str, str] = {
    # Read, label, draft and send mail; no permanent deletes.
    "gmail": "https://www.googleapis.com/auth/gmail.modify",
    "calendar": "https://www.googleapis.com/auth/calendar",
    "contacts": "https://www.googleapis.com/auth/contacts.readonly",
    "tasks": "https://www.googleapis.com/auth/tasks",
    "drive": "https://www.googleapis.com/auth/drive.readonly",
}
SIGN_IN_SCOPES = "openid email profile"
# Refresh this long before Google says the access token expires.
EXPIRY_MARGIN = timedelta(seconds=60)


class GoogleNotConnected(Exception):
    """The user hasn't granted the service, or Google no longer accepts their grant."""


def requested_scopes(settings: Settings) -> list[str]:
    unknown = [s for s in settings.google_service_list if s not in SERVICE_SCOPES]
    if unknown:
        raise ValueError(f"Unknown GOOGLE_SERVICES: {', '.join(unknown)}")
    return [SERVICE_SCOPES[s] for s in settings.google_service_list]


def sign_in_scope(settings: Settings) -> str:
    return " ".join([SIGN_IN_SCOPES, *requested_scopes(settings)])


def services_in(scopes: str) -> list[str]:
    granted = set(scopes.split())
    return [name for name, scope in SERVICE_SCOPES.items() if scope in granted]


def _fernet(settings: Settings) -> Fernet:
    digest = hashlib.sha256(b"jarvis-google-tokens:" + settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(settings: Settings, value: str) -> str:
    return _fernet(settings).encrypt(value.encode()).decode()


def decrypt(settings: Settings, value: str) -> str | None:
    """None when the value was encrypted under another SECRET_KEY."""
    try:
        return _fernet(settings).decrypt(value.encode()).decode()
    except InvalidToken:
        return None


async def get_grant(db: AsyncSession, settings: Settings, user_id: uuid.UUID) -> GoogleGrant | None:
    """The user's grant, or None. A grant that no longer decrypts (SECRET_KEY changed) is
    dropped, so the next sign-in asks again."""
    grant = await db.get(GoogleGrant, user_id)
    if grant is not None and decrypt(settings, grant.refresh_token) is None:
        await db.delete(grant)
        await db.flush()
        return None
    return grant


async def save_from_sign_in(
    db: AsyncSession, settings: Settings, user_id: uuid.UUID, token: dict
) -> bool:
    """Store the services granted in a sign-in's token response.

    Returns False when the user granted services but Google sent no refresh token and none is
    on file (it only sends one on the first consent), so the caller must ask for consent again.
    """
    services = services_in(token.get("scope", ""))
    grant = await get_grant(db, settings, user_id)
    if not services:
        # Nothing ticked, or every service revoked in the Google account since.
        if grant is not None:
            await db.delete(grant)
            await db.flush()
        return True
    refresh = token.get("refresh_token")
    if refresh is None and grant is None:
        return False
    if grant is None:
        grant = GoogleGrant(user_id=user_id)
        db.add(grant)
    if refresh is not None:
        grant.refresh_token = encrypt(settings, refresh)
    grant.scopes = token["scope"]
    _set_access(settings, grant, token)
    await db.flush()
    return True


def _set_access(settings: Settings, grant: GoogleGrant, token: dict) -> None:
    access = token.get("access_token")
    grant.access_token = encrypt(settings, access) if access else None
    expires_in = token.get("expires_in")
    grant.access_expires_at = (
        utcnow() + timedelta(seconds=int(expires_in)) if access and expires_in else None
    )


async def access_token(
    db: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    service: str,
    http: httpx.AsyncClient | None = None,
) -> str:
    """A live access token covering `service`, refreshed if needed. Raises GoogleNotConnected.

    Commits nothing; the caller commits so a refreshed token is kept.
    """
    grant = await get_grant(db, settings, user_id)
    if grant is None or service not in services_in(grant.scopes):
        raise GoogleNotConnected(service)
    if grant.access_token and grant.access_expires_at:
        if grant.access_expires_at - EXPIRY_MARGIN > utcnow():
            access = decrypt(settings, grant.access_token)
            if access:
                return access

    data = {
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "refresh_token": decrypt(settings, grant.refresh_token),
        "grant_type": "refresh_token",
    }
    client = http or httpx.AsyncClient(timeout=20)
    try:
        resp = await client.post(TOKEN_URL, data=data)
    finally:
        if http is None:
            await client.aclose()
    if resp.status_code in (400, 401):
        # invalid_grant: revoked in the Google account, or expired (7 days while the OAuth app
        # is in Testing). The user has to sign in again to restore it.
        log.info("Google refused the refresh token for %s: %s", user_id, resp.text[:200])
        await db.delete(grant)
        await db.flush()
        raise GoogleNotConnected(service)
    resp.raise_for_status()
    token = resp.json()
    if token.get("scope"):
        grant.scopes = token["scope"]
    _set_access(settings, grant, token)
    await db.flush()
    if service not in services_in(grant.scopes):
        raise GoogleNotConnected(service)
    return token["access_token"]


async def disconnect(
    db: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    http: httpx.AsyncClient | None = None,
) -> None:
    """Revoke the grant at Google (best effort) and forget it."""
    grant = await get_grant(db, settings, user_id)
    if grant is None:
        return
    refresh = decrypt(settings, grant.refresh_token)
    client = http or httpx.AsyncClient(timeout=20)
    try:
        resp = await client.post(REVOKE_URL, data={"token": refresh})
        if resp.status_code >= 400:
            log.info("Google revoke for %s returned %s", user_id, resp.status_code)
    except httpx.HTTPError as exc:
        log.warning("Google revoke for %s failed: %s", user_id, exc)
    finally:
        if http is None:
            await client.aclose()
    await db.delete(grant)
    await db.flush()
