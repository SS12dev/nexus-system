"""Money's database side, shared by the API (uploads, pages) and the orchestrator (Gmail sync,
nightly NAVs). Parsing lives in nexus_common.money; this file stores and adds up.

Every function takes the user id and only ever touches that person's rows. Logs carry counts.
"""

import dataclasses
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import (
    money,
    money_habits,
    money_links,
    money_orders,
    money_salary,
    money_stocks,
)
from nexus_common import money_plan as plan
from nexus_common.events import Event, publish
from nexus_common.models import (
    MoneyAccount,
    MoneyAsset,
    MoneyBudget,
    MoneyGoal,
    MoneyHolding,
    MoneyImport,
    MoneyItem,
    MoneyLoan,
    MoneyMailStatement,
    MoneySalary,
    MoneySettings,
    MoneyStock,
    MoneySubscription,
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


async def attach_debit_card(
    db: AsyncSession, user_id: uuid.UUID, account: MoneyAccount, mask: str | None
) -> int:
    """Give a bank account its debit card's last four digits. Rows already read from that
    card's alerts (which made an account of their own) move onto the bank account, and the
    stand-in account goes. Returns how many rows moved."""
    account.card_mask = mask
    if not mask:
        return 0
    moved = 0
    for dup in (
        await db.scalars(
            select(MoneyAccount).where(
                MoneyAccount.user_id == user_id,
                MoneyAccount.kind == "bank",
                MoneyAccount.mask == mask,
                MoneyAccount.id != account.id,
            )
        )
    ).all():
        if dup.institution and account.institution and dup.institution != account.institution:
            continue
        rows = (await db.scalars(select(MoneyTxn).where(MoneyTxn.account_id == dup.id))).all()
        for t in rows:
            t.account_id = account.id
        moved += len(rows)
        await db.flush()
        await db.delete(dup)
    await db.flush()
    return moved


async def merge_account(
    db: AsyncSession, user_id: uuid.UUID, src: MoneyAccount, dst: MoneyAccount
) -> dict:
    """Fold `src` into `dst`: the same account seen under other digits (a statement that quoted
    a different number, or an account added twice). Its transactions move over, a row that's
    already on `dst` (the same amount and direction within MATCH_DAYS, from another source) is
    dropped rather than counted twice, everything that pointed at `src` points at `dst`, and
    `src`'s digits keep leading to `dst`. Returns {"moved", "matched"}."""
    from nexus_common.models import MoneyLoan

    existing = (await db.scalars(select(MoneyTxn).where(MoneyTxn.account_id == dst.id))).all()
    taken: set[uuid.UUID] = set()
    moved = matched = 0
    for t in (await db.scalars(select(MoneyTxn).where(MoneyTxn.account_id == src.id))).all():
        twin = next(
            (
                e
                for e in existing
                if e.id not in taken
                and e.amount_paise == t.amount_paise
                and e.direction == t.direction
                and e.source != t.source
                and abs((e.occurred_on - t.occurred_on).days) <= MATCH_DAYS
            ),
            None,
        )
        if twin is not None:
            taken.add(twin.id)
            await money_habits.move_items(db, t.id, twin.id)
            await db.delete(t)
            matched += 1
        else:
            t.account_id = dst.id
            moved += 1
    for model, column in (
        (MoneyImport, MoneyImport.account_id),
        (MoneySubscription, MoneySubscription.account_id),
        (MoneySubscription, MoneySubscription.move_to_id),
        (MoneyLoan, MoneyLoan.account_id),
        (MoneyAccount, MoneyAccount.bill_from_id),
        (MoneyAccount, MoneyAccount.limit_with_id),
    ):
        for row in (
            await db.scalars(select(model).where(model.user_id == user_id, column == src.id))
        ).all():
            setattr(row, column.key, dst.id)
    if src.balance_on and (dst.balance_on is None or src.balance_on > dst.balance_on):
        dst.balance_paise, dst.balance_on = src.balance_paise, src.balance_on
    dst.institution = dst.institution or src.institution
    dst.card_mask = dst.card_mask or src.card_mask
    dst.expires_on = dst.expires_on or src.expires_on
    if dst.kind == "card":
        dst.credit_limit_paise = dst.credit_limit_paise or src.credit_limit_paise
    known = {dst.mask, dst.card_mask}
    extra = [m for m in (src.mask, src.card_mask, *(src.aliases or [])) if m and m not in known]
    dst.aliases = list(dict.fromkeys([*(dst.aliases or []), *extra]))
    await db.flush()
    await db.delete(src)
    await db.flush()
    log.info(
        "money.accounts_merged", extra={"user_id": str(user_id), "moved": moved, "matched": matched}
    )
    return {"moved": moved, "matched": matched}


async def account_for(
    db: AsyncSession,
    user_id: uuid.UUID,
    institution: str | None,
    mask: str | None,
    kind: str,
    *,
    options: tuple[str, ...] = (),
) -> MoneyAccount | None:
    """The account a transaction belongs to, made the first time it is seen. Without the last
    four digits there's nothing reliable to tie it to. `options` are other digits the same
    statement quotes: one that's already an account is used before a new one is made."""
    if not mask:
        return None
    for other in options:
        if other != mask and (found := await _known_account(db, user_id, institution, other, kind)):
            known = await _known_account(db, user_id, institution, mask, kind)
            return known or found
    return await _known_account(db, user_id, institution, mask, kind) or await _new_account(
        db, user_id, institution, mask, kind
    )


async def _known_account(
    db: AsyncSession, user_id: uuid.UUID, institution: str | None, mask: str, kind: str
) -> MoneyAccount | None:
    if kind == "bank":
        # A debit card's alerts quote the card's digits: they belong to its bank account.
        for row in (
            await db.scalars(
                select(MoneyAccount).where(
                    MoneyAccount.user_id == user_id,
                    MoneyAccount.kind == "bank",
                    MoneyAccount.card_mask == mask,
                )
            )
        ).all():
            if not institution or not row.institution or row.institution == institution:
                return row
    # Digits of an account that was merged into another.
    for row in (
        await db.scalars(
            select(MoneyAccount).where(MoneyAccount.user_id == user_id, MoneyAccount.kind == kind)
        )
    ).all():
        if mask in (row.aliases or []) and (
            not institution or not row.institution or row.institution == institution
        ):
            return row
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
    if len(mask) == 3:
        # Some banks quote only three digits ("Account XX320"): the one account of that kind
        # at that bank ending with them.
        ends = [
            row
            for row in (
                await db.scalars(
                    select(MoneyAccount).where(
                        MoneyAccount.user_id == user_id, MoneyAccount.kind == kind
                    )
                )
            ).all()
            if row.mask
            and len(row.mask) > 3
            and row.mask.endswith(mask)
            and (not institution or not row.institution or row.institution == institution)
        ]
        if len(ends) == 1:
            return ends[0]
    return None


async def _new_account(
    db: AsyncSession, user_id: uuid.UUID, institution: str | None, mask: str, kind: str
) -> MoneyAccount:
    account = MoneyAccount(
        user_id=user_id, institution=institution, mask=mask, kind=kind, name="", created_at=utcnow()
    )
    db.add(account)
    await db.flush()
    return account


# "at 18:09:02" in a card alert: two charges at different times are two charges.
_ALERT_TIME = re.compile(r"\bat\s+(\d{1,2}:\d{2}(?::\d{2})?)\b")


def _alert_time(text: str) -> str | None:
    m = _ALERT_TIME.search(text or "")
    return m.group(1) if m else None


def same_alert(
    cfg: Settings,
    nearby: list[MoneyTxn],
    p: money.Parsed,
    account_id: uuid.UUID | None,
    source_ref: str | None,
) -> MoneyTxn | None:
    """An alert already stored from another email for the same payment: same account, amount
    and direction within a day, the same payee or one of them an autopay notice, and no
    reference number or time telling them apart."""
    for t in nearby:
        if (
            t.source != "alert"
            or t.direction != p.direction
            or t.amount_paise != p.amount_paise
            or t.account_id != account_id
            or abs((t.occurred_on - p.occurred_on).days) > 1
            or (source_ref and t.source_ref == source_ref)
        ):
            continue
        if t.ref and p.ref and t.ref != p.ref[:40]:
            continue
        payee = money.merchant_key(t.merchant) == money.merchant_key(p.merchant)
        if not payee and "autopay" not in (t.channel, p.channel):
            continue
        mine, theirs = _alert_time(p.narration), _alert_time(money.unseal(cfg, t.narration_enc))
        if mine and theirs and mine != theirs:
            continue
        return t
    return None


async def _clear_period(
    db: AsyncSession, user_id: uuid.UUID, rows: list[money.Parsed], account_id: uuid.UUID | None
) -> int:
    """Drop the statement rows already stored for these accounts and dates, and let the alerts
    they confirmed be matched again. Returns how many rows went."""
    targets: set[uuid.UUID] = set()
    if account_id:
        targets.add(account_id)
    else:
        seen: set[tuple] = set()
        for p in rows:
            what = (p.institution, p.account_mask, p.account_kind, tuple(p.mask_options or ()))
            if what in seen:
                continue
            seen.add(what)
            account = await account_for(
                db, user_id, p.institution, p.account_mask, p.account_kind, options=p.mask_options
            )
            if account is not None:
                targets.add(account.id)
    if not targets:
        return 0
    lo, hi = min(r.occurred_on for r in rows), max(r.occurred_on for r in rows)
    old = list(
        (
            await db.scalars(
                select(MoneyTxn).where(
                    MoneyTxn.user_id == user_id,
                    MoneyTxn.source == "statement",
                    MoneyTxn.account_id.in_(targets),
                    MoneyTxn.occurred_on >= lo,
                    MoneyTxn.occurred_on <= hi,
                )
            )
        ).all()
    )
    ids = {t.id for t in old}
    rules = (await settings_for(db, user_id)).rules or {}
    for t in old:
        # The other half of a transfer or refund goes back to standing alone; linking runs
        # again once the new rows are in.
        if t.pair_id and t.pair_id not in ids and (other := await db.get(MoneyTxn, t.pair_id)):
            money_links.unpair(other, None, rules=rules)
    if ids:
        await db.execute(delete(MoneyTxn).where(MoneyTxn.id.in_(ids)))
    for t in (
        await db.scalars(
            select(MoneyTxn).where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.account_id.in_(targets),
                MoneyTxn.source != "statement",
                MoneyTxn.alt_key.is_not(None),
                MoneyTxn.occurred_on >= lo - timedelta(days=MATCH_DAYS),
                MoneyTxn.occurred_on <= hi + timedelta(days=MATCH_DAYS),
            )
        )
    ).all():
        t.alt_key, t.confirmed = None, False
    await db.flush()
    log.info(
        "money.statement_replaced",
        extra={"user_id": str(user_id), "accounts": len(targets), "removed": len(ids)},
    )
    return len(ids)


