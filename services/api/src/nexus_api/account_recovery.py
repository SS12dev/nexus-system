"""The verified former account owner can request admin-approved recovery or a clean reset."""

import logging

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api import access
from nexus_api.queue import JobQueue
from nexus_common import notices
from nexus_common.events import Event, publish
from nexus_common.models import (
    AccountRecoveryRequest,
    AccountRecoveryStatus,
    AuditLog,
    NotificationKind,
    User,
    utcnow,
)
from nexus_common.settings import Settings

log = logging.getLogger(__name__)


async def submit(
    db: AsyncSession,
    settings: Settings,
    request: Request,
    redis: Redis,
    queue: JobQueue,
    who: dict,
    action: str,
):
    user = await db.scalar(select(User).where(User.email == who["email"]).with_for_update())
    if user is None or user.account_deleted_at is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This account is no longer awaiting recovery."
        )

    now = utcnow()
    if action == "recover":
        expires_at = user.account_delete_expires_at
        if (
            expires_at is None
            or expires_at <= now
            or user.account_purge_started_at is not None
            or user.account_reset_requested_at is not None
        ):
            raise HTTPException(
                status.HTTP_410_GONE,
                "This account can no longer be recovered. You can request a clean account instead.",
            )
        recovery = await db.scalar(
            select(AccountRecoveryRequest)
            .where(
                AccountRecoveryRequest.user_id == user.id,
                AccountRecoveryRequest.status == AccountRecoveryStatus.pending,
            )
            .with_for_update()
        )
        created = recovery is None
        if recovery is None:
            headers = {key.lower(): value for key, value in request.headers.items()}
            recovery = AccountRecoveryRequest(
                user_id=user.id,
                email=user.email,
                name=who.get("name") or user.name,
                avatar_url=who.get("avatar_url") or user.avatar_url,
                role=user.role,
                status=AccountRecoveryStatus.pending,
                ip=access.client_ip(headers, request.client.host if request.client else None)[:64],
                user_agent=(headers.get("user-agent") or "")[:500],
                expires_at=expires_at,
            )
            db.add(recovery)
            db.add(
                AuditLog(
                    actor_id=None,
                    action="account.recovery_requested",
                    target=str(user.id),
                    data={"request_id": str(recovery.id)},
                )
            )
            await db.flush()

        await db.commit()
        if created:
            await _notify_deciders(db, redis, settings, recovery)
        if recovery.email_queued_at is None:
            try:
                await queue.enqueue_account_recovery_notice("requested", recovery.id)
            except Exception as exc:
                log.exception(
                    "account.recovery_email_enqueue_failed",
                    extra={"request_id": str(recovery.id)},
                )
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Your recovery request could not be sent. Please try again.",
                ) from exc
            recovery.email_queued_at = now
            await db.commit()
        return await access.state(db, settings, who)

    if action != "new":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown account action.")

    if user.account_reset_requested_at is None:
        if user.account_purge_started_at is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Permanent deletion has already started. Try signing in again shortly.",
            )
        user.account_reset_requested_at = now
        user.account_purge_started_at = now
        await db.execute(
            update(AccountRecoveryRequest)
            .where(
                AccountRecoveryRequest.user_id == user.id,
                AccountRecoveryRequest.status == AccountRecoveryStatus.pending,
            )
            .values(status=AccountRecoveryStatus.cancelled, decided_at=now)
        )
        db.add(
            AuditLog(
                actor_id=None,
                action="user.account_reset_requested",
                target=str(user.id),
                data={"email": user.email},
            )
        )
        await db.commit()
    try:
        await queue.enqueue_account_purge(user.id)
    except Exception as exc:
        log.exception("account.reset_enqueue_failed", extra={"user_id": str(user.id)})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Your clean account is queued for deletion but the worker is unavailable. Try again.",
        ) from exc
    return await access.state(db, settings, who)


async def _notify_deciders(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    recovery: AccountRecoveryRequest,
) -> None:
    deciders = await access.deciders(db)
    pending = await db.scalar(
        select(func.count()).where(
            AccountRecoveryRequest.status == AccountRecoveryStatus.pending,
            AccountRecoveryRequest.expires_at > utcnow(),
        )
    )
    for decider in deciders:
        try:
            await notices.notify(
                db,
                redis,
                settings,
                decider.id,
                NotificationKind.account_recovery,
                f"{recovery.name or recovery.email} requested account recovery",
                "Review the request on the People page.",
                url="/people",
                tag=f"account-recovery:{recovery.id}",
            )
        except Exception:
            log.exception(
                "account.recovery_notification_failed",
                extra={
                    "request_id": str(recovery.id),
                    "user_id": str(decider.id),
                },
            )
        try:
            await publish(
                redis,
                decider.id,
                Event(
                    type="account.recovery.pending",
                    data={"request_id": str(recovery.id), "count": pending},
                ),
            )
        except Exception:
            log.exception(
                "account.recovery_event_failed",
                extra={"request_id": str(recovery.id), "user_id": str(decider.id)},
            )
