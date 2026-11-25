"""Money suggests: what Money worked out from the person's own data, put as one-tap questions
with its best guess ready, so keeping Money right takes taps, not chores.

What it is sure of it does itself (orders matched to payments read later). The rest is asked
once: how an order was paid, whether a repeating payment is a subscription, whether monthly
money to a person is rent, what an unfiled shop is, how long a card EMI runs, whether a
one-off charge at a shop that sells plans is a yearly one, and what was bought in a recent
quick-commerce order whose email didn't list it (money_items). Each answer is applied the way the
person would have done it by hand (a rule for the merchant, a subscription decision, a loan),
and remembered so it isn't asked again.

Owner only. Nothing here goes to an AI model; logs carry kinds and counts, never names or
amounts.
"""

import logging
import re
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import money, money_habits, money_items
from nexus_common import money_plan as plan
from nexus_common import money_store as store
from nexus_common.models import (
    MoneyAccount,
    MoneyItem,
    MoneyLoan,
    MoneySettings,
    MoneyTxn,
    utcnow,
)
from nexus_common.settings import Settings, get_settings

log = logging.getLogger(__name__)

# How far back each question looks.
RECENT = timedelta(days=120)
OTHER_WINDOW = timedelta(days=90)
YEARLY_WINDOW = timedelta(days=60)
# Monthly money to a person at least this much may be rent.
RENT_FLOOR_PAISE = 3_000_00
# Shops in "Other" are asked about once they add up to this.
OTHER_FLOOR_PAISE = 500_00
OTHER_ASKED = 5
EMI_FLOOR_PAISE = 3_000_00
EMI_MONTHS = (3, 6, 9, 12, 18, 24)
YEARLY_FLOOR_PAISE = 500_00
# Quick-commerce orders asked "what did you buy?": recent ones only, a few at a time.
BASKET_WINDOW = timedelta(days=35)
BASKETS_ASKED = 6
# Answers at a shop that must agree before its usual kind is offered as the guess.
BASKET_USUAL = 3
BASKET_CHOICES = (
    ("staples", "Groceries and staples"),
    ("fresh", "Fruit, veg or meat"),
    ("dairy", "Milk, bread, eggs"),
    ("snacks", "Snacks and sweets"),
    ("drinks", "Cold drinks"),
    ("instant", "Instant or frozen food"),
    ("household", "Home and personal care"),
    ("tobacco", "Cigarettes"),
    ("mixed", "A bit of everything"),
)
# The answer that carries typed items ("milk 60, chips 40").
TYPED = "items"

# Shops that sell yearly plans: a single charge there may be a subscription renewing next year.
PLAN_SELLERS = {
    "playstation": "PlayStation Plus",
    "xbox": "Xbox Game Pass",
    "amazon prime": "Amazon Prime",
    "prime video": "Amazon Prime",
    "hotstar": "JioHotstar",
    "jiohotstar": "JioHotstar",
    "linkedin": "LinkedIn Premium",
    "microsoft": "Microsoft 365",
    "adobe": "Adobe",
    "canva": "Canva",
    "duolingo": "Duolingo",
    "nintendo": "Nintendo Switch Online",
}
# A subscription in these categories is most likely kept; in the others, most likely a shop
# used often.
KEPT_CATEGORIES = {
    "Subscriptions", "Entertainment", "Education", "Bills and utilities", "Investments",
    "Health", "Rent and housing", "Loans and EMIs",
}  # fmt: skip
CATEGORY_CHOICES = (
    "Food and dining", "Groceries", "Shopping", "Bills and utilities", "Health",
    "Entertainment", "Transport", "Travel", "Education", "Subscriptions", "Smoking and tobacco",
)  # fmt: skip


# Answers anyone can give: not now (asked again after a week).
LATER = "later"
LATER_DAYS = 7
# How many of a question's payments it lists, newest first.
DETAILS = 6
HOW = {"upi": "UPI", "card": "Card", "autopay": "Autopay", "netbanking": "Net banking",
       "cash": "Cash", "neft": "NEFT", "imps": "IMPS"}  # fmt: skip
