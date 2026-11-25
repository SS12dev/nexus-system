"""Day by day: what each day of a month cost, the habits under it (which weekdays cost most,
small spends that add up, how often quick-commerce and food-delivery orders come), and what
went into the quick-commerce baskets (money_items).

Habits come first from what Money has tracked; tips are only offered where the numbers behind
them are there. Owner only; logs carry counts, never names or amounts.
"""

import logging
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import money, money_items
from nexus_common.models import MoneyItem, MoneyTxn, utcnow
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

# Spending that's fixed by a bill or a contract, not a day's choices: left out of "everyday"
# spending so a rent day doesn't hide the habits.
FIXED = frozenset({"Rent and housing", "Loans and EMIs", "Bills and utilities", "Subscriptions"})
# A spend under this is "small": the coffees and top-ups that add up.
SMALL_PAISE = 300_00
# Weekday averages look back this far, so one odd week doesn't decide them.
WEEKDAY_WINDOW = 56
# A shop visited this often in a month is a habit worth showing.
FREQUENT = 4
# Food delivery apps (their grocery arms are baskets, money_items.BASKET_SHOPS).
DELIVERY = ("swiggy", "zomato", "eatsure", "eatclub", "box8", "faasos", "dominos", "domino s",
            "pizza hut", "mcdonald", "kfc", "behrouz", "magicpin")  # fmt: skip
# Baskets need this share of their spend told (by email or by the person) before Money breaks
# them down; below it the split would mislead.
TOLD_ENOUGH = 0.5


def is_delivery(merchant: str) -> bool:
    key = money.merchant_key(merchant)
    return not money_items.is_basket(merchant) and any(d in key for d in DELIVERY)


# --- Items -------------------------------------------------------------------------------------


async def save_items(
    db: AsyncSession,
    cfg: Settings,
    user_id: uuid.UUID,
    txn_id: uuid.UUID,
    items: list[money_items.Item],
    source: str,
) -> int:
    """Store what was bought in one order. An email's items never replace ones already there;
    the person's own answer replaces everything. Returns how many were stored."""
    existing = (await db.scalars(select(MoneyItem).where(MoneyItem.txn_id == txn_id))).all()
    if existing and source == "email":
        return 0
    for row in existing:
        await db.delete(row)
    for n, it in enumerate(items[: money_items.MAX_ITEMS]):
        db.add(
            MoneyItem(
                user_id=user_id,
                txn_id=txn_id,
                name_enc=money.seal(cfg, it.name[:120]) if it.name else "",
                quantity=max(1, min(it.quantity, 99)),
                price_paise=it.price_paise,
                kind=it.kind if it.kind in money_items.KINDS else "other",
                source=source,
                position=n,
                created_at=utcnow(),
            )
        )
    await db.flush()
    return len(items)


async def move_items(db: AsyncSession, src: uuid.UUID, dst: uuid.UUID) -> None:
    """An order row folded into its payment: the items follow, unless the payment has its own."""
    if await db.scalar(select(MoneyItem.id).where(MoneyItem.txn_id == dst).limit(1)):
        return
    await db.execute(update(MoneyItem).where(MoneyItem.txn_id == src).values(txn_id=dst))


async def has_items(db: AsyncSession, txn_id: uuid.UUID) -> bool:
    return bool(await db.scalar(select(MoneyItem.id).where(MoneyItem.txn_id == txn_id).limit(1)))


def item_out(cfg: Settings, row: MoneyItem) -> dict:
    return {
        "id": str(row.id),
        "name": money.unseal(cfg, row.name_enc) if row.name_enc else "",
        "quantity": row.quantity,
        "price_paise": row.price_paise,
        "kind": row.kind,
        "kind_label": money_items.KINDS.get(row.kind, ("Other items", True))[0],
        "source": row.source,
    }


async def items_of(db: AsyncSession, cfg: Settings, txn_id: uuid.UUID) -> list[dict]:
    rows = (
        await db.scalars(
            select(MoneyItem).where(MoneyItem.txn_id == txn_id).order_by(MoneyItem.position)
        )
    ).all()
    return [item_out(cfg, r) for r in rows]


