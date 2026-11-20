"""Money: a read-only view of the owner's finances (nexus_common/money.py, money_store.py).

Transactions come from bank and card alert emails (read by the orchestrator, when switched on),
statement files uploaded here, and anything typed in by hand; mutual funds from a CAMS or
KFintech consolidated statement. Nothing here can move money or reach a bank.

Owner only, and only ever the owner's own rows. Uploaded files are parsed in memory and never
stored. Logs carry counts and ids, never amounts, merchants or file names.
"""

import asyncio
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from nexus_api.deps import DbDep, OwnerUser, QueueDep, RedisDep, SettingsDep
from nexus_api.ratelimit import hit, too_many
from nexus_common import google, money
from nexus_common import money_store as store
from nexus_common.models import (
    MoneyAccount,
    MoneyBudget,
    MoneyHolding,
    MoneyImport,
    MoneyTxn,
    utcnow,
)
from nexus_common.organiser import local_today

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/money", tags=["money"])

MAX_FILE_MB = 10
IMPORTS_PER_HOUR = 30
SYNCS_PER_HOUR = 12
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
Category = Annotated[str, Field(max_length=40)]


def _category(value: str) -> str:
    if value not in money.CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown category.")
    return value


# --- Settings -------------------------------------------------------------------------------


class SettingsOut(BaseModel):
    gmail_sync: bool
    assistant_access: bool
    cas_password_set: bool
    gmail_connected: bool
    gmail_synced_at: datetime | None
    cas_synced_at: datetime | None
    last_error: str | None
    rules: int
    categories: list[str]


class SettingsIn(BaseModel):
    gmail_sync: bool | None = None
    assistant_access: bool | None = None
    # "" forgets it.
    cas_password: str | None = Field(default=None, max_length=100)


async def _gmail_connected(db, cfg, user_id: uuid.UUID) -> bool:
    grant = await google.get_grant(db, cfg, user_id)
    return bool(grant) and "gmail" in google.services_in(grant.scopes)


@router.get("/settings", response_model=SettingsOut)
async def get_settings(user: OwnerUser, db: DbDep, cfg: SettingsDep) -> SettingsOut:
    prefs = await store.settings_for(db, user.id)
    out = SettingsOut(
        gmail_sync=prefs.gmail_sync,
        assistant_access=prefs.assistant_access,
        cas_password_set=bool(prefs.cas_password_enc),
        gmail_connected=await _gmail_connected(db, cfg, user.id),
        gmail_synced_at=prefs.gmail_synced_at,
        cas_synced_at=prefs.cas_synced_at,
        last_error=prefs.last_error,
        rules=len(prefs.rules or {}),
        categories=list(money.CATEGORIES),
    )
    await db.commit()
    return out


@router.patch("/settings", response_model=SettingsOut)
async def update_settings(
    body: SettingsIn, user: OwnerUser, db: DbDep, cfg: SettingsDep, queue: QueueDep
) -> SettingsOut:
    prefs = await store.settings_for(db, user.id)
    start_sync = False
    if body.gmail_sync is not None:
        if body.gmail_sync and not await _gmail_connected(db, cfg, user.id):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Gmail isn't connected. Allow Gmail on the Settings page first.",
            )
        start_sync = body.gmail_sync and not prefs.gmail_sync
        prefs.gmail_sync = body.gmail_sync
        if body.gmail_sync:
            prefs.last_error = None
    if body.assistant_access is not None:
        prefs.assistant_access = body.assistant_access
    if body.cas_password is not None:
        pw = body.cas_password.strip()
        prefs.cas_password_enc = money.seal(cfg, pw) if pw else None
        start_sync = start_sync or (bool(pw) and prefs.gmail_sync)
    prefs.updated_at = utcnow()
    await db.commit()
    log.info(
        "money.settings_changed",
        extra={
            "user_id": str(user.id),
            "gmail_sync": prefs.gmail_sync,
            "assistant_access": prefs.assistant_access,
        },
    )
    if start_sync:
        await queue.enqueue_money_sync(user.id)
    return await get_settings(user, db, cfg)


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_now(user: OwnerUser, db: DbDep, redis: RedisDep, queue: QueueDep) -> dict:
    prefs = await store.settings_for(db, user.id)
    if not prefs.gmail_sync:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reading alert emails is switched off.")
    if wait := await hit(redis, "money-sync", str(user.id), SYNCS_PER_HOUR, 3600):
        raise too_many(wait, "syncs")
    await queue.enqueue_money_sync(user.id)
    return {"queued": True}