# Words that mark a shop or a firm, not a person, in a payee's name.
_FIRM = re.compile(
    r"\b(?:the|and|co|company|ltd|limited|pvt|private|llp|inc|india|store|stores|shop|mart"
    r"|cafe|restaurant|kitchen|foods?|bakery|sweets|hotel|dhaba|medical|medicals|pharmacy"
    r"|chemist|clinic|hospital|enterprises?|traders?|trading|services|solutions|agency"
    r"|motors|centre|center|general|place|bazaar|super|market|fuels?|petroleum|digital"
    r"|technologies|tech|labs?|club|bank|finance|online|retail|wholesale|collection)\b",
    re.I,
)
# Person-to-person answers: what money to someone usually is.
PERSON_CHOICES = (
    ("Transfers", "Not spending (lent or my own)"),
    ("Food and dining", "Food or a shared bill"),
    ("Transport", "Cab, auto or driver"),
    ("Shopping", "Bought something"),
    ("Rent and housing", "Rent or house help"),
    ("Smoking and tobacco", "Cigarettes or a paan shop"),
    ("Other", "Other spending"),
)

EVERY = {
    "monthly": "every month",
    "bimonthly": "every two months",
    "quarterly": "every three months",
    "yearly": "once a year",
}


def _choice(id_: str, label: str) -> dict:
    return {"id": id_, "label": label}


def looks_like_person(merchant: str) -> bool:
    """Two to four plain words with no shop or firm word: a person's name, as UPI shows it."""
    words = merchant.split()
    return (
        2 <= len(words) <= 4
        and all(w.isalpha() and len(w) > 1 for w in words)
        and not _FIRM.search(merchant)
    )


def _wording(cfg: Settings, t: MoneyTxn) -> str:
    """The bank's own line for a payment, short, with long numbers cut to their last digits."""
    text = " ".join(money.unseal(cfg, t.narration_enc).split())
    text = re.sub(r"\d{6,}", lambda m: "…" + m.group()[-4:], text)
    return text[:90] + ("…" if len(text) > 90 else "")


def _context(rows: list[MoneyTxn], labels: dict, cfg: Settings) -> dict:
    """When, from where, how and the bank's words, for the newest payment, and the list."""
    if not rows:
        return {"details": []}
    rows = sorted(rows, key=lambda t: t.occurred_on, reverse=True)
    details = [
        {
            "on": t.occurred_on,
            "amount_paise": t.amount_paise,
            "account": labels.get(t.account_id),
            "how": HOW.get(t.channel or ""),
            "note": _wording(cfg, t),
        }
        for t in rows[:DETAILS]
    ]
    return {
        **{k: details[0][k] for k in ("account", "how", "note")},
        "first_on": rows[-1].occurred_on,
        "details": details,
    }


def solve_rate_bp(principal_paise: int, emi_paise: int, months: int) -> int:
    """The yearly rate (basis points) at which `months` instalments of `emi_paise` repay
    `principal_paise`: what a bank's EMI really costs. 0 when it's no-cost (or less)."""
    if emi_paise * months <= principal_paise:
        return 0
    lo, hi = 0, 60_00
    while lo < hi:
        mid = (lo + hi) // 2
        if plan.emi_for(principal_paise, mid, months) < emi_paise:
            lo = mid + 1
        else:
            hi = mid
    return lo


async def file_own(db: AsyncSession, user_id: uuid.UUID, owner: str | None) -> int:
    """Payments to the person's own name (to an account of theirs Money may not know) are
    transfers, not spending: filed so without asking."""
    if not owner:
        return 0
    rows = (
        await db.scalars(
            select(MoneyTxn).where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.category == "Other",
                MoneyTxn.category_by == "auto",
                MoneyTxn.pair_id.is_(None),
            )
        )
    ).all()
    n = 0
    for t in rows:
        if money.is_self_payee(t.merchant, owner):
            t.category = "Transfers"
            n += 1
    if n:
        await db.flush()
        log.info("money.own_transfers_filed", extra={"user_id": str(user_id), "count": n})
    return n


