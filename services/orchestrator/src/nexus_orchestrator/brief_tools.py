"""The assistant's `brief_today`: the same morning brief as the Brief page, as text, so "what's
my day?" gets the weather, calendar, tasks and (for the owner, when the assistant may read
Money) the money lines in one call."""

from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select

from nexus_common import brief
from nexus_common.models import MoneySettings, User


def build_brief_tools(ctx: Any, note: Callable) -> dict[str, Callable]:
    deps, user_id = ctx.deps, ctx.user_id

    async def brief_today() -> str:
        """The user's day at a glance: weather and air quality where they are, today's
        calendar, overdue and due tasks with this week's focus, and for the owner, safe to
        spend, payments due in the next three days, yesterday's spending and the next money
        safety step. Use it for "what's my day?", "brief me" or a morning routine."""
        await note("brief_today")
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            prefs = await brief.settings_for(db, user)
            access = await db.scalar(
                select(MoneySettings.assistant_access).where(MoneySettings.user_id == user_id)
            )
            http = deps.google_http
            if http is None:
                async with httpx.AsyncClient(timeout=20) as own:
                    b = await brief.build(
                        db, deps.settings, user, prefs, http=own, redis=deps.redis
                    )
            else:
                b = await brief.build(db, deps.settings, user, prefs, http=http, redis=deps.redis)
        text = brief.as_text(b, with_money=access is not False)
        notes = []
        if "weather" in b["sections"] and not b["place"]:
            notes.append(
                "No place is set for the weather; the user can pick one on the Brief page."
            )
        if b["calendar"] is not None and not b["calendar"]["connected"]:
            notes.append("Google Calendar isn't connected (Settings page).")
        for part in b["errors"]:
            notes.append(f"The {part} part couldn't be read just now.")
        if access is False:
            notes.append(
                "Money lines are left out: the user switched off the assistant's Money access."
            )
        return text + ("\n\n" + "\n".join(notes) if notes else "")

    return {"brief_today": brief_today}
