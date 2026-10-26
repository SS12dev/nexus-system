"""Deciding access requests, on the People page. Owners and admins can; approving invites the
person with the role chosen here (an admin can only grant roles below their own)."""

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from nexus_api import access
from nexus_api.deps import AdminUser, DbDep, GatewayDep, QueueDep, RedisDep, SettingsDep
from nexus_api.routes.admin import admin_view
from nexus_api.schemas import AccessRequestListOut, AccessRequestOut, ApproveIn, ApproveOut
from nexus_api.users import can_assign, create_user
from nexus_common.access import pending_count
from nexus_common.litellm import get_budget
from nexus_common.models import (
    AccessRequest,
    AccessRequestStatus,
    AuditLog,
    User,
    UserStatus,
    utcnow,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/access-requests", tags=["admin"])


async def _out(db, rows: list[AccessRequest]) -> list[AccessRequestOut]:
    deciders = {r.decided_by for r in rows if r.decided_by}
    names = {}
    if deciders:
        for u in (await db.scalars(select(User).where(User.id.in_(deciders)))).all():
            names[u.id] = u.name or u.email
    emails = {r.email for r in rows}
    counts = {}
    if emails:
        counts = dict(
            (
                await db.execute(
                    select(AccessRequest.email, func.count())
                    .where(AccessRequest.email.in_(emails))
                    .group_by(AccessRequest.email)
                )
            ).all()
        )
    out = []
    for r in rows:
        item = AccessRequestOut.model_validate(r)
        item.decided_by_name = names.get(r.decided_by)
        item.previous = max(0, counts.get(r.email, 1) - 1)
        out.append(item)
    return out


@router.get("", response_model=AccessRequestListOut)
async def list_requests(
    actor: AdminUser,
    db: DbDep,
    settings: SettingsDep,
    which: Literal["pending", "decided"] = Query("pending", alias="status"),
    limit: int = Query(50, ge=1, le=200),
):
    q = select(AccessRequest)
    if which == "pending":
        q = q.where(AccessRequest.status == AccessRequestStatus.pending).order_by(
            AccessRequest.created_at
        )
    else:
        q = q.where(AccessRequest.status != AccessRequestStatus.pending).order_by(
            AccessRequest.decided_at.desc()
        )
    rows = list((await db.scalars(q.limit(limit))).all())
    return AccessRequestListOut(
        requests=await _out(db, rows),
        pending=await pending_count(db),
        email_notifications=settings.smtp_enabled,
        enabled=settings.access_requests_open,
    )


async def _pending(db, request_id: uuid.UUID) -> AccessRequest:
    # Locked, so two admins deciding at once can't both act on it.
    req = await db.scalar(
        select(AccessRequest).where(AccessRequest.id == request_id).with_for_update()
    )
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if req.status != AccessRequestStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request was already {req.status}")
    return req


@router.post("/{request_id}/approve", response_model=ApproveOut)
async def approve(
    request_id: uuid.UUID,
    body: ApproveIn,
    actor: AdminUser,
    db: DbDep,
    settings: SettingsDep,
    gateway: GatewayDep,
    redis: RedisDep,
    queue: QueueDep,
):
    """Invite the person who asked. They join the next time they sign in."""
    if not can_assign(actor, body.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"You can't grant the {body.role} role")
    req = await _pending(db, request_id)
    user = await db.scalar(select(User).where(User.email == req.email))
    if user is None:
        user = await create_user(
            db,
            email=req.email,
            role=body.role,
            status=UserStatus.invited,
            invited_by=actor,
            name=req.name,
        )
        budget = await get_budget(db, settings, user)
        if body.budget_usd is not None:
            budget.max_usd = body.budget_usd
    elif user.status == UserStatus.disabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That address belongs to a disabled account. Enable it on the People page instead.",
        )
    req.status = AccessRequestStatus.approved
    req.decided_by = actor.id
    req.decided_at = utcnow()
    req.user_id = user.id
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="access.approved",
            target=str(req.id),
            data={"email": req.email, "role": user.role, "user_id": str(user.id)},
        )
    )
    await db.commit()
    log.info(
        "access.approved",
        extra={"access_request_id": str(req.id), "actor_id": str(actor.id), "role": user.role},
    )
    await access.announce(db, redis, queue, "approved", req.id, settings)
    return ApproveOut(
        request=(await _out(db, [req]))[0], user=await admin_view(db, settings, gateway, user)
    )


@router.post("/{request_id}/deny", response_model=AccessRequestOut)
async def deny(
    request_id: uuid.UUID,
    actor: AdminUser,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
):
    """Turn the request down. The person isn't emailed; the login page tells them if they look,
    and they may ask again after ACCESS_REQUEST_COOLDOWN_DAYS."""
    req = await _pending(db, request_id)
    req.status = AccessRequestStatus.denied
    req.decided_by = actor.id
    req.decided_at = utcnow()
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="access.denied",
            target=str(req.id),
            data={"email": req.email},
        )
    )
    await db.commit()
    log.info("access.denied", extra={"access_request_id": str(req.id), "actor_id": str(actor.id)})
    await access.announce(db, redis, None, None, req.id, settings)
    return (await _out(db, [req]))[0]
