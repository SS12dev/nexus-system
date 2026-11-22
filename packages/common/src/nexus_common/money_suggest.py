"""Money suggests: what Money worked out from the person's own data, put as one-tap questions
with its best guess ready, so keeping Money right takes taps, not chores.

What it is sure of it does itself (orders matched to payments read later). The rest is asked
once: how an order was paid, whether a repeating payment is a subscription, whether monthly
money to a person is rent, what an unfiled shop is, how long a card EMI runs, and whether a
one-off charge at a shop that sells plans is a yearly one. Each answer is applied the way the
person would have done it by hand (a rule for the merchant, a subscription decision, a loan),
and remembered so it isn't asked again.

Owner only. Nothing here goes to an AI model; logs carry kinds and counts, never names or
amounts.
"""

import logging
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import money
from nexus_common import money_store as store
from nexus_common.models import MoneyAccount, MoneyLoan, MoneySettings, MoneyTxn, utcnow

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
    "Entertainment", "Transport", "Travel", "Education", "Subscriptions",
)  # fmt: skip


EVERY = {
    "monthly": "every month",
    "bimonthly": "every two months",
    "quarterly": "every three months",
    "yearly": "once a year",
}


def _choice(id_: str, label: str) -> dict:
    return {"id": id_, "label": label}


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


async def _orders(db: AsyncSession, user_id: uuid.UUID, today: date, accounts) -> list[dict]:
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
                "why": why,
                "choices": choices,
                "guess": f"account:{guess}" if guess else None,
            }
        )
    return items


async def _subscriptions(db: AsyncSession, user_id: uuid.UUID, today: date) -> list[dict]:
    view = await store.subscriptions_view(db, user_id, today)
    items = []
    for s in view["items"]:
        if s["status"] != "new":
            continue
        kept = s["category"] in KEPT_CATEGORIES
        items.append(
            {
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


def _rent(txns: list[MoneyTxn], rules: dict, owner: str | None) -> list[dict]:
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


def _unfiled(txns: list[MoneyTxn], rules: dict, today: date) -> list[dict]:
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
        ):
            spend[t.merchant].append(t)
    top = sorted(spend.items(), key=lambda kv: -sum(t.amount_paise for t in kv[1]))
    items = []
    for merchant, rows in top:
        total = sum(t.amount_paise for t in rows)
        if total < OTHER_FLOOR_PAISE or len(items) >= OTHER_ASKED:
            break
        items.append(
            {
                "key": f"category:{money.merchant_key(merchant)}",
                "kind": "category",
                "merchant": merchant,
                "amount_paise": total,
                "on": max(t.occurred_on for t in rows),
                "count": len(rows),
                "why": "Filed under Other. Your answer files every payment there, now and later.",
                "choices": [_choice(c, c) for c in CATEGORY_CHOICES]
                + [_choice("Other", "Leave as Other")],
                "guess": None,
            }
        )
    return items


async def _emis(db: AsyncSession, user_id: uuid.UUID, txns: list[MoneyTxn]) -> list[dict]:
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
                "key": f"emi:{t.id}",
                "kind": "emi",
                "merchant": t.merchant,
                "amount_paise": t.amount_paise,
                "on": t.occurred_on,
                "why": (
                    "The bank's email didn't say how many months. "
                    "Your answer adds the EMI under Loans."
                ),
                "choices": [_choice(str(m), f"{m} months") for m in EMI_MONTHS],
                "guess": None,
            }
        )
    return items


async def _yearly(
    db: AsyncSession, user_id: uuid.UUID, txns: list[MoneyTxn], today: date
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
    """Everything Money wants a tap on, most useful first. Matches orders first (flushes)."""
    matched = await match_orders(db, user_id)
    prefs = await db.get(MoneySettings, user_id)
    rules = (prefs.rules if prefs else None) or {}
    answered = (prefs.answered if prefs else None) or {}
    accounts = await _accounts(db, user_id)
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
        await _emis(db, user_id, txns)
        + await _yearly(db, user_id, txns, today)
        + _rent(txns, rules, owner)
        + await _subscriptions(db, user_id, today)
        + await _orders(db, user_id, today, accounts)
        + _unfiled(txns, rules, today)
    )
    items = [i for i in items if i["key"] not in answered]
    return {"items": items, "matched": matched}


async def answer(
    db: AsyncSession, user_id: uuid.UUID, key: str, choice: str, today: date, owner: str | None
) -> str:
    """Apply one answer the way the person would by hand, and remember it. Returns what was
    done, to show. Raises ValueError for a question or choice that no longer fits."""
    current = {i["key"]: i for i in (await suggestions(db, user_id, today, owner))["items"]}
    item = current.get(key)
    if item is None:
        raise ValueError("That's already been answered.")
    return await _apply(db, user_id, item, choice, today)


async def _apply(db: AsyncSession, user_id: uuid.UUID, item: dict, choice: str, today: date) -> str:
    if choice not in {c["id"] for c in item["choices"]}:
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
        db.add(
            MoneyLoan(
                user_id=user_id,
                kind="card_emi",
                name=f"EMI {merchant}"[:120],
                account_id=txn.account_id,
                lender=None,
                principal_paise=txn.amount_paise,
                rate_bp=0,
                months=months,
                emi_paise=-(-txn.amount_paise // months),
                first_due_on=money.add_months(txn.occurred_on, 1),
                status="active",
                note="Added from Money's question. Taken as no-cost; edit the rate if it isn't.",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        done = f"EMI added: {months} months"
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
    prefs = await store.settings_for(db, user_id)
    prefs.answered = {**(prefs.answered or {}), key: choice}
    prefs.updated_at = utcnow()
    await db.flush()
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
