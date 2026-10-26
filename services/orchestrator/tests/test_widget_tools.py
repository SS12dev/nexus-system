"""The assistant's widget tools: a timer asked for in chat lands on the main screen, and can be
listed, paused and removed by id."""

import re

from sqlalchemy import select

from nexus_common.models import Widget
from nexus_orchestrator.tools import ToolContext, build_tools

NAMES = ["start_timer", "set_alarm", "remind_me", "add_widget", "list_widgets", "control_widget"]


def tools_for(deps, user, chat_id):
    async def emit(type_, **data):
        pass

    ctx = ToolContext(deps=deps, user_id=user.id, chat_id=chat_id, emit=emit)
    return {t.name: t for t in build_tools(ctx, NAMES)}


async def test_a_timer_from_chat_lands_on_the_screen(deps, owner):
    user, chat_id = owner
    tools = tools_for(deps, user, chat_id)
    out = await tools["start_timer"].ainvoke({"minutes": 5, "label": "Tea"})
    assert out.startswith("Added to the main screen: timer 'Tea'")
    async with deps.sessionmaker() as db:
        w = await db.scalar(select(Widget).where(Widget.user_id == user.id))
    assert w.chat_id == chat_id and w.state["status"] == "running"

    listed = await tools["list_widgets"].ainvoke({})
    wid = re.search(r"id ([0-9a-f-]{36})", listed).group(1)
    assert "Done: timer 'Tea'" in await tools["control_widget"].ainvoke(
        {"widget_id": wid, "action": "pause"}
    )
    assert "Removed Tea" in await tools["control_widget"].ainvoke(
        {"widget_id": wid, "action": "remove"}
    )
    assert await tools["list_widgets"].ainvoke({}) == "No widgets on the screen."


async def test_bad_requests_say_what_is_wrong(deps, owner):
    user, chat_id = owner
    tools = tools_for(deps, user, chat_id)
    assert "Not added" in await tools["set_alarm"].ainvoke({"time": "7pm"})
    assert "Not added" in await tools["remind_me"].ainvoke({"text": "Call mum"})
    out = await tools["remind_me"].ainvoke(
        {"text": "Job hunting", "time": "21:00", "days": "daily"}
    )
    assert out.startswith("Added") and "every day" in out.lower()


async def test_an_alarm_asked_for_with_a_tune_rings_with_it(deps, owner):
    user, chat_id = owner
    tools = tools_for(deps, user, chat_id)
    out = await tools["set_alarm"].ainvoke({"time": "07:00", "days": "weekdays", "tune": "sunrise"})
    assert out.startswith("Added")
    async with deps.sessionmaker() as db:
        w = await db.scalar(select(Widget).where(Widget.user_id == user.id))
    assert w.state["tune"] == "sunrise"