async def record_mail_statement(
    db: AsyncSession,
    user_id: uuid.UUID,
    mailbox_id: uuid.UUID | None,
    message_id: str,
    received_at: datetime,
    found: money.StatementMail,
) -> bool:
    """Note a statement PDF found in the mail, once. True when it's new."""
    if await db.scalar(
        select(MoneyMailStatement.id).where(
            MoneyMailStatement.user_id == user_id, MoneyMailStatement.message_id == message_id
        )
    ):
        return False
    db.add(
        MoneyMailStatement(
            user_id=user_id,
            mailbox_id=mailbox_id,
            message_id=message_id[:64],
            received_at=received_at,
            institution=found.institution,
            mask=(found.mask or "")[-8:] or None,
            period_from=found.period_from,
            period_to=found.period_to,
            created_at=utcnow(),
        )
    )
    await db.flush()
    return True


async def ingest(
    db: AsyncSession,
    cfg: Settings,
    user_id: uuid.UUID,
    rows: list[money.Parsed],
    *,
    source: str,
    source_ref: str | None = None,
    account_id: uuid.UUID | None = None,
    replace: bool = False,
) -> dict[str, int]:
    """Store parsed transactions. A row seen before (same source) is skipped; an alert and a
    statement row for the same transaction become one row, marked confirmed. With replace (a
    statement only), the rows an earlier import of the same account and dates left are dropped
    first, so a file read better this time replaces what was read before."""
    counts = {"added": 0, "matched": 0, "skipped": 0}
    if not rows:
        return counts
    if replace and source == "statement":
        counts["replaced"] = await _clear_period(db, user_id, rows, account_id)
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
    # Which other sources can hold the same transaction. A screenshot can repeat anything already
    # in (or another screenshot of the same list), so it only adds what's new.
    others = {
        "alert": ("statement", "screenshot", "receipt"),
        "statement": ("alert", "screenshot", "receipt"),
        "screenshot": ("alert", "statement", "screenshot", "manual"),
    }.get(source, ())
    before = list(nearby)
    used: set[uuid.UUID] = set()
    conversions: list[MoneyTxn] = []
    for p in rows:
        account = fixed or await account_for(
            db, user_id, p.institution, p.account_mask, p.account_kind, options=p.mask_options
        )
        if account is not None:
            _note_balance(account, p)
            if account.limit_with_id:
                # A card sharing its limit: the available amount is the pair's.
                shared = await db.get(MoneyAccount, account.limit_with_id)
                if shared is not None:
                    _note_balance(shared, p)
        key = money.dedupe_key(p, str(account.id) if account else None)
        if key in keys:
            counts["skipped"] += 1
            continue
        keys.add(key)
        if source == "alert" and (
            same := same_alert(cfg, nearby, p, account.id if account else None, source_ref)
        ):
            # A second email about the same payment (the card's "debited" and the autopay's
            # "bill paid", or the same alert in two mailboxes): one transaction.
            if p.channel == "autopay":
                same.channel = "autopay"
            if same.merchant == "Unknown" and p.merchant != "Unknown":
                same.merchant = p.merchant[:120]
            counts["matched"] += 1
            continue
        twin = None
        if others:
            twin = next(
                (
                    t
                    for t in (before if source == "screenshot" else nearby)
                    if t.source in others
                    and (source == "screenshot" or not t.confirmed)
                    and t.id not in used
                    and t.amount_paise == p.amount_paise
                    and t.direction == p.direction
                    and abs((t.occurred_on - p.occurred_on).days) <= MATCH_DAYS
                    and (account is None or t.account_id in (None, account.id))
                ),
                None,
            )
        if twin is not None and source == "screenshot":
            # Already in Money; a screenshot only confirms an alert, it never overrides it.
            used.add(twin.id)
            if twin.source == "alert":
                twin.confirmed = True
            if account is not None and twin.account_id is None:
                twin.account_id = account.id
            counts["matched"] += 1
            continue
        if twin is not None:
            used.add(twin.id)
            if twin.source == "receipt":
                # The bank's record of an order Money read first: the order keeps the shop's
                # name, the bank says how it was paid.
                twin.channel = p.channel[:8]
                twin.narration_enc = money.seal(cfg, p.narration)
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
        if credit and txn.account_id and money.is_emi_conversion(f"{p.merchant} {p.narration}"):
            conversions.append(txn)
    await db.flush()
    for credit_row in conversions:
        await _pair_emi_conversion(db, user_id, credit_row)
    if counts["added"]:
        await money_links.link(db, cfg, user_id, min(r.occurred_on for r in rows))
        if any(p.direction == "credit" for p in rows):
            # A salary credit read at last replaces the expected row for its month.
            await money_salary.fill(db, user_id)
    log.info("money.ingested", extra={"user_id": str(user_id), "source": source, **counts})
    return counts


# A payment for an order can be dated a day before the email (an authorisation) or a few days
# after (a statement's posting date).
ORDER_BEFORE, ORDER_AFTER = 1, 3


async def record_order(
    db: AsyncSession, cfg: Settings, user_id: uuid.UUID, order: money_orders.Order, source_ref: str
) -> str:
    """One order email: names the bank or card payment it matches ("matched"), or becomes a
    transaction of its own ("added"), which a later alert joins. "skipped" when already read."""
    p = money.Parsed(
        order.occurred_on, order.amount_paise, "debit", order.shop, "",
        channel=money_orders.CHANNEL.get(order.paid_by, "other"), ref=order.order_no,
    )  # fmt: skip
    key = money.dedupe_key(p, None)
    lo, hi = (
        order.occurred_on - timedelta(days=ORDER_BEFORE),
        order.occurred_on + timedelta(days=ORDER_AFTER),
    )
    nearby = (
        await db.scalars(
            select(MoneyTxn)
            .where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.direction == "debit",
                MoneyTxn.occurred_on >= lo,
                MoneyTxn.occurred_on <= hi,
            )
            .order_by(MoneyTxn.occurred_on)
        )
    ).all()
    if seen := next((t for t in nearby if key in (t.dedupe_key, t.alt_key)), None):
        # Read before; the items are kept if they weren't then (an email read again for history).
        if order.place and not seen.place:
            seen.place = order.place
        if order.items and not await money_habits.has_items(db, seen.id):
            await money_habits.save_items(db, cfg, user_id, seen.id, order.items, "email")
        return "skipped"
    prefs = await settings_for(db, user_id)
    rules = prefs.rules or {}
    category = money.categorise(order.shop, rules=rules)
    if category == "Other" and order.category:
        category = order.category
    # A payment of the same amount, not already named after another order: a shop's name
    # stays when it's already right.
    twins = [
        t
        for t in nearby
        if t.source != "receipt"
        and abs(t.amount_paise - order.amount_paise) <= 100
        and t.pair_kind is None
    ]
    twins.sort(
        key=lambda t: (t.merchant == order.shop, abs((t.occurred_on - order.occurred_on).days))
    )
    if twins:
        twin = twins[0]
        twin.place = twin.place or order.place
        if order.items:
            await money_habits.save_items(db, cfg, user_id, twin.id, order.items, "email")
        if twin.merchant == order.shop:
            return "skipped"
        twin.merchant = order.shop[:120]
        if twin.category_by == "auto":
            twin.category = category
        twin.alt_key = twin.alt_key or key
        return "matched"
    account = None
    if order.card_mask:
        account = await db.scalar(
            select(MoneyAccount).where(
                MoneyAccount.user_id == user_id,
                (MoneyAccount.mask == order.card_mask)
                | (MoneyAccount.card_mask == order.card_mask),
            )
        )
    row = MoneyTxn(
        user_id=user_id,
        account_id=account.id if account else None,
        occurred_on=order.occurred_on,
        amount_paise=order.amount_paise,
        direction="debit",
        merchant=order.shop[:120],
        category=category,
        category_by="rule" if rules.get(money.merchant_key(order.shop)) else "auto",
        narration_enc=money.seal(cfg, ""),
        channel=p.channel,
        source="receipt",
        source_ref=source_ref[:64],
        ref=(order.order_no or "")[:40] or None,
        place=order.place,
        dedupe_key=key,
        created_at=utcnow(),
    )
    db.add(row)
    await db.flush()
    if order.items:
        await money_habits.save_items(db, cfg, user_id, row.id, order.items, "email")
    return "added"


