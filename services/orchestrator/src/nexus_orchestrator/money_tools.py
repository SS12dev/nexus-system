"""The assistant's Money tools, owner only, and only while "Let the assistant use Money" is on
in Money's settings (it is by default).

The assistant reads totals, transactions (date, amount, merchant, category, account; never the
bank's narration, full account numbers or statements), accounts and card bills, savings and
loans, subscriptions and the forecast. The one thing it can change is what the person decided
about a subscription (keep, cancelled, not a subscription, reminder days). Nothing here can
move money.
"""

from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import or_, select

from nexus_common import money
from nexus_common import money_store as store
from nexus_common.models import MoneyAccount, MoneySettings, MoneyTxn, User
from nexus_common.organiser import local_today

OFF = (
    "The user has switched off the assistant's access to Money. Tell them they can switch on "
    "'Let the assistant use Money' on the Money page, Import and settings tab."
)
r = money.rupees


def _d(day: date | None) -> str:
    return f"{day:%d %b %Y}" if day else "unknown"


def build_money_tools(ctx: Any, note: Callable) -> dict[str, Callable]:
    deps, user_id = ctx.deps, ctx.user_id

    async def _open(db) -> date | None:
        """Today in the user's time zone, or None when access is off."""
        prefs = await db.scalar(select(MoneySettings).where(MoneySettings.user_id == user_id))
        if prefs is not None and not prefs.assistant_access:
            return None
        user = await db.get(User, user_id)
        return local_today(user)

    async def money_summary(month: str | None = None) -> str:
        """The user's spending and income for a month (YYYY-MM; this month when left out):
        totals by category against their budgets, the biggest merchants, repeating payments
        and the current value of their mutual funds. Read-only, amounts in rupees."""
        await note("money_summary", month=month)
        async with deps.sessionmaker() as db:
            today = await _open(db)
            if today is None:
                return OFF
            month = month or today.strftime("%Y-%m")
            try:
                money.month_bounds(month)
            except ValueError:
                return "month must look like 2026-11."
            ov = await store.overview(db, user_id, month, today)
        if not ov["has_data"]:
            return "Money has no transactions or holdings yet."
        lines = [
            f"<money month='{month}'>",
            f"Spent {r(ov['spent_paise'])}, income {r(ov['income_paise'])}, "
            f"invested {r(ov['invested_paise'])}, left over {r(ov['net_paise'])} "
            f"({ov['count']} transactions; transfers between the user's own accounts and hidden "
            "rows are left out; EMIs count as spending).",
            "By category: "
            + (
                "; ".join(f"{c['category']} {r(c['spent_paise'])}" for c in ov["categories"])
                or "none"
            ),  # fmt: skip
            "Biggest merchants: "
            + (
                "; ".join(
                    f"{m['merchant']} {r(m['spent_paise'])} ({m['count']}x)"
                    for m in ov["merchants"][:5]
                )
                or "none"
            ),  # fmt: skip
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
        if ov["upcoming"]:
            lines.append(
                "Due in the next two weeks: "
                + "; ".join(
                    f"{u['label']} {r(u['amount_paise'])} on {_d(u['on'])}" for u in ov["upcoming"]
                )
            )
        lines.append(
            f"Subscriptions and repeating bills: {r(ov['subscriptions_monthly_paise'])} a month"
            + (
                f" ({ov['subscriptions_to_review']} new to review)"
                if ov["subscriptions_to_review"]
                else ""
            )  # fmt: skip
        )
        if ov["funds_value_paise"]:
            lines.append(
                f"Mutual funds worth {r(ov['funds_value_paise'])}"
                + (f" (cost {r(ov['funds_cost_paise'])})" if ov["funds_cost_paise"] else "")
            )
        lines.append("</money>")
        return "\n".join(lines)

    async def money_transactions(
        month: str | None = None,
        category: str | None = None,
        search: str | None = None,
        direction: str | None = None,
        limit: int = 40,
    ) -> str:
        """The user's transactions, newest first: date, amount, merchant, category and
        account. Filter by month (YYYY-MM), category (one of Money's categories), search (part
        of a merchant name or note) and direction ("debit" or "credit"). At most 100."""
        await note("money_transactions", month=month, category=category)
        limit = max(1, min(int(limit or 40), 100))
        async with deps.sessionmaker() as db:
            today = await _open(db)
            if today is None:
                return OFF
            q = select(MoneyTxn).where(MoneyTxn.user_id == user_id)
            if month:
                try:
                    start, end = money.month_bounds(month)
                except ValueError:
                    return "month must look like 2026-11."
                q = q.where(MoneyTxn.occurred_on >= start, MoneyTxn.occurred_on < end)
            if category:
                if category not in money.CATEGORIES:
                    return "Unknown category. Use one of: " + ", ".join(money.CATEGORIES)
                q = q.where(MoneyTxn.category == category)
            if direction in ("debit", "credit"):
                q = q.where(MoneyTxn.direction == direction)
            if search:
                like = f"%{search.strip()[:60]}%"
                q = q.where(or_(MoneyTxn.merchant.ilike(like), MoneyTxn.note.ilike(like)))
            rows = (
                await db.scalars(
                    q.order_by(MoneyTxn.occurred_on.desc(), MoneyTxn.created_at.desc()).limit(limit)
                )
            ).all()
            accounts = {
                a.id: store.account_label(a)
                for a in (
                    await db.scalars(select(MoneyAccount).where(MoneyAccount.user_id == user_id))
                ).all()
            }
        if not rows:
            return "No transactions match."
        lines = ["<transactions>"]
        for t in rows:
            sign = "-" if t.direction == "debit" else "+"
            flags = (" (left out of totals)" if t.excluded else "") + (
                f" note: {t.note}" if t.note else ""
            )
            lines.append(
                f"{t.occurred_on:%d %b %Y} {sign}{r(t.amount_paise)} {t.merchant} "
                f"[{t.category}] {accounts.get(t.account_id, 'cash or unknown account')}{flags}"
            )
        lines.append("</transactions>")
        return "\n".join(lines)

    async def money_accounts() -> str:
        """Every bank account and card the user has: balances, card bills (amount, due date,
        paid or not), what's owed and the limit left, plus savings (FDs, RDs, PPF, EPF, NPS,
        gold), mutual funds and loans or EMIs with what's left and when they end."""
        await note("money_accounts")
        async with deps.sessionmaker() as db:
            today = await _open(db)
            if today is None:
                return OFF
            accounts = await store.accounts_view(db, user_id, today)
            savings = await store.savings_view(db, user_id, today)
            loans = await store.loans_view(db, user_id, today)
        lines = ["<accounts>"]
        for a in accounts["items"]:
            if a["hidden"]:
                continue
            if a["kind"] == "card":
                bill = a["bill"]
                text = f"Card {a['label']}: spent {r(a['spent_month_paise'])} this month"
                if bill["status"] != "none":
                    text += (
                        f"; bill {r(a['due_paise'])} due {_d(a['due_on'])}, {bill['status']}"
                        + (f", {r(bill['left_paise'])} left" if bill["left_paise"] else "")
                    )
                if a["owed_paise"] is not None:
                    text += f"; owed now about {r(a['owed_paise'])}"
                if a["available_paise"] is not None:
                    text += f"; limit left {r(a['available_paise'])}"
            else:
                bal = (
                    f"balance {r(a['balance_paise'])} on {_d(a['balance_on'])}"
                    if a["balance_paise"] is not None
                    else "balance unknown"
                )
                text = f"Bank {a['label']}: {bal}; spent {r(a['spent_month_paise'])} this month"
            lines.append(text)
        for s in savings["items"]:
            if s["status"] != "active":
                continue
            rate = f"{s['rate_bp'] / 100:g}%"
            text = f"{s['kind_label']} {s['name']}: worth {r(s['value_paise'])} at {rate}"
            if s["maturity_on"]:
                text += f", matures {_d(s['maturity_on'])}"
                if s["maturity_value_paise"]:
                    text += f" at about {r(s['maturity_value_paise'])}"
            lines.append(text)
        if savings["funds_value_paise"]:
            lines.append(f"Mutual funds: {r(savings['funds_value_paise'])}")
        for x in loans["items"]:
            if x["done"] or x["status"] != "active":
                continue
            lines.append(
                f"{x['kind_label']} {x['name']}: EMI {r(x['emi_paise'])}, {x['left']} of "
                f"{x['months']} left, about {r(x['outstanding_paise'])} owed, "
                f"ends {_d(x['ends_on'])}"
            )
        for e in loans["untracked"]:
            lines.append(
                f"Untracked EMI payments: {e['merchant']} {r(e['monthly_paise'])} a month on "
                f"{e['account'] or 'an account'} (the user can add the loan in Money › Accounts)"
            )
        lines.append(
            f"Totals: bank balances {r(accounts['cash_paise'])} (from {len(accounts['items'])} "
            f"accounts; {accounts['unknown_balances']} without a known balance), owed on cards "
            f"{r(accounts['owed_paise'])}, savings {r(savings['total_paise'])}, loans owed "
            f"{r(loans['outstanding_paise'])}."
        )
        lines.append("</accounts>")
        return "\n".join(lines)

    async def money_subscriptions() -> str:
        """The user's subscriptions and repeating bills: amount, how often, next charge, and
        status (new = not reviewed yet, active = keeping, cancelled, ignored = not a
        subscription), with flags for a price change or a charge after cancelling."""
        await note("money_subscriptions")
        async with deps.sessionmaker() as db:
            today = await _open(db)
            if today is None:
                return OFF
            view = await store.subscriptions_view(db, user_id, today)
        if not view["items"]:
            return "No subscriptions or repeating payments found yet."
        lines = ["<subscriptions>"]
        for s in view["items"]:
            text = (
                f"{s['merchant']} ({s['group']}, {s['status']}): {r(s['amount_paise'])} "
                f"{s['cadence']}, next {_d(s['next_on'])}, last {_d(s['last_on'])}"
            )
            if s["price_change_paise"]:
                text += f"; price changed from {r(s['previous_paise'] or 0)}"
            if s["charged_after_cancel"]:
                c = s["charged_after_cancel"]
                text += f"; CHARGED AFTER CANCELLING {r(c['amount_paise'])} on {_d(c['on'])}"
            if s["not_seen"]:
                text += "; no charge lately"
            if s["manage_url"]:
                text += f"; manage at {s['manage_url']}"
            lines.append(text)
        lines.append(
            f"Live total {r(view['subscriptions_monthly_paise'])} subscriptions + "
            f"{r(view['bills_monthly_paise'])} bills a month; cancelled ones save "
            f"{r(view['saved_monthly_paise'])} a month."
        )
        lines.append("</subscriptions>")
        return "\n".join(lines)

    async def money_subscription_update(
        merchant: str,
        status: str | None = None,
        remind_days: int | None = None,
        note_text: str | None = None,
    ) -> str:
        """Record the user's decision about a subscription: status "active" (keep), "cancelled"
        (they cancelled it; Money then watches for a charge after) or "ignored" (not a
        subscription), and how many days before renewal to remind them (0 for none). Use the
        merchant name exactly as money_subscriptions shows it. This doesn't cancel anything
        with the company; tell the user to do that at the manage link."""
        await note("money_subscription_update", status=status)
        if status is not None and status not in ("active", "cancelled", "ignored"):
            return "status must be active, cancelled or ignored."
        fields: dict = {}
        if status:
            fields["status"] = status
        if remind_days is not None:
            fields["remind_days"] = max(0, min(int(remind_days), 30))
        if note_text is not None:
            fields["note"] = note_text.strip()[:300] or None
        if not fields:
            return "Nothing to change."
        async with deps.sessionmaker() as db:
            today = await _open(db)
            if today is None:
                return OFF
            view = await store.subscriptions_view(db, user_id, today)
            match = next(
                (s for s in view["items"] if s["key"] == money.merchant_key(merchant)), None
            )
            if match is None:
                return f"No subscription called {merchant!r}. Check money_subscriptions."
            await store.save_subscription(db, user_id, match["merchant"], **fields)
            await db.commit()
        await store.changed(deps.redis, user_id)
        return f"Saved: {match['merchant']} " + ", ".join(f"{k} {v}" for k, v in fields.items())

    async def money_plan(months: int = 24, try_monthly_sip: int | None = None) -> str:
        """The user's net worth now and forecast for the next `months` (3-120), with a likely
        range, the assumptions behind it, goals and how much each needs a month, and plain
        observations (emergency fund, saving rate, when EMIs end). try_monthly_sip (rupees)
        shows what investing that much a month would do, without saving it."""
        await note("money_plan", months=months)
        months = max(3, min(int(months or 24), 120))
        overrides = {"new_sip_paise": int(try_monthly_sip) * 100} if try_monthly_sip else None
        async with deps.sessionmaker() as db:
            today = await _open(db)
            if today is None:
                return OFF
            p = await store.plan_view(db, user_id, today, months=months, overrides=overrides)
        s, i, a = p["start"], p["inputs"], p["assumptions"]
        end = p["points"][-1]
        lines = [
            "<plan>",
            f"Net worth now {r(s['net_worth_paise'])}: bank less card dues {r(s['cash_paise'])}, "
            f"funds {r(s['funds_paise'])}, savings {r(s['savings_paise'])}, loans "
            f"-{r(s['debt_paise'])}.",
            f"Monthly: income {r(i['income_paise'])}, spending {r(i['spend_paise'])} "
            f"(EMIs {r(i['emis_paise'])}), into savings {r(i['contributions_paise'])}, into "
            f"funds {r(i['existing_investing_paise'])}, surplus {r(i['surplus_paise'])} "
            f"(from {i['months_of_history']} months of history).",
            f"In {months} months ({end['month']}): about {r(end['net_worth_paise'])}, likely "
            f"between {r(end['low_paise'])} and {r(end['high_paise'])}.",
            f"Assumptions: raise {a['income_growth_bp'] / 100:g}%/yr, inflation "
            f"{a['inflation_bp'] / 100:g}%/yr, funds {a['fund_return_bp'] / 100:g}% (range "
            f"{a['fund_low_bp'] / 100:g}-{a['fund_high_bp'] / 100:g}%), new SIP "
            f"{r(a['new_sip_paise'])}/month.",
        ]
        if p["emergency_months"] is not None:
            lines.append(f"Emergency fund covers {p['emergency_months']} months of spending.")
        for g in p["goals"]:
            lines.append(
                f"Goal {g['name']}: {r(g['target_paise'])} by {_d(g['by_on'])}, have "
                f"{r(g['have_paise'])}, needs {r(g['monthly_needed_paise'])}/month."
            )
        for x in p["insights"]:
            lines.append(f"- {x['title']}: {x['body']}")
        if p["unknown_balances"]:
            lines.append(
                f"{p['unknown_balances']} bank accounts have no known balance, so cash is "
                "understated; the user can type balances in Money › Accounts."
            )
        lines.append(
            "These are projections from the user's own history and stated assumptions, not "
            "advice; returns are not guaranteed.</plan>"
        )
        return "\n".join(lines)

    return {
        "money_summary": money_summary,
        "money_transactions": money_transactions,
        "money_accounts": money_accounts,
        "money_subscriptions": money_subscriptions,
        "money_subscription_update": money_subscription_update,
        "money_plan": money_plan,
    }
