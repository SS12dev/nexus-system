"""How complete Money's picture is: for each account, what was read from alert emails and from
statements, month by month, what's missing, and the one thing that would fill the gap.

Tracking comes first: suggestions built on a month Money can't see would be wrong. Owner only;
nothing here carries amounts or payees, only counts and dates.
"""

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import (
    MoneyAccount,
    MoneyMailbox,
    MoneyMailStatement,
    MoneySettings,
    MoneyTxn,
)
from nexus_common.money_store import account_label

MONTHS = 12
# An account that emails alerts and has been quiet this long is worth a look.
QUIET_DAYS = 10


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def months_back(today: date, n: int = MONTHS) -> list[str]:
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:])
    return f"{y + (m == 12):04d}-{1 if m == 12 else m + 1:02d}"


def assess(
    kind: str,
    months: list[str],
    cells: dict[str, dict[str, int]],
    last_alert: date | None,
    last_statement: date | None,
    credit_alerts: int,
    today: date,
) -> tuple[str, str]:
    """(status, what to do): ok, stale, gaps or empty."""
    seen = [m for m in months if cells.get(m, {}).get("read")]
    if not seen and not last_alert and not last_statement:
        return "empty", (
            "Nothing read for this one yet. If you use it, add a statement, or make sure its "
            "alert emails reach a Gmail connected under Import."
        )
    if last_alert and (today - last_alert).days <= QUIET_DAYS:
        missing = [m for m in months if m >= _month(last_alert - timedelta(days=60))]
        missing = [m for m in missing if not cells.get(m, {}).get("read")]
        if missing:
            return "gaps", f"Alerts are arriving, but nothing was read for {_names(missing)}."
        if kind == "bank" and not credit_alerts:
            return "ok", (
                "Spending arrives from alert emails. This bank sends none for money coming in, "
                "so income comes from statements or your salary details."
            )
        return "ok", "Alert emails are arriving."
    if last_alert:
        return "stale", (
            f"No alert email since {last_alert:%-d %b}. Check the bank still emails a Gmail "
            "connected under Import, or add a statement from then."
        )
    # Statements only.
    if last_statement:
        after = _next_month(_month(last_statement))
        if after <= _month(today):
            return "stale", (
                f"Only statements, and the latest ends {last_statement:%-d %b %Y}. Add one from "
                f"{_names([after])} on; this bank's alerts don't reach Money."
            )
        return "ok", "Statements up to date; this bank's alerts don't reach Money."
    return "stale", "Nothing recent was read for this one."


def _names(months: list[str]) -> str:
    names = [date(int(m[:4]), int(m[5:]), 1).strftime("%b %Y") for m in months]
    if len(names) > 3:
        return f"{names[0]} to {names[-1]}"
    return " and ".join(names)


async def view(db: AsyncSession, user_id: uuid.UUID, today: date) -> dict:
    months = months_back(today)
    start = date(int(months[0][:4]), int(months[0][5:]), 1)
    accounts = (
        await db.scalars(
            select(MoneyAccount)
            .where(MoneyAccount.user_id == user_id)
            .order_by(MoneyAccount.kind, MoneyAccount.institution, MoneyAccount.mask)
        )
    ).all()
    month_of = func.to_char(MoneyTxn.occurred_on, "YYYY-MM")
    grid: dict[uuid.UUID | None, dict[str, dict[str, int]]] = {}
    for account_id, month, source, n in (
        await db.execute(
            select(MoneyTxn.account_id, month_of, MoneyTxn.source, func.count())
            .where(MoneyTxn.user_id == user_id, MoneyTxn.occurred_on >= start)
            .group_by(MoneyTxn.account_id, month_of, MoneyTxn.source)
        )
    ).all():
        cell = grid.setdefault(account_id, {}).setdefault(month, {})
        group = {"alert": "alerts", "statement": "statements", "salary": "expected"}.get(
            source, "other"
        )
        cell[group] = cell.get(group, 0) + n
        if source != "salary":
            cell["read"] = cell.get("read", 0) + n
    lasts: dict[tuple, date] = {
        (account_id, source): last
        for account_id, source, last in (
            await db.execute(
                select(MoneyTxn.account_id, MoneyTxn.source, func.max(MoneyTxn.occurred_on))
                .where(MoneyTxn.user_id == user_id)
                .group_by(MoneyTxn.account_id, MoneyTxn.source)
            )
        ).all()
    }
    credits = dict(
        (
            await db.execute(
                select(MoneyTxn.account_id, func.count())
                .where(
                    MoneyTxn.user_id == user_id,
                    MoneyTxn.source == "alert",
                    MoneyTxn.direction == "credit",
                )
                .group_by(MoneyTxn.account_id)
            )
        ).all()
    )
    waiting = (
        await db.scalars(
            select(MoneyMailStatement).where(
                MoneyMailStatement.user_id == user_id, MoneyMailStatement.status == "new"
            )
        )
    ).all()

    def emailed(a: MoneyAccount) -> MoneyMailStatement | None:
        """The newest statement in the mail that looks like this account's."""
        fits = [
            w
            for w in waiting
            if w.institution == a.institution
            and w.mask
            and any((m or "").endswith(w.mask) for m in (a.mask, a.card_mask))
        ]
        return max(fits, key=lambda w: w.received_at) if fits else None

    items = []
    for a in accounts:
        cells = grid.get(a.id, {})
        last_alert, last_statement = lasts.get((a.id, "alert")), lasts.get((a.id, "statement"))
        status, todo = assess(
            a.kind, months, cells, last_alert, last_statement, credits.get(a.id, 0), today
        )
        if a.hidden and status == "empty":
            continue
        if (mail := emailed(a)) and status != "ok":
            span = (
                f" for {mail.period_from:%-d %b %Y} to {mail.period_to:%-d %b %Y}"
                if mail.period_from and mail.period_to
                else ""
            )
            todo = f"Your bank emailed a statement{span}. Import it below to fill the gap."
        items.append(
            {
                "id": str(a.id),
                "label": account_label(a),
                "kind": a.kind,
                "status": status,
                "todo": todo,
                "last_alert_on": last_alert,
                "last_statement_on": last_statement,
                "credit_alerts": credits.get(a.id, 0) > 0,
                "months": [
                    {"month": m, **{k: v for k, v in cells.get(m, {}).items() if k != "read"}}
                    for m in months
                ],
            }
        )
    order = {"stale": 0, "gaps": 1, "empty": 3, "ok": 2}
    items.sort(key=lambda x: order[x["status"]])
    loose = grid.get(None, {})
    prefs = await db.get(MoneySettings, user_id)
    boxes = (await db.scalars(select(MoneyMailbox).where(MoneyMailbox.user_id == user_id))).all()

    def when(t: datetime | None) -> str | None:
        return t.isoformat() if t else None

    mail = [
        {
            "label": "Your sign-in Gmail",
            "on": bool(prefs and prefs.gmail_sync),
            "checked_at": when(prefs.gmail_synced_at if prefs else None),
            "read_until": when(prefs.gmail_synced_until if prefs else None),
            "error": prefs.last_error if prefs else None,
        }
    ] + [
        {
            "label": b.email_masked,
            "on": True,
            "checked_at": when(b.synced_at),
            "read_until": when(b.synced_until),
            "error": b.last_error,
        }
        for b in boxes
    ]
    return {
        "months": months,
        "accounts": items,
        "unassigned": sum(c.get("read", 0) for c in loose.values()),
        "mail": mail,
        "needs_attention": sum(1 for i in items if i["status"] in ("stale", "gaps")),
        "statements_waiting": len(waiting),
    }