def _unpaid(user_id: uuid.UUID, today: date):
    """Orders no payment explains yet: not cash, no account, a day or more old (an alert
    usually arrives within the hour)."""
    return (
        MoneyTxn.user_id == user_id,
        MoneyTxn.source == "receipt",
        MoneyTxn.account_id.is_(None),
        MoneyTxn.channel != "cash",
        MoneyTxn.excluded.is_(False),
        MoneyTxn.occurred_on < today,
    )


async def orders_to_confirm(db: AsyncSession, user_id: uuid.UUID, today: date) -> list[dict]:
    rows = (
        await db.scalars(
            select(MoneyTxn)
            .where(*_unpaid(user_id, today))
            .order_by(MoneyTxn.occurred_on.desc())
            .limit(50)
        )
    ).all()
    return [
        {
            "id": str(t.id),
            "occurred_on": t.occurred_on,
            "merchant": t.merchant,
            "amount_paise": t.amount_paise,
            "category": t.category,
            "paid_by": t.channel,
        }
        for t in rows
    ]


async def confirm_order(
    db: AsyncSession, user_id: uuid.UUID, txn: MoneyTxn, how: str, account: MoneyAccount | None
) -> None:
    """The person's answer to "how did you pay?": cash, one of their accounts, or leave it out
    (someone else paid, or it's already counted)."""
    if how == "cash":
        txn.channel, txn.account_id, txn.excluded = "cash", None, False
    elif how == "account" and account is not None:
        txn.account_id, txn.excluded = account.id, False
        if txn.channel == "cash":
            txn.channel = "other"
    elif how == "leave_out":
        txn.excluded = True
    await db.flush()


async def share_limit(
    db: AsyncSession, user_id: uuid.UUID, card: MoneyAccount, other_id: uuid.UUID | None
) -> str | None:
    """Make `card` use another card's credit limit (a pair issued together), or stop. The
    limit lives on the other card; a limit typed on this one moves there when that has none.
    Returns a problem to show, or None."""
    if other_id is None:
        card.limit_with_id = None
        return None
    other = await db.get(MoneyAccount, other_id)
    if other is None or other.user_id != user_id or other.kind != "card" or other.id == card.id:
        return "Pick another of your credit cards."
    if other.limit_with_id is not None:
        # Point at the card that holds the limit, never at another sharer.
        other = await db.get(MoneyAccount, other.limit_with_id) or other
    if other.id == card.id:
        return "Those cards already share this card's limit."
    if card.credit_limit_paise and not other.credit_limit_paise:
        other.credit_limit_paise = card.credit_limit_paise
    # Cards that shared this one's limit now share the other's.
    for row in (
        await db.scalars(
            select(MoneyAccount).where(
                MoneyAccount.user_id == user_id, MoneyAccount.limit_with_id == card.id
            )
        )
    ).all():
        row.limit_with_id = other.id
    card.limit_with_id = other.id
    card.credit_limit_paise = None
    card.available_paise = card.available_on = None
    await db.flush()
    return None


def _note_balance(account: MoneyAccount, p: money.Parsed) -> None:
    """Keep the newest balance (banks) and available limit (cards) a row shows."""
    if p.balance_paise is not None and account.kind == "bank":
        if account.balance_on is None or p.occurred_on >= account.balance_on:
            account.balance_paise, account.balance_on = p.balance_paise, p.occurred_on
    limit = p.extra.get("available_limit_paise")
    if limit is not None and account.kind == "card":
        if account.available_on is None or p.occurred_on >= account.available_on:
            account.available_paise, account.available_on = limit, p.occurred_on


async def _pair_emi_conversion(db: AsyncSession, user_id: uuid.UUID, credit: MoneyTxn) -> None:
    """A purchase turned into EMIs comes back as a credit and is billed as instalments, which
    count as spending. The purchase itself is left out so it isn't counted twice."""
    purchase = await db.scalar(
        select(MoneyTxn)
        .where(
            MoneyTxn.user_id == user_id,
            MoneyTxn.account_id == credit.account_id,
            MoneyTxn.direction == "debit",
            MoneyTxn.excluded.is_(False),
            MoneyTxn.amount_paise.between(credit.amount_paise - 100, credit.amount_paise + 100),
            MoneyTxn.occurred_on >= credit.occurred_on - timedelta(days=60),
            MoneyTxn.occurred_on <= credit.occurred_on,
        )
        .order_by(MoneyTxn.occurred_on.desc())
        .limit(1)
    )
    if purchase is None:
        return
    note = "Turned into EMIs: the instalments count as spending instead."
    purchase.excluded = True
    purchase.note = purchase.note or note
    credit.excluded = True
    credit.note = credit.note or note


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
                # Not one the person set, nor half of a transfer or refund.
                MoneyTxn.category_by.not_in(("user", "pair")),
            )
        )
    ).all()
    for row in rows:
        row.category = category
        row.category_by = "rule"
    return len(rows)


async def load(
    db: AsyncSession, user_id: uuid.UUID, start: date, end: date, *, pay_months: bool = False
) -> list[money.Txn]:
    """Transactions from `start` up to `end`. With `pay_months`, a salary paid in the last days
    of a month is dated the first of the next month (the month it pays for), for month totals;
    its real date stays in `paid_on`."""
    salaries = []
    if pay_months:
        salaries = list(
            (await db.scalars(select(MoneySalary).where(MoneySalary.user_id == user_id))).all()
        )
    since = start - timedelta(days=money_salary.EARLY_DAYS + 3) if salaries else start
    rows = (
        await db.execute(
            select(
                MoneyTxn.occurred_on,
                MoneyTxn.amount_paise,
                MoneyTxn.direction,
                MoneyTxn.merchant,
                MoneyTxn.category,
                MoneyTxn.excluded,
                MoneyTxn.account_id,
                MoneyTxn.channel,
            ).where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.occurred_on >= since,
                MoneyTxn.occurred_on < end,
            )
        )
    ).all()
    out = []
    for r in rows:
        t = money.Txn(*r[:6], account_id=str(r[6]) if r[6] else None, channel=r[7] or "other")
        if salaries and money.income(t):
            moved = money_salary.pays_next_month(
                salaries, t.occurred_on, t.amount_paise, t.account_id
            )
            if moved:
                t = dataclasses.replace(t, occurred_on=moved, paid_on=t.occurred_on)
        if start <= t.occurred_on < end:
            out.append(t)
    return out


def shift_month(month: str, by: int) -> str:
    y, m = (int(x) for x in month.split("-"))
    n = y * 12 + (m - 1) + by
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