# --- Overview --------------------------------------------------------------------------------


def _month(user, month: str | None) -> str:
    return month or local_today(user).strftime("%Y-%m")


@router.get("/overview")
async def overview(
    user: OwnerUser, db: DbDep, month: Annotated[str | None, Query(pattern=MONTH)] = None
) -> dict:
    out = await store.overview(db, user.id, _month(user, month), local_today(user))
    await db.commit()
    return out


# --- Transactions ----------------------------------------------------------------------------


class TxnOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID | None
    occurred_on: date
    amount_paise: int
    direction: str
    merchant: str
    category: str
    category_by: str
    channel: str
    source: str
    confirmed: bool
    excluded: bool
    note: str
    narration: str | None = None


def _txn(t: MoneyTxn, cfg=None) -> TxnOut:
    return TxnOut(
        id=t.id,
        account_id=t.account_id,
        occurred_on=t.occurred_on,
        amount_paise=t.amount_paise,
        direction=t.direction,
        merchant=t.merchant,
        category=t.category,
        category_by=t.category_by,
        channel=t.channel,
        source=t.source,
        confirmed=t.confirmed,
        excluded=t.excluded,
        note=t.note,
        narration=money.unseal(cfg, t.narration_enc) if cfg else None,
    )


class TxnPage(BaseModel):
    items: list[TxnOut]
    total: int
    spent_paise: int
    income_paise: int


