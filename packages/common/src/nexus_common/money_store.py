"""Money's database side, shared by the API (uploads, pages) and the orchestrator (Gmail sync,
nightly NAVs). Parsing lives in nexus_common.money; this file stores and adds up.

Every function takes the user id and only ever touches that person's rows. Logs carry counts.
"""

import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import money
from nexus_common.events import Event, publish
from nexus_common.models import (
    MoneyAccount,
    MoneyBudget,
    MoneyHolding,
    MoneyImport,
    MoneySettings,
    MoneyTxn,
    utcnow,
)
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

# How close in days an alert and a statement row must be to count as the same transaction:
# alerts carry the transaction date, statements sometimes the value date.
MATCH_DAYS = 2
TREND_MONTHS = 6
SUBSCRIPTION_LOOKBACK = timedelta(days=400)


async def settings_for(db: AsyncSession, user_id: uuid.UUID) -> MoneySettings:
    row = await db.get(MoneySettings, user_id)
    if row is None:
        row = MoneySettings(user_id=user_id, rules={}, created_at=utcnow(), updated_at=utcnow())
        db.add(row)
        await db.flush()
    return row


def account_label(account: MoneyAccount | None) -> str:
    if account is None:
        return "Unknown account"
    if account.name:
        return account.name
    bank = account.institution or ("Card" if account.kind == "card" else "Account")
    if account.kind == "card" and account.institution:
        bank += " card"
    return f"{bank} ••{account.mask}" if account.mask else bank


async def account_for(
    db: AsyncSession, user_id: uuid.UUID, institution: str | None, mask: str | None, kind: str
) -> MoneyAccount | None:
    """The account a transaction belongs to, made the first time it is seen. Without the last
    four digits there's nothing reliable to tie it to."""
    if not mask:
        return None
    rows = (
        await db.scalars(
            select(MoneyAccount).where(MoneyAccount.user_id == user_id, MoneyAccount.mask == mask)
        )
    ).all()
    # Same digits at the same bank (or a bank we couldn't tell) is the same account.
    for row in rows:
        if row.kind == kind and (
            not institution or not row.institution or row.institution == institution
        ):
            if institution and not row.institution:
                row.institution = institution
            return row
    account = MoneyAccount(
        user_id=user_id, institution=institution, mask=mask, kind=kind, name="", created_at=utcnow()
    )
    db.add(account)
    await db.flush()
    return account


