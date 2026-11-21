"""Money's planning side: balances and card bills, subscription management, savings (FDs, RDs,
PPF, EPF, NPS), loans and EMIs, goals and the forecast. The maths lives in
nexus_common/money_plan.py and the queries in money_store.py; this file checks input and
ownership.

Owner only, and only ever the owner's own rows. Logs carry ids and counts, never amounts or
names. Nothing here can move money.
"""

import logging
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, HttpUrl, model_validator
from sqlalchemy import select

from nexus_api.deps import DbDep, OwnerUser, RedisDep, SettingsDep
from nexus_api.ratelimit import hit, too_many
from nexus_common import money
from nexus_common import money_plan as plan
from nexus_common import money_store as store
from nexus_common.models import (
    AuditLog,
    MoneyAccount,
    MoneyAsset,
    MoneyGoal,
    MoneyLoan,
    MoneySubscription,
    utcnow,
)
from nexus_common.organiser import local_today

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/money", tags=["money"])

Paise = Annotated[int, Field(ge=0, le=10**13)]
RateBp = Annotated[int, Field(ge=0, le=5000)]
Note = Annotated[str | None, Field(default=None, max_length=300)]
# An account, folio, PRAN or UAN number: letters, digits, spaces, "/" and "-". "" clears it.
AccountNo = Annotated[str | None, Field(default=None, max_length=40, pattern=r"^[0-9A-Za-z /-]*$")]
DETAILS_SHOWN_PER_HOUR = 60


