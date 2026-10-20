from fastapi import APIRouter, Request, status

from jarvis_api.deps import CurrentUser, DbDep, GatewayDep, SettingsDep
from jarvis_api.schemas import BudgetOut, GoogleAccessOut, MeOut, UserOut
from jarvis_common import google
from jarvis_common.litellm import active_key, get_budget

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUser, db: DbDep, settings: SettingsDep, gateway: GatewayDep) -> MeOut:
    budget = await get_budget(db, settings, user)
    await db.commit()
    key = await active_key(db, user.id)
    spend = await gateway.spend(key.secret) if key else None
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        budget=BudgetOut(max_usd=budget.max_usd, duration=budget.duration, spend_usd=spend),
    )


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
