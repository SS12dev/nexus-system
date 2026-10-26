"""Admin decisions for deleted-account recovery requests."""

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from nexus_api import access
from nexus_api.deps import AdminUser, DbDep, GatewayDep, QueueDep, RedisDep, SettingsDep
from nexus_api.schemas import AccountRecoveryListOut, AccountRecoveryRequestOut
from nexus_api.users import can_manage
from nexus_common.events import Event, publish
from nexus_common.litellm import active_key
from nexus_common.models import (
    AccountRecoveryRequest,
    AccountRecoveryStatus,
    AuditLog,
    User,
    UserStatus,
    utcnow,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/account-recovery", tags=["admin"])


async def _out(db, rows: list[AccountRecoveryRequest]) -> list[AccountRecoveryRequestOut]:
    decider_ids = {row.decided_by for row in rows if row.decided_by}
    names = {}
    if decider_ids:
        users = await db.scalars(select(User).where(User.id.in_(decider_ids)))
        names = {user.id: user.name or user.email for user in users}
    result = []
    for row in rows:
        item = AccountRecoveryRequestOut.model_validate(row)
        item.decided_by_name = names.get(row.decided_by)
        result.append(item)
    return result


async def _pending_count(db) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(AccountRecoveryRequest)
        .where(
            AccountRecoveryRequest.status == AccountRecoveryStatus.pending,
            AccountRecoveryRequest.expires_at > utcnow(),
        )
    )


async def _announce(db, redis, fields: dict[str, str]) -> None:
    try:
        count = await _pending_count(db)
        event = Event(type="account.recovery.pending", data={"count": count})
        for decider in await access.deciders(db):
            await publish(redis, decider.id, event)
    except Exception:
        log.exception("account.recovery_event_failed", extra=fields)


async def _locked(db, request_id: uuid.UUID) -> tuple[AccountRecoveryRequest, User]:
    snapshot = await db.get(AccountRecoveryRequest, request_id)
    if snapshot is None or snapshot.user_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recovery request not found")
    user = await db.scalar(select(User).where(User.id == snapshot.user_id).with_for_update())
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recovery request not found")
    row = await db.scalar(
        select(AccountRecoveryRequest)
        .where(AccountRecoveryRequest.id == request_id)
        .with_for_update()
    )
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recovery request not found")
    return row, user


@router.get("", response_model=AccountRecoveryListOut)
async def list_requests(
    actor: AdminUser,
    db: DbDep,
    settings: SettingsDep,
    which: Literal["pending", "decided"] = Query("pending", alias="status"),
    limit: int = Query(50, ge=1, le=200),
):
    del actor
    q = select(AccountRecoveryRequest)
    if which == "pending":
        q = q.where(
            AccountRecoveryRequest.status == AccountRecoveryStatus.pending,
            AccountRecoveryRequest.expires_at > utcnow(),
        ).order_by(AccountRecoveryRequest.created_at)
    else:
        q = q.where(AccountRecoveryRequest.status != AccountRecoveryStatus.pending).order_by(
            AccountRecoveryRequest.decided_at.desc()
        )
    rows = list((await db.scalars(q.limit(limit))).all())
    return AccountRecoveryListOut(
        requests=await _out(db, rows),
        pending=await _pending_count(db),
        email_notifications=settings.smtp_enabled,
    )


@router.post("/{request_id}/approve", response_model=AccountRecoveryRequestOut)
async def approve(
    request_id: uuid.UUID,
    actor: AdminUser,
    db: DbDep,
    settings: SettingsDep,
    gateway: GatewayDep,
    redis: RedisDep,
    queue: QueueDep,
):
    row, user = await _locked(db, request_id)
    if not can_manage(actor, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recovery request not found")

    if row.status == AccountRecoveryStatus.approved:
        if settings.smtp_enabled and row.decision_email_queued_at is None:
            await _queue_decision_email(db, queue, row)
        return (await _out(db, [row]))[0]
    if row.status != AccountRecoveryStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request was already {row.status}")
    now = utcnow()
    if (
        row.expires_at <= now
        or user.account_deleted_at is None
        or user.account_delete_expires_at is None
        or user.account_delete_expires_at <= now
        or user.account_purge_started_at is not None
        or user.account_reset_requested_at is not None
    ):
        raise HTTPException(status.HTTP_410_GONE, "This account is no longer recoverable")

    key = await active_key(db, user.id)
    if key:
        try:
            await gateway.set_blocked(key.secret, False)
        except Exception as exc:
            log.exception(
                "account.recovery_gateway_unblock_failed",
                extra={"request_id": str(row.id), "user_id": str(user.id)},
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "The account could not be restored because its model access could not be enabled.",
            ) from exc

    user.account_deleted_at = None
    user.account_delete_expires_at = None
    user.account_purge_started_at = None
    user.account_reset_requested_at = None
    user.status = UserStatus.active
    if row.name:
        user.name = row.name
    if row.avatar_url:
        user.avatar_url = row.avatar_url
    row.status = AccountRecoveryStatus.approved
    row.decided_by = actor.id
    row.decided_at = now
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="account.recovery_approved",
            target=str(row.id),
            data={"user_id": str(user.id), "email": user.email},
        )
    )
    await db.commit()
    log.info(
        "account.recovery_approved",
        extra={"request_id": str(row.id), "user_id": str(user.id), "actor_id": str(actor.id)},
    )
    await _announce(db, redis, {"request_id": str(row.id), "action": "approved"})
    if settings.smtp_enabled:
        await _queue_decision_email(db, queue, row)
    return (await _out(db, [row]))[0]


async def _queue_decision_email(db, queue, row: AccountRecoveryRequest):
    try:
        await queue.enqueue_account_recovery_notice("approved", row.id)
    except Exception as exc:
        log.exception(
            "account.recovery_email_enqueue_failed",
            extra={"request_id": str(row.id), "kind": "approved"},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The account was restored, but its confirmation email could not be queued. "
            "Retry approval to send it.",
        ) from exc
    row.decision_email_queued_at = utcnow()
    await db.commit()


@router.post("/{request_id}/deny", response_model=AccountRecoveryRequestOut)
async def deny(
    request_id: uuid.UUID,
    actor: AdminUser,
    db: DbDep,
    redis: RedisDep,
):
    row, user = await _locked(db, request_id)
    if not can_manage(actor, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recovery request not found")
    if row.status != AccountRecoveryStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request was already {row.status}")
    row.status = AccountRecoveryStatus.denied
    row.decided_by = actor.id
    row.decided_at = utcnow()
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="account.recovery_denied",
            target=str(row.id),
            data={"user_id": str(user.id), "email": user.email},
        )
    )
    await db.commit()
    log.info(
        "account.recovery_denied",
        extra={"request_id": str(row.id), "user_id": str(user.id), "actor_id": str(actor.id)},
    )
    await _announce(db, redis, {"request_id": str(row.id), "action": "denied"})
    return (await _out(db, [row]))[0]