async def _kinds_by_txn(
    db: AsyncSession, user_id: uuid.UUID, txn_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[str, int | None, int]]]:
    out: dict[uuid.UUID, list[tuple[str, int | None, int]]] = defaultdict(list)
    for i in range(0, len(txn_ids), 1000):
        rows = (
            await db.execute(
                select(MoneyItem.txn_id, MoneyItem.kind, MoneyItem.price_paise, MoneyItem.quantity)
                .where(MoneyItem.user_id == user_id, MoneyItem.txn_id.in_(txn_ids[i : i + 1000]))
                .order_by(MoneyItem.position)
            )
        ).all()
        for txn_id, kind, price, quantity in rows:
            out[txn_id].append((kind, price, quantity))
    return out


# --- The day-by-day view -----------------------------------------------------------------------


def _row(t: MoneyTxn) -> money.Txn:
    return money.Txn(
        t.occurred_on, t.amount_paise, t.direction, t.merchant, t.category, t.excluded,
        channel=t.channel or "other",
    )  # fmt: skip


def _cost(t: MoneyTxn) -> int:
    """What a row adds to spending: a purchase adds, a refund of one takes away, the rest is 0."""
    r = _row(t)
    if money.spending(r):
        return t.amount_paise
    if money.refund(r):
        return -t.amount_paise
    return 0


def _summary(rows: list[MoneyTxn], kinds: dict, which) -> dict:
    """Orders of one sort (quick-commerce baskets, food deliveries): how many, what they cost,
    and what was in them where Money knows."""
    orders = [t for t in rows if _cost(t) > 0 and which(t.merchant)]
    spent = sum(t.amount_paise for t in orders)
    by_kind: Counter = Counter()
    told = 0
    places: dict[str, list[int]] = defaultdict(list)
    for t in orders:
        if kinds.get(t.id):
            told += t.amount_paise
            by_kind.update(money_items.shares(kinds[t.id], t.amount_paise))
        if t.place:
            places[t.place].append(t.amount_paise)
    return {
        "orders": len(orders),
        "spent_paise": spent,
        "average_paise": spent // len(orders) if orders else 0,
        "told_paise": told,
        "untold": sum(1 for t in orders if not kinds.get(t.id)),
        "fees_paise": by_kind.get("fees", 0),
        "by_kind": [
            {
                "kind": k,
                "label": money_items.KINDS[k][0],
                "need": money_items.KINDS[k][1],
                "spent_paise": v,
            }
            for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])
            if v > 0 and k in money_items.KINDS
        ],
        "treats_paise": sum(v for k, v in by_kind.items() if k in money_items.TREATS),
        "places": sorted(
            ({"place": p, "count": len(v), "spent_paise": sum(v)} for p, v in places.items()),
            key=lambda x: (-x["count"], -x["spent_paise"]),
        )[:5],
    }


def _short(p: int) -> str:
    return money.rupees(p).split(".")[0]