@router.get("/transactions", response_model=TxnPage)
async def list_transactions(
    user: OwnerUser,
    db: DbDep,
    month: Annotated[str | None, Query(pattern=MONTH)] = None,
    category: str | None = None,
    account_id: uuid.UUID | None = None,
    direction: Literal["debit", "credit"] | None = None,
    merchant: str | None = Query(default=None, max_length=120),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> TxnPage:
    where = [MoneyTxn.user_id == user.id]
    if month:
        start, end = money.month_bounds(month)
        where += [MoneyTxn.occurred_on >= start, MoneyTxn.occurred_on < end]
    if category:
        where.append(MoneyTxn.category == category)
    if account_id:
        where.append(MoneyTxn.account_id == account_id)
    if direction:
        where.append(MoneyTxn.direction == direction)
    if merchant:
        where.append(MoneyTxn.merchant == merchant)
    if q and q.strip():
        like = "%" + re.sub(r"([%_\\])", r"\\\1", q.strip()) + "%"
        where.append(MoneyTxn.merchant.ilike(like) | MoneyTxn.note.ilike(like))
    total, spent, income = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(
                    func.sum(MoneyTxn.amount_paise).filter(
                        MoneyTxn.direction == "debit", ~MoneyTxn.excluded
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(MoneyTxn.amount_paise).filter(
                        MoneyTxn.direction == "credit", ~MoneyTxn.excluded
                    ),
                    0,
                ),
            ).where(*where)
        )
    ).one()
    rows = await db.scalars(
        select(MoneyTxn)
        .where(*where)
        .order_by(MoneyTxn.occurred_on.desc(), MoneyTxn.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return TxnPage(
        items=[_txn(t) for t in rows],
        total=total,
        spent_paise=int(spent),
        income_paise=int(income),
    )


async def _own_txn(db, user_id: uuid.UUID, txn_id: uuid.UUID) -> MoneyTxn:
    t = await db.get(MoneyTxn, txn_id)
    if t is None or t.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found.")
    return t


@router.get("/transactions/{txn_id}", response_model=TxnOut)
async def get_transaction(
    txn_id: uuid.UUID, user: OwnerUser, db: DbDep, cfg: SettingsDep
) -> TxnOut:
    return _txn(await _own_txn(db, user.id, txn_id), cfg)


class TxnPatch(BaseModel):
    category: Category | None = None
    # Also use this category for the merchant's other rows and future ones.
    for_merchant: bool = False
    excluded: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class TxnPatchOut(BaseModel):
    txn: TxnOut
    also_changed: int = 0


@router.patch("/transactions/{txn_id}", response_model=TxnPatchOut)
async def update_transaction(
    txn_id: uuid.UUID,
    body: TxnPatch,
    user: OwnerUser,
    db: DbDep,
    cfg: SettingsDep,
    redis: RedisDep,
) -> TxnPatchOut:
    t = await _own_txn(db, user.id, txn_id)
    also = 0
    if body.category is not None:
        category = _category(body.category)
        if body.for_merchant and t.merchant != "Unknown":
            also = await store.set_rule(db, user.id, t.merchant, category)
            also = max(also - 1, 0)
        t.category = category
        t.category_by = "user"
    if body.excluded is not None:
        t.excluded = body.excluded
    if body.note is not None:
        t.note = body.note.strip()
    await db.commit()
    await db.refresh(t)
    await store.changed(redis, user.id)
    return TxnPatchOut(txn=_txn(t, cfg), also_changed=also)


class TxnIn(BaseModel):
    occurred_on: date
    # Rupees as typed: "250", "1,299.50".
    amount: str = Field(max_length=20)
    direction: Literal["debit", "credit"] = "debit"
    merchant: str = Field(min_length=1, max_length=120)
    category: Category | None = None
    account_id: uuid.UUID | None = None
    note: str = Field(default="", max_length=500)

    @field_validator("amount")
    @classmethod
    def _amount(cls, v: str) -> str:
        try:
            if money.paise(v) <= 0:
                raise ValueError
        except ValueError:
            raise ValueError("Enter an amount in rupees, like 250 or 1,299.50.") from None
        return v


@router.post("/transactions", response_model=TxnOut, status_code=status.HTTP_201_CREATED)
async def add_transaction(
    body: TxnIn, user: OwnerUser, db: DbDep, cfg: SettingsDep, redis: RedisDep
) -> TxnOut:
    """A cash spend or anything no email or statement will show."""
    if body.account_id:
        account = await db.get(MoneyAccount, body.account_id)
        if account is None or account.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    prefs = await store.settings_for(db, user.id)
    merchant = " ".join(body.merchant.split())
    credit = body.direction == "credit"
    t = MoneyTxn(
        user_id=user.id,
        account_id=body.account_id,
        occurred_on=body.occurred_on,
        amount_paise=money.paise(body.amount),
        direction=body.direction,
        merchant=merchant,
        category=_category(body.category)
        if body.category
        else money.categorise(merchant, credit=credit, rules=prefs.rules),
        category_by="user" if body.category else "auto",
        narration_enc="",
        channel="cash" if not body.account_id else "other",
        source="manual",
        dedupe_key=uuid.uuid4().hex[:40],
        note=body.note.strip(),
        created_at=utcnow(),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    log.info("money.manual_added", extra={"user_id": str(user.id), "txn_id": str(t.id)})
    await store.changed(redis, user.id, added=1)
    return _txn(t, cfg)


@router.delete("/transactions/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    txn_id: uuid.UUID, user: OwnerUser, db: DbDep, redis: RedisDep
) -> Response:
    t = await _own_txn(db, user.id, txn_id)
    if t.source != "manual":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only rows you added by hand can be deleted. Leave this one out of totals instead.",
        )
    await db.delete(t)
    await db.commit()
    await store.changed(redis, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Accounts --------------------------------------------------------------------------------


class AccountOut(BaseModel):
    id: uuid.UUID
    institution: str | None
    mask: str | None
    kind: str
    name: str
    label: str
    count: int
    last_on: date | None


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(user: OwnerUser, db: DbDep) -> list[AccountOut]:
    stats = {
        r[0]: (r[1], r[2])
        for r in (
            await db.execute(
                select(MoneyTxn.account_id, func.count(), func.max(MoneyTxn.occurred_on))
                .where(MoneyTxn.user_id == user.id)
                .group_by(MoneyTxn.account_id)
            )
        ).all()
    }
    rows = await db.scalars(
        select(MoneyAccount)
        .where(MoneyAccount.user_id == user.id)
        .order_by(MoneyAccount.created_at)
    )
    return [
        AccountOut(
            id=a.id,
            institution=a.institution,
            mask=a.mask,
            kind=a.kind,
            name=a.name,
            label=store.account_label(a),
            count=stats.get(a.id, (0, None))[0],
            last_on=stats.get(a.id, (0, None))[1],
        )
        for a in rows
    ]


class AccountPatch(BaseModel):
    name: str = Field(max_length=120)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def rename_account(
    account_id: uuid.UUID, body: AccountPatch, user: OwnerUser, db: DbDep
) -> AccountOut:
    a = await db.get(MoneyAccount, account_id)
    if a is None or a.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    a.name = " ".join(body.name.split())
    await db.commit()
    return next(x for x in await list_accounts(user, db) if x.id == account_id)


# --- Budgets ---------------------------------------------------------------------------------


class BudgetIn(BaseModel):
    monthly_paise: int = Field(gt=0, le=10**13)


@router.put("/budgets/{category}")
async def set_budget(category: str, body: BudgetIn, user: OwnerUser, db: DbDep) -> dict:
    category = _category(category)
    row = await db.scalar(
        select(MoneyBudget).where(MoneyBudget.user_id == user.id, MoneyBudget.category == category)
    )
    if row is None:
        row = MoneyBudget(user_id=user.id, category=category, created_at=utcnow())
        db.add(row)
    row.monthly_paise = body.monthly_paise
    await db.commit()
    return {"category": category, "monthly_paise": row.monthly_paise}


@router.delete("/budgets/{category}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(category: str, user: OwnerUser, db: DbDep) -> Response:
    row = await db.scalar(
        select(MoneyBudget).where(MoneyBudget.user_id == user.id, MoneyBudget.category == category)
    )
    if row is not None:
        await db.delete(row)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Subscriptions and investments -----------------------------------------------------------


@router.get("/subscriptions")
async def subscriptions(user: OwnerUser, db: DbDep) -> dict:
    today = local_today(user)
    txns = await store.load(
        db, user.id, today - store.SUBSCRIPTION_LOOKBACK, today + timedelta(days=1)
    )
    subs = money.find_subscriptions(txns, today)
    return {
        "items": [store.subscription_out(s) for s in subs],
        "monthly_paise": sum(s.monthly_paise for s in subs),
        "yearly_paise": sum(s.monthly_paise for s in subs) * 12,
    }


class HoldingOut(BaseModel):
    id: uuid.UUID
    folio: str
    amc: str
    scheme: str
    isin: str | None
    units: float
    nav: float | None
    nav_date: date | None
    value_paise: int | None
    cost_paise: int | None
    as_of: date | None


@router.get("/investments")
async def investments(user: OwnerUser, db: DbDep) -> dict:
    rows = (
        await db.scalars(
            select(MoneyHolding)
            .where(MoneyHolding.user_id == user.id)
            .order_by(MoneyHolding.value_paise.desc().nulls_last())
        )
    ).all()
    value = sum(h.value_paise or 0 for h in rows)
    cost = sum(h.cost_paise or 0 for h in rows if h.value_paise is not None)
    return {
        "items": [
            HoldingOut(
                id=h.id,
                folio=h.folio,
                amc=h.amc,
                scheme=h.scheme,
                isin=h.isin,
                units=float(h.units),
                nav=float(h.nav) if h.nav is not None else None,
                nav_date=h.nav_date,
                value_paise=h.value_paise,
                cost_paise=h.cost_paise,
                as_of=h.as_of,
            ).model_dump(mode="json")
            for h in rows
        ],
        "value_paise": value,
        "cost_paise": cost,
        "gain_paise": value - cost if cost else None,
        "as_of": max((h.as_of for h in rows if h.as_of), default=None),
        "nav_date": max((h.nav_date for h in rows if h.nav_date), default=None),
    }


# --- Imports ---------------------------------------------------------------------------------


class ImportOut(BaseModel):
    id: uuid.UUID
    kind: str
    origin: str
    filename: str
    status: str
    counts: dict
    error: str | None
    created_at: datetime


def _import(row: MoneyImport, cfg) -> ImportOut:
    return ImportOut(
        id=row.id,
        kind=row.kind,
        origin=row.origin,
        filename=money.unseal(cfg, row.filename_enc),
        status=row.status,
        counts=row.counts or {},
        error=row.error,
        created_at=row.created_at,
    )


@router.get("/imports", response_model=list[ImportOut])
async def list_imports(user: OwnerUser, db: DbDep, cfg: SettingsDep) -> list[ImportOut]:
    rows = await db.scalars(
        select(MoneyImport)
        .where(MoneyImport.user_id == user.id)
        .order_by(MoneyImport.created_at.desc())
        .limit(50)
    )
    return [_import(r, cfg) for r in rows]


@router.post("/imports", response_model=ImportOut, status_code=status.HTTP_201_CREATED)
async def import_file(
    user: OwnerUser,
    db: DbDep,
    cfg: SettingsDep,
    redis: RedisDep,
    file: Annotated[UploadFile, File()],
    password: Annotated[str, Form(max_length=100)] = "",
    remember_password: Annotated[bool, Form()] = True,
    account_id: Annotated[uuid.UUID | None, Form()] = None,
) -> ImportOut:
    """Read a statement (CSV or .xlsx) or a CAS (PDF). The file is read in memory and dropped."""
    uid = user.id  # the user object expires if a failed import is rolled back
    if wait := await hit(redis, "money-import", str(user.id), IMPORTS_PER_HOUR, 3600):
        raise too_many(wait, "imports")
    name = (file.filename or "statement").strip()[:200]
    data = await file.read(MAX_FILE_MB * 1024 * 1024 + 1)
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Files up to {MAX_FILE_MB} MB."
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That file is empty.")
    if account_id:
        account = await db.get(MoneyAccount, account_id)
        if account is None or account.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    prefs = await store.settings_for(db, user.id)
    is_pdf = name.lower().endswith(".pdf") or data[:5] == b"%PDF-"
    kind = "cas" if is_pdf else "statement"
    try:
        if is_pdf:
            pw = password.strip() or money.unseal(cfg, prefs.cas_password_enc)
            if not pw:
                raise money.StatementError(
                    "This PDF needs its password (for a CAS, the one you chose when you asked "
                    "for it, often your PAN in capitals)."
                )
            holdings, as_of = await asyncio.to_thread(money.read_cas, data, pw)
            if not holdings:
                raise money.StatementError("No mutual fund holdings were found in that statement.")
            saved = await store.save_holdings(db, user.id, holdings, as_of)
            counts = {"holdings": saved}
            if password.strip() and remember_password:
                prefs.cas_password_enc = money.seal(cfg, password.strip())
            prefs.cas_synced_at = utcnow()
        else:
            rows = await asyncio.to_thread(money.parse_statement, data, name)
            if not rows:
                raise money.StatementError("No transactions were found in that file.")
            counts = await store.ingest(
                db, cfg, user.id, rows, source="statement", account_id=account_id
            )
    except money.StatementError as exc:
        await db.rollback()
        row = await store.record_import(
            db, cfg, uid, kind=kind, origin="upload", filename=name, status="failed",
            error=str(exc), account_id=account_id,
        )  # fmt: skip
        await db.commit()
        log.info("money.import_failed", extra={"user_id": str(uid), "kind": kind})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    row = await store.record_import(
        db, cfg, user.id, kind=kind, origin="upload", filename=name, status="done",
        counts=counts, account_id=account_id,
    )  # fmt: skip
    await db.commit()
    log.info("money.imported", extra={"user_id": str(user.id), "kind": kind, **counts})
    await store.changed(redis, user.id, **counts)
    return _import(row, cfg)


# --- Deleting everything ---------------------------------------------------------------------


class DeleteAll(BaseModel):
    confirm: Literal["delete"]


@router.post("/delete-all", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all(body: DeleteAll, user: OwnerUser, db: DbDep, redis: RedisDep) -> Response:
    await store.delete_all(db, user.id)
    await db.commit()
    await store.changed(redis, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
