"""How investments have grown, for the graph on Savings.

Three sources, one per kind of value:
- Shares and funds are priced by the market, so a past value can't be worked out again later.
  Each night, after prices come in, Nexus stores what each holding is worth (money_snapshots).
  For shares it also fills in the months before they were added, from NSE's old daily files
  (the same public file for everyone, so nothing about the holding is sent), using the
  quantity typed in today.
- Deposits and schemes (FD, RD, PPF, EPF, NPS, gold, other) follow their terms, so any day's
  value is worked out on the spot from money_plan.asset_value.
- Today's point is always the live value, so the graph is current before the night's run.

Owner only; logs carry ids and counts, never names or amounts.
"""

import bisect
import logging
import uuid
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import money_plan as plan
from nexus_common import money_stocks
from nexus_common.models import MoneyAsset, MoneyHolding, MoneySnapshot, MoneyStock

log = logging.getLogger(__name__)

# The graph's groups, in their fixed colour order (the web's --viz-1..5).
GROUPS = ("shares", "funds", "deposits", "retirement", "other")
GROUP_OF = {
    "share": "shares",
    "fund": "funds",
    "fd": "deposits",
    "rd": "deposits",
    "ppf": "retirement",
    "epf": "retirement",
    "nps": "retirement",
    "gold": "other",
    "other": "other",
}
RANGES = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "3y": 1096, "all": None}
# The longest a stored value stands in for the days after it (a month between filled points,
# plus holidays).
CARRY_DAYS = 40
# How far back a share's history is filled in, and where monthly points give way to weekly.
FILL_YEARS = 5
WEEKLY_DAYS = 92
_FUND_NS = uuid.UUID("0b5c8a52-6a0e-4f61-9d0e-6f3a2b1c7d10")
_KIND_LABEL = {
    "fd": "Fixed deposit",
    "rd": "Recurring deposit",
    "ppf": "PPF",
    "epf": "EPF",
    "nps": "NPS",
    "gold": "Gold",
    "other": "Savings",
}


def fund_id(user_id: uuid.UUID, folio: str, scheme: str) -> uuid.UUID:
    """A fund holding's lasting id: its rows are replaced by each newer CAS."""
    return uuid.uuid5(_FUND_NS, f"{user_id}|{folio}|{scheme}")


