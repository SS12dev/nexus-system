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

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from nexus_api.deps import DbDep, OwnerUser, QueueDep, RedisDep, SettingsDep
from nexus_api.ratelimit import hit, too_many
from nexus_common import (
    google,
    money,
    money_habits,
    money_history,
    money_items,
    money_links,
    money_mail,
    money_salary,
    money_suggest,
    money_tracking,
)
from nexus_common import money_store as store
from nexus_common.models import (
    MoneyAccount,
    MoneyAsset,
    MoneyBudget,
    MoneyHolding,
    MoneyImport,
    MoneyItem,
    MoneyMailbox,
    MoneyMailStatement,
    MoneyTxn,
    utcnow,
)
from nexus_common.organiser import local_today

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/money", tags=["money"])

MAX_FILE_MB = 25  # a year-long statement with logos on every page
IMPORTS_PER_HOUR = 30
SYNCS_PER_HOUR = 12
MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"
Category = Annotated[str, Field(max_length=40)]


def _item_kinds() -> list[dict]:
    return [
        {"kind": k, "label": label, "need": need} for k, (label, need) in money_items.KINDS.items()
    ]


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
    # Other Gmail accounts alerts are read from (masked addresses only).
    mailboxes: list[dict]
    cas_synced_at: datetime | None
    last_error: str | None
    rules: int
    categories: list[str]
    # Kinds of things bought (money_items.KINDS): {kind, label, need}.
    item_kinds: list[dict] = []


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
        mailboxes=[money_mail.view(b) for b in await money_mail.mailboxes(db, user.id)],
        cas_synced_at=prefs.cas_synced_at,
        last_error=prefs.last_error,
        rules=len(prefs.rules or {}),
        categories=list(money.CATEGORIES),
        item_kinds=_item_kinds(),
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
        if (
            body.gmail_sync
            and not await _gmail_connected(db, cfg, user.id)
            and not await money_mail.mailboxes(db, user.id)
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Gmail isn't connected. Allow Gmail on the Settings page, or connect the Gmail "
                "your bank alerts go to, first.",
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


@router.delete("/mailboxes/{mailbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_mailbox(
    mailbox_id: uuid.UUID, user: OwnerUser, db: DbDep, cfg: SettingsDep
) -> Response:
    """Stop reading another Gmail account: its access is revoked at Google and forgotten.
    Transactions already read from it stay."""
    box = await db.get(MoneyMailbox, mailbox_id)
    if box is None or box.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mailbox not found.")
    await money_mail.remove(db, cfg, box)
    await db.commit()
    log.info(
        "money.mailbox_removed", extra={"user_id": str(user.id), "mailbox_id": str(mailbox_id)}
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.get("/daily")
async def daily(
    user: OwnerUser,
    db: DbDep,
    cfg: SettingsDep,
    month: Annotated[str | None, Query(pattern=MONTH)] = None,
) -> dict:
    """Day by day: what each day of the month cost, the habits under it, and what went into
    quick-commerce baskets."""
    return await money_habits.daily(db, cfg, user.id, _month(user, month), local_today(user))


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
    # Tied to another row (money_links): transfer, card or refund; and the saving it went into.
    pair_id: uuid.UUID | None = None
    pair_kind: str | None = None
    asset_id: uuid.UUID | None = None
    narration: str | None = None
    # Where on a platform it was bought (the restaurant on Zomato).
    place: str | None = None


class Linked(BaseModel):
    """The other half of a pair, or a row that could be, as the details panel shows it."""

    id: uuid.UUID
    occurred_on: date
    amount_paise: int
    direction: str
    merchant: str
    account: str | None


class TxnDetail(TxnOut):
    paired: Linked | None = None
    saving: str | None = None
    # What was bought (money_items), from the order email or the person.
    items: list[dict] = []
    # A quick-commerce order: "what did you buy?" applies.
    basket: bool = False


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
        pair_id=t.pair_id,
        pair_kind=t.pair_kind,
        asset_id=t.asset_id,
        narration=money.unseal(cfg, t.narration_enc) if cfg else None,
        place=t.place,
    )


async def _linked(db, t: MoneyTxn) -> Linked:
    account = await db.get(MoneyAccount, t.account_id) if t.account_id else None
    return Linked(
        id=t.id,
        occurred_on=t.occurred_on,
        amount_paise=t.amount_paise,
        direction=t.direction,
        merchant=t.merchant,
        account=store.account_label(account) if account else None,
    )


async def _detail(db, t: MoneyTxn, cfg) -> TxnDetail:
    other = await db.get(MoneyTxn, t.pair_id) if t.pair_id else None
    asset = await db.get(MoneyAsset, t.asset_id) if t.asset_id else None
    return TxnDetail(
        **_txn(t, cfg).model_dump(),
        paired=await _linked(db, other) if other is not None else None,
        saving=(asset.name or asset.kind.upper()) if asset is not None else None,
        items=await money_habits.items_of(db, cfg, t.id),
        basket=money_items.is_basket(t.merchant),
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
        where.append(
            MoneyTxn.merchant.ilike(like) | MoneyTxn.note.ilike(like) | MoneyTxn.place.ilike(like)
        )
    total, spent, income = (
        await db.execute(
            select(
                func.count(),
                # Spending and income the way the overview counts them (money.spending,
                # money.refund, money.income): refunds lower spending.
                func.coalesce(
                    func.sum(MoneyTxn.amount_paise).filter(
                        MoneyTxn.direction == "debit",
                        ~MoneyTxn.excluded,
                        MoneyTxn.category.not_in(("Transfers", "Investments")),
                    ),
                    0,
                )
                - func.coalesce(
                    func.sum(MoneyTxn.amount_paise).filter(
                        MoneyTxn.direction == "credit",
                        ~MoneyTxn.excluded,
                        MoneyTxn.category.not_in(tuple(money.NOT_SPENDING)),
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(MoneyTxn.amount_paise).filter(
                        MoneyTxn.direction == "credit",
                        ~MoneyTxn.excluded,
                        MoneyTxn.category == "Income",
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


@router.get("/suggestions")
async def suggestions(user: OwnerUser, db: DbDep, redis: RedisDep) -> dict:
    """Money's one-tap questions, each with its best guess. Orders a statement or later alert
    now explains are matched first, without asking."""
    out = await money_suggest.suggestions(db, user.id, local_today(user), user.name)
    if out["matched"] or out["filed"]:
        await db.commit()
        await store.changed(redis, user.id)
    return out


class SuggestionIn(BaseModel):
    key: str = Field(max_length=200)
    choice: str = Field(max_length=80)
    # An EMI's monthly instalment, when known: Money works out the interest from it.
    amount_paise: int | None = Field(default=None, gt=0, le=10**12)
    # What was bought, typed ("milk 60, chips 40"), for a quick-commerce order.
    text: str | None = Field(default=None, max_length=1000)


@router.post("/suggestions/answer")
async def answer_suggestion(
    body: SuggestionIn, user: OwnerUser, db: DbDep, redis: RedisDep
) -> dict:
    today = local_today(user)
    try:
        done = await money_suggest.answer(
            db, user.id, body.key, body.choice, today, user.name, body.amount_paise, body.text
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    await db.commit()
    await store.changed(redis, user.id)
    return {"done": done, **await money_suggest.suggestions(db, user.id, today, user.name)}


@router.post("/suggestions/accept-all")
async def accept_suggestions(user: OwnerUser, db: DbDep, redis: RedisDep) -> dict:
    """Take Money's guess for every question that has one."""
    today = local_today(user)
    n = await money_suggest.accept_all(db, user.id, today, user.name)
    await db.commit()
    await store.changed(redis, user.id)
    log.info("money.suggestions_accepted", extra={"user_id": str(user.id), "count": n})
    return {"accepted": n, **await money_suggest.suggestions(db, user.id, today, user.name)}


@router.get("/orders/to-confirm")
async def orders_to_confirm(user: OwnerUser, db: DbDep) -> dict:
    """Orders from shop emails that no bank or card payment explains: "how did you pay?"."""
    return {"items": await store.orders_to_confirm(db, user.id, local_today(user))}


class OrderPaidIn(BaseModel):
    how: Literal["cash", "account", "leave_out"]
    account_id: uuid.UUID | None = None


@router.post("/orders/{txn_id}/paid")
async def order_paid(
    txn_id: uuid.UUID, body: OrderPaidIn, user: OwnerUser, db: DbDep, redis: RedisDep
) -> dict:
    txn = await _own_txn(db, user.id, txn_id)
    if txn.source != "receipt":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Not an order from an email.")
    account = None
    if body.how == "account":
        account = await db.get(MoneyAccount, body.account_id) if body.account_id else None
        if account is None or account.user_id != user.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick one of your accounts.")
    await store.confirm_order(db, user.id, txn, body.how, account)
    await db.commit()
    log.info("money.order_paid", extra={"user_id": str(user.id), "how": body.how})
    await store.changed(redis, user.id)
    return {"items": await store.orders_to_confirm(db, user.id, local_today(user))}


class ItemsIn(BaseModel):
    """What was bought: typed items ("milk 60, chips 40"), or one kind for the whole order.
    Neither clears them."""

    text: str | None = Field(default=None, max_length=1000)
    kind: str | None = Field(default=None, max_length=16)


@router.put("/transactions/{txn_id}/items")
async def set_items(
    txn_id: uuid.UUID,
    body: ItemsIn,
    user: OwnerUser,
    db: DbDep,
    cfg: SettingsDep,
    redis: RedisDep,
) -> dict:
    t = await _own_txn(db, user.id, txn_id)
    if body.kind is not None and body.kind not in money_items.KINDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown kind.")
    if body.text and body.text.strip():
        bought = money_items.parse_typed(body.text)
        if not bought:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                'Type what you bought, like "milk 60, chips 40, coke".',
            )
    elif body.kind:
        bought = [money_items.Item("", None, 1, body.kind)]
    else:
        bought = []
    await money_habits.save_items(db, cfg, user.id, t.id, bought, "you")
    await db.commit()
    log.info("money.items_set", extra={"user_id": str(user.id), "count": len(bought)})
    await store.changed(redis, user.id)
    return {"items": await money_habits.items_of(db, cfg, t.id)}


class ItemPatch(BaseModel):
    kind: str = Field(max_length=16)


@router.patch("/items/{item_id}")
async def update_item(
    item_id: uuid.UUID,
    body: ItemPatch,
    user: OwnerUser,
    db: DbDep,
    cfg: SettingsDep,
    redis: RedisDep,
) -> dict:
    """Correct one item's kind (Money read "Bread Pakoda" as bread)."""
    row = await db.get(MoneyItem, item_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")
    if body.kind not in money_items.KINDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown kind.")
    row.kind, row.source = body.kind, "you"
    await db.commit()
    await store.changed(redis, user.id)
    return money_habits.item_out(cfg, row)


@router.get("/transactions/{txn_id}", response_model=TxnDetail)
async def get_transaction(
    txn_id: uuid.UUID, user: OwnerUser, db: DbDep, cfg: SettingsDep
) -> TxnDetail:
    return await _detail(db, await _own_txn(db, user.id, txn_id), cfg)


MATCH_DAYS = 7


@router.get("/transactions/{txn_id}/matches")
async def transaction_matches(txn_id: uuid.UUID, user: OwnerUser, db: DbDep) -> dict:
    """Rows this one could be paired with by hand: the same amount the other way on another
    account within a week (a transfer), or, for money in, an earlier purchase from the same
    merchant (a refund). And the savings it could belong to."""
    t = await _own_txn(db, user.id, txn_id)
    other = "credit" if t.direction == "debit" else "debit"
    near = (
        await db.scalars(
            select(MoneyTxn)
            .where(
                MoneyTxn.user_id == user.id,
                MoneyTxn.id != t.id,
                MoneyTxn.direction == other,
                MoneyTxn.amount_paise == t.amount_paise,
                MoneyTxn.pair_id.is_(None),
                MoneyTxn.occurred_on.between(
                    t.occurred_on - timedelta(days=MATCH_DAYS),
                    t.occurred_on + timedelta(days=MATCH_DAYS),
                ),
            )
            .order_by(MoneyTxn.occurred_on)
            .limit(10)
        )
    ).all()
    transfers = [x for x in near if x.account_id != t.account_id or x.account_id is None]
    refunds = []
    if t.direction == "credit" and t.merchant != "Unknown":
        refunds = (
            await db.scalars(
                select(MoneyTxn)
                .where(
                    MoneyTxn.user_id == user.id,
                    MoneyTxn.direction == "debit",
                    MoneyTxn.merchant == t.merchant,
                    MoneyTxn.amount_paise >= t.amount_paise,
                    MoneyTxn.occurred_on.between(
                        t.occurred_on - timedelta(days=money_links.REFUND_DAYS), t.occurred_on
                    ),
                )
                .order_by(MoneyTxn.occurred_on.desc())
                .limit(5)
            )
        ).all()
    savings = (
        await db.scalars(
            select(MoneyAsset)
            .where(MoneyAsset.user_id == user.id)
            .order_by(MoneyAsset.status, MoneyAsset.created_at)
        )
    ).all()
    return {
        "transfers": [await _linked(db, x) for x in transfers],
        "refunds": [await _linked(db, x) for x in refunds],
        "savings": [
            {"id": str(a.id), "name": a.name or a.kind.upper(), "kind": a.kind}
            for a in savings
            if a.status == "active" or a.id == t.asset_id
        ],
    }


class TxnPatch(BaseModel):
    category: Category | None = None
    # Also use this category for the merchant's other rows and future ones.
    for_merchant: bool = False
    excluded: bool | None = None
    note: str | None = Field(default=None, max_length=500)
    # Undo this row's pair; or pair it with another row as a transfer or a refund.
    unpair: bool = False
    pair_with: uuid.UUID | None = None
    pair_as: Literal["transfer", "refund"] = "transfer"
    # The saving it went into or came from; null clears it.
    asset_id: uuid.UUID | None = None


class TxnPatchOut(BaseModel):
    txn: TxnDetail
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
    if body.unpair or body.pair_with:
        prefs = await store.settings_for(db, user.id)
        partner = await db.get(MoneyTxn, t.pair_id) if t.pair_id else None
        money_links.unpair(t, partner, rules=prefs.rules or {})
    if body.pair_with:
        await _pair_by_hand(db, user.id, t, body.pair_with, body.pair_as)
    if "asset_id" in body.model_fields_set:
        if body.asset_id is not None:
            asset = await db.get(MoneyAsset, body.asset_id)
            if asset is None or asset.user_id != user.id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Saving not found.")
            if t.category_by != "user":
                t.category, t.category_by = "Investments", "pair"
        t.asset_id = body.asset_id
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
    return TxnPatchOut(txn=await _detail(db, t, cfg), also_changed=also)


async def _pair_by_hand(
    db, user_id: uuid.UUID, t: MoneyTxn, other_id: uuid.UUID, kind: str
) -> None:
    other = await _own_txn(db, user_id, other_id)
    if other.id == t.id or other.direction == t.direction:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pair money out with money in.")
    if kind == "transfer" and other.amount_paise != t.amount_paise:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "A transfer's two halves are the same amount."
        )
    if other.pair_id:
        prefs = await store.settings_for(db, user_id)
        money_links.unpair(other, await db.get(MoneyTxn, other.pair_id), rules=prefs.rules or {})
    if kind == "refund":
        debit, credit = (t, other) if t.direction == "debit" else (other, t)
        credit.pair_id, credit.pair_kind = debit.id, "refund"
        debit.pair_id, debit.pair_kind = credit.id, "refund"
        credit.category, credit.category_by = debit.category, "pair"
        return
    accounts = [await db.get(MoneyAccount, x.account_id) for x in (t, other) if x.account_id]
    card = any(a is not None and a.kind == "card" for a in accounts)
    money_links.pair(t, other, "card" if card else "transfer")
    # Chosen by hand: keep it even if the word list would say otherwise.
    for x in (t, other):
        x.category, x.category_by = "Transfers", "user"


@router.post("/links/scan")
async def scan_links(user: OwnerUser, db: DbDep, cfg: SettingsDep, redis: RedisDep) -> dict:
    """Look through the last two years for transfers, card payments, refunds and savings
    payments that aren't tied yet (new rows are checked as they arrive)."""
    if wait := await hit(redis, "money-links", str(user.id), 6, 3600):
        raise too_many(wait, "scans")
    counts = await money_links.link(db, cfg, user.id, local_today(user) - timedelta(days=730))
    await db.commit()
    await store.changed(redis, user.id)
    return counts.__dict__


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
    if credit:
        # Income typed in for a month stands in for that month's expected salary.
        await db.flush()
        await money_salary.fill(db, user.id, local_today(user))
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
    if t.pair_id:
        prefs = await store.settings_for(db, user.id)
        money_links.unpair(t, await db.get(MoneyTxn, t.pair_id), rules=prefs.rules or {})
    credit = t.direction == "credit"
    await db.delete(t)
    if credit:
        await db.flush()
        await money_salary.fill(db, user.id, local_today(user))
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
    hidden: bool = False


@router.get("/tracking")
async def tracking(user: OwnerUser, db: DbDep) -> dict:
    """What Money has read for each account, month by month, and what would fill the gaps."""
    return await money_tracking.view(db, user.id, local_today(user))


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
            hidden=a.hidden,
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


# --- Investments -----------------------------------------------------------


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


@router.get("/investments/history")
async def investments_history(
    user: OwnerUser,
    db: DbDep,
    range: Annotated[Literal["1m", "3m", "6m", "1y", "3y", "all"], Query()] = "1y",  # noqa: A002
) -> dict:
    """Each share, fund and saving's value on each day of the Savings graph."""
    return await money_history.view(db, user.id, local_today(user), range)


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
    replace: Annotated[bool, Form()] = False,
) -> ImportOut:
    """Read a statement (CSV, .xlsx or PDF) or a CAS (PDF). The file is read in memory and
    dropped. A PDF's password opens it in memory; only a CAS password is ever kept, sealed, and
    only when asked."""
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
    typed = password.strip()
    kind = "statement"
    known = set(
        (await db.scalars(select(MoneyAccount.id).where(MoneyAccount.user_id == user.id))).all()
    )
    try:
        if is_pdf:
            # The typed password first, then the saved CAS one. A bank statement's password is
            # used here, in memory, and never kept.
            saved = money.unseal(cfg, prefs.cas_password_enc) or ""
            what, pw = await asyncio.to_thread(money.open_pdf, data, [typed, saved])
            if what == "cas":
                kind = "cas"
                holdings, as_of = await asyncio.to_thread(money.read_cas, data, pw)
                if not holdings:
                    raise money.StatementError(
                        "No mutual fund holdings were found in that statement."
                    )
                saved_count = await store.save_holdings(db, user.id, holdings, as_of)
                counts = {"holdings": saved_count}
                if typed and pw == typed and remember_password:
                    prefs.cas_password_enc = money.seal(cfg, typed)
                prefs.cas_synced_at = utcnow()
            else:
                rows = await asyncio.to_thread(money.read_pdf_statement, data, pw, name)
                counts = await store.ingest(
                    db,
                    cfg,
                    user.id,
                    rows,
                    source="statement",
                    account_id=account_id,
                    replace=replace,
                )
            del pw, saved
        else:
            rows = await asyncio.to_thread(money.parse_statement, data, name)
            if not rows:
                raise money.StatementError("No transactions were found in that file.")
            counts = await store.ingest(
                db,
                cfg,
                user.id,
                rows,
                source="statement",
                account_id=account_id,
                replace=replace,
            )
        if kind == "statement" and not account_id and known:
            now = set(
                (await db.scalars(select(MoneyAccount.id).where(MoneyAccount.user_id == uid))).all()
            )
            # Rows that landed on an account Money hadn't seen: the statement quoted other
            # digits. The upload says so, and Accounts can merge it into the right one.
            if made := len(now - known):
                counts["new_accounts"] = made
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


# --- Statements found in the mail -------------------------------------------------------------


def _mail_statement(
    r: MoneyMailStatement, labels: dict, gone: set | frozenset = frozenset()
) -> dict:
    return {
        "id": str(r.id),
        "received_at": r.received_at,
        "institution": r.institution,
        "mask": r.mask,
        "period_from": r.period_from,
        "period_to": r.period_to,
        # "covered": a statement read in already spans its dates.
        "status": "covered" if r.id in gone else r.status,
        "mailbox": labels.get(r.mailbox_id, "Your sign-in Gmail"),
    }


@router.get("/mail-statements")
async def mail_statements(user: OwnerUser, db: DbDep) -> list[dict]:
    """Statement PDFs banks emailed: new ones first, then the last few imported."""
    rows = (
        await db.scalars(
            select(MoneyMailStatement)
            .where(MoneyMailStatement.user_id == user.id, MoneyMailStatement.status != "dismissed")
            .order_by(MoneyMailStatement.received_at.desc())
            .limit(40)
        )
    ).all()
    labels = {b.id: b.email_masked for b in await money_mail.mailboxes(db, user.id)}
    new = [r for r in rows if r.status == "new"]
    gone = await money_tracking.covered(db, user.id, new)
    done = [r for r in rows if r.status == "imported"][:6]
    return [_mail_statement(r, labels, gone) for r in new + done]


class MailImportIn(BaseModel):
    # Used once, in memory, to open the PDF; never stored or logged.
    password: str = Field(default="", max_length=100)
    replace: bool = True
    account_id: uuid.UUID | None = None


@router.post("/mail-statements/{statement_id}/import", response_model=ImportOut)
async def import_mail_statement(
    statement_id: uuid.UUID,
    body: MailImportIn,
    request: Request,
    user: OwnerUser,
    db: DbDep,
    cfg: SettingsDep,
    redis: RedisDep,
) -> ImportOut:
    """Fetch an emailed statement's PDF, open it with the typed password and read it in place
    of what earlier imports read for those dates. The file is never kept."""
    uid = user.id
    if wait := await hit(redis, "money-import", str(uid), IMPORTS_PER_HOUR, 3600):
        raise too_many(wait, "imports")
    row = await db.get(MoneyMailStatement, statement_id)
    if row is None or row.user_id != uid:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement not found.")
    if body.account_id:
        account = await db.get(MoneyAccount, body.account_id)
        if account is None or account.user_id != uid:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    try:
        name, data = await money_mail.fetch_statement_pdf(
            db, cfg, uid, row.mailbox_id, row.message_id, request.app.state.google_http
        )
    except money_mail.StatementGone as exc:
        await db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    await db.commit()  # a refreshed Gmail token is kept
    try:
        what, pw = await asyncio.to_thread(money.open_pdf, data, [body.password.strip()])
        if what == "cas":
            raise money.StatementError(
                "That's a mutual fund statement: upload it under Import instead."
            )
        rows = await asyncio.to_thread(money.read_pdf_statement, data, pw, name)
        del pw
        counts = await store.ingest(
            db, cfg, uid, rows, source="statement", account_id=body.account_id,
            replace=body.replace,
        )  # fmt: skip
    except money.StatementError as exc:
        await db.rollback()
        log.info("money.mail_statement_failed", extra={"user_id": str(uid)})
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    finally:
        del data
    imp = await store.record_import(
        db, cfg, uid, kind="statement", origin="gmail", filename=name, status="done",
        counts=counts, account_id=body.account_id,
    )  # fmt: skip
    row = await db.get(MoneyMailStatement, statement_id)
    row.status, row.import_id = "imported", imp.id
    await db.commit()
    log.info("money.mail_statement_imported", extra={"user_id": str(uid), **counts})
    await store.changed(redis, uid, **counts)
    return _import(imp, cfg)


@router.post("/mail-statements/{statement_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_mail_statement(statement_id: uuid.UUID, user: OwnerUser, db: DbDep) -> Response:
    row = await db.get(MoneyMailStatement, statement_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement not found.")
    row.status = "dismissed"
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Deleting everything ---------------------------------------------------------------------


class DeleteAll(BaseModel):
    confirm: Literal["delete"]


@router.post("/delete-all", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all(body: DeleteAll, user: OwnerUser, db: DbDep, redis: RedisDep) -> Response:
    await store.delete_all(db, user.id)
    await db.commit()
    await store.changed(redis, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