async def ingest(
    db: AsyncSession,
    cfg: Settings,
    user_id: uuid.UUID,
    rows: list[money.Parsed],
    *,
    source: str,
    source_ref: str | None = None,
    account_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Store parsed transactions. A row seen before (same source) is skipped; an alert and a
    statement row for the same transaction become one row, marked confirmed."""
    counts = {"added": 0, "matched": 0, "skipped": 0}
    if not rows:
        return counts
    prefs = await settings_for(db, user_id)
    rules = prefs.rules or {}
    lo = min(r.occurred_on for r in rows) - timedelta(days=MATCH_DAYS)
    hi = max(r.occurred_on for r in rows) + timedelta(days=MATCH_DAYS)
    nearby = list(
        (
            await db.scalars(
                select(MoneyTxn).where(
                    MoneyTxn.user_id == user_id,
                    MoneyTxn.occurred_on >= lo,
                    MoneyTxn.occurred_on <= hi,
                )
            )
        ).all()
    )
    keys = {t.dedupe_key for t in nearby} | {t.alt_key for t in nearby if t.alt_key}
    fixed = await db.get(MoneyAccount, account_id) if account_id else None
    if fixed is not None and fixed.user_id != user_id:
        fixed = None
    other = {"alert": "statement", "statement": "alert"}.get(source)
    for p in rows:
        account = fixed or await account_for(
            db, user_id, p.institution, p.account_mask, p.account_kind
        )
        key = money.dedupe_key(p, str(account.id) if account else None)
        if key in keys:
            counts["skipped"] += 1
            continue
        keys.add(key)
        twin = None
        if other:
            twin = next(
                (
                    t
                    for t in nearby
                    if t.source == other
                    and not t.confirmed
                    and t.amount_paise == p.amount_paise
                    and t.direction == p.direction
                    and abs((t.occurred_on - p.occurred_on).days) <= MATCH_DAYS
                    and (account is None or t.account_id in (None, account.id))
                ),
                None,
            )
        if twin is not None:
            twin.confirmed = True
            twin.alt_key = key
            if source == "statement":
                # The statement's date, reference and balance are the bank's record.
                twin.occurred_on = p.occurred_on
                twin.ref = twin.ref or (p.ref or "")[:40] or None
                twin.balance_paise = p.balance_paise
            if account is not None and twin.account_id is None:
                twin.account_id = account.id
            counts["matched"] += 1
            continue
        credit = p.direction == "credit"
        category = money.categorise(p.merchant, p.narration, credit=credit, rules=rules)
        by_rule = bool(rules.get(money.merchant_key(p.merchant)))
        txn = MoneyTxn(
            user_id=user_id,
            account_id=account.id if account else None,
            occurred_on=p.occurred_on,
            amount_paise=p.amount_paise,
            direction=p.direction,
            merchant=p.merchant[:120],
            category=category,
            category_by="rule" if by_rule else "auto",
            narration_enc=money.seal(cfg, p.narration),
            channel=p.channel[:8],
            source=source,
            source_ref=(source_ref or "")[:64] or None,
            ref=(p.ref or "")[:40] or None,
            balance_paise=p.balance_paise,
            dedupe_key=key,
            created_at=utcnow(),
        )
        db.add(txn)
        nearby.append(txn)
        counts["added"] += 1
    await db.flush()
    log.info("money.ingested", extra={"user_id": str(user_id), "source": source, **counts})
    return counts


async def set_rule(db: AsyncSession, user_id: uuid.UUID, merchant: str, category: str) -> int:
    """Remember a category for a merchant and apply it to that merchant's rows the person
    hasn't set by hand. Returns how many rows changed."""
    prefs = await settings_for(db, user_id)
    key = money.merchant_key(merchant)
    prefs.rules = {**(prefs.rules or {}), key: category}
    prefs.updated_at = utcnow()
    rows = (
        await db.scalars(
            select(MoneyTxn).where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.merchant == merchant,
                MoneyTxn.category_by != "user",
            )
        )
    ).all()
    for row in rows:
        row.category = category
        row.category_by = "rule"
    return len(rows)


async def load(db: AsyncSession, user_id: uuid.UUID, start: date, end: date) -> list[money.Txn]:
    rows = (
        await db.execute(
            select(
                MoneyTxn.occurred_on,
                MoneyTxn.amount_paise,
                MoneyTxn.direction,
                MoneyTxn.merchant,
                MoneyTxn.category,
                MoneyTxn.excluded,
            ).where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.occurred_on >= start,
                MoneyTxn.occurred_on < end,
            )
        )
    ).all()
    return [money.Txn(*r) for r in rows]


def shift_month(month: str, by: int) -> str:
    y, m = (int(x) for x in month.split("-"))
    n = y * 12 + (m - 1) + by
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


async def overview(db: AsyncSession, user_id: uuid.UUID, month: str, today: date) -> dict:
    """Everything the Money overview shows for one month."""
    start, end = money.month_bounds(month)
    first = money.month_bounds(shift_month(month, -(TREND_MONTHS - 1)))[0]
    txns = await load(
        db, user_id, min(first, today - SUBSCRIPTION_LOOKBACK), max(end, today + timedelta(days=1))
    )
    this = [t for t in txns if start <= t.occurred_on < end]
    summary = money.summarise(this)
    trend = []
    for i in range(TREND_MONTHS - 1, -1, -1):
        m = shift_month(month, -i)
        a, b = money.month_bounds(m)
        s = money.summarise([t for t in txns if a <= t.occurred_on < b])
        trend.append(
            {"month": m, "spent_paise": s["spent_paise"], "income_paise": s["income_paise"]}
        )
    spent_by = {c["category"]: c["spent_paise"] for c in summary["categories"]}
    budgets = (
        await db.scalars(
            select(MoneyBudget).where(MoneyBudget.user_id == user_id).order_by(MoneyBudget.category)
        )
    ).all()
    # How far through the month we are, to say whether a budget is on pace.
    days = (end - start).days
    elapsed = min(max((today - start).days + 1, 0), days)
    subs = money.find_subscriptions(
        [t for t in txns if t.occurred_on >= today - SUBSCRIPTION_LOOKBACK], today
    )
    holdings = (await db.scalars(select(MoneyHolding).where(MoneyHolding.user_id == user_id))).all()
    prefs = await settings_for(db, user_id)
    return {
        "month": month,
        **summary,
        "trend": trend,
        "budgets": [
            {
                "category": b.category,
                "monthly_paise": b.monthly_paise,
                "spent_paise": spent_by.get(b.category, 0),
            }
            for b in budgets
        ],
        "month_progress": elapsed / days if days else 1.0,
        "subscriptions_monthly_paise": sum(s.monthly_paise for s in subs),
        "upcoming": [
            subscription_out(s) for s in subs if today <= s.next_on <= today + timedelta(days=14)
        ],
        "invested_paise": sum(h.value_paise or 0 for h in holdings),
        "invested_cost_paise": sum(h.cost_paise or 0 for h in holdings),
        "has_data": bool(txns) or bool(holdings),
        "gmail_sync": prefs.gmail_sync,
        "gmail_synced_at": prefs.gmail_synced_at,
    }


