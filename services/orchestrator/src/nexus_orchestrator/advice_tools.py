"""The assistant's `advice_list`: the same items as the Advice page, as text, so "what should I
sort out?" gets the card, subscription, spending and Organiser items in one call. Money items
only for the owner, and only while "Let the assistant use Money" is on."""

from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from nexus_common import advice_store
from nexus_common.models import MoneySettings, User
from nexus_common.organiser import local_today

LEVEL_WORDS = {"act": "do now", "look": "worth a look", "good": "going well"}


def build_advice_tools(ctx: Any, note: Callable) -> dict[str, Callable]:
    deps, user_id = ctx.deps, ctx.user_id

    async def advice_list() -> str:
        """What in the user's own data deserves a decision, most urgent first: card use
        against the statement date, overdue bills, card expiry, subscriptions all on one card
        or still on a bank account, foreign-currency charges, price rises, duplicate services,
        yearly renewals, unusual or double charges, bank fees, the Money safety checks, and
        overdue tasks or an old Inbox. Each says why and the one step to take. Use it for
        "what should I sort out?", "anything I should know about my money?" or a weekly
        check-in."""
        await note("advice_list")
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            access = await db.scalar(
                select(MoneySettings.assistant_access).where(MoneySettings.user_id == user_id)
            )
            out = await advice_store.view(
                db, user, local_today(user), with_money=access is not False
            )
            await db.commit()
        if not out["items"]:
            return "Nothing needs a decision right now."
        lines = ["<advice>"]
        for i in out["items"]:
            lines.append(f"- [{LEVEL_WORDS[i['level']]}, {i['area']}] {i['title']}. {i['body']}")
        if out["hidden"]:
            lines.append(f"{out['hidden']} more the user marked done, snoozed or dismissed.")
        if access is False:
            lines.append("Money items are left out: the user switched off Money access.")
        lines.append(
            "These are rules of thumb from the user's own data, not product advice. The user "
            "can mark items done, snooze or dismiss them on the Advice page.</advice>"
        )
        return "\n".join(lines)

    return {"advice_list": advice_list}
