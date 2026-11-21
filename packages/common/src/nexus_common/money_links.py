"""Tying transactions together so money moving around isn't counted as spending or income.

The same rupees often show up twice: once leaving one of the person's accounts and once
arriving in another. Counted naively, a salary moved to a savings account looks like spending
plus income, and paying a card bill counts the card's purchases twice. Following what tools
like Firefly III, Monarch and Copilot do, `link` looks at rows that aren't tied to anything yet
and:

- **pairs transfers** between the person's own accounts: a debit and a credit of exactly the
  same amount on two different accounts within three days. A bank paying a card is a "card"
  pair. Both halves become Transfers;
- **pairs refunds** with the purchase they undo: money back from the same merchant within 90
  days, either named a refund or reversal, or of exactly the purchase's amount. The refund
  takes the purchase's category, so it lowers that category's spending instead of counting as
  income (money.refund);
- **ties savings** to the deposit they belong to: an RD instalment of the RD's amount, a PPF or
  NPS deposit, an FD booked for the FD's amount near its start date, or a maturity credit near
  the maturity date. They stay Investments (saved, not spent), and the Savings tab can say when
  the last instalment went in;
- **spots self-transfers** to accounts Money doesn't know: a UPI credit from someone with the
  person's own first and last name is a Transfer, not income.

A category the person set by hand is never changed. Pairs can be undone or made by hand in a
transaction's details (routes/money.py). Logs carry counts only.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import money
from nexus_common import money_plan as plan
from nexus_common.models import MoneyAccount, MoneyAsset, MoneyTxn, User
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

TRANSFER_DAYS = 3
REFUND_DAYS = 90
FD_START_DAYS = 7
MATURITY_DAYS = 10
_REFUND_WORDS = ("refund", "reversal", "reversed", "returned", "cancelled", "chargeback")


@dataclass
class Counts:
    transfers: int = 0
    card_payments: int = 0
    refunds: int = 0
    savings: int = 0
    self_transfers: int = 0

    def total(self) -> int:
        return (
            self.transfers + self.card_payments + self.refunds + self.savings + self.self_transfers
        )


def _free(t: MoneyTxn) -> bool:
    """Not already tied, not left out, and not a category the person chose."""
    return t.pair_id is None and not t.excluded and t.category_by != "user"


def _set_category(t: MoneyTxn, category: str) -> None:
    if t.category_by != "user":
        t.category = category
        t.category_by = "pair"


def pair(a: MoneyTxn, b: MoneyTxn, kind: str) -> None:
    """Tie two rows. Transfers and card payments become Transfers on both sides."""
    a.pair_id, b.pair_id = b.id, a.id
    a.pair_kind = b.pair_kind = kind
    if kind in ("transfer", "card"):
        _set_category(a, "Transfers")
        _set_category(b, "Transfers")


def unpair(a: MoneyTxn, b: MoneyTxn | None, *, rules: dict[str, str]) -> None:
    """Undo a pair: both rows go back to the category the word list or a rule gives them."""
    for t in (a, b):
        if t is None:
            continue
        t.pair_id = None
        t.pair_kind = None
        if t.category_by == "pair":
            credit = t.direction == "credit"
            t.category = money.categorise(t.merchant, credit=credit, rules=rules)
            t.category_by = "rule" if rules.get(money.merchant_key(t.merchant)) else "auto"


async def link(
    db: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    since: date,
) -> Counts:
    """Tie together what can be tied among rows from `since` on (and the rows they pair with).
    Flushes but doesn't commit."""
    counts = Counts()
    lo = since - timedelta(days=REFUND_DAYS)
    rows = list(
        (
            await db.scalars(
                select(MoneyTxn)
                .where(MoneyTxn.user_id == user_id, MoneyTxn.occurred_on >= lo)
                .order_by(MoneyTxn.occurred_on, MoneyTxn.created_at)
            )
        ).all()
    )
    if not rows:
        return counts
    kinds = {
        a.id: a
        for a in (
            await db.scalars(select(MoneyAccount).where(MoneyAccount.user_id == user_id))
        ).all()
    }
    user = await db.get(User, user_id)
    recent = [t for t in rows if t.occurred_on >= since - timedelta(days=TRANSFER_DAYS)]

    # 1. Transfers between the person's own accounts, and card bill payments.
    debits: dict[int, list[MoneyTxn]] = {}
    for t in recent:
        if t.direction == "debit" and t.account_id and _free(t):
            debits.setdefault(t.amount_paise, []).append(t)
    for c in recent:
        if c.direction != "credit" or not c.account_id or not _free(c):
            continue
        if c.category == "Income" and c.category_by == "rule":
            continue
        options = [
            d
            for d in debits.get(c.amount_paise, [])
            if d.pair_id is None
            and d.account_id != c.account_id
            and abs((d.occurred_on - c.occurred_on).days) <= TRANSFER_DAYS
        ]
        if not options:
            continue
        d = min(options, key=lambda x: (abs((x.occurred_on - c.occurred_on).days), x.created_at))
        card = any(
            kinds.get(x.account_id) is not None and kinds[x.account_id].kind == "card"
            for x in (c, d)
        )
        pair(d, c, "card" if card else "transfer")
        if card:
            counts.card_payments += 1
        else:
            counts.transfers += 1

    # 2. Refunds: money back from a merchant after a purchase there.
    purchases: dict[str, list[MoneyTxn]] = {}
    for t in rows:
        if (
            t.direction == "debit"
            and not t.excluded
            and t.category not in money.NOT_SPENDING
            and t.merchant != "Unknown"
        ):
            purchases.setdefault(money.merchant_key(t.merchant), []).append(t)
    for c in recent:
        if c.direction != "credit" or not _free(c) or c.category not in ("Income", "Other"):
            continue
        text = f"{c.merchant} {money.unseal(settings, c.narration_enc)}".lower()
        named = any(w in text for w in _REFUND_WORDS)
        options = [
            d
            for d in purchases.get(money.merchant_key(c.merchant), [])
            if d.occurred_on <= c.occurred_on
            and (c.occurred_on - d.occurred_on).days <= REFUND_DAYS
            and (d.amount_paise == c.amount_paise or (named and d.amount_paise >= c.amount_paise))
            and d.pair_kind in (None, "refund")
        ]
        if not options:
            continue
        d = max(options, key=lambda x: (x.amount_paise == c.amount_paise, x.occurred_on))
        c.pair_id, c.pair_kind = d.id, "refund"
        if d.pair_id is None:
            d.pair_id, d.pair_kind = c.id, "refund"
        if c.category_by != "user":
            c.category, c.category_by = d.category, "pair"
        counts.refunds += 1

    # 3. Money into and out of tracked savings.
    assets = list((await db.scalars(select(MoneyAsset).where(MoneyAsset.user_id == user_id))).all())
    if assets:
        for t in recent:
            if t.asset_id is not None or t.excluded or t.pair_id is not None:
                continue
            text = f"{t.merchant} {money.unseal(settings, t.narration_enc)}"
            asset = _saving_for(t, text, assets, kinds.get(t.account_id) if t.account_id else None)
            if asset is None:
                continue
            t.asset_id = asset.id
            if t.category_by != "user":
                t.category, t.category_by = "Investments", "pair"
            counts.savings += 1

    # 4. Credits from the person's own name: an account Money doesn't track.
    name = user.name if user else None
    for t in recent:
        if (
            t.direction == "credit"
            and _free(t)
            and t.category_by == "auto"
            and t.category == "Income"
            and money.is_self_payee(t.merchant, name)
        ):
            t.category, t.category_by = "Transfers", "pair"
            counts.self_transfers += 1

    await db.flush()
    if counts.total():
        log.info("money.linked", extra={"user_id": str(user_id), **counts.__dict__})
    return counts


