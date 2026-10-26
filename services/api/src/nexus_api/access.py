"""Access requests: someone whose sign-in was refused for lack of an invite asks for one, and an
owner or admin approves (which invites them) or denies it.

The address on a request is always one the identity provider verified for this browser a few
minutes earlier; the login page never sends one. That stops people asking on someone else's
behalf, and filling the People page with addresses nobody controls.
"""

import logging
import time
import uuid
from datetime import timedelta

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.queue import JobQueue
from nexus_api.schemas import AccessStateOut
from nexus_api.sessions import client_ip
from nexus_api.users import normalize_email
from nexus_common import notices
from nexus_common.access import deciders, pending_count
from nexus_common.events import Event, publish
from nexus_common.models import (
    AccessRequest,
    AccessRequestStatus,
    AccountRecoveryRequest,
    AccountRecoveryStatus,
    AuditLog,
    NotificationKind,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

# Kept in the signed OAuth session cookie after a refused sign-in.
SESSION_KEY = "access_identity"
# How long that proof of the address is good for.
IDENTITY_MAX_AGE_S = 30 * 60


class RequestsFull(Exception):
    """Too many requests are already waiting."""


def remember_identity(
    request: Request,
    email: str,
    name: str | None = None,
    avatar_url: str | None = None,
    sub: str | None = None,
) -> None:
    """Note, in the signed session cookie, the verified address whose sign-in was just refused."""
    request.session[SESSION_KEY] = {
        "email": normalize_email(email),
        "name": (name or "")[:200] or None,
        "avatar_url": (avatar_url or "")[:1000] or None,
        "sub": (sub or "")[:255] or None,
        "at": int(time.time()),
    }


def forget_identity(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def identity(request: Request) -> dict | None:
    """The address this browser proved it owns, if it did so recently."""
    data = request.session.get(SESSION_KEY)
    if not isinstance(data, dict) or not isinstance(data.get("email"), str):
        return None
    if time.time() - int(data.get("at", 0)) > IDENTITY_MAX_AGE_S:
        forget_identity(request)
        return None
    return data


async def _latest(db: AsyncSession, email: str) -> AccessRequest | None:
    return await db.scalar(
        select(AccessRequest)
        .where(AccessRequest.email == email)
        .order_by(AccessRequest.created_at.desc())
        .limit(1)
    )


async def state(db: AsyncSession, settings: Settings, who: dict) -> AccessStateOut:
    email = who["email"]
    out = AccessStateOut(
        email=email,
        name=who.get("name"),
        avatar_url=who.get("avatar_url"),
        status="can_request",
        email_on_approval=settings.smtp_enabled,
    )
    user = await db.scalar(select(User).where(User.email == email))
    if user is not None and user.account_deleted_at is not None:
        recovery = await db.scalar(
            select(AccountRecoveryRequest)
            .where(AccountRecoveryRequest.user_id == user.id)
            .order_by(AccountRecoveryRequest.created_at.desc())
            .limit(1)
        )
        out.account_delete_expires_at = user.account_delete_expires_at
        if user.account_reset_requested_at is not None:
            out.status = "account_resetting"
        elif (
            user.account_purge_started_at is not None
            or user.account_delete_expires_at is None
            or user.account_delete_expires_at <= utcnow()
        ):
            out.status = "account_expired"
        elif recovery is not None and recovery.status == AccountRecoveryStatus.pending:
            out.status = "recovery_pending"
            out.recovery_requested_at = recovery.created_at
        elif recovery is not None and recovery.status == AccountRecoveryStatus.denied:
            out.status = "recovery_denied"
            out.recovery_requested_at = recovery.created_at
        else:
            out.status = "account_deleted"
        return out
    if user is not None and user.status != UserStatus.disabled:
        out.status = "approved"
        return out
    latest = await _latest(db, email)
    if latest is None:
        return out
    out.requested_at = latest.created_at
    if latest.status == AccessRequestStatus.pending:
        out.status = "pending"
    elif latest.status == AccessRequestStatus.denied and latest.decided_at:
        retry = latest.decided_at + timedelta(days=settings.access_request_cooldown_days)
        if retry > utcnow():
            out.status = "denied"
            out.retry_after = retry
    return out


async def submit(
    db: AsyncSession, settings: Settings, request: Request, who: dict, note: str | None
) -> tuple[AccessStateOut, AccessRequest | None]:
    """Ask for access as `who`. Returns the new state, and the request if a new one was made
    (the caller then tells the deciders). Asking again while pending only updates the note."""
    current = await state(db, settings, who)
    if current.status in ("approved", "denied"):
        return current, None
    if current.status == "pending":
        pending = await db.scalar(
            select(AccessRequest).where(
                AccessRequest.email == who["email"],
                AccessRequest.status == AccessRequestStatus.pending,
            )
        )
        if pending is not None and note and note != pending.note:
            pending.note = note
            await db.commit()
        return current, None

    if await pending_count(db) >= settings.access_request_max_pending:
        raise RequestsFull
    headers = {k.lower(): v for k, v in request.headers.items()}
    row = AccessRequest(
        email=who["email"],
        name=who.get("name"),
        avatar_url=who.get("avatar_url"),
        note=note,
        status=AccessRequestStatus.pending,
        ip=client_ip(headers, request.client.host if request.client else None)[:64],
        user_agent=(headers.get("user-agent") or "")[:500],
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        # The same person double-submitted; the other request won.
        await db.rollback()
        return await state(db, settings, who), None
    db.add(
        AuditLog(
            actor_id=None,
            action="access.requested",
            target=str(row.id),
            data={"email": row.email},
        )
    )
    await db.commit()
    return await state(db, settings, who), row


async def announce(
    db: AsyncSession,
    redis: Redis,
    queue: JobQueue | None,
    kind: str | None,
    request_id: uuid.UUID,
    settings: Settings,
) -> None:
    """After a commit: update every decider's badge live, and queue the email for `kind`.
    Neither may fail the request that caused it; a lost badge update is fixed by the next page
    load, and a lost email is logged."""
    fields = {"access_request_id": str(request_id), "kind": kind}
    if queue is not None and kind is not None:
        try:
            await queue.enqueue_access_notice(kind, request_id)
        except Exception:
            log.exception("access.enqueue_failed", extra=fields)
    if kind == "requested":
        req = await db.get(AccessRequest, request_id)
        title = (
            f"{req.name or req.email} requested access" if req is not None else "New access request"
        )
        for decider in await deciders(db):
            try:
                await notices.notify(
                    db,
                    redis,
                    settings,
                    decider.id,
                    NotificationKind.access_request,
                    title,
                    "Review the request on the People page.",
                    url="/people",
                    tag=f"access-request:{request_id}",
                )
            except Exception:
                log.exception(
                    "access.notification_failed",
                    extra={**fields, "user_id": str(decider.id)},
                )
    try:
        count = await pending_count(db)
        event = Event(type="access.pending", data={"count": count})
        for user in await deciders(db):
            await publish(redis, user.id, event)
    except RedisError:
        log.warning("access.publish_failed", extra=fields, exc_info=True)