async def overview(db: AsyncSession, user_id: uuid.UUID, month: str, today: date) -> dict:
    """Everything the Money overview shows for one month."""
    start, end = money.month_bounds(month)
    first = money.month_bounds(shift_month(month, -(TREND_MONTHS - 1)))[0]
    txns = await load(
        db,
        user_id,
        min(first, today - SUBSCRIPTION_LOOKBACK),
        max(end, today + timedelta(days=1)),
        pay_months=True,
    )
    this = [t for t in txns if start <= t.occurred_on < end]
    summary = money.summarise(this)
    # A salary from the end of last month counted here, to say so beside "Came in".
    salary_from = max((t.paid_on for t in this if t.paid_on), default=None)
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
    subs = await subscriptions_view(db, user_id, today)
    upcoming = await upcoming_payments(db, user_id, today, subs=subs)
    holdings = (await db.scalars(select(MoneyHolding).where(MoneyHolding.user_id == user_id))).all()
    stocks = await stocks_view(db, user_id)
    prefs = await settings_for(db, user_id)
    return {
        "month": month,
        **summary,
        "salary_from": salary_from,
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
        "subscriptions_monthly_paise": subs["subscriptions_monthly_paise"]
        + subs["bills_monthly_paise"],
        "subscriptions_to_review": subs["to_review"],
        "upcoming": upcoming,
        "funds_value_paise": sum(h.value_paise or 0 for h in holdings),
        "funds_cost_paise": sum(h.cost_paise or 0 for h in holdings),
        "stocks_value_paise": stocks["value_paise"],
        "has_data": bool(txns) or bool(holdings) or bool(stocks["items"]),
        "gmail_sync": prefs.gmail_sync,
        "gmail_synced_at": prefs.gmail_synced_at,
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


def stock_out(s: MoneyStock) -> dict:
    value = money_stocks.value_paise(s.quantity, s.price_paise)
    cost = money_stocks.value_paise(s.quantity, s.avg_price_paise)
    return {
        "id": str(s.id),
        "symbol": s.symbol,
        "exchange": s.exchange,
        "name": s.name,
        "isin": s.isin,
        "quantity": float(s.quantity),
        "avg_price_paise": s.avg_price_paise,
        "bought_on": s.bought_on,
        "price_paise": s.price_paise,
        "price_on": s.price_on,
        "value_paise": value,
        "cost_paise": cost,
        "gain_paise": value - cost if value is not None and cost is not None else None,
        "note": s.note,
    }


async def stocks_view(db: AsyncSession, user_id: uuid.UUID) -> dict:
    rows = (
        await db.scalars(
            select(MoneyStock).where(MoneyStock.user_id == user_id).order_by(MoneyStock.symbol)
        )
    ).all()
    items = sorted((stock_out(s) for s in rows), key=lambda x: -(x["value_paise"] or 0))
    priced = [x for x in items if x["value_paise"] is not None]
    costed = [x for x in priced if x["cost_paise"] is not None]
    cost = sum(x["cost_paise"] for x in costed)
    return {
        "items": items,
        "value_paise": sum(x["value_paise"] for x in priced),
        "cost_paise": cost,
        "gain_paise": sum(x["value_paise"] for x in costed) - cost if costed else None,
        "price_on": max((x["price_on"] for x in items if x["price_on"]), default=None),
    }


def reprice(s: MoneyStock, quotes: dict[str, money_stocks.Quote]) -> bool:
    """Price a share at the newest close NSE published for it, found by symbol or, after a
    rename, by ISIN. Returns whether it changed."""
    q = quotes.get(s.symbol)
    if q is None and s.isin:
        q = next((x for x in quotes.values() if x.isin == s.isin), None)
    if q is None or (s.price_on and q.day < s.price_on):
        return False
    if (s.price_paise, s.price_on, s.symbol) == (q.close_paise, q.day, q.symbol):
        return False
    # A rename keeps the ISIN; follow the new symbol and name.
    s.symbol, s.name = q.symbol, q.name or s.name
    s.price_paise, s.price_on = q.close_paise, q.day
    s.updated_at = utcnow()
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
    for model in (
        MoneyGoal,
        MoneyLoan,
        MoneyAsset,
        MoneySubscription,
        MoneyItem,
        MoneyTxn,
        MoneyImport,
        MoneyBudget,
        MoneyHolding,
        MoneyStock,
        MoneyAccount,
        MoneySettings,
    ):
        await db.execute(delete(model).where(model.user_id == user_id))
    log.info("money.deleted_all", extra={"user_id": str(user_id)})


async def changed(redis: Redis | None, user_id: uuid.UUID, **counts: int) -> None:
    if redis is not None:
        await publish(redis, user_id, Event(type="money.changed", data=counts))


# --- Accounts and card bills -------------------------------------------------------------------


async def record_card_bill(db: AsyncSession, user_id: uuid.UUID, bill: money.CardBill) -> bool:
    """Store a card statement's bill on its card. An older statement changes nothing."""
    account = await account_for(db, user_id, bill.institution, bill.account_mask, "card")
    if account is None:
        return False
    if account.statement_on and bill.statement_on < account.statement_on:
        return False
    if account.due_on == bill.due_on and account.due_paise == bill.total_due_paise:
        return False
    account.due_paise = bill.total_due_paise
    account.min_due_paise = bill.min_due_paise
    account.due_on = bill.due_on
    account.statement_on = bill.statement_on
    account.paid_on = None
    return True


async def record_emi_booking(
    db: AsyncSession, user_id: uuid.UUID, booking: money.EmiBooking
) -> MoneyLoan | None:
    """Track a purchase turned into EMIs, from the card's confirmation email. The first
    instalment is taken to be on the next statement, about a month on; it can be edited."""
    account = await account_for(db, user_id, booking.institution, booking.account_mask, "card")
    # The purchase itself isn't spending any more: the instalments are, as they're billed.
    purchase = await db.scalar(
        select(MoneyTxn)
        .where(
            MoneyTxn.user_id == user_id,
            MoneyTxn.account_id == (account.id if account else None),
            MoneyTxn.direction == "debit",
            MoneyTxn.excluded.is_(False),
            MoneyTxn.amount_paise == booking.principal_paise,
            MoneyTxn.occurred_on.between(
                booking.booked_on - timedelta(days=5), booking.booked_on + timedelta(days=2)
            ),
        )
        .limit(1)
    )
    if purchase is not None:
        purchase.excluded = True
        purchase.note = purchase.note or "Turned into EMIs: the instalments count as spending."
    if booking.months is None:
        # The term is only in the attached schedule: Money lists the EMIs once the card bills
        # them (Accounts › EMIs seen), and the person adds the term there.
        await db.flush()
        return None
    existing = await db.scalar(
        select(MoneyLoan).where(
            MoneyLoan.user_id == user_id,
            MoneyLoan.account_id == (account.id if account else None),
            MoneyLoan.principal_paise == booking.principal_paise,
        )
    )
    if existing is not None:
        return None
    name = f"EMI {booking.merchant}".strip() if booking.merchant else "Card EMI"
    loan = MoneyLoan(
        user_id=user_id,
        kind="card_emi",
        name=name[:120],
        account_id=account.id if account else None,
        lender=booking.institution,
        principal_paise=booking.principal_paise,
        rate_bp=booking.rate_bp,
        months=booking.months,
        emi_paise=booking.emi_paise
        or plan.emi_for(booking.principal_paise, booking.rate_bp, booking.months),
        first_due_on=money.add_months(booking.booked_on, 1),
        status="active",
        note="Found in the card's EMI email. Check the first due date and rate.",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(loan)
    await db.flush()
    log.info("money.emi_booked", extra={"user_id": str(user_id), "loan_id": str(loan.id)})
    return loan


async def _card_flows(
    db: AsyncSession, user_id: uuid.UUID, since: date
) -> dict[uuid.UUID, dict[str, int]]:
    """Per account: money in and spending out since a day (for bills and what's owed)."""
    rows = (
        await db.execute(
            select(
                MoneyTxn.account_id,
                MoneyTxn.direction,
                MoneyTxn.amount_paise,
                MoneyTxn.occurred_on,
                MoneyTxn.excluded,
            ).where(MoneyTxn.user_id == user_id, MoneyTxn.occurred_on >= since)  # fmt: skip
        )
    ).all()
    out: dict[uuid.UUID, list] = {}
    for account_id, direction, amount, on, excluded in rows:
        if account_id is not None:
            out.setdefault(account_id, []).append((direction, amount, on, excluded))
    return out  # type: ignore[return-value]


def _sum(flows: list, direction: str, since: date | None, *, counted_only: bool = False) -> int:
    return sum(
        amount
        for d, amount, on, excluded in flows
        if d == direction and (since is None or on >= since) and not (counted_only and excluded)
    )


async def accounts_view(db: AsyncSession, user_id: uuid.UUID, today: date) -> dict:
    """Every bank account and card: balance, this month's spending and, for cards, the bill,
    what's owed now and the limit left. Totals add up the known balances and what's owed."""
    accounts = (
        await db.scalars(
            select(MoneyAccount)
            .where(MoneyAccount.user_id == user_id)
            .order_by(MoneyAccount.kind, MoneyAccount.institution, MoneyAccount.mask)
        )
    ).all()
    month_start = date(today.year, today.month, 1)
    oldest = min([a.statement_on for a in accounts if a.statement_on] + [month_start])
    flows = await _card_flows(db, user_id, min(oldest, month_start))
    counts = {
        account_id: (n, last)
        for account_id, n, last in (
            await db.execute(
                select(MoneyTxn.account_id, func.count(), func.max(MoneyTxn.occurred_on))
                .where(MoneyTxn.user_id == user_id)
                .group_by(MoneyTxn.account_id)
            )
        ).all()
    }
    loans = (
        await db.scalars(
            select(MoneyLoan).where(MoneyLoan.user_id == user_id, MoneyLoan.status == "active")
        )
    ).all()
    checks = await balance_checks(db, user_id, today)
    items = []
    cash = owed_total = 0
    for a in accounts:
        f = flows.get(a.id, [])
        count, last_on = counts.get(a.id, (0, None))
        item = {
            "id": str(a.id),
            "label": account_label(a),
            "name": a.name,
            "institution": a.institution,
            "mask": a.mask,
            "kind": a.kind,
            "hidden": a.hidden,
            "count": count,
            "last_on": last_on,
            "balance_paise": a.balance_paise,
            "balance_on": a.balance_on,
            "spent_month_paise": _sum(f, "debit", month_start, counted_only=True),
            "in_month_paise": _sum(f, "credit", month_start, counted_only=True),
            "balance_check": checks.get(a.id),
            "card_mask": a.card_mask,
        }
        if a.kind == "bank":
            item["card_expires_on"] = a.expires_on if a.card_mask else None
        if a.kind == "card":
            payments = _sum(f, "credit", a.statement_on) if a.statement_on else 0
            bill = money.bill_status(
                a.due_paise, a.due_on, a.statement_on, a.paid_on, payments, today
            )
            owed = None
            shares = a.limit_with_id is not None
            if a.credit_limit_paise and a.available_paise is not None and not shares:
                owed = max(a.credit_limit_paise - a.available_paise, 0)
            elif a.statement_on:
                owed = bill["left_paise"] + _sum(f, "debit", a.statement_on + timedelta(days=1))
            emis = [
                {"id": str(loan.id), "name": loan.name, "emi_paise": loan.emi_paise}
                for loan in loans
                if loan.account_id == a.id
            ]
            item |= {
                "credit_limit_paise": a.credit_limit_paise,
                "available_paise": a.available_paise,
                "available_on": a.available_on,
                "due_paise": a.due_paise,
                "min_due_paise": a.min_due_paise,
                "due_on": a.due_on,
                "statement_on": a.statement_on,
                "paid_on": a.paid_on,
                "paid_since_statement_paise": payments,
                "expires_on": a.expires_on,
                "bill_from_id": str(a.bill_from_id) if a.bill_from_id else None,
                "bill_from_label": next(
                    (account_label(x) for x in accounts if x.id == a.bill_from_id), None
                ),
                "bill": bill,
                "owed_paise": owed,
                "owed_from_limit": bool(
                    a.credit_limit_paise and a.available_paise is not None and not shares
                ),
                "emis": emis,
                # Cards sharing one limit: this one uses `limit_with`'s; `shared_with` use
                # this one's.
                "limit_with_id": str(a.limit_with_id) if shares else None,
                "limit_with_label": next(
                    (account_label(x) for x in accounts if x.id == a.limit_with_id), None
                ),
                "shared_with": [
                    account_label(x) for x in accounts if x.limit_with_id == a.id and x.id != a.id
                ],
            }
            # The pair's owed amount is counted once, on the card holding the limit.
            if owed and not a.hidden and not shares:
                owed_total += owed
        elif a.balance_paise is not None and not a.hidden:
            cash += a.balance_paise
        items.append(item)
    # A shared limit without an available amount from alerts: the pair owes what both
    # statements say, shown on the card holding the limit.
    for item in items:
        if item["kind"] != "card" or not item["shared_with"] or item["owed_from_limit"]:
            continue
        extra = sum(
            x["owed_paise"] or 0
            for x in items
            if x["kind"] == "card" and x["limit_with_id"] == item["id"]
        )
        if extra:
            item["owed_paise"] = (item["owed_paise"] or 0) + extra
            if not item["hidden"]:
                owed_total += extra
    return {
        "items": items,
        "cash_paise": cash,
        "owed_paise": owed_total,
        "unknown_balances": sum(
            1 for a in accounts if a.kind == "bank" and a.balance_paise is None and not a.hidden
        ),
    }


BALANCE_CHECK_DAYS = 120


async def balance_checks(db: AsyncSession, user_id: uuid.UUID, today: date) -> dict:
    """For each bank account, whether its transactions add up to the balances the bank printed
    over the last four months (money.reconcile). Gaps mean a missing or doubled row."""
    rows = (
        await db.execute(
            select(
                MoneyTxn.account_id,
                MoneyTxn.occurred_on,
                MoneyTxn.amount_paise,
                MoneyTxn.direction,
                MoneyTxn.balance_paise,
            )
            .join(MoneyAccount, MoneyAccount.id == MoneyTxn.account_id)
            .where(
                MoneyTxn.user_id == user_id,
                MoneyAccount.kind == "bank",
                MoneyTxn.occurred_on >= today - timedelta(days=BALANCE_CHECK_DAYS),
            )
            .order_by(MoneyTxn.occurred_on, MoneyTxn.created_at)
        )
    ).all()
    by_account: dict[uuid.UUID, list] = {}
    for account_id, on, amount, direction, balance in rows:
        signed = amount if direction == "credit" else -amount
        by_account.setdefault(account_id, []).append((on, signed, balance))
    out = {}
    for account_id, flows in by_account.items():
        checked, gaps = money.reconcile(flows)
        if checked:
            out[account_id] = {
                "days_checked": checked,
                "gaps": [{"on": g.on, "gap_paise": g.gap_paise} for g in gaps[-3:]],
                "gap_count": len(gaps),
            }
    return out


# --- Subscriptions -----------------------------------------------------------------------------


async def subscriptions_view(db: AsyncSession, user_id: uuid.UUID, today: date) -> dict:
    """Detected repeating payments joined with what the person decided about each, plus the
    ones they added by hand. Flags what needs a look: new ones, a price change, a charge after
    cancelling, and an active one that hasn't been charged lately."""
    txns = await load(db, user_id, today - SUBSCRIPTION_LOOKBACK, today + timedelta(days=1))
    detected = money.find_subscriptions(txns, today)
    saved = {
        row.key: row
        for row in (
            await db.scalars(select(MoneySubscription).where(MoneySubscription.user_id == user_id))
        ).all()
    }
    accounts = {
        str(a.id): a
        for a in (
            await db.scalars(select(MoneyAccount).where(MoneyAccount.user_id == user_id))
        ).all()
    }
    items = []
    for sub in detected:
        items.append(_subscription_item(sub, saved.pop(money.merchant_key(sub.merchant), None),
                                        txns, today, accounts))  # fmt: skip
    for row in saved.values():
        items.append(_subscription_item(None, row, txns, today, accounts))
    if any(i.pop("_moved", False) for i in items):
        await db.flush()
    live = [i for i in items if i["status"] in ("new", "active")]

    def monthly(group: str) -> int:
        return sum(i["monthly_paise"] for i in live if i["group"] == group)

    items.sort(key=lambda i: (
        {"new": 0, "active": 1, "cancelled": 2, "ignored": 3}[i["status"]],
        i["next_on"] or date.max,
    ))  # fmt: skip
    return {
        "items": items,
        "subscriptions_monthly_paise": monthly("subscription"),
        "bills_monthly_paise": monthly("bill"),
        "yearly_paise": sum(i["monthly_paise"] for i in live) * 12,
        "saved_monthly_paise": sum(i["monthly_paise"] for i in items if i["status"] == "cancelled"),
        "to_review": sum(1 for i in items if i["status"] == "new"),
    }


def _subscription_item(
    sub: money.Subscription | None,
    row: MoneySubscription | None,
    txns: list[money.Txn],
    today: date,
    accounts: dict[str, MoneyAccount] | None = None,
) -> dict:
    merchant = row.merchant if row else sub.merchant  # type: ignore[union-attr]
    category = (sub.category if sub else None) or (row.category if row else "Subscriptions")
    if row and row.manual:
        cadence, amount = row.cadence, row.amount_paise or 0
        next_on = money.roll_forward(row.next_on, cadence, today) if row.next_on else None
    elif sub:
        cadence, amount, next_on = sub.cadence, sub.typical_paise, sub.next_on
    else:
        cadence, amount, next_on = (row.cadence, row.amount_paise or 0, None)  # type: ignore[union-attr]
    status = row.status if row else "new"
    charges = sorted(
        (t for t in txns if t.merchant == merchant and t.direction == "debit" and not t.excluded),
        key=lambda t: t.occurred_on,
    )
    accounts = accounts or {}
    moved = _follow_move(row, charges, accounts)
    account_id = (
        str(row.account_id)
        if row and row.account_id
        else (charges[-1].account_id if charges else None)
    )
    account = accounts.get(account_id) if account_id else None
    moving = None
    if row and row.move_to_id:
        to = accounts.get(str(row.move_to_id))
        stuck = [
            t for t in charges
            if row.move_since and t.occurred_on >= row.move_since
            and t.account_id and t.account_id != str(row.move_to_id)
        ]  # fmt: skip
        moving = {
            "to_id": str(row.move_to_id),
            "to_label": account_label(to) if to else "the new card",
            "from_label": account_label(account) if account else None,
            "since": row.move_since,
            "stuck_on": stuck[-1].occurred_on if stuck else None,
        }
    after_cancel = None
    if row and status == "cancelled" and row.cancelled_on:
        late = [t for t in charges if t.occurred_on > row.cancelled_on]
        if late:
            after_cancel = {"on": late[-1].occurred_on, "amount_paise": late[-1].amount_paise}
    return {
        "key": money.merchant_key(merchant),
        "id": str(row.id) if row else None,
        "merchant": merchant,
        "category": category,
        "group": "bill" if category in money.BILL_CATEGORIES else "subscription",
        "status": status,
        "manual": bool(row and row.manual),
        "detected": sub is not None,
        "cadence": cadence,
        "amount_paise": amount,
        "monthly_paise": money.per_month(amount, cadence),
        "next_on": next_on,
        "last_on": charges[-1].occurred_on if charges else (sub.last_on if sub else None),
        "count": len(charges),
        "price_change_paise": sub.price_change_paise if sub else 0,
        "previous_paise": sub.previous_paise if sub else None,
        "charged_after_cancel": after_cancel,
        # Kept, but no charge for two cycles: maybe already cancelled elsewhere.
        "not_seen": bool(row and not row.manual and status == "active" and sub is None),
        "remind_days": row.remind_days if row else 3,
        "manage_url": (row.manage_url if row else None) or money.manage_url(merchant),
        "note": row.note if row else None,
        "cancelled_on": row.cancelled_on if row else None,
        "account_id": account_id,
        "account_label": account_label(account) if account else None,
        "account_kind": account.kind if account else None,
        "account_set": bool(row and row.account_id),
        "moving": moving,
        "moved_on": row.moved_on if row else None,
        "_moved": moved,
    }


def _follow_move(
    row: MoneySubscription | None, charges: list[money.Txn], accounts: dict[str, MoneyAccount]
) -> bool:
    """A move to another card is done once a charge on or after the day it was asked for lands
    on that card: the card becomes the subscription's, and the move is cleared."""
    if row is None or row.move_to_id is None or row.move_since is None:
        return False
    target = str(row.move_to_id)
    landed = [t for t in charges if t.occurred_on >= row.move_since and t.account_id == target]
    if not landed:
        return False
    row.account_id = row.move_to_id
    row.move_to_id = None
    row.moved_on = landed[0].occurred_on
    row.updated_at = utcnow()
    return True


async def save_subscription(
    db: AsyncSession, user_id: uuid.UUID, merchant: str, **fields
) -> MoneySubscription:
    """Record a decision about a repeating payment (or add one by hand). Only the fields
    given change; cancelling stamps today's date unless one is given."""
    key = money.merchant_key(merchant)
    row = await db.scalar(
        select(MoneySubscription).where(
            MoneySubscription.user_id == user_id, MoneySubscription.key == key
        )
    )
    if row is None:
        row = MoneySubscription(
            user_id=user_id,
            key=key,
            merchant=merchant[:120],
            status="active",
            manual=False,
            category="Subscriptions",
            cadence="monthly",
            remind_days=3,
            created_at=utcnow(),
        )
        db.add(row)
    previous = row.status
    for name, value in fields.items():
        setattr(row, name, value)
    if row.status == "cancelled" and previous != "cancelled" and "cancelled_on" not in fields:
        row.cancelled_on = date.today()
    if row.status != "cancelled":
        row.cancelled_on = None
    row.updated_at = utcnow()
    await db.flush()
    return row


# A card about to expire is mentioned every morning from two weeks before its last day until
# a week after, or until its new expiry is typed in.
EXPIRY_REMIND_DAYS = 14


def card_label(a: dict) -> tuple[str, date | None]:
    """A credit card's own label and expiry, or a bank account's debit card's."""
    if a["kind"] == "card":
        return a["label"], a.get("expires_on")
    if a.get("card_mask"):
        return f"Debit card ••{a['card_mask']} ({a['label']})", a.get("card_expires_on")
    return a["label"], None


def card_expiry(a: dict, today: date) -> dict | None:
    label, expires = card_label(a)
    if not expires or a.get("hidden"):
        return None
    last = money.add_months(expires.replace(day=1), 1) - timedelta(days=1)
    days = (last - today).days
    if days > EXPIRY_REMIND_DAYS or days < -7:
        return None
    when = (
        "has expired" if days < 0 else "expires today" if days == 0
        else "expires tomorrow" if days == 1 else f"expires in {days} days"
    )  # fmt: skip
    return {
        "tag": f"expiry:{a['id']}:{'debit' if a['kind'] == 'bank' else 'card'}:{expires}:{today}",
        "title": f"{label} {when}",
        "body": "If the new card hasn't come, ask the bank for it now. When it arrives, activate "
        "it and type its expiry in Money › Accounts; these reminders stop then.",
        "url": "/money/accounts",
    }


def reminders(subs: dict, accounts: dict, today: date) -> list[dict]:
    """What to tell the person today: renewals coming up, a charge after cancelling, a price
    rise, a card bill due soon or missed. Each has a tag, so it's sent once."""
    out = []
    for s in subs["items"]:
        amount = money.rupees(s["amount_paise"])
        if s["status"] in ("new", "active") and s["remind_days"] and s["next_on"]:
            days = (s["next_on"] - today).days
            if 0 <= days <= s["remind_days"]:
                when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
                out.append({
                    "tag": f"sub:{s['key']}:{s['next_on']}",
                    "title": f"{s['merchant']} renews {when}",
                    "body": f"{amount} on {s['next_on']:%d %b}. Keep it, or cancel before then.",
                })  # fmt: skip
        if s["charged_after_cancel"]:
            c = s["charged_after_cancel"]
            out.append({
                "tag": f"sub-late:{s['key']}:{c['on']}",
                "title": f"{s['merchant']} charged after you cancelled",
                "body": f"{money.rupees(c['amount_paise'])} on {c['on']:%d %b}. Worth asking "
                "for a refund.",
            })  # fmt: skip
        if s["price_change_paise"] > 0 and s["last_on"] and (today - s["last_on"]).days <= 7:
            out.append({
                "tag": f"sub-price:{s['key']}:{s['last_on']}",
                "title": f"{s['merchant']} went up",
                "body": f"From {money.rupees(s['previous_paise'] or 0)} to {amount}.",
            })  # fmt: skip
    for a in accounts["items"]:
        bill = a.get("bill") or {}
        if bill.get("status") in ("due", "part") and a.get("due_on"):
            days = (a["due_on"] - today).days
            if 0 <= days <= 3:
                out.append({
                    "tag": f"card:{a['id']}:{a['due_on']}:{days == 0}",
                    "title": f"{a['label']} bill due "
                    + ("today" if days == 0 else f"on {a['due_on']:%d %b}"),
                    "body": f"{money.rupees(bill['left_paise'])} left to pay"
                    + (f" (minimum {money.rupees(a['min_due_paise'])})."
                       if a.get("min_due_paise") else "."),
                })  # fmt: skip
        if expiry := card_expiry(a, today):
            out.append(expiry)
        if bill.get("status") == "overdue":
            out.append({
                "tag": f"card-late:{a['id']}:{a['due_on']}",
                "title": f"{a['label']} bill is overdue",
                "body": f"{money.rupees(bill['left_paise'])} was due on {a['due_on']:%d %b}. "
                "If it's paid, mark it paid in Money › Accounts.",
            })  # fmt: skip
    return out


# --- Savings, loans, goals and the forecast ----------------------------------------------------


def as_asset(row: MoneyAsset) -> plan.Asset:
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
        cadence=row.contribution_cadence,
    )


def as_loan(row: MoneyLoan) -> plan.Loan:
    return plan.Loan(
        principal_paise=row.principal_paise,
        rate_bp=row.rate_bp,
        months=row.months,
        emi_paise=row.emi_paise,
        first_due_on=row.first_due_on,
        status=row.status,
    )


def asset_out(row: MoneyAsset, today: date) -> dict:
    a = as_asset(row)
    value = plan.asset_value(a, today)
    matured = bool(row.maturity_on and row.maturity_on <= today)
    return {
        "id": str(row.id),
        "kind": row.kind,
        "kind_label": plan.ASSET_KINDS.get(row.kind, row.kind),
        "name": row.name or plan.ASSET_KINDS.get(row.kind, "Savings"),
        "institution": row.institution,
        "amount_paise": row.amount_paise,
        "rate_bp": row.rate_bp,
        "start_on": row.start_on,
        "maturity_on": row.maturity_on,
        "balance_on": row.balance_on,
        "contribution_paise": row.contribution_paise,
        "contribution_cadence": plan.contribution_cadence(row.kind, row.contribution_cadence),
        "payout": row.payout,
        "status": row.status,
        "note": row.note,
        "branch": row.branch,
        # The number and nominee stay encrypted; only the last four and the relation show.
        "account_no_masked": money.masked(row.account_last4),
        "has_nominee": bool(row.nominee_enc),
        "nominee_relation": row.nominee_relation,
        "value_paise": value,
        "maturity_value_paise": plan.maturity_value(a),
        "monthly_paise": plan.monthly_contribution(a),
        "matured": matured,
    }


def loan_out(row: MoneyLoan, today: date, labels: dict[uuid.UUID, str]) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "kind_label": plan.LOAN_KINDS.get(row.kind, row.kind),
        "name": row.name,
        "account_id": str(row.account_id) if row.account_id else None,
        "account": labels.get(row.account_id) if row.account_id else None,
        "lender": row.lender,
        "principal_paise": row.principal_paise,
        "rate_bp": row.rate_bp,
        "months": row.months,
        "emi_paise": row.emi_paise,
        "first_due_on": row.first_due_on,
        "status": row.status,
        "note": row.note,
        "account_no_masked": money.masked(row.account_last4),
        **plan.loan_state(as_loan(row), today),
    }


