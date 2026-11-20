"""The assistant's one Money tool: totals for a month, only when the person has switched on
"Let the assistant read totals" in Money's settings. It never sees single transactions, bank
narrations, account numbers or statements, and it can't change anything."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from nexus_common import money
from nexus_common import money_store as store
from nexus_common.models import MoneySettings, User
from nexus_common.organiser import local_today

OFF = (
    "The user hasn't let the assistant read Money. Tell them they can switch on 'Let the "
    "assistant read totals' in Money's settings (the Money page, Settings tab)."
)


def build_money_tools(ctx: Any, note: Callable) -> dict[str, Callable]:
    deps, user_id = ctx.deps, ctx.user_id

    async def money_summary(month: str | None = None) -> str:
        """The user's spending and income for a month (YYYY-MM; this month when left out):
        totals by category against their budgets, the biggest merchants, repeating payments
        and the current value of their mutual funds. Read-only, amounts in rupees."""
        await note("money_summary", month=month)
        async with deps.sessionmaker() as db:
            prefs = await db.scalar(select(MoneySettings).where(MoneySettings.user_id == user_id))
            if prefs is None or not prefs.assistant_access:
                return OFF
            user = await db.get(User, user_id)
            today = local_today(user)
            month = month or today.strftime("%Y-%m")
            try:
                money.month_bounds(month)
            except ValueError:
                return "month must look like 2026-11."
            ov = await store.overview(db, user_id, month, today)
            subs = money.find_subscriptions(
                await store.load(
                    db, user_id, today - store.SUBSCRIPTION_LOOKBACK, today + timedelta(days=1)
                ),
                today,
            )
        if not ov["has_data"]:
            return "Money has no transactions or holdings yet."
        r = money.rupees
        lines = [
            f"<money month='{month}'>",
            f"Spent {r(ov['spent_paise'])}, income {r(ov['income_paise'])}, "
            f"invested {r(ov['invested_paise'])}, left over {r(ov['net_paise'])} "
            f"({ov['count']} transactions; transfers between the user's own accounts and hidden "
            "rows are left out).",
            "By category: "
            + (
                "; ".join(f"{c['category']} {r(c['spent_paise'])}" for c in ov["categories"])
                or "none"
            ),
            "Biggest merchants: "
            + (
                "; ".join(
                    f"{m['merchant']} {r(m['spent_paise'])} ({m['count']}x)"
                    for m in ov["merchants"][:5]
                )
                or "none"
            ),
        ]
        if ov["budgets"]:
            lines.append(
                "Budgets: "
                + "; ".join(
                    f"{b['category']} {r(b['spent_paise'])} of {r(b['monthly_paise'])}"
                    for b in ov["budgets"]
                )
                + f" ({round(ov['month_progress'] * 100)}% through the month)"
            )
        if subs:
            lines.append(
                "Repeating payments: "
                + "; ".join(
                    f"{s.merchant} {r(s.typical_paise)} {s.cadence}, next about {s.next_on:%d %b}"
                    for s in subs[:10]
                )
            )
        if ov["funds_value_paise"]:
            lines.append(
                f"Mutual funds worth {r(ov['funds_value_paise'])}"
                + (f" (cost {r(ov['funds_cost_paise'])})" if ov["funds_cost_paise"] else "")
            )
        lines.append("</money>")
        return "\n".join(lines)

    return {"money_summary": money_summary}