async def _own(db, model, user_id: uuid.UUID, row_id: uuid.UUID, what: str):
    row = await db.get(model, row_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found.")
    return row


def _clean(text: str | None) -> str | None:
    return " ".join(text.split()) if text else text


def _set_account_no(row, cfg, number: str | None) -> None:
    """Seal an account number and keep its last four apart; empty clears both."""
    number = (_clean(number) or "").upper()
    row.account_no_enc = money.seal(cfg, number) or None
    row.account_last4 = money.last4(number)


async def _show_details(db, redis, user, kind: str, row_id: uuid.UUID) -> None:
    """Revealing an account number or nominee is rate-limited and kept in the audit log."""
    if wait := await hit(redis, "money-details", str(user.id), DETAILS_SHOWN_PER_HOUR, 3600):
        raise too_many(wait, "requests to show account details")
    db.add(AuditLog(actor_id=user.id, action="money.details_shown", target=f"{kind}:{row_id}"))
    await db.commit()
    log.info("money.details_shown", extra={"user_id": str(user.id), "kind": kind})


# --- Accounts and cards ------------------------------------------------------------------------


@router.get("/balances")
async def balances(user: OwnerUser, db: DbDep) -> dict:
    """Every account and card with its balance, bill and what's owed."""
    out = await store.accounts_view(db, user.id, local_today(user))
    loans = await store.loans_view(db, user.id, local_today(user))
    await db.commit()
    return {**out, "loans": loans}


class AccountEdit(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    hidden: bool | None = None
    # A balance typed in (banks) or the card's limit.
    balance_paise: int | None = Field(default=None, ge=-(10**13), le=10**13)
    credit_limit_paise: Paise | None = None
    # The current bill, when no statement email has been read.
    due_paise: Paise | None = None
    min_due_paise: Paise | None = None
    due_on: date | None = None
    # True marks the current bill paid; False undoes it.
    paid: bool | None = None


@router.patch("/accounts/{account_id}/details")
async def edit_account(
    account_id: uuid.UUID, body: AccountEdit, user: OwnerUser, db: DbDep, redis: RedisDep
) -> dict:
    a = await _own(db, MoneyAccount, user.id, account_id, "Account")
    today = local_today(user)
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields:
        a.name = _clean(body.name) or ""
    if body.hidden is not None:
        a.hidden = body.hidden
    if "balance_paise" in fields:
        a.balance_paise = body.balance_paise
        a.balance_on = today if body.balance_paise is not None else None
    if "credit_limit_paise" in fields:
        a.credit_limit_paise = body.credit_limit_paise
    if "due_paise" in fields or "due_on" in fields:
        a.due_paise = body.due_paise if "due_paise" in fields else a.due_paise
        a.due_on = body.due_on if "due_on" in fields else a.due_on
        a.statement_on = a.statement_on if a.statement_on and a.due_on else today
        a.paid_on = None
    if "min_due_paise" in fields:
        a.min_due_paise = body.min_due_paise
    if body.paid is not None:
        a.paid_on = today if body.paid else None
    await db.commit()
    log.info("money.account_edited", extra={"user_id": str(user.id), "account_id": str(a.id)})
    await store.changed(redis, user.id)
    view = await store.accounts_view(db, user.id, today)
    return next(x for x in view["items"] if x["id"] == str(a.id))


# --- Subscriptions -----------------------------------------------------------------------------


@router.get("/subscriptions")
async def subscriptions(user: OwnerUser, db: DbDep) -> dict:
    out = await store.subscriptions_view(db, user.id, local_today(user))
    await db.commit()
    return out


class SubscriptionIn(BaseModel):
    merchant: str = Field(min_length=1, max_length=120)
    status: Literal["active", "cancelled", "ignored"] | None = None
    remind_days: int | None = Field(default=None, ge=0, le=30)
    manage_url: HttpUrl | None = None
    note: Note
    cancelled_on: date | None = None
    # Adding one by hand.
    manual: bool | None = None
    amount_paise: Paise | None = None
    cadence: Literal["monthly", "quarterly", "yearly"] | None = None
    next_on: date | None = None
    category: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _manual_needs_terms(self) -> "SubscriptionIn":
        if self.manual and not (self.amount_paise and self.next_on):
            raise ValueError("A subscription added by hand needs an amount and a next date.")
        if self.category and self.category not in money.CATEGORIES:
            raise ValueError("Unknown category.")
        return self


@router.put("/subscriptions")
async def save_subscription(
    body: SubscriptionIn, user: OwnerUser, db: DbDep, redis: RedisDep
) -> dict:
    """Keep, cancel or hide a repeating payment, set its reminder, or add one by hand."""
    fields = body.model_dump(exclude_unset=True, exclude={"merchant"})
    if "manage_url" in fields:
        fields["manage_url"] = str(body.manage_url) if body.manage_url else None
    if "note" in fields:
        fields["note"] = _clean(body.note)
    merchant = _clean(body.merchant) or body.merchant
    row = await store.save_subscription(db, user.id, merchant, **fields)
    await db.commit()
    log.info(
        "money.subscription_saved",
        extra={"user_id": str(user.id), "subscription_id": str(row.id), "status": row.status},
    )
    await store.changed(redis, user.id)
    view = await store.subscriptions_view(db, user.id, local_today(user))
    return next(i for i in view["items"] if i["key"] == row.key)


@router.delete("/subscriptions/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_subscription(key: str, user: OwnerUser, db: DbDep, redis: RedisDep) -> Response:
    """Remove one added by hand, or undo a decision (a detected one shows as new again)."""
    row = await db.scalar(
        select(MoneySubscription).where(
            MoneySubscription.user_id == user.id, MoneySubscription.key == key
        )
    )
    if row is not None:
        await db.delete(row)
        await db.commit()
        await store.changed(redis, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Savings: FD, RD, PPF, EPF, NPS, gold --------------------------------------------------


class AssetIn(BaseModel):
    kind: Literal["fd", "rd", "ppf", "epf", "nps", "gold", "other"]
    name: str = Field(default="", max_length=120)
    institution: str | None = Field(default=None, max_length=60)
    amount_paise: Paise
    rate_bp: RateBp | None = None
    start_on: date | None = None
    maturity_on: date | None = None
    balance_on: date | None = None
    contribution_paise: Paise = 0
    payout: bool = False
    status: Literal["active", "closed"] = "active"
    note: Note
    branch: str | None = Field(default=None, max_length=80)
    account_no: AccountNo
    nominee: str | None = Field(default=None, max_length=80)
    nominee_relation: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def _terms(self) -> "AssetIn":
        if self.kind in ("fd", "rd") and not (self.start_on and self.maturity_on):
            raise ValueError("A deposit needs its start and maturity dates.")
        if self.start_on and self.maturity_on and self.maturity_on <= self.start_on:
            raise ValueError("The maturity date must be after the start.")
        if self.rate_bp is None:
            self.rate_bp = plan.DEFAULT_RATE_BP.get(self.kind, 0)
        return self


class AssetPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    institution: str | None = Field(default=None, max_length=60)
    amount_paise: Paise | None = None
    rate_bp: RateBp | None = None
    start_on: date | None = None
    maturity_on: date | None = None
    balance_on: date | None = None
    contribution_paise: Paise | None = None
    payout: bool | None = None
    status: Literal["active", "closed"] | None = None
    note: Note
    # Left out: unchanged. "" or null: cleared.
    branch: str | None = Field(default=None, max_length=80)
    account_no: AccountNo
    nominee: str | None = Field(default=None, max_length=80)
    nominee_relation: str | None = Field(default=None, max_length=30)


SECRET = {"account_no", "nominee"}
TEXT = ("name", "institution", "note", "branch", "nominee_relation")


def _set_asset_details(row: MoneyAsset, cfg, fields: dict) -> None:
    if "account_no" in fields:
        _set_account_no(row, cfg, fields["account_no"])
    if "nominee" in fields:
        row.nominee_enc = money.seal(cfg, _clean(fields["nominee"]) or "") or None


@router.get("/savings")
async def savings(user: OwnerUser, db: DbDep) -> dict:
    out = await store.savings_view(db, user.id, local_today(user))
    return {**out, "kinds": plan.ASSET_KINDS, "default_rates": plan.DEFAULT_RATE_BP}


@router.post("/savings", status_code=status.HTTP_201_CREATED)
async def add_saving(
    body: AssetIn, user: OwnerUser, db: DbDep, redis: RedisDep, cfg: SettingsDep
) -> dict:
    today = local_today(user)
    row = MoneyAsset(
        user_id=user.id,
        **body.model_dump(exclude={*TEXT, *SECRET}),
        **{name: _clean(getattr(body, name)) or None for name in TEXT},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    row.name = row.name or ""
    _set_asset_details(row, cfg, body.model_dump(include=SECRET))
    if row.kind in plan.BALANCE_KINDS and row.balance_on is None:
        row.balance_on = today
    db.add(row)
    await db.commit()
    log.info("money.saving_added", extra={"user_id": str(user.id), "kind": row.kind})
    await store.changed(redis, user.id)
    return store.asset_out(row, today)


@router.patch("/savings/{asset_id}")
async def edit_saving(
    asset_id: uuid.UUID,
    body: AssetPatch,
    user: OwnerUser,
    db: DbDep,
    redis: RedisDep,
    cfg: SettingsDep,
) -> dict:
    row = await _own(db, MoneyAsset, user.id, asset_id, "Saving")
    today = local_today(user)
    fields = body.model_dump(exclude_unset=True)
    _set_asset_details(row, cfg, {k: fields.pop(k) for k in SECRET & fields.keys()})
    for name in TEXT:
        if name in fields:
            fields[name] = _clean(fields[name]) or ("" if name == "name" else None)
    if "amount_paise" in fields and row.kind in plan.BALANCE_KINDS and "balance_on" not in fields:
        # A new balance is as of today.
        fields["balance_on"] = today
    for name, value in fields.items():
        setattr(row, name, value)
    if row.start_on and row.maturity_on and row.maturity_on <= row.start_on:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The maturity date must be after the start."
        )
    row.updated_at = utcnow()
    await db.commit()
    await store.changed(redis, user.id)
    return store.asset_out(row, today)


@router.get("/savings/{asset_id}/details")
async def saving_details(
    asset_id: uuid.UUID, user: OwnerUser, db: DbDep, redis: RedisDep, cfg: SettingsDep
) -> dict:
    """The full account number and nominee, decrypted, for the eye button."""
    row = await _own(db, MoneyAsset, user.id, asset_id, "Saving")
    await _show_details(db, redis, user, "saving", row.id)
    return {
        "account_no": money.unseal(cfg, row.account_no_enc) or None,
        "nominee": money.unseal(cfg, row.nominee_enc) or None,
    }


@router.delete("/savings/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saving(asset_id: uuid.UUID, user: OwnerUser, db: DbDep, redis: RedisDep):
    row = await _own(db, MoneyAsset, user.id, asset_id, "Saving")
    await db.delete(row)
    await db.commit()
    await store.changed(redis, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Loans and EMIs ----------------------------------------------------------------------------


class LoanIn(BaseModel):
    kind: Literal["card_emi", "personal", "home", "vehicle", "education", "other"] = "card_emi"
    name: str = Field(min_length=1, max_length=120)
    account_id: uuid.UUID | None = None
    lender: str | None = Field(default=None, max_length=60)
    principal_paise: Annotated[int, Field(gt=0, le=10**13)]
    rate_bp: RateBp = 0
    months: int = Field(ge=1, le=480)
    # Worked out from the terms when left out.
    emi_paise: Paise | None = None
    first_due_on: date
    note: Note
    account_no: AccountNo


class LoanPatch(BaseModel):
    kind: Literal["card_emi", "personal", "home", "vehicle", "education", "other"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    account_id: uuid.UUID | None = None
    lender: str | None = Field(default=None, max_length=60)
    principal_paise: Annotated[int, Field(gt=0, le=10**13)] | None = None
    rate_bp: RateBp | None = None
    months: int | None = Field(default=None, ge=1, le=480)
    emi_paise: Paise | None = None
    first_due_on: date | None = None
    status: Literal["active", "closed"] | None = None
    note: Note
    account_no: AccountNo


async def _check_account(db, user_id: uuid.UUID, account_id: uuid.UUID | None) -> None:
    if account_id:
        await _own(db, MoneyAccount, user_id, account_id, "Account")


async def _loan_out(db, user_id: uuid.UUID, row: MoneyLoan, today: date) -> dict:
    view = await store.loans_view(db, user_id, today)
    return next(x for x in view["items"] if x["id"] == str(row.id))


@router.get("/loans")
async def loans(user: OwnerUser, db: DbDep) -> dict:
    out = await store.loans_view(db, user.id, local_today(user))
    return {**out, "kinds": plan.LOAN_KINDS}


@router.post("/loans", status_code=status.HTTP_201_CREATED)
async def add_loan(
    body: LoanIn, user: OwnerUser, db: DbDep, redis: RedisDep, cfg: SettingsDep
) -> dict:
    await _check_account(db, user.id, body.account_id)
    row = MoneyLoan(
        user_id=user.id,
        **body.model_dump(exclude={"name", "lender", "note", "emi_paise", "account_no"}),
        name=_clean(body.name) or body.name,
        lender=_clean(body.lender),
        note=_clean(body.note),
        emi_paise=body.emi_paise or plan.emi_for(body.principal_paise, body.rate_bp, body.months),
        status="active",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    _set_account_no(row, cfg, body.account_no)
    db.add(row)
    await db.commit()
    log.info("money.loan_added", extra={"user_id": str(user.id), "loan_id": str(row.id)})
    await store.changed(redis, user.id)
    return await _loan_out(db, user.id, row, local_today(user))


@router.patch("/loans/{loan_id}")
async def edit_loan(
    loan_id: uuid.UUID,
    body: LoanPatch,
    user: OwnerUser,
    db: DbDep,
    redis: RedisDep,
    cfg: SettingsDep,
) -> dict:
    row = await _own(db, MoneyLoan, user.id, loan_id, "Loan")
    fields = body.model_dump(exclude_unset=True)
    if "account_no" in fields:
        _set_account_no(row, cfg, fields.pop("account_no"))
    if "account_id" in fields:
        await _check_account(db, user.id, body.account_id)
    for name in ("name", "lender", "note"):
        if name in fields:
            fields[name] = _clean(fields[name])
    terms = {"principal_paise", "rate_bp", "months"} & fields.keys()
    for name, value in fields.items():
        setattr(row, name, value)
    if terms and "emi_paise" not in fields:
        row.emi_paise = plan.emi_for(row.principal_paise, row.rate_bp, row.months)
    if fields.get("emi_paise") == 0:
        row.emi_paise = plan.emi_for(row.principal_paise, row.rate_bp, row.months)
    row.updated_at = utcnow()
    await db.commit()
    await store.changed(redis, user.id)
    return await _loan_out(db, user.id, row, local_today(user))


@router.get("/loans/{loan_id}/details")
async def loan_details(
    loan_id: uuid.UUID, user: OwnerUser, db: DbDep, redis: RedisDep, cfg: SettingsDep
) -> dict:
    """The full loan account number, decrypted, for the eye button."""
    row = await _own(db, MoneyLoan, user.id, loan_id, "Loan")
    await _show_details(db, redis, user, "loan", row.id)
    return {"account_no": money.unseal(cfg, row.account_no_enc) or None}


@router.delete("/loans/{loan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_loan(loan_id: uuid.UUID, user: OwnerUser, db: DbDep, redis: RedisDep):
    row = await _own(db, MoneyLoan, user.id, loan_id, "Loan")
    await db.delete(row)
    await db.commit()
    await store.changed(redis, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Plan: forecast, goals and assumptions ----------------------------------------------------


class AssumptionsIn(BaseModel):
    """None for income or spending means "from the last three months"."""

    income_paise: Paise | None = None
    spend_paise: Paise | None = None
    income_growth_bp: RateBp = 500
    inflation_bp: RateBp = 500
    fund_return_bp: RateBp = 1000
    fund_low_bp: RateBp = 600
    fund_high_bp: RateBp = 1300
    new_sip_paise: Paise = 0
    emergency_months: int = Field(default=6, ge=1, le=24)
    save_target_bp: Annotated[int, Field(ge=0, le=9000)] = 2000
    health_cover_paise: Paise | None = None
    life_cover_paise: Paise | None = None
    dependants: bool | None = None


@router.get("/plan")
async def get_plan(
    user: OwnerUser,
    db: DbDep,
    months: Annotated[int, Query(ge=3, le=120)] = 24,
    new_sip_paise: Annotated[int | None, Query(ge=0, le=10**12)] = None,
    fund_return_bp: Annotated[int | None, Query(ge=0, le=5000)] = None,
) -> dict:
    """The forecast with the saved assumptions; the query can try a SIP or a return without
    saving it."""
    overrides = {
        k: v
        for k, v in {"new_sip_paise": new_sip_paise, "fund_return_bp": fund_return_bp}.items()
        if v is not None
    }
    out = await store.plan_view(db, user.id, local_today(user), months=months, overrides=overrides)
    await db.commit()
    return out


@router.put("/plan/assumptions")
async def save_assumptions(
    body: AssumptionsIn, user: OwnerUser, db: DbDep, redis: RedisDep
) -> dict:
    """Only the fields sent change, so the forecast's assumptions and the safety targets can
    be saved from different places without overwriting each other."""
    prefs = await store.settings_for(db, user.id)
    prefs.plan = {**(prefs.plan or {}), **body.model_dump(exclude_unset=True)}
    prefs.updated_at = utcnow()
    await db.commit()
    await store.changed(redis, user.id)
    log.info(
        "money.assumptions_saved",
        extra={"user_id": str(user.id), "fields": sorted(body.model_fields_set)},
    )
    return prefs.plan


@router.get("/health")
async def get_health(user: OwnerUser, db: DbDep) -> dict:
    """How safe the money is: the checks, safe-to-spend this month and the balance between
    essentials, lifestyle and saving."""
    out = await store.health_view(db, user.id, local_today(user))
    await db.commit()
    return out


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_paise: Annotated[int, Field(gt=0, le=10**13)]
    by_on: date
    saved_paise: Paise = 0
    asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class GoalPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target_paise: Annotated[int, Field(gt=0, le=10**13)] | None = None
    by_on: date | None = None
    saved_paise: Paise | None = None
    asset_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)


async def _own_assets(db, user_id: uuid.UUID, ids: list[uuid.UUID]) -> list[str]:
    for asset_id in ids:
        await _own(db, MoneyAsset, user_id, asset_id, "Saving")
    return [str(i) for i in ids]


@router.post("/goals", status_code=status.HTTP_201_CREATED)
async def add_goal(body: GoalIn, user: OwnerUser, db: DbDep, redis: RedisDep) -> dict:
    row = MoneyGoal(
        user_id=user.id,
        name=_clean(body.name) or body.name,
        target_paise=body.target_paise,
        by_on=body.by_on,
        saved_paise=body.saved_paise,
        asset_ids=await _own_assets(db, user.id, body.asset_ids),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(row)
    await db.commit()
    log.info("money.goal_added", extra={"user_id": str(user.id), "goal_id": str(row.id)})
    await store.changed(redis, user.id)
    return {"id": str(row.id)}


@router.patch("/goals/{goal_id}")
async def edit_goal(
    goal_id: uuid.UUID, body: GoalPatch, user: OwnerUser, db: DbDep, redis: RedisDep
) -> dict:
    row = await _own(db, MoneyGoal, user.id, goal_id, "Goal")
    fields = body.model_dump(exclude_unset=True)
    if "asset_ids" in fields:
        fields["asset_ids"] = await _own_assets(db, user.id, body.asset_ids or [])
    if "name" in fields:
        fields["name"] = _clean(fields["name"])
    for name, value in fields.items():
        setattr(row, name, value)
    row.updated_at = utcnow()
    await db.commit()
    await store.changed(redis, user.id)
    return {"id": str(row.id)}


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(goal_id: uuid.UUID, user: OwnerUser, db: DbDep, redis: RedisDep):
    row = await _own(db, MoneyGoal, user.id, goal_id, "Goal")
    await db.delete(row)
    await db.commit()
    await store.changed(redis, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