async def _rows(db: AsyncSession, model, user_id: uuid.UUID) -> list:
    return list(
        (
            await db.scalars(
                select(model).where(model.user_id == user_id).order_by(model.created_at)
            )
        ).all()
    )


async def savings_view(db: AsyncSession, user_id: uuid.UUID, today: date) -> dict:
    assets = [asset_out(r, today) for r in await _rows(db, MoneyAsset, user_id)]
    month_start = date(today.year, today.month, 1)
    moves = (
        await db.execute(
            select(
                MoneyTxn.asset_id,
                MoneyTxn.direction,
                func.max(MoneyTxn.occurred_on),
                func.count().filter(MoneyTxn.occurred_on >= month_start),
            )
            .where(MoneyTxn.user_id == user_id, MoneyTxn.asset_id.is_not(None))
            .group_by(MoneyTxn.asset_id, MoneyTxn.direction)
        )
    ).all()
    seen = {(str(a), d): (last, this_month) for a, d, last, this_month in moves}
    for a in assets:
        paid = seen.get((a["id"], "debit"))
        back = seen.get((a["id"], "credit"))
        # Linked payments in (money_links): when the last went in, and whether this month's has.
        a["last_paid_on"] = paid[0] if paid else None
        a["paid_this_month"] = bool(paid and paid[1])
        a["received_on"] = back[0] if back else None
    active = [a for a in assets if a["status"] == "active"]
    holdings = (await db.scalars(select(MoneyHolding).where(MoneyHolding.user_id == user_id))).all()
    stocks = await stocks_view(db, user_id)
    return {
        "items": assets,
        "total_paise": sum(a["value_paise"] for a in active),
        "monthly_paise": sum(a["monthly_paise"] for a in active),
        "funds_value_paise": sum(h.value_paise or 0 for h in holdings),
        "funds_cost_paise": sum(h.cost_paise or 0 for h in holdings),
        "stocks_value_paise": stocks["value_paise"],
        "maturing": [
            a
            for a in active
            if a["maturity_on"] and today <= a["maturity_on"] <= today + timedelta(days=60)
        ],  # fmt: skip
    }