def subscription_out(s: money.Subscription) -> dict:
    return {
        "merchant": s.merchant,
        "category": s.category,
        "cadence": s.cadence,
        "typical_paise": s.typical_paise,
        "last_on": s.last_on,
        "next_on": s.next_on,
        "count": s.count,
        "monthly_paise": s.monthly_paise,
    }


async def save_holdings(
    db: AsyncSession, user_id: uuid.UUID, holdings: list[money.Holding], as_of: date | None
) -> int:
    """Replace the person's holdings with a newer statement's. An older statement than the one
    already stored changes nothing."""
    current = (await db.scalars(select(MoneyHolding).where(MoneyHolding.user_id == user_id))).all()
    newest = max((h.as_of for h in current if h.as_of), default=None)
    if as_of and newest and as_of < newest:
        return 0
    await db.execute(delete(MoneyHolding).where(MoneyHolding.user_id == user_id))
    seen: set[tuple[str, str]] = set()
    for h in holdings:
        if (h.folio, h.scheme) in seen:
            continue
        seen.add((h.folio, h.scheme))
        db.add(
            MoneyHolding(
                user_id=user_id,
                folio=h.folio,
                amc=h.amc,
                scheme=h.scheme,
                isin=(h.isin or "")[:16] or None,
                amfi_code=(h.amfi_code or "")[:16] or None,
                units=h.units,
                nav=h.nav,
                nav_date=h.nav_date,
                value_paise=h.value_paise,
                cost_paise=h.cost_paise,
                as_of=as_of,
                updated_at=utcnow(),
            )
        )
    await db.flush()
    log.info("money.holdings_saved", extra={"user_id": str(user_id), "count": len(seen)})
    return len(seen)


def revalue(h: MoneyHolding, navs: dict[str, tuple[Decimal, date]]) -> bool:
    """Value a holding at the newest NAV AMFI published for it. Returns whether it changed."""
    found = navs.get(h.isin or "") or navs.get(h.amfi_code or "")
    if not found:
        return False
    nav, when = found
    if h.nav_date and when < h.nav_date:
        return False
    h.nav, h.nav_date = nav, when
    h.value_paise = money.paise(Decimal(h.units) * nav)
    h.updated_at = utcnow()
    return True


async def record_import(
    db: AsyncSession,
    cfg: Settings,
    user_id: uuid.UUID,
    *,
    kind: str,
    origin: str,
    filename: str,
    status: str,
    counts: dict | None = None,
    error: str | None = None,
    account_id: uuid.UUID | None = None,
) -> MoneyImport:
    row = MoneyImport(
        user_id=user_id,
        kind=kind,
        origin=origin,
        filename_enc=money.seal(cfg, filename[:200]),
        account_id=account_id,
        status=status,
        counts=counts or {},
        error=(error or "")[:300] or None,
        created_at=utcnow(),
    )
    db.add(row)
    await db.flush()
    return row


async def delete_all(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Everything Money holds about the person, gone; the switches go back to off."""
    for model in (MoneyTxn, MoneyImport, MoneyBudget, MoneyHolding, MoneyAccount, MoneySettings):
        await db.execute(delete(model).where(model.user_id == user_id))
    log.info("money.deleted_all", extra={"user_id": str(user_id)})


async def changed(redis: Redis | None, user_id: uuid.UUID, **counts: int) -> None:
    if redis is not None:
        await publish(redis, user_id, Event(type="money.changed", data=counts))