def _tips(month: dict, last: dict, small: dict, weekdays: list[dict], delivery: dict,
          last_delivery: dict, days: int) -> list[dict]:  # fmt: skip
    """Plain, specific observations with a way to act on each; only where the data holds them.
    `month` and `last` are basket summaries."""
    tips: list[dict] = []
    told = month["told_paise"] / month["spent_paise"] if month["spent_paise"] else 0
    if month["orders"] >= 3:
        every = days / month["orders"]
        line = (
            f"{month['orders']} quick-commerce orders so far, about one every "
            f"{every:.1f} days, averaging {_short(month['average_paise'])}."
            if every >= 1
            else f"{month['orders']} quick-commerce orders in {days} days, averaging "
            f"{_short(month['average_paise'])}."
        )
        if month["orders"] >= 8:
            line += (
                " Small orders are where fees and impulse buys creep in: one planned weekly "
                "order with top-ups only for milk and fresh food usually halves the count."
            )
        tips.append({"kind": "baskets", "text": line})
    if told >= TOLD_ENOUGH and month["fees_paise"] >= 50_00:
        tips.append({
            "kind": "fees",
            "text": f"{_short(month['fees_paise'])} went on delivery, handling and small-cart "
            "fees. Fewer, fuller baskets (past the free-delivery amount) would save most of it.",
        })  # fmt: skip
    if told >= TOLD_ENOUGH and month["treats_paise"] >= 300_00:
        share = month["treats_paise"] / max(month["told_paise"], 1)
        tips.append({
            "kind": "treats",
            "text": f"Snacks, cold drinks and instant food were {_short(month['treats_paise'])}"
            f", about {share:.0%} of the baskets Money knows the contents of. That's the part "
            "to trim first: it's the most impulse-driven.",
        })  # fmt: skip
    if last["spent_paise"] and month["spent_paise"] > last["spent_paise"] * 1.25 and days >= 20:
        tips.append({
            "kind": "baskets_up",
            "text": f"Quick commerce is up from {_short(last['spent_paise'])} last month to "
            f"{_short(month['spent_paise'])} this month.",
        })  # fmt: skip
    if delivery["orders"] >= 6:
        line = (
            f"{delivery['orders']} food deliveries came to {_short(delivery['spent_paise'])}"
            f", about {_short(delivery['average_paise'])} each"
        )
        if last_delivery["orders"]:
            line += f" (last month {last_delivery['orders']} for "
            line += f"{_short(last_delivery['spent_paise'])})"
        line += "."
        if days and delivery["orders"] / days >= 2 / 7:
            line += (
                " Every order carries platform and delivery charges on top of the food: "
                "cooking or a tiffin on two of those days a week is the biggest single saving "
                "here."
            )
        tips.append({"kind": "delivery", "text": line})
    extras = {k["kind"]: k["spent_paise"] for k in delivery["by_kind"]}
    sweet_drink = extras.get("sweets", 0) + extras.get("drinks", 0)
    if delivery["told_paise"] and sweet_drink >= 300_00:
        tips.append({
            "kind": "delivery_extras",
            "text": f"Desserts, bakes and drinks in food orders came to about "
            f"{_short(sweet_drink)} (split from the order emails by item count, as Zomato "
            "doesn't list prices). They're the easiest part of an order to leave out.",
        })  # fmt: skip
    if delivery["places"] and delivery["places"][0]["count"] >= 3:
        top = delivery["places"][0]
        tips.append({
            "kind": "delivery_place",
            "text": f"{top['place']} is your most-ordered place: {top['count']} times for "
            f"{_short(top['spent_paise'])}.",
        })  # fmt: skip
    if small["count"] >= 15:
        tips.append({
            "kind": "small",
            "text": f"{small['count']} spends under {_short(SMALL_PAISE)} add up to "
            f"{_short(small['spent_paise'])}. Each feels like nothing; together they're a "
            "real line in the budget.",
        })  # fmt: skip
    known = [w for w in weekdays if w["days"]]
    if len(known) == 7:
        weekend = (known[5]["average_paise"] + known[6]["average_paise"]) / 2
        weekday = sum(w["average_paise"] for w in known[:5]) / 5
        if weekday and weekend > weekday * 1.5 and weekend - weekday >= 300_00:
            tips.append({
                "kind": "weekend",
                "text": f"Weekends cost about {_short(int(weekend))} a day against "
                f"{_short(int(weekday))} on weekdays.",
            })  # fmt: skip
    return tips


