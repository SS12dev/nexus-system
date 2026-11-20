"""The organiser's background jobs and the assistant's organiser tools."""

import io
import json
import zipfile
from datetime import UTC, date, datetime
from unittest.mock import patch

from sqlalchemy import select

from nexus_common import organiser as org
from nexus_common.models import Notification, OrgItem, OrgReview, User
from nexus_orchestrator import organiser
from nexus_orchestrator.tools import ToolContext, build_tools


async def items(sessionmaker, user_id) -> list[OrgItem]:
    async with sessionmaker() as db:
        rows = await db.scalars(
            select(OrgItem).where(OrgItem.user_id == user_id).order_by(OrgItem.created_at)
        )
        return list(rows.all())


async def add(sessionmaker, user_id, **kw) -> OrgItem:
    async with sessionmaker() as db:
        user = await db.get(User, user_id)
        return await org.create(db, user, **kw)


def test_read_suggestion_keeps_only_what_checks_out():
    garden = OrgItem(id=org.uuid.uuid4(), type="project", title="Garden")
    today = date(2026, 11, 20)
    got = organiser.read_suggestion(
        {"type": "task", "container": 1, "due_on": "2026-11-22", "why": "  Fits  Garden. "},
        [garden],
        today,
    )
    assert got == {
        "type": "task",
        "parent_id": str(garden.id),
        "parent_title": "Garden",
        "parent_type": "project",
        "due_on": "2026-11-22",
        "why": "Fits Garden.",
    }
    # Out-of-range container, a far-off date, a made-up type and a bool: all dropped.
    got = organiser.read_suggestion(
        {"type": "idea", "container": 7, "due_on": "2031-01-01", "why": 3}, [garden], today
    )
    assert got["type"] == "note" and got["parent_id"] is None and got["due_on"] is None
    assert organiser.read_suggestion({"container": True}, [garden], today)["parent_id"] is None


async def test_inbox_items_get_a_filing_suggestion(deps, model, owner, sessionmaker):
    user, _ = owner
    garden = await add(sessionmaker, user.id, type="project", title="Garden")
    item = await add(sessionmaker, user.id, type="note", title="buy soil", inbox=True)
    model.script = [
        json.dumps({"type": "task", "container": 1, "due_on": None, "why": "Fits Garden."})
    ]
    assert await organiser.suggest_filing(deps, item.id)
    prompt = model.seen[0][-1].content
    assert "1. project: Garden" in prompt and "buy soil" in prompt
    assert deps.factory_calls[0]["model"] == deps.settings.organiser_model
    async with sessionmaker() as db:
        saved = await db.get(OrgItem, item.id)
    assert saved.status == "inbox"  # only suggested, never moved
    assert saved.suggestion["parent_id"] == str(garden.id)
    # Once there's a suggestion, or once it's filed, nothing more happens.
    assert not await organiser.suggest_filing(deps, item.id)
    assert len(model.seen) == 1


async def test_sunday_evening_review_reminder_goes_once(deps, owner, sessionmaker):
    user, _ = owner
    async with sessionmaker() as db:
        u = await db.get(User, user.id)
        u.timezone = "Asia/Kolkata"
        await db.commit()
    await add(sessionmaker, user.id, type="note", title="loose", inbox=True)
    sunday_6pm_ist = datetime(2026, 11, 22, 12, 40, tzinfo=UTC)
    with patch.object(organiser, "utcnow", lambda: sunday_6pm_ist):
        assert await organiser.review_reminders(deps) == 1
        assert await organiser.review_reminders(deps) == 0
    async with sessionmaker() as db:
        notes = (await db.scalars(select(Notification))).all()
    assert [n.title for n in notes] == ["Time for your weekly review"]
    assert notes[0].url == "/organiser/review" and "1 in your Inbox" in notes[0].body
    # Not on a Saturday, and not once the review is done.
    with patch.object(organiser, "utcnow", lambda: datetime(2026, 11, 21, 12, 40, tzinfo=UTC)):
        assert await organiser.review_reminders(deps) == 0
    async with sessionmaker() as db:
        db.add(OrgReview(user_id=user.id, week_of=date(2026, 11, 30), completed_at=sunday_6pm_ist))
        await db.commit()
    await deps.redis.delete(*[k async for k in deps.redis.scan_iter("nexus:organiser:*")])
    nxt = datetime(2026, 11, 29, 12, 40, tzinfo=UTC)
    with patch.object(organiser, "utcnow", lambda: nxt):
        assert await organiser.review_reminders(deps) == 0


async def test_nightly_export_writes_a_vault(sdeps, owner, sessionmaker, storage):
    user, _ = owner
    await add(sessionmaker, user.id, type="area", title="Health")
    assert await organiser.nightly_exports(sdeps) == 1
    data = await storage.get(org.export_key(user.id))
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "Nexus/2 Areas/Health.md" in names


async def test_assistant_tools(deps, owner, sessionmaker, enqueued):
    user, chat_id = owner

    async def emit(*a, **k):
        pass

    names = [
        "organiser_add",
        "organiser_find",
        "organiser_read",
        "organiser_update",
        "organiser_today",
    ]
    tools = {
        t.name: t
        for t in build_tools(
            ToolContext(deps=deps, user_id=user.id, chat_id=chat_id, emit=emit), names
        )
    }
    assert set(names) <= set(tools)
    out = await tools["organiser_add"].ainvoke({"title": "Garden", "type": "project"})
    assert out.startswith("Added project Garden")
    out = await tools["organiser_add"].ainvoke(
        {"title": "Buy soil", "where": "garden", "due_on": "2026-01-02", "priority": 3}
    )
    assert "in project Garden" in out
    out = await tools["organiser_add"].ainvoke({"title": "Seeds", "where": "Kitchen"})
    assert out.startswith('No project, area or resource is called "Kitchen"')
    out = await tools["organiser_add"].ainvoke({"title": "idea", "type": "note", "inbox": True})
    assert "to the Inbox" in out
    assert [j[0] for j in enqueued.jobs] == ["organiser_suggest"]

    listed = await tools["organiser_find"].ainvoke({"due": "overdue"})
    assert "Buy soil" in listed and "high priority" in listed and "in project Garden" in listed
    soil = next(i for i in await items(sessionmaker, user.id) if i.title == "Buy soil")
    out = await tools["organiser_update"].ainvoke({"item_id": str(soil.id), "status": "done"})
    assert out.startswith("Done: Buy soil") and "done" in out
    assert "Nothing in the organiser" in await tools["organiser_find"].ainvoke({"type": "task"})

    garden = next(i for i in await items(sessionmaker, user.id) if i.title == "Garden")
    await tools["organiser_update"].ainvoke({"item_id": str(garden.id), "append": "Plan beds"})
    read = await tools["organiser_read"].ainvoke({"item_id": str(garden.id)})
    assert "Plan beds" in read and "Buy soil" in read

    today = await tools["organiser_today"].ainvoke({"add_to_daily_note": "Felt good"})
    assert "Today is" in today and "- Felt good" in today
    assert "1 item(s) waiting in the Inbox" in today
    # The assistant's edits keep revisions, so a bad one can be undone.
    async with sessionmaker() as db:
        revs = await db.scalar(
            select(org.func.count())
            .select_from(org.OrgRevision)
            .where(org.OrgRevision.item_id == garden.id)
        )
    assert revs == 1
