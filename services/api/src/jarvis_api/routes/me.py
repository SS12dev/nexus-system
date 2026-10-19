from fastapi import APIRouter

from jarvis_api.deps import CurrentUser, DbDep, GatewayDep, SettingsDep
from jarvis_api.schemas import BudgetOut, MeOut, UserOut
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