def _saving_for(
    t: MoneyTxn, text: str, assets: list[MoneyAsset], account: MoneyAccount | None
) -> MoneyAsset | None:
    """The one tracked saving this row belongs to, or None when it isn't clear."""
    kind = money.saving_kind(text)
    if t.direction == "credit" and t.category != "Investments":
        return None
    if t.direction == "debit" and kind is None and t.category != "Investments":
        return None
    bank = (account.institution or "").lower() if account else ""
    options: list[tuple[int, MoneyAsset]] = []
    for a in assets:
        if kind and a.kind != kind:
            continue
        score = 0
        if t.direction == "debit":
            if a.status != "active":
                continue
            if a.kind == "rd":
                if t.amount_paise != a.amount_paise:
                    continue
                score += 2
            elif a.kind == "fd":
                if t.amount_paise != a.amount_paise or not a.start_on:
                    continue
                if abs((t.occurred_on - a.start_on).days) > FD_START_DAYS:
                    continue
                score += 2
            elif a.kind not in plan.BALANCE_KINDS or kind is None:
                continue
        else:
            if a.kind not in ("fd", "rd") or not a.maturity_on:
                continue
            if abs((t.occurred_on - a.maturity_on).days) > MATURITY_DAYS:
                continue
            due = plan.maturity_value(_plan_asset(a)) or a.amount_paise
            if abs(t.amount_paise - due) > max(due * 3 // 100, 100):
                continue
            score += 2
        own = (a.institution or "").lower()
        if bank and own and (own in bank or bank in own):
            score += 1
        options.append((score, a))
    if not options:
        return None
    options.sort(key=lambda x: -x[0])
    if len(options) > 1 and options[0][0] == options[1][0]:
        return None
    return options[0][1]


def _plan_asset(row: MoneyAsset) -> plan.Asset:
    return plan.Asset(
        kind=row.kind,
        amount_paise=row.amount_paise,
        rate_bp=row.rate_bp,
        start_on=row.start_on,
        maturity_on=row.maturity_on,
        balance_on=row.balance_on,
        contribution_paise=row.contribution_paise,
        payout=row.payout,
        status=row.status,
    )