async def loans_view(db: AsyncSession, user_id: uuid.UUID, today: date) -> dict:
    accounts = (await db.scalars(select(MoneyAccount).where(MoneyAccount.user_id == user_id))).all()
    labels = {a.id: account_label(a) for a in accounts}
    loans = [loan_out(r, today, labels) for r in await _rows(db, MoneyLoan, user_id)]
    active = [x for x in loans if x["status"] == "active" and not x["done"]]
    tracked = {(x["account_id"], x["name"]) for x in loans}
    txns = await load(db, user_id, today - timedelta(days=70), today + timedelta(days=1))
    seen = [
        {
            "merchant": e.merchant,
            "account_id": e.account_id,
            "account": labels.get(uuid.UUID(e.account_id)) if e.account_id else None,
            "monthly_paise": e.monthly_paise,
            "months_seen": e.months_seen,
            "first_on": e.first_on,
            "last_on": e.last_on,
        }
        for e in money.find_emis(txns, today)
        if (e.account_id, e.merchant) not in tracked
        and not any(x["account_id"] == e.account_id for x in active)
    ]
    return {
        "items": loans,
        "monthly_paise": sum(x["emi_paise"] for x in active),
        "outstanding_paise": sum(x["outstanding_paise"] for x in active),
        "untracked": seen,
    }