def _months_back(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    return date(y, m + 1, 1)


def grid(start: date, today: date) -> list[date]:
    """The days the graph has a point on: daily up to about three months, weekly up to about
    a year, monthly beyond. Always ends today."""
    span = (today - start).days
    if span <= 0:
        return [today]
    if span <= 100:
        days = [start + timedelta(days=i) for i in range(span + 1)]
    elif span <= 400:
        days = [start + timedelta(days=i) for i in range(0, span, 7)]
    else:
        days, d = [start], date(start.year, start.month, 1)
        while True:
            y, m = divmod(d.year * 12 + d.month, 12)
            d = date(y, m + 1, 1)
            if d >= today:
                break
            days.append(d)
    if days[-1] != today:
        days.append(today)
    return days


def fill_days(bought_on: date, today: date) -> list[date]:
    """The days to fetch an earlier close for: monthly from the purchase, weekly over the last
    three months, never today (today's is the live price)."""
    start = max(bought_on, _months_back(today, FILL_YEARS * 12))
    weekly_from = today - timedelta(days=WEEKLY_DAYS)
    out: list[date] = []
    if start < weekly_from:
        out.append(start)
        k = 1
        while (d := _months_back(start, -k)) < weekly_from:
            out.append(d)
            k += 1
    d = max(start, weekly_from)
    while d < today:
        out.append(d)
        d += timedelta(days=7)
    return out


def _rows(values: list[dict]):
    return insert(MoneySnapshot).values(values)


async def record(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Store what each share and fund is worth on its latest price's day. Run nightly after
    repricing; a second run the same day just updates the row."""
    values: list[dict] = []
    stocks = (await db.scalars(select(MoneyStock).where(MoneyStock.user_id == user_id))).all()
    for s in stocks:
        value = money_stocks.value_paise(s.quantity, s.price_paise)
        if value is None or s.price_on is None:
            continue
        values.append({
            "id": uuid.uuid4(), "user_id": user_id, "item_id": s.id, "kind": "share",
            "day": s.price_on, "value_paise": value,
            "cost_paise": money_stocks.value_paise(s.quantity, s.avg_price_paise),
            "filled": False,
        })  # fmt: skip
    funds = (await db.scalars(select(MoneyHolding).where(MoneyHolding.user_id == user_id))).all()
    for h in funds:
        day = h.nav_date or h.as_of
        if h.value_paise is None or day is None:
            continue
        values.append({
            "id": uuid.uuid4(), "user_id": user_id, "item_id": fund_id(user_id, h.folio, h.scheme),
            "kind": "fund", "day": day, "value_paise": h.value_paise,
            "cost_paise": h.cost_paise, "filled": False,
        })  # fmt: skip
    if not values:
        return 0
    stmt = _rows(values)
    await db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_money_snapshots_item_day",
            set_={
                "value_paise": stmt.excluded.value_paise,
                "cost_paise": stmt.excluded.cost_paise,
                "filled": False,
            },
        )
    )
    return len(values)


async def save_filled(db: AsyncSession, stock: MoneyStock, closes: list[tuple[date, int]]) -> int:
    """Store a share's earlier closes (day, price per share), valued at today's quantity. A
    nightly value already stored for a day is kept."""
    if not closes:
        return 0
    cost = money_stocks.value_paise(stock.quantity, stock.avg_price_paise)
    stmt = _rows([
        {
            "id": uuid.uuid4(), "user_id": stock.user_id, "item_id": stock.id, "kind": "share",
            "day": day, "value_paise": money_stocks.value_paise(stock.quantity, close),
            "cost_paise": cost, "filled": True,
        }
        for day, close in closes
    ])  # fmt: skip
    await db.execute(stmt.on_conflict_do_nothing(constraint="uq_money_snapshots_item_day"))
    return len(closes)


async def forget(db: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID, *, filled_only=False):
    """Drop a holding's stored history: all of it when it's removed, or only the filled-in
    part when what it was filled from (quantity, price paid, date bought) changes."""
    q = delete(MoneySnapshot).where(
        MoneySnapshot.user_id == user_id, MoneySnapshot.item_id == item_id
    )
    if filled_only:
        q = q.where(MoneySnapshot.filled.is_(True))
    await db.execute(q)


def _asset_span(a: MoneyAsset, today: date) -> tuple[date, date]:
    """The days a saving counts on the graph: from when its value is known until it closed."""
    if a.kind in ("fd", "rd"):
        start = a.start_on or a.created_at.date()
    else:
        start = a.balance_on or a.start_on or a.created_at.date()
    end = today
    if a.status != "active":
        end = min(a.maturity_on or a.updated_at.date(), today)
    return start, end


def _carry(rows: list[tuple[date, int, int | None]], days: list[date]):
    """Each grid day's latest stored value at most CARRY_DAYS old, else None."""
    keys = [r[0] for r in rows]
    values: list[int | None] = []
    costs: list[int | None] = []
    for d in days:
        i = bisect.bisect_right(keys, d) - 1
        if i >= 0 and (d - keys[i]).days <= CARRY_DAYS:
            values.append(rows[i][1])
            costs.append(rows[i][2])
        else:
            values.append(None)
            costs.append(None)
    return values, costs


async def view(db: AsyncSession, user_id: uuid.UUID, today: date, range_key: str) -> dict:
    """Every holding's value on each day of the graph, for the web to filter and add up."""
    stocks = (await db.scalars(select(MoneyStock).where(MoneyStock.user_id == user_id))).all()
    funds = (await db.scalars(select(MoneyHolding).where(MoneyHolding.user_id == user_id))).all()
    assets = (await db.scalars(select(MoneyAsset).where(MoneyAsset.user_id == user_id))).all()

    first_stored = await db.scalar(
        select(func.min(MoneySnapshot.day)).where(MoneySnapshot.user_id == user_id)
    )
    spans = {a.id: _asset_span(a, today) for a in assets}
    starts = [s for s, _ in spans.values()] + ([first_stored] if first_stored else [])
    earliest = min(starts, default=today)
    days_back = RANGES.get(range_key, RANGES["1y"])
    start = earliest if days_back is None else max(earliest, today - timedelta(days=days_back))
    start = max(start, _months_back(today, 12 * 10))
    days = grid(start, today)

    live: dict[uuid.UUID, dict] = {}
    for s in stocks:
        live[s.id] = {
            "kind": "share", "label": s.name or s.symbol,
            "value": money_stocks.value_paise(s.quantity, s.price_paise),
            "cost": money_stocks.value_paise(s.quantity, s.avg_price_paise),
        }  # fmt: skip
    for h in funds:
        live[fund_id(user_id, h.folio, h.scheme)] = {
            "kind": "fund", "label": h.scheme, "value": h.value_paise, "cost": h.cost_paise,
        }  # fmt: skip

    stored: dict[uuid.UUID, list[tuple[date, int, int | None]]] = {}
    kinds: dict[uuid.UUID, str] = {}
    rows = await db.execute(
        select(
            MoneySnapshot.item_id,
            MoneySnapshot.kind,
            MoneySnapshot.day,
            MoneySnapshot.value_paise,
            MoneySnapshot.cost_paise,
        )
        .where(
            MoneySnapshot.user_id == user_id,
            MoneySnapshot.day >= start - timedelta(days=CARRY_DAYS),
            MoneySnapshot.day <= today,
        )
        .order_by(MoneySnapshot.item_id, MoneySnapshot.day)
    )
    for item_id, kind, day, value, cost in rows:
        stored.setdefault(item_id, []).append((day, value, cost))
        kinds[item_id] = kind

    series: list[dict] = []
    for item_id in set(stored) | set(live):
        values, costs = _carry(stored.get(item_id, []), days)
        now = live.get(item_id)
        # Today is the live value; a holding that's gone (a fund no longer in the CAS) ends.
        values[-1] = now["value"] if now else None
        costs[-1] = now["cost"] if now else None
        if not any(v is not None for v in values):
            continue
        kind = now["kind"] if now else kinds[item_id]
        label = now["label"] if now else ("Earlier fund" if kind == "fund" else "Earlier share")
        series.append({
            "id": str(item_id), "kind": kind, "group": GROUP_OF[kind], "label": label,
            "values": values, "costs": costs,
        })  # fmt: skip
    for a in assets:
        first, last = spans[a.id]
        terms = plan.Asset(
            kind=a.kind, amount_paise=a.amount_paise, rate_bp=a.rate_bp, start_on=a.start_on,
            maturity_on=a.maturity_on, balance_on=a.balance_on,
            contribution_paise=a.contribution_paise, payout=a.payout, status=a.status,
            cadence=a.contribution_cadence,
        )  # fmt: skip
        values = [plan.asset_value(terms, d) if first <= d <= last else None for d in days]
        if not any(v is not None for v in values):
            continue
        series.append({
            "id": str(a.id), "kind": a.kind, "group": GROUP_OF.get(a.kind, "other"),
            "label": a.name or _KIND_LABEL.get(a.kind, "Savings"),
            "values": values, "costs": [None] * len(days),
        })  # fmt: skip

    def order(s: dict):
        last = next((v for v in reversed(s["values"]) if v is not None), 0)
        return GROUPS.index(s["group"]), -last, s["label"]

    series.sort(key=order)
    filling = any(s.bought_on and s.history_from != s.bought_on for s in stocks)
    return {
        "range": range_key if range_key in RANGES else "1y",
        "days": days,
        "series": series,
        "first_on": earliest if (series and earliest < today) else None,
        "filling": filling,
    }
