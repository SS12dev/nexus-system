import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from nexus_api.cache import cached_spend
from nexus_api.deps import AdminUser, DbDep, GatewayDep, RedisDep, SettingsDep
from nexus_api.schemas import (
    AdminUserOut,
    BudgetOut,
    GrantsIn,
    InviteIn,
    UserOut,
    UserUpdateIn,
)
from nexus_api.sessions import revoke_all
from nexus_api.users import (
    can_assign,
    can_manage,
    create_user,
    normalize_email,
    owner_count,
    set_role,
)
from nexus_common.litellm import GatewayError, LiteLLMAdmin, active_key, get_budget
from nexus_common.models import AuditLog, Capability, RoleName, ToolGrant, User, UserStatus
from nexus_common.settings import Settings
from nexus_common.systems import granted_capabilities
from nexus_common.toolinfo import capabilities

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


async def admin_view(db, settings: Settings, gateway: LiteLLMAdmin, user: User) -> AdminUserOut:
    budget = await get_budget(db, settings, user)
    key = await active_key(db, user.id)
    # Not cached: this follows a change, so show LiteLLM's current figure.
    spend = await gateway.spend(key.secret) if key else None
    return AdminUserOut(
        **UserOut.model_validate(user).model_dump(),
        budget=BudgetOut(max_usd=budget.max_usd, duration=budget.duration, spend_usd=spend),
    )


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    actor: AdminUser, db: DbDep, settings: SettingsDep, gateway: GatewayDep, redis: RedisDep
):
    users = (await db.scalars(select(User).order_by(User.created_at))).all()
    budgets = [await get_budget(db, settings, u) for u in users]
    await db.commit()
    keys = [await active_key(db, u.id) for u in users]
    spends = await asyncio.gather(
        *(cached_spend(redis, gateway, k.secret) if k else _none() for k in keys)
    )
    return [
        AdminUserOut(
            **UserOut.model_validate(u).model_dump(),
            budget=BudgetOut(max_usd=b.max_usd, duration=b.duration, spend_usd=s),
        )
        for u, b, s in zip(users, budgets, spends, strict=True)
    ]


async def _none() -> None:
    return None


@router.post("/users", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: InviteIn, actor: AdminUser, db: DbDep, settings: SettingsDep, gateway: GatewayDep
):
    """Invite someone by email. They join the first time they sign in with that address."""
    if not can_assign(actor, body.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"You can't grant the {body.role} role")
    email = normalize_email(body.email)
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That email already has an account")
    user = await create_user(
        db, email=email, role=body.role, status=UserStatus.invited, invited_by=actor
    )
    budget = await get_budget(db, settings, user)
    if body.budget_usd is not None:
        budget.max_usd = body.budget_usd
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="user.invited",
            target=str(user.id),
            data={"email": email, "role": body.role, "budget_usd": str(budget.max_usd)},
        )
    )
    await db.commit()
    return await admin_view(db, settings, gateway, user)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateIn,
    actor: AdminUser,
    db: DbDep,
    settings: SettingsDep,
    gateway: GatewayDep,
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not can_manage(actor, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change this user")

    changes: dict[str, str] = {}
    losing_owner = user.role == RoleName.owner and (
        (body.role is not None and body.role != RoleName.owner)
        or body.status == UserStatus.disabled
    )
    if losing_owner and await owner_count(db) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nexus needs at least one active owner")

    if body.role is not None and body.role != user.role:
        if not can_assign(actor, body.role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"You can't grant the {body.role} role")
        changes["role"] = body.role
        await set_role(db, user, body.role)

    key = await active_key(db, user.id)
    if body.status is not None and body.status != user.status:
        if body.status == UserStatus.invited:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Can't set a user to invited"
            )
        if body.status == UserStatus.active and user.last_login_at is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Invited users activate by signing in"
            )
        changes["status"] = body.status
        user.status = body.status
        if body.status == UserStatus.disabled:
            await revoke_all(db, user)
        if key:
            await _gateway_call(gateway.set_blocked(key.secret, body.status == UserStatus.disabled))

    if body.budget_usd is not None:
        budget = await get_budget(db, settings, user)
        if budget.max_usd != body.budget_usd:
            changes["budget_usd"] = str(body.budget_usd)
            budget.max_usd = body.budget_usd
            if key:
                await _gateway_call(
                    gateway.update_budget(key.secret, budget.max_usd, budget.duration)
                )

    if changes:
        db.add(
            AuditLog(actor_id=actor.id, action="user.updated", target=str(user.id), data=changes)
        )
    await db.commit()
    return await admin_view(db, settings, gateway, user)


async def _gateway_call(coro) -> None:
    """Apply a change to the user's gateway key. The change must land before we record it,
    otherwise the budget or block shown here would not be the one enforced."""
    try:
        await coro
    except GatewayError as exc:
        log.warning("Gateway update failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Couldn't update the model gateway"
        ) from exc


@router.get("/users/{user_id}/grants")
async def get_grants(user_id: uuid.UUID, actor: AdminUser, db: DbDep):
    """Capabilities beyond a member's defaults: exec (run code in the sandbox) and outbound
    (send email and other actions outside Nexus, always with approval). Admins have both."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    granted = await granted_capabilities(db, user.id)
    return {
        "granted": sorted(c.value for c in granted),
        "effective": sorted(c.value for c in capabilities(user, granted)),
    }


@router.put("/users/{user_id}/grants")
async def set_grants(user_id: uuid.UUID, body: GrantsIn, actor: AdminUser, db: DbDep):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    try:
        wanted = {Capability(c) for c in body.capabilities}
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown capability") from exc
    await db.execute(delete(ToolGrant).where(ToolGrant.user_id == user.id))
    for c in sorted(wanted):
        db.add(ToolGrant(user_id=user.id, capability=c))
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="user.grants",
            target=str(user.id),
            data={"capabilities": sorted(c.value for c in wanted)},
        )
    )
    await db.commit()
    return await get_grants(user_id, actor, db)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(user_id: uuid.UUID, actor: AdminUser, db: DbDep) -> None:
    """Take back an invite nobody has used yet. People who have signed in are disabled
    instead, which keeps their data and the audit trail."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not can_manage(actor, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change this user")
    if user.status != UserStatus.invited or user.last_login_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only unused invites can be removed; disable the user"
        )
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="user.invite_revoked",
            target=str(user.id),
            data={"email": user.email, "role": user.role},
        )
    )
    await db.delete(user)
    await db.commit()


@router.post("/users/{user_id}/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_user(user_id: uuid.UUID, actor: AdminUser, db: DbDep) -> None:
    """Sign someone out on every device, e.g. after a lost phone. They can sign in again."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id != actor.id and not can_manage(actor, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change this user")
    await revoke_all(db, user)
    db.add(AuditLog(actor_id=actor.id, action="user.signed_out", target=str(user.id)))
    await db.commit()
