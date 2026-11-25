"""One-off repairs to rows Money stored before its reading got better.

Run once per person (the orchestrator keeps a Redis flag per version):

- an alert's payee read again from its stored text, now that a bank's sign-off ("we're here to
  support you in every step") is cut first and a UPI payee in brackets is read;
- categories the word list chose worked out again (never one the person or a rule set);
- (version 2) alerts read again from mail for the last REREAD_DAYS, for emails an earlier
  version couldn't read (the orchestrator does the reading);
- (version 3) statement lines paid through Google Pay's bills named "Google Pay bills";
- (version 5) order emails read again for the items in them and the restaurant;
- (version 6) categories worked out again for the new Smoking and tobacco category;
- two emails about one payment (a card's "debited" and the autopay's "bill paid", or one alert
  in two mailboxes) made one row.

Owner's rows only; logs carry ids and counts, never amounts or names.
"""

import logging
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import money
from nexus_common import money_store as store
from nexus_common.models import MoneySettings, MoneyTxn
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

# 2: ICICI card alerts (thrown away for the OTP warning in their footer) read again.
# 3: Google Pay bill payments (electricity, recharges) named and filed as bills; pay-later
#    repayments and GRE prep given their categories.
# 4: a debit card's currency-conversion fee (DCC) filed as a fee, not found as a subscription.
# 5: order emails read again so the items bought and the restaurant are kept (money_items).
# 6: paan shops and tobacconists filed under Smoking and tobacco (a new category).
VERSION = 6
# How far back alerts are read again when a new version's reading fixes call for it: four
# months, so the habits view has last month and more to compare with.
REREAD_DAYS = 120


def _payee_lost(old: str, cleaned: str) -> bool:
    """The stored payee isn't in the alert once its sign-off is cut: it came from there."""
    key = money.merchant_key(old)
    return old == "Unknown" or not key or key not in money.merchant_key(cleaned)


def _free(t: MoneyTxn) -> bool:
    """Nothing the person did or Money tied hangs on the row, so it can go."""
    return (
        t.pair_id is None
        and t.asset_id is None
        and not t.excluded
        and t.category_by != "user"
        and not t.note
    )


async def repair(db: AsyncSession, cfg: Settings, user_id: uuid.UUID) -> dict[str, int]:
    """Flushes but doesn't commit. Returns {"payees", "categories", "merged"}."""
    counts = {"payees": 0, "categories": 0, "merged": 0}
    prefs = await db.get(MoneySettings, user_id)
    rules = (prefs.rules if prefs else None) or {}
    rows = list(
        (
            await db.scalars(
                select(MoneyTxn)
                .where(MoneyTxn.user_id == user_id)
                .order_by(MoneyTxn.occurred_on, MoneyTxn.created_at)
            )
        ).all()
    )
    kept: list[MoneyTxn] = []
    gone: set[uuid.UUID] = set()
    for t in rows:
        text = money.unseal(cfg, t.narration_enc) or ""
        p = money.parse_alert("", text, t.occurred_on) if t.source == "alert" and text else None
        cleaned = p.narration if p else text
        if t.source == "statement" and text and re.search(r"gpayrecharge", text, re.I):
            # Statement lines paid through Google Pay's bills were named just "Google".
            name = money.statement_merchant(text)
            if name != t.merchant:
                t.merchant = name[:120]
                counts["payees"] += 1
        if p is not None and p.merchant != t.merchant and p.merchant != "Unknown":
            if _payee_lost(t.merchant, cleaned):
                t.merchant = p.merchant[:120]
                counts["payees"] += 1
        if t.category_by == "auto" and t.pair_id is None:
            category = money.categorise(
                t.merchant, cleaned, credit=t.direction == "credit", rules=rules
            )
            if category != t.category:
                t.category = category
                counts["categories"] += 1
        if p is None:
            continue
        # Check the row against the alerts already kept, as a new email would be.
        p.occurred_on, p.amount_paise, p.direction = t.occurred_on, t.amount_paise, t.direction
        p.merchant, p.ref = t.merchant, t.ref
        p.channel = t.channel if t.channel == "autopay" else p.channel
        near = [k for k in kept if abs((k.occurred_on - t.occurred_on).days) <= 1]
        same = store.same_alert(cfg, near, p, t.account_id, t.source_ref)
        if same is None or not t.source_ref:
            kept.append(t)
            continue
        first, second = (same, t) if _free(t) else (t, same) if _free(same) else (None, None)
        if first is None:
            kept.append(t)
            continue
        if second.channel == "autopay":
            first.channel = "autopay"
        if first.merchant == "Unknown" and second.merchant != "Unknown":
            first.merchant = second.merchant
        if second.confirmed and not first.confirmed:
            first.confirmed, first.alt_key = True, second.alt_key
        if first is t:
            kept.remove(same)
            kept.append(t)
        gone.add(second.id)
    if gone:
        await db.execute(delete(MoneyTxn).where(MoneyTxn.id.in_(gone)))
        counts["merged"] = len(gone)
    await db.flush()
    log.info("money.repaired", extra={"user_id": str(user_id), "version": VERSION, **counts})
    return counts