async def history(db: AsyncSession, user_id: uuid.UUID, today: date, months: int = 6):
    """The last full months' income, spending and investing (newest last), skipping months
    before the first transaction so an empty start doesn't drag the averages down."""
    this_month = f"{today.year:04d}-{today.month:02d}"
    first = money.month_bounds(shift_month(this_month, -months))[0]
    txns = await load(db, user_id, first, money.month_bounds(this_month)[0], pay_months=True)
    h = plan.History()
    if not txns:
        return h
    earliest = min(t.occurred_on for t in txns)
    for i in range(months, 0, -1):
        m = shift_month(this_month, -i)
        a, b = money.month_bounds(m)
        if b <= earliest:
            continue
        s = money.summarise([t for t in txns if a <= t.occurred_on < b])
        h.income.append(s["income_paise"])
        h.spend.append(s["spent_paise"])
        h.invested.append(s["invested_paise"])
        h.essentials.append(s["essential_paise"])
    return h


async def salary_now(db: AsyncSession, user_id: uuid.UUID, today: date) -> int | None:
    rows = list((await db.scalars(select(MoneySalary).where(MoneySalary.user_id == user_id))).all())
    now = money_salary.current(rows, today)
    return now.monthly_paise if now else None


async def plan_view(
    db: AsyncSession,
    user_id: uuid.UUID,
    today: date,
    *,
    months: int = 24,
    overrides: dict | None = None,
) -> dict:
    """Net worth now, the forecast, goals and plain observations, for the Plan page and the
    assistant. `overrides` tries other assumptions without saving them."""
    prefs = await settings_for(db, user_id)
    assume = plan.Assumptions.from_dict({**(prefs.plan or {}), **(overrides or {})})
    accounts = await accounts_view(db, user_id, today)
    savings = await savings_view(db, user_id, today)
    loan_rows = [r for r in await _rows(db, MoneyLoan, user_id) if r.status == "active"]
    # A card's EMIs sit inside what's owed on it when that comes from the limit; don't count
    # them twice.
    from_limit = {uuid.UUID(a["id"]) for a in accounts["items"] if a.get("owed_from_limit")}
    debt_loans = [r for r in loan_rows if r.account_id not in from_limit]
    asset_rows = [r for r in await _rows(db, MoneyAsset, user_id) if r.status == "active"]
    h = await history(db, user_id, today)
    salary = await salary_now(db, user_id, today)
    ahead = assume
    if assume.income_paise is None and salary:
        # Today's salary, not an average across a raise; more when other income adds to it.
        # Only the forecast uses it: the saved assumptions still say "from recent months".
        ahead = dataclasses.replace(assume, income_paise=max(salary, plan.recent(h.income) or 0))
    cash = accounts["cash_paise"] - accounts["owed_paise"]
    result = plan.forecast(
        today=today,
        months=months,
        cash_paise=cash,
        # Shares grow like funds do: both are market-linked.
        funds_paise=savings["funds_value_paise"] + savings["stocks_value_paise"],
        assets=[as_asset(r) for r in asset_rows],
        loans=[as_loan(r) for r in debt_loans],
        history=h,
        assume=ahead,
    )
    # EMIs on limit-based cards still come out of cash each month.
    inputs = result["inputs"]
    liquid = accounts["cash_paise"] + sum(
        plan.asset_value(as_asset(r), today) for r in asset_rows if r.kind in plan.LIQUID_KINDS
    )
    goals = []
    for g in await _rows(db, MoneyGoal, user_id):
        have = g.saved_paise + sum(
            plan.asset_value(as_asset(r), today) for r in asset_rows if str(r.id) in g.asset_ids
        )
        left = max((g.by_on.year - today.year) * 12 + g.by_on.month - today.month, 0)
        rate = plan.goal_rate_bp(left, assume.fund_return_bp)
        goals.append({
            "id": str(g.id),
            "name": g.name,
            "target_paise": g.target_paise,
            "by_on": g.by_on,
            "saved_paise": g.saved_paise,
            "asset_ids": g.asset_ids,
            "have_paise": have,
            "months_left": left,
            "rate_bp": rate,
            "monthly_needed_paise": plan.monthly_needed(g.target_paise, have, rate, left),
            "progress": min(have / g.target_paise, 1.0) if g.target_paise else 1.0,
        })  # fmt: skip
    labelled = [(r.name, as_loan(r)) for r in loan_rows]
    return {
        **result,
        "assumptions": assume.to_dict(),
        "saved_assumptions": prefs.plan or {},
        "salary_paise": salary,
        "accounts_known": len([a for a in accounts["items"] if a["kind"] == "bank"])
        - accounts["unknown_balances"],
        "unknown_balances": accounts["unknown_balances"],
        "liquid_paise": liquid,
        "emergency_months": round(liquid / inputs["spend_paise"], 1)
        if inputs["spend_paise"]
        else None,
        "goals": goals,
        "goals_monthly_paise": sum(g["monthly_needed_paise"] for g in goals),
        "insights": plan.insights(
            today=today,
            liquid_paise=liquid,
            spend_paise=inputs["spend_paise"],
            surplus_paise=inputs["surplus_paise"],
            income_paise=inputs["income_paise"],
            loans=labelled,
            assume=assume,
        ),
    }


