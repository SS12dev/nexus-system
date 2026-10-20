import uuid

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from jarvis_api.appsettings import maintenance_notice
from jarvis_api.cache import cached_spend
from jarvis_api.deps import CurrentUser, DbDep, GatewayDep, RedisDep, SettingsDep
from jarvis_api.schemas import BudgetOut, GoogleAccessOut, MeOut, SessionOut, UserOut
from jarvis_api.sessions import hash_token, revoke_all
from jarvis_common import google
from jarvis_common.litellm import active_key, get_budget
from jarvis_common.models import AuditLog, Session, utcnow

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/me", response_model=MeOut)
async def me(
    user: CurrentUser, db: DbDep, settings: SettingsDep, gateway: GatewayDep, redis: RedisDep
) -> MeOut:
    budget = await get_budget(db, settings, user)
    await db.commit()
    key = await active_key(db, user.id)
    spend = await cached_spend(redis, gateway, key.secret) if key else None
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        budget=BudgetOut(max_usd=budget.max_usd, duration=budget.duration, spend_usd=spend),
        notice=await maintenance_notice(db),
    )


@router.get("/me/sessions", response_model=list[SessionOut])
async def my_sessions(
    request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> list[SessionOut]:
    """Browsers signed in as this user, newest first."""
    current = hash_token(request.cookies.get(settings.session_cookie_name) or "")
    rows = await db.scalars(
        select(Session)
        .where(
            Session.user_id == user.id,
            Session.revoked_at.is_(None),
            Session.expires_at > utcnow(),
        )
        .order_by(Session.created_at.desc())
    )
    out = []
    for s in rows.all():
        item = SessionOut.model_validate(s)
        item.current = s.token_hash == current
        out.append(item)
    return out


@router.delete("/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_session(session_id: uuid.UUID, user: CurrentUser, db: DbDep) -> None:
    s = await db.scalar(select(Session).where(Session.id == session_id, Session.user_id == user.id))
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    s.revoked_at = s.revoked_at or utcnow()
    db.add(AuditLog(actor_id=user.id, action="session.revoked", target=str(s.id)))
    await db.commit()


@router.post("/me/sessions/sign-out-others", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_others(
    request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> None:
    """Sign out every other browser, keeping this one."""
    current = hash_token(request.cookies.get(settings.session_cookie_name) or "")
    await revoke_all(db, user, keep_hash=current)
    db.add(AuditLog(actor_id=user.id, action="session.revoked_others", target=str(user.id)))
    await db.commit()


@router.get("/me/google", response_model=GoogleAccessOut)
async def google_access(user: CurrentUser, db: DbDep, settings: SettingsDep) -> GoogleAccessOut:
    """Which Google services this user let Jarvis use, out of those it asks for."""
    grant = await google.get_grant(db, settings, user.id)
    await db.commit()
    return GoogleAccessOut(
        available=settings.oidc_enabled and bool(settings.google_service_list),
        requested=settings.google_service_list,
        granted=google.services_in(grant.scopes) if grant else [],
        connected_at=grant.updated_at if grant else None,
    )


@router.delete("/me/google", status_code=status.HTTP_204_NO_CONTENT)
async def google_disconnect(
    request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> None:
    """Revoke Jarvis's access to the user's Google services."""
    await google.disconnect(db, settings, user.id, http=request.app.state.google_http)
    await db.commit()