async def match_orders(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Orders no payment explained when their email came, now that a statement or a later
    alert shows the payment: the payment takes the shop's name and the order row goes. Done
    without asking: same amount (to a rupee), within the days an order and its payment fall."""
    orders = (
        await db.scalars(
            select(MoneyTxn).where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.source == "receipt",
                MoneyTxn.account_id.is_(None),
                MoneyTxn.channel != "cash",
                MoneyTxn.excluded.is_(False),
            )
        )
    ).all()
    if not orders:
        return 0
    lo = min(o.occurred_on for o in orders) - timedelta(days=store.ORDER_BEFORE)
    hi = max(o.occurred_on for o in orders) + timedelta(days=store.ORDER_AFTER)
    payments = list(
        (
            await db.scalars(
                select(MoneyTxn).where(
                    MoneyTxn.user_id == user_id,
                    MoneyTxn.direction == "debit",
                    MoneyTxn.source != "receipt",
                    MoneyTxn.account_id.is_not(None),
                    MoneyTxn.alt_key.is_(None),
                    MoneyTxn.pair_kind.is_(None),
                    MoneyTxn.excluded.is_(False),
                    MoneyTxn.occurred_on >= lo,
                    MoneyTxn.occurred_on <= hi,
                )
            )
        ).all()
    )
    gone: list[uuid.UUID] = []
    for order in sorted(orders, key=lambda o: o.occurred_on):
        fits = [
            p
            for p in payments
            if abs(p.amount_paise - order.amount_paise) <= 100
            and -store.ORDER_BEFORE <= (p.occurred_on - order.occurred_on).days <= store.ORDER_AFTER
        ]
        if not fits:
            continue
        pay = min(fits, key=lambda p: abs((p.occurred_on - order.occurred_on).days))
        payments.remove(pay)
        pay.merchant = order.merchant
        if pay.category_by == "auto":
            pay.category = order.category
        # The order email's key, so reading that email again finds this payment.
        pay.alt_key = order.dedupe_key
        pay.place = pay.place or order.place
        await money_habits.move_items(db, order.id, pay.id)
        gone.append(order.id)
    if gone:
        await db.execute(delete(MoneyTxn).where(MoneyTxn.id.in_(gone)))
        await db.flush()
        log.info("money.orders_matched", extra={"user_id": str(user_id), "count": len(gone)})
    return len(gone)


async def _accounts(db: AsyncSession, user_id: uuid.UUID) -> list[MoneyAccount]:
    return list(
        (
            await db.scalars(
                select(MoneyAccount)
                .where(MoneyAccount.user_id == user_id, MoneyAccount.hidden.is_(False))
                .order_by(MoneyAccount.created_at)
            )
        ).all()
    )


async def _orders(
    db: AsyncSession, user_id: uuid.UUID, today: date, accounts, cfg: Settings
) -> list[dict]:
    rows = (
        await db.scalars(
            select(MoneyTxn)
            .where(*store._unpaid(user_id, today))
            .order_by(MoneyTxn.occurred_on.desc())
            .limit(30)
        )
    ).all()
    if not rows:
        return []
    labels = {a.id: store.account_label(a) for a in accounts}
    since = today - timedelta(days=180)
    # Where each shop's payments usually come from, and the account most used lately.
    history = (
        await db.execute(
            select(MoneyTxn.merchant, MoneyTxn.account_id, MoneyTxn.channel).where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.direction == "debit",
                MoneyTxn.account_id.is_not(None),
                MoneyTxn.occurred_on >= since,
            )
        )
    ).all()
    by_shop: dict[str, Counter] = defaultdict(Counter)
    by_channel: dict[str, Counter] = defaultdict(Counter)
    for merchant, account_id, channel in history:
        if account_id in labels:
            by_shop[merchant][account_id] += 1
            by_channel[channel or "other"][account_id] += 1
    items = []
    for t in rows:
        guess, why = None, ""
        if by_shop.get(t.merchant):
            guess = by_shop[t.merchant].most_common(1)[0][0]
            why = f"Your {t.merchant} payments usually come from {labels[guess]}."
        elif by_channel.get(t.channel):
            guess = by_channel[t.channel].most_common(1)[0][0]
            kind = "UPI payments" if t.channel == "upi" else "payments"
            why = f"Most of your recent {kind} are from {labels[guess]}."
        choices = [_choice(f"account:{a.id}", labels[a.id]) for a in accounts]
        if guess is not None:
            choices.sort(key=lambda c: c["id"] != f"account:{guess}")
        choices += [_choice("cash", "Cash"), _choice("leave_out", "Someone else paid")]
        items.append(
            {
                "key": f"order:{t.id}",
                "kind": "order",
                "merchant": t.merchant,
                "amount_paise": t.amount_paise,
                "on": t.occurred_on,
                "note": _wording(cfg, t) if t.narration_enc else None,
                "details": [],
                "why": why,
                "choices": choices,
                "guess": f"account:{guess}" if guess else None,
            }
        )
    return items


async def _subscriptions(
    db: AsyncSession, user_id: uuid.UUID, today: date, txns: list[MoneyTxn], labels, cfg
) -> list[dict]:
    view = await store.subscriptions_view(db, user_id, today)
    by_key: dict[str, list[MoneyTxn]] = defaultdict(list)
    for t in txns:
        if t.direction == "debit":
            by_key[money.merchant_key(t.merchant)].append(t)
    items = []
    for s in view["items"]:
        if s["status"] != "new":
            continue
        kept = s["category"] in KEPT_CATEGORIES
        items.append(
            {
                **_context(by_key.get(money.merchant_key(s["merchant"]), []), labels, cfg),
                "key": f"subscription:{money.merchant_key(s['merchant'])}",
                "kind": "subscription",
                "merchant": s["merchant"],
                "amount_paise": s["amount_paise"],
                "on": s["next_on"],
                "cadence": s["cadence"],
                "count": s.get("count"),
                "why": f"Charged {s['count'] or 'several'} times, {EVERY[s['cadence']]}.",
                "choices": [
                    _choice("keep", "Keep, remind me"),
                    _choice("ignored", "Not a subscription"),
                    _choice("cancelled", "I've cancelled it"),
                ],
                "guess": "keep" if kept else "ignored",
            }
        )
    return items


def _rent(txns: list[MoneyTxn], rules: dict, owner: str | None, labels, cfg) -> list[dict]:
    """Money to the same person most months, about the same each time: often rent, hidden as a
    transfer (transfers aren't spending)."""
    by: dict[str, list[MoneyTxn]] = defaultdict(list)
    for t in txns:
        if (
            t.direction == "debit"
            and t.category == "Transfers"
            and t.category_by == "auto"
            and t.pair_id is None
            and not t.excluded
            and t.amount_paise >= RENT_FLOOR_PAISE
            and t.merchant != "Unknown"
            and not money.is_self_payee(t.merchant, owner)
            and money.merchant_key(t.merchant) not in rules
            # A card bill paid through Cred is a transfer by name, never rent.
            and money.categorise(t.merchant) != "Transfers"
        ):
            by[t.merchant].append(t)
    items = []
    for merchant, rows in by.items():
        months = {(t.occurred_on.year, t.occurred_on.month) for t in rows}
        if len(months) < 3:
            continue
        usual = int(statistics.median(t.amount_paise for t in rows))
        if any(abs(t.amount_paise - usual) > usual * 0.15 for t in rows):
            continue
        last = max(rows, key=lambda t: t.occurred_on)
        items.append(
            {
                **_context(rows, labels, cfg),
                "key": f"rent:{money.merchant_key(merchant)}",
                "kind": "rent",
                "merchant": merchant,
                "amount_paise": usual,
                "on": last.occurred_on,
                "count": len(months),
                "why": f"Paid in {len(months)} of the last 4 months, about the same each time.",
                "choices": [
                    _choice("Rent and housing", "Yes, rent"),
                    _choice("Other", "Other spending"),
                    _choice("Transfers", "Not spending (I owe them, or it's mine)"),
                ],
                "guess": "Rent and housing",
            }
        )
    return items


def _unfiled(
    txns: list[MoneyTxn], rules: dict, today: date, owner: str | None, labels, cfg
) -> list[dict]:
    """The shops in "Other" that add up most: one tap files every payment there, now and later."""
    spend: dict[str, list[MoneyTxn]] = defaultdict(list)
    for t in txns:
        if (
            t.direction == "debit"
            and t.category == "Other"
            and not t.excluded
            and t.occurred_on >= today - OTHER_WINDOW
            and t.merchant != "Unknown"
            and money.merchant_key(t.merchant) not in rules
            and not money.is_self_payee(t.merchant, owner)
        ):
            spend[t.merchant].append(t)
    top = sorted(spend.items(), key=lambda kv: -sum(t.amount_paise for t in kv[1]))
    items = []
    for merchant, rows in top:
        total = sum(t.amount_paise for t in rows)
        if total < OTHER_FLOOR_PAISE or len(items) >= OTHER_ASKED:
            break
        person = looks_like_person(merchant) and all(t.channel == "upi" for t in rows)
        guess, why = (
            None,
            "Filed under Other. Your answer files every payment there, now and later.",
        )
        if person:
            choices = [_choice(c, label) for c, label in PERSON_CHOICES]
            why = (
                "A payment to a person. Your answer files every payment to them the same way; "
                "you can change one later."
            )
        else:
            choices = [_choice(c, c) for c in CATEGORY_CHOICES] + [
                _choice("Other", "Leave as Other")
            ]
            if money.merchant_key(merchant).startswith("google") and all(
                t.channel == "upi" and t.amount_paise >= 1_000_00 for t in rows
            ):
                # Bills paid in Google Pay (electricity, phone) can reach the bank as "Google".
                guess = "Bills and utilities"
                why = (
                    "Bills paid in Google Pay (electricity, phone) often reach the bank as "
                    "just Google."
                )
        items.append(
            {
                **_context(rows, labels, cfg),
                "key": f"category:{money.merchant_key(merchant)}",
                "kind": "category",
                "person": person,
                "merchant": merchant,
                "amount_paise": total,
                "on": max(t.occurred_on for t in rows),
                "count": len(rows),
                "why": why,
                "choices": choices,
                "guess": guess,
            }
        )
    return items


async def _emis(
    db: AsyncSession, user_id: uuid.UUID, txns: list[MoneyTxn], labels, cfg
) -> list[dict]:
    """Card purchases turned into EMIs whose term the bank's email didn't give."""
    loans = (await db.scalars(select(MoneyLoan).where(MoneyLoan.user_id == user_id))).all()
    items = []
    for t in txns:
        if not (
            t.direction == "debit"
            and t.excluded
            and (t.note or "").startswith("Turned into EMIs")
            and t.amount_paise >= EMI_FLOOR_PAISE
        ):
            continue
        if any(
            abs(loan.principal_paise - t.amount_paise) <= 100
            and loan.account_id in (t.account_id, None)
            for loan in loans
        ):
            continue
        items.append(
            {
                **_context([t], labels, cfg),
                "key": f"emi:{t.id}",
                "kind": "emi",
                "merchant": t.merchant,
                "amount_paise": t.amount_paise,
                "on": t.occurred_on,
                "why": (
                    "The bank's email didn't say how many months. Add the monthly instalment "
                    "if you know it (the bank's app shows it) and Money works out the interest."
                ),
                "choices": [_choice(str(m), f"{m} months") for m in EMI_MONTHS],
                "guess": None,
            }
        )
    return items


async def _baskets(
    db: AsyncSession, user_id: uuid.UUID, today: date, txns: list[MoneyTxn]
) -> list[dict]:
    """Recent quick-commerce orders whose email didn't list the items: "what did you buy?".
    One tap files the whole order as one kind; typing the items splits it."""
    rows = [
        t
        for t in txns
        if t.direction == "debit"
        and not t.excluded
        and t.category not in money.NOT_SPENDING
        and t.occurred_on >= today - BASKET_WINDOW
        and money_items.is_basket(t.merchant)
        # An order nobody's payment explains is asked "how did you pay?" first.
        and not (t.source == "receipt" and t.account_id is None and t.channel != "cash")
    ]
    if not rows:
        return []
    told = set(
        (
            await db.scalars(
                select(MoneyItem.txn_id).where(
                    MoneyItem.user_id == user_id, MoneyItem.txn_id.in_([t.id for t in rows])
                )
            )
        ).all()
    )
    rows = sorted((t for t in rows if t.id not in told), key=lambda t: t.occurred_on, reverse=True)
    if not rows:
        return []
    # What the person usually buys at each shop: the main kind of each order they told Money
    # about. Guessed only when their recent answers there agree, so "Accept all" stays right.
    told_rows = (
        await db.execute(
            select(
                MoneyTxn.id,
                MoneyTxn.merchant,
                MoneyTxn.amount_paise,
                MoneyItem.kind,
                MoneyItem.price_paise,
                MoneyItem.quantity,
            )  # fmt: skip
            .join(MoneyTxn, MoneyTxn.id == MoneyItem.txn_id)
            .where(
                MoneyItem.user_id == user_id,
                MoneyItem.source == "you",
                MoneyTxn.occurred_on >= today - timedelta(days=180),
            )
        )
    ).all()
    orders: dict[uuid.UUID, tuple[str, int, list]] = {}
    for txn_id, merchant, amount, kind, price, quantity in told_rows:
        orders.setdefault(txn_id, (merchant, amount, []))[2].append((kind, price, quantity))
    usual: dict[str, Counter] = defaultdict(Counter)
    for merchant, amount, its in orders.values():
        split = money_items.shares(its, amount)
        usual[merchant][max(split, key=split.get) if split else "mixed"] += 1
    offered = {k for k, _ in BASKET_CHOICES}
    items = []
    for t in rows[:BASKETS_ASKED]:
        seen = usual[t.merchant]
        top = seen.most_common(1)
        guess = (
            top[0][0]
            if top
            and top[0][0] in offered
            and top[0][1] >= BASKET_USUAL
            and top[0][1] >= sum(seen.values()) * 2 / 3
            else None
        )
        items.append(
            {
                "key": f"basket:{t.id}",
                "kind": "basket",
                "merchant": t.merchant,
                "amount_paise": t.amount_paise,
                "on": t.occurred_on,
                "details": [],
                "why": ("What you usually buy here is highlighted. " if guess else "")
                + "One tap files the whole order; typing the items splits it.",
                "choices": [_choice(k, label) for k, label in BASKET_CHOICES],
                "guess": guess,
            }
        )
    return items


async def _yearly(
    db: AsyncSession, user_id: uuid.UUID, txns: list[MoneyTxn], today: date, labels, cfg
) -> list[dict]:
    """A recent single charge at a shop that sells yearly plans (PlayStation Plus): a year's
    subscription Money can't see repeat until next year."""
    view = await store.subscriptions_view(db, user_id, today)
    known = {money.merchant_key(s["merchant"]) for s in view["items"]}
    latest: dict[str, MoneyTxn] = {}
    for t in txns:
        key = money.merchant_key(t.merchant)
        if (
            t.direction == "debit"
            and not t.excluded
            and t.occurred_on >= today - YEARLY_WINDOW
            and t.amount_paise >= YEARLY_FLOOR_PAISE
            and key not in known
            and any(key.startswith(seller) for seller in PLAN_SELLERS)
        ):
            if key not in latest or t.occurred_on > latest[key].occurred_on:
                latest[key] = t
    items = []
    for key, t in latest.items():
        plan = next(name for seller, name in PLAN_SELLERS.items() if key.startswith(seller))
        items.append(
            {
                **_context([t], labels, cfg),
                "key": f"yearly:{t.id}",
                "kind": "yearly",
                "merchant": t.merchant,
                "amount_paise": t.amount_paise,
                "on": t.occurred_on,
                "plan": plan,
                "why": (
                    f"{t.merchant} sells plans like {plan}. Say if this one renews, "
                    "and Money reminds you before it does."
                ),
                "choices": [
                    _choice("yearly", "Yes, yearly"),
                    _choice("monthly", "Yes, monthly"),
                    _choice("once", "No, a one-off"),
                ],
                "guess": None,
            }
        )
    return items


async def suggestions(
    db: AsyncSession, user_id: uuid.UUID, today: date, owner: str | None = None
) -> dict:
    """Everything Money wants a tap on, most useful first. Does the sure things first (orders
    matched to payments, payments to the person's own name filed as transfers; flushes)."""
    cfg = get_settings()
    matched = await match_orders(db, user_id)
    filed = await file_own(db, user_id, owner)
    prefs = await db.get(MoneySettings, user_id)
    rules = (prefs.rules if prefs else None) or {}
    answered = (prefs.answered if prefs else None) or {}
    accounts = await _accounts(db, user_id)
    labels = {
        a.id: store.account_label(a)
        for a in (
            await db.scalars(select(MoneyAccount).where(MoneyAccount.user_id == user_id))
        ).all()
    }
    txns = list(
        (
            await db.scalars(
                select(MoneyTxn).where(
                    MoneyTxn.user_id == user_id, MoneyTxn.occurred_on >= today - RECENT
                )
            )
        ).all()
    )
    items = (
        await _emis(db, user_id, txns, labels, cfg)
        + await _yearly(db, user_id, txns, today, labels, cfg)
        + _rent(txns, rules, owner, labels, cfg)
        + await _subscriptions(db, user_id, today, txns, labels, cfg)
        + await _orders(db, user_id, today, accounts, cfg)
        + _unfiled(txns, rules, today, owner, labels, cfg)
        + await _baskets(db, user_id, today, txns)
    )
    items = [i for i in items if _open(answered.get(i["key"]), today)]
    return {"items": items, "matched": matched, "filed": filed}


def _open(answer: str | None, today: date) -> bool:
    """Not answered, or put off until a day that has come."""
    if answer is None:
        return True
    if answer.startswith(f"{LATER}:"):
        return date.fromisoformat(answer.split(":", 1)[1]) <= today
    return False


async def answer(
    db: AsyncSession,
    user_id: uuid.UUID,
    key: str,
    choice: str,
    today: date,
    owner: str | None,
    amount_paise: int | None = None,
    text: str | None = None,
) -> str:
    """Apply one answer the way the person would by hand, and remember it. Returns what was
    done, to show. Raises ValueError for a question or choice that no longer fits."""
    current = {i["key"]: i for i in (await suggestions(db, user_id, today, owner))["items"]}
    item = current.get(key)
    if item is None:
        raise ValueError("That's already been answered.")
    if choice == LATER:
        await _remember(db, user_id, key, f"{LATER}:{today + timedelta(days=LATER_DAYS)}")
        log.info("money.suggestion_later", extra={"user_id": str(user_id), "kind": item["kind"]})
        return f"Money will ask about {item['merchant']} again in a week"
    return await _apply(db, user_id, item, choice, today, amount_paise, text)


async def _remember(db: AsyncSession, user_id: uuid.UUID, key: str, value: str) -> None:
    prefs = await store.settings_for(db, user_id)
    prefs.answered = {**(prefs.answered or {}), key: value}
    prefs.updated_at = utcnow()
    await db.flush()


async def _apply(
    db: AsyncSession,
    user_id: uuid.UUID,
    item: dict,
    choice: str,
    today: date,
    amount_paise: int | None = None,
    text: str | None = None,
) -> str:
    typed = item["kind"] == "basket" and choice == TYPED
    if not typed and choice not in {c["id"] for c in item["choices"]}:
        raise ValueError("Pick one of the answers offered.")
    key = item["key"]
    kind, _, ref = key.partition(":")
    merchant = item["merchant"]
    done = ""
    if kind == "order":
        txn = await db.get(MoneyTxn, uuid.UUID(ref))
        if choice.startswith("account:"):
            account = await db.get(MoneyAccount, uuid.UUID(choice.split(":", 1)[1]))
            await store.confirm_order(db, user_id, txn, "account", account)
            done = f"{merchant}: paid from {store.account_label(account)}"
        else:
            await store.confirm_order(db, user_id, txn, choice, None)
            done = f"{merchant}: {'cash' if choice == 'cash' else 'left out'}"
    elif kind == "subscription":
        status = "active" if choice == "keep" else choice
        await store.save_subscription(db, user_id, merchant, status=status)
        done = {
            "keep": f"Keeping {merchant}; you'll be reminded before it renews",
            "ignored": f"{merchant} isn't a subscription",
            "cancelled": f"{merchant} marked cancelled; Money warns you if it charges again",
        }[choice]
    elif kind in ("rent", "category"):
        changed = await store.set_rule(db, user_id, merchant, choice)
        done = f"{merchant} is {choice} now ({changed} payment{'s' if changed != 1 else ''})"
        if kind == "rent" and choice == "Rent and housing":
            # Rent repeats: track it as a bill straight away, with a reminder, no second tap.
            await store.save_subscription(
                db, user_id, merchant, status="active", category="Rent and housing"
            )
            done += "; you'll be reminded before it's due"
    elif kind == "emi":
        txn = await db.get(MoneyTxn, uuid.UUID(ref))
        months = int(choice)
        principal = txn.amount_paise
        if amount_paise is not None and not principal // (months * 2) <= amount_paise <= principal:
            raise ValueError("That instalment doesn't fit the purchase. Check the amount.")
        rate = solve_rate_bp(principal, amount_paise, months) if amount_paise else 0
        emi = amount_paise or -(-principal // months)
        note = (
            "Added from Money's question; rate worked out from your monthly instalment."
            if amount_paise
            else "Added from Money's question. Taken as no-cost; edit the rate if it isn't."
        )
        db.add(
            MoneyLoan(
                user_id=user_id,
                kind="card_emi",
                name=f"EMI {merchant}"[:120],
                account_id=txn.account_id,
                lender=None,
                principal_paise=principal,
                rate_bp=rate,
                months=months,
                emi_paise=emi,
                first_due_on=money.add_months(txn.occurred_on, 1),
                status="active",
                note=note,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        done = f"EMI added: {months} months of {money.rupees(emi)}"
        if amount_paise:
            done += f", about {rate / 100:.1f}% a year" if rate else ", no interest"
    elif kind == "basket":
        txn = await db.get(MoneyTxn, uuid.UUID(ref))
        if typed:
            bought = money_items.parse_typed(text or "")
            if not bought:
                raise ValueError('Type what you bought, like "milk 60, chips 40, coke".')
            kinds = {money_items.KINDS[i.kind][0].lower() for i in bought if i.kind != "other"}
            done = f"{merchant}: {len(bought)} item{'s' if len(bought) != 1 else ''} saved"
            if kinds:
                done += f" ({', '.join(sorted(kinds))})"
        else:
            bought = [money_items.Item("", None, 1, choice)]
            done = f"{merchant}: {money_items.KINDS[choice][0].lower()}"
        await money_habits.save_items(db, get_settings(), user_id, txn.id, bought, "you")
    elif kind == "yearly" and choice in ("yearly", "monthly"):
        txn = await db.get(MoneyTxn, uuid.UUID(ref))
        step = 12 if choice == "yearly" else 1
        await store.save_subscription(
            db,
            user_id,
            merchant,
            status="active",
            manual=True,
            category="Subscriptions" if txn.category == "Other" else txn.category,
            cadence=choice,
            amount_paise=txn.amount_paise,
            next_on=money.roll_forward(money.add_months(txn.occurred_on, step), choice, today),
        )
        done = f"{item['plan']} added; you'll be reminded before it renews"
    else:
        done = "Noted"
    await _remember(db, user_id, key, choice)
    log.info("money.suggestion_answered", extra={"user_id": str(user_id), "kind": kind})
    return done


async def accept_all(db: AsyncSession, user_id: uuid.UUID, today: date, owner: str | None) -> int:
    """Take Money's guess for every question that has one. Returns how many were answered."""
    items = (await suggestions(db, user_id, today, owner))["items"]
    n = 0
    for item in items:
        if item["guess"] is None:
            continue
        await _apply(db, user_id, item, item["guess"], today)
        n += 1
    return n
