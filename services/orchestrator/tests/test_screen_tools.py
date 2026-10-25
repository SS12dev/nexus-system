"""The assistant's screen tool: it opens a file in the orb's place, switches the layout, orders
the dock and lights names in the orb, every change audited; and it leaves alone what the person
changed by hand, unless they asked."""

import json
import uuid

from sqlalchemy import select

from nexus_common import screen as sc
from nexus_common.events import user_channel
from nexus_common.models import AuditLog, Entity, File, FileStatus, ScreenState, Widget
from nexus_orchestrator.tools import ToolContext, build_tools


def tools_for(deps, user, chat_id, run_id=None):
    async def emit(type_, **data):
        pass

    ctx = ToolContext(deps=deps, user_id=user.id, chat_id=chat_id, emit=emit, run_id=run_id)
    return {t.name: t for t in build_tools(ctx, ["screen", "start_timer", "add_widget"])}


async def collect(redis, user_id, call) -> tuple[str, list[dict]]:
    pubsub = redis.pubsub()
    await pubsub.subscribe(user_channel(user_id))
    await pubsub.get_message(timeout=1)
    out = await call
    events = []
    while (
        msg := await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3)
    ) is not None:
        events.append(json.loads(msg["data"]))
    await pubsub.aclose()
    return out, events


async def test_focus_opens_a_file_and_is_audited(deps, owner, redis):
    user, chat_id = owner
    async with deps.sessionmaker() as db:
        f = File(
            user_id=user.id,
            chat_id=chat_id,
            name="rocket.stl",
            content_type="model/stl",
            size=10,
            sha256="0" * 64,
            storage_key="k",
            status=FileStatus.ready,
        )
        db.add(f)
        await db.commit()
    tools = tools_for(deps, user, chat_id)
    out, events = await collect(
        redis, user.id, tools["screen"].ainvoke({"op": "focus", "target": "rocket.stl"})
    )
    assert out.startswith("Opening rocket.stl")
    op = next(e for e in events if e["type"] == "screen.op")
    assert op["data"]["op"] == "focus" and op["data"]["by"] == "assistant"
    assert op["data"]["file"]["id"] == str(f.id)
    assert op["data"]["screen"]["focus"]["chat_id"] == str(chat_id)
    async with deps.sessionmaker() as db:
        log = await db.scalar(select(AuditLog).where(AuditLog.action == "screen.focus"))
    assert log.actor_id == user.id and log.data["chat_id"] == str(chat_id)

    # Closed by hand: Orion doesn't reopen it unless asked.
    async with deps.sessionmaker() as db:
        state = await sc.load(db, user.id)
        sc.mark_override(state, sc.override_key("focus", "file", str(f.id)))
        state.focus = None
        await db.commit()
    assert "closed that file" in await tools["screen"].ainvoke(
        {"op": "focus", "target": "rocket.stl"}
    )
    out = await tools["screen"].ainvoke({"op": "focus", "target": str(f.id), "user_asked": True})
    assert out.startswith("Opening")


async def test_mode_respects_the_persons_own_switch(deps, owner):
    user, chat_id = owner
    tools = tools_for(deps, user, chat_id)
    assert "dashboard" in await tools["screen"].ainvoke({"op": "mode", "mode": "dashboard"})
    async with deps.sessionmaker() as db:
        state = await db.get(ScreenState, user.id)
        assert state.mode == "dashboard"
        sc.mark_override(state, "mode")
        await db.commit()
    assert "Left as it is" in await tools["screen"].ainvoke({"op": "mode", "mode": "orb"})
    await tools["screen"].ainvoke({"op": "mode", "mode": "list", "user_asked": True})
    async with deps.sessionmaker() as db:
        assert (await db.get(ScreenState, user.id)).mode == "list"


async def test_pin_moves_a_widget_first(deps, owner):
    user, chat_id = owner
    tools = tools_for(deps, user, chat_id)
    await tools["start_timer"].ainvoke({"minutes": 5, "label": "Tea"})
    await tools["add_widget"].ainvoke({"kind": "note", "text": "milk"})
    async with deps.sessionmaker() as db:
        note = await db.scalar(select(Widget).where(Widget.kind == "note"))
    assert "place 1" in await tools["screen"].ainvoke({"op": "pin", "target": str(note.id)})
    async with deps.sessionmaker() as db:
        order = (await db.scalars(select(Widget.kind).order_by(Widget.slot))).all()
    assert order == ["note", "timer"]


async def test_highlight_lights_known_names(deps, owner, redis):
    user, chat_id = owner
    async with deps.sessionmaker() as db:
        e = Entity(user_id=user.id, type="person", name="Asha", aliases=["ash"])
        db.add(e)
        await db.commit()
    tools = tools_for(deps, user, chat_id)
    out, events = await collect(
        redis, user.id, tools["screen"].ainvoke({"op": "highlight", "target": "ash, nobody"})
    )
    assert out == "Lit Asha in the orb."
    op = next(e for e in events if e["type"] == "screen.op")
    assert op["data"]["recall"]["entities"] == [{"id": str(e.id), "name": "Asha", "type": "person"}]


async def test_teams_cannot_repaint_the_screen(deps, owner):
    user, chat_id = owner
    tools = tools_for(deps, user, chat_id, run_id=uuid.uuid4())
    out = await tools["screen"].ainvoke({"op": "mode", "mode": "dashboard"})
    assert "Only the assistant" in out
