"""Salary: take-home pay over time, typed in once with each raise.

Some banks don't email when money comes in, so a salary credit can be missing from what was
read. For each month since the first salary row, when no credit like it reached the account
around the pay day, an expected row is added (source "salary", left out of nothing: it's
income). When the real credit turns up later, from an alert or a statement, the expected row
goes. Rows stay within the last two years.

Owner only; logs carry ids and counts, never amounts.
"""

import calendar
import logging
import uuid
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import MoneySalary, MoneyTxn, utcnow

log = logging.getLogger(__name__)

SOURCE = "salary"
# How far either side of the pay day a credit counts as that month's salary, and how close
# to the expected amount it must be (take-home moves a little with tax and deductions).
WINDOW_DAYS = 6
# Salaries come early more often than late (paid before a holiday), so look further back.
EARLY_DAYS = 10
# pay_day 0: the last working day (Monday to Friday) of the month.
LAST_WORKING_DAY = 0
LOW, HIGH = 0.8, 1.25
MONTHS_BACK = 24
NOTE = "Expected from your salary details: no credit like it was read for this month."
# A salary paid in the last days of a month is what the next month runs on (paid on 30 Oct,
# spent in November), so month totals count it there. Applies when the pay day is this late.
LATE_PAY_DAY = 20


def pay_date(year: int, month: int, day: int) -> date:
    last = calendar.monthrange(year, month)[1]
    if day == LAST_WORKING_DAY:
        end = date(year, month, last)
        return end - timedelta(days=max(end.weekday() - 4, 0))
    return date(year, month, min(max(day, 1), last))


def salary_on(rows: list[MoneySalary], day: date) -> MoneySalary | None:
    """The salary that applies on a day: the latest one starting on or before it."""
    current = [r for r in rows if r.from_on <= day]
    return max(current, key=lambda r: r.from_on) if current else None


def current(rows: list[MoneySalary], today: date) -> MoneySalary | None:
    return salary_on(rows, today)


def pays_next_month(
    rows: list[MoneySalary], on: date, amount_paise: int, account_id: str | None
) -> date | None:
    """The first of next month when a credit on `on` is a salary paid late in its month (on or
    a few days before a late pay day, about the expected amount, into the salary's account),
    so it counts towards the month it pays for. None otherwise."""
    pay = None
    for r in sorted(rows, key=lambda r: r.from_on):
        if r.from_on <= pay_date(on.year, on.month, r.pay_day):
            pay = r
    if pay is None or (pay.pay_day != LAST_WORKING_DAY and pay.pay_day < LATE_PAY_DAY):
        return None
    due = pay_date(on.year, on.month, pay.pay_day)
    if on < due - timedelta(days=EARLY_DAYS):
        return None
    if not pay.monthly_paise * LOW <= amount_paise <= pay.monthly_paise * HIGH:
        return None
    if pay.account_id and account_id and str(pay.account_id) != str(account_id):
        return None
    return date(on.year + (on.month == 12), on.month % 12 + 1, 1)


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


async def fill(db: AsyncSession, user_id: uuid.UUID, today: date | None = None) -> dict:
    """Add an expected salary row for each month whose credit wasn't read, and drop the ones a
    real credit has since turned up for. Returns {"added", "removed"}."""
    today = today or date.today()
    rows = list((await db.scalars(select(MoneySalary).where(MoneySalary.user_id == user_id))).all())
    placeholders = {
        t.dedupe_key: t
        for t in (
            await db.scalars(
                select(MoneyTxn).where(MoneyTxn.user_id == user_id, MoneyTxn.source == SOURCE)
            )
        ).all()
    }
    keep: set[str] = set()
    added = 0
    if rows:
        y0, m0 = divmod(today.year * 12 + today.month - 1 - MONTHS_BACK, 12)
        start = max(min(r.from_on for r in rows), date(y0, m0 + 1, 1))
        for y, m in _months(start, today):
            # The month's salary: what applied on its pay day.
            pay = None
            for r in sorted(rows, key=lambda r: r.from_on):
                if r.from_on <= pay_date(y, m, r.pay_day):
                    pay = r
            if pay is None:
                continue
            due = pay_date(y, m, pay.pay_day)
            if due > today:
                continue
            key = f"salary:{y:04d}-{m:02d}"
            lo, hi = due - timedelta(days=EARLY_DAYS), due + timedelta(days=WINDOW_DAYS)
            q = select(MoneyTxn.id).where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.direction == "credit",
                MoneyTxn.source != SOURCE,
                MoneyTxn.excluded.is_(False),
                MoneyTxn.occurred_on >= lo,
                MoneyTxn.occurred_on <= hi,
                MoneyTxn.amount_paise >= int(pay.monthly_paise * LOW),
                MoneyTxn.amount_paise <= int(pay.monthly_paise * HIGH),
            )
            if pay.account_id:
                q = q.where(MoneyTxn.account_id == pay.account_id)
            if await db.scalar(q.limit(1)):
                continue
            keep.add(key)
            t = placeholders.get(key)
            if t is None:
                db.add(
                    MoneyTxn(
                        user_id=user_id, account_id=pay.account_id, occurred_on=due,
                        amount_paise=pay.monthly_paise, direction="credit",
                        merchant=pay.employer or "Salary", category="Income",
                        category_by="auto", narration_enc="", channel="other",
                        source=SOURCE, dedupe_key=key, note=NOTE, created_at=utcnow(),
                    )
                )  # fmt: skip
                added += 1
            else:
                t.occurred_on, t.amount_paise = due, pay.monthly_paise
                t.account_id, t.merchant = pay.account_id, pay.employer or "Salary"
    gone = [k for k in placeholders if k not in keep]
    if gone:
        await db.execute(
            delete(MoneyTxn).where(
                MoneyTxn.user_id == user_id,
                MoneyTxn.source == SOURCE,
                MoneyTxn.dedupe_key.in_(gone),
            )
        )
    await db.flush()
    if added or gone:
        log.info(
            "money.salary_filled",
            extra={"user_id": str(user_id), "added": added, "removed": len(gone)},
        )
    return {"added": added, "removed": len(gone)}