async def health_view(db: AsyncSession, user_id: uuid.UUID, today: date) -> dict:
    """The safety checks, safe-to-spend and balance for the Health tab and the assistant."""
    prefs = await settings_for(db, user_id)
    assume = plan.Assumptions.from_dict(prefs.plan)
    h = await history(db, user_id, today)
    income = assume.income_paise if assume.income_paise is not None else plan.recent(h.income)
    if assume.income_paise is None and (salary := await salary_now(db, user_id, today)):
        income = max(salary, income or 0)
    spend = assume.spend_paise if assume.spend_paise is not None else plan.recent(h.spend)
    accounts = await accounts_view(db, user_id, today)
    subs = await subscriptions_view(db, user_id, today)
    asset_rows = [r for r in await _rows(db, MoneyAsset, user_id) if r.status == "active"]
    loan_rows = [r for r in await _rows(db, MoneyLoan, user_id) if r.status == "active"]
    month_start = date(today.year, today.month, 1)
    month_end = money.add_months(month_start, 1)
    emis = sum(plan.emi_in_month(as_loan(r), month_start) for r in loan_rows)

    banks = [a for a in accounts["items"] if a["kind"] == "bank" and not a["hidden"]]
    cards = [a for a in accounts["items"] if a["kind"] == "card" and not a["hidden"]]
    limited = [a for a in cards if a.get("credit_limit_paise") and a.get("owed_paise") is not None]
    liquid = accounts["cash_paise"] + sum(
        plan.asset_value(as_asset(r), today) for r in asset_rows if r.kind in plan.LIQUID_KINDS
    )
    upcoming = await upcoming_payments(db, user_id, today, days=30, subs=subs)
    # Still to go out this month, not counting card bills: their spending is already counted
    # on the day of each purchase. Savings instalments aren't spending either.
    rest = [
        u["amount_paise"] for u in upcoming if u["kind"] == "emi" and today < u["on"] < month_end
    ] + [
        s["amount_paise"]
        for s in subs["items"]
        if s["status"] in ("new", "active")
        and s["category"] != "Investments"
        and s["next_on"]
        and today < s["next_on"] < month_end
    ]
    this_month = money.summarise(
        await load(db, user_id, month_start, today + timedelta(days=1), pay_months=True)
    )
    snap = plan.Snapshot(
        income_paise=income,
        spend_paise=spend,
        essential_paise=plan.recent(h.essentials),
        emis_paise=emis,
        liquid_paise=liquid,
        cash_paise=accounts["cash_paise"],
        balances_known=any(a["balance_paise"] is not None for a in banks),
        cards=len(cards),
        card_owed_paise=sum(a["owed_paise"] for a in limited),
        card_limit_paise=sum(a["credit_limit_paise"] for a in limited),
        overdue_bills=sum(1 for a in cards if (a.get("bill") or {}).get("status") == "overdue"),
        due_30_paise=sum(u["amount_paise"] for u in upcoming),
        month_income_paise=this_month["income_paise"],
        month_spent_paise=this_month["spent_paise"],
        month_committed_paise=sum(rest),
        days_left=(month_end - today).days,
        months_of_history=len(h.income),
    )
    return {
        **plan.health(snap, assume),
        "targets": {
            "save_target_bp": assume.save_target_bp,
            "emergency_months": assume.emergency_months,
            "health_cover_paise": assume.health_cover_paise,
            "life_cover_paise": assume.life_cover_paise,
            "dependants": assume.dependants,
        },
        "unknown_balances": accounts["unknown_balances"],
    }


def health_reminders(health: dict, today: date) -> list[dict]:
    """Once a month at most: spending has gone past what's safe this month, or the bank won't
    cover the next 30 days of bills. Tags carry the month, so each is sent once a month."""
    out = []
    month = f"{today.year:04d}-{today.month:02d}"
    safe = health["safe_to_spend"]
    if safe["known"] and safe["left_paise"] < 0 and safe["days_left"] > 1:
        out.append({
            "tag": f"safe:{month}",
            "title": "Spending is past this month's safe amount",
            "body": f"{money.rupees(-safe['left_paise'])} over, after keeping "
            f"{money.rupees(safe['keep_paise'])} as savings and what's still due. "
            f"{safe['days_left']} days to go.",
            "url": "/money/health",
        })  # fmt: skip
    bills = next((c for c in health["checks"] if c["key"] == "bills"), None)
    if bills and bills["status"] == "act":
        out.append({
            "tag": f"cover:{month}",
            "title": "Bills due soon are more than the bank balance",
            "body": f"{bills['value']} in the next 30 days. Move money in or check the balances.",
            "url": "/money/health",
        })  # fmt: skip
    return out


async def upcoming_payments(
    db: AsyncSession, user_id: uuid.UUID, today: date, *, days: int = 14, subs: dict | None = None
) -> list[dict]:
    """Card bills, EMIs and renewals due in the next `days`, soonest first."""
    subs = subs or await subscriptions_view(db, user_id, today)
    until = today + timedelta(days=days)
    out = [
        {
            "kind": "subscription",
            "label": s["merchant"],
            "amount_paise": s["amount_paise"],
            "on": s["next_on"],
            "detail": s["cadence"],
        }
        for s in subs["items"]
        if s["status"] in ("new", "active") and s["next_on"] and today <= s["next_on"] <= until
    ]
    accounts = await accounts_view(db, user_id, today)
    for a in accounts["items"]:
        bill = a.get("bill") or {}
        if bill.get("status") in ("due", "part", "overdue") and a["due_on"] <= until:
            out.append({
                "kind": "card",
                "label": f"{a['label']} bill",
                "amount_paise": bill["left_paise"],
                "on": a["due_on"],
                "detail": "overdue" if bill["status"] == "overdue" else "card bill",
            })  # fmt: skip
    for row in await _rows(db, MoneyLoan, user_id):
        state = plan.loan_state(as_loan(row), today)
        nxt = state["next_due_on"]
        if row.status == "active" and nxt and today <= nxt <= until:
            out.append({
                "kind": "emi",
                "label": row.name,
                "amount_paise": row.emi_paise,
                "on": nxt,
                "detail": f"EMI {state['paid'] + 1} of {row.months}",
            })  # fmt: skip
    return sorted(out, key=lambda x: x["on"])


# --- Moving sealed values to Money's own key ---------------------------------------------------


def _sealed_columns():
    return [
        (MoneyTxn, MoneyTxn.id, MoneyTxn.narration_enc),
        (MoneyItem, MoneyItem.id, MoneyItem.name_enc),
        (MoneySettings, MoneySettings.user_id, MoneySettings.cas_password_enc),
        (MoneyImport, MoneyImport.id, MoneyImport.filename_enc),
        (MoneyAsset, MoneyAsset.id, MoneyAsset.account_no_enc),
        (MoneyAsset, MoneyAsset.id, MoneyAsset.nominee_enc),
        (MoneyLoan, MoneyLoan.id, MoneyLoan.account_no_enc),
    ]


async def reseal(db: AsyncSession, cfg: Settings, batch: int = 500) -> int:
    """Re-encrypt sealed values still under an older key (the token keys, before
    MONEY_ENCRYPTION_KEYS was set, or a rotated-out money key) under the current one. Values no
    configured key opens are left alone. Returns how many changed; run until it returns 0."""
    if not cfg.money_encryption_keys.strip():
        return 0
    moved = 0
    for model, key, column in _sealed_columns():
        last = None
        while True:
            q = select(key, column).where(column.is_not(None), column != "").order_by(key)
            if last is not None:
                q = q.where(key > last)
            rows = (await db.execute(q.limit(batch))).all()
            if not rows:
                break
            last = rows[-1][0]
            for row_id, value in rows:
                if money.sealed_current(cfg, value):
                    continue
                plain = money.unseal(cfg, value)
                if not plain:
                    continue
                obj = await db.get(model, row_id)
                setattr(obj, column.key, money.seal(cfg, plain))
                moved += 1
            await db.commit()
    if moved:
        log.info("money.resealed", extra={"count": moved})
    return moved