async def daily(
    db: AsyncSession, cfg: Settings, user_id: uuid.UUID, month: str, today: date
) -> dict:
    """Every day of `month`: what it cost, everyday spending (without rent, EMIs, bills and
    subscriptions), and the rows; plus the month's habits and the baskets' contents."""
    start, end = money.month_bounds(month)
    last_start = money.month_bounds(_shift(month, -1))[0]
    stop = min(end, today + timedelta(days=1))
    window = min(stop, end) - timedelta(days=WEEKDAY_WINDOW)
    rows = list(
        (
            await db.scalars(
                select(MoneyTxn)
                .where(
                    MoneyTxn.user_id == user_id,
                    MoneyTxn.occurred_on >= min(last_start, window),
                    MoneyTxn.occurred_on < end,
                )
                .order_by(MoneyTxn.occurred_on, MoneyTxn.created_at)
            )
        ).all()
    )
    this = [t for t in rows if start <= t.occurred_on < end]
    before = [t for t in rows if last_start <= t.occurred_on < start]
    kinds = await _kinds_by_txn(
        db,
        user_id,
        [
            t.id
            for t in this + before
            if money_items.is_basket(t.merchant) or is_delivery(t.merchant)
        ],
    )
    counted_days = max((stop - start).days, 0) if start <= today else 0
    if today >= end:
        counted_days = (end - start).days

    by_day: dict[date, list[MoneyTxn]] = defaultdict(list)
    for t in this:
        if _cost(t):
            by_day[t.occurred_on].append(t)
    days = []
    d = start
    while d < end:
        spends = by_day.get(d, [])
        spent = sum(_cost(t) for t in spends)
        everyday = sum(_cost(t) for t in spends if t.category not in FIXED)
        days.append(
            {
                "on": d,
                "spent_paise": spent,
                "everyday_paise": everyday,
                "fixed_paise": spent - everyday,
                "count": len(spends),
                "future": d > today,
                "items": [
                    {
                        "id": str(t.id),
                        "merchant": t.merchant,
                        "amount_paise": _cost(t),
                        "category": t.category,
                        "fixed": t.category in FIXED,
                        "basket": money_items.is_basket(t.merchant),
                        "place": t.place,
                        "kinds": sorted({k[0] for k in kinds.get(t.id, [])}),
                    }
                    for t in sorted(spends, key=lambda t: -abs(_cost(t)))
                ],
            }
        )
        d += timedelta(days=1)
    past = [x for x in days if not x["future"]][:counted_days] if counted_days else []
    everyday_total = sum(x["everyday_paise"] for x in past)
    spent_total = sum(x["spent_paise"] for x in past)
    biggest = max(past, key=lambda x: x["everyday_paise"], default=None)

    # Weekday habits over the last few weeks up to the end of this month (or today).
    per_day: dict[date, int] = defaultdict(int)
    for t in rows:
        if window <= t.occurred_on < stop and t.category not in FIXED:
            per_day[t.occurred_on] += _cost(t)
    totals = [[0, 0] for _ in range(7)]
    d = max(window, rows[0].occurred_on if rows else window)
    while d < stop:
        totals[d.weekday()][0] += per_day.get(d, 0)
        totals[d.weekday()][1] += 1
        d += timedelta(days=1)
    weekdays = [
        {"weekday": i, "average_paise": (s // n) if n else 0, "days": n}
        for i, (s, n) in enumerate(totals)
    ]

    small_rows = [
        t for t in this
        if 0 < _cost(t) < SMALL_PAISE and t.category not in FIXED and t.occurred_on < stop
    ]  # fmt: skip
    small = {"count": len(small_rows), "spent_paise": sum(t.amount_paise for t in small_rows)}
    visits: dict[str, list[int]] = defaultdict(list)
    for t in this:
        if _cost(t) > 0 and t.category not in FIXED and t.merchant != "Unknown":
            visits[t.merchant].append(t.amount_paise)
    frequent = sorted(
        (
            {"merchant": m, "count": len(v), "spent_paise": sum(v)}
            for m, v in visits.items()
            if len(v) >= FREQUENT
        ),
        key=lambda x: (-x["count"], -x["spent_paise"]),
    )[:5]

    basket = _summary(this, kinds, money_items.is_basket)
    last_basket = _summary(before, kinds, money_items.is_basket)
    delivery = _summary(this, kinds, is_delivery)
    last_delivery = _summary(before, kinds, is_delivery)
    return {
        "month": month,
        "days": days,
        "counted_days": counted_days,
        "spent_paise": spent_total,
        "everyday_paise": everyday_total,
        "everyday_average_paise": everyday_total // counted_days if counted_days else 0,
        "no_spend_days": sum(1 for x in past if x["everyday_paise"] <= 0),
        "biggest": (
            {"on": biggest["on"], "everyday_paise": biggest["everyday_paise"]}
            if biggest and biggest["everyday_paise"] > 0
            else None
        ),
        "weekdays": weekdays,
        "small": small,
        "frequent": frequent,
        "baskets": basket,
        "baskets_last": {
            "orders": last_basket["orders"],
            "spent_paise": last_basket["spent_paise"],
        },
        "delivery": delivery,
        "delivery_last": {
            "orders": last_delivery["orders"],
            "spent_paise": last_delivery["spent_paise"],
        },
        "tips": _tips(
            basket, last_basket, small, weekdays, delivery, last_delivery, counted_days or 1
        ),
    }


def _shift(month: str, by: int) -> str:
    y, m = (int(x) for x in month.split("-"))
    n = y * 12 + (m - 1) + by
    return f"{n // 12:04d}-{n % 12 + 1:02d}"
