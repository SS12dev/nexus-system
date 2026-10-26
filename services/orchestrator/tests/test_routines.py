from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from nexus_common import whatsapp
from nexus_common.models import (
    Chat,
    Message,
    MessageRole,
    Notification,
    RoleName,
    Routine,
    RoutineRun,
    User,
    UserStatus,
)
from nexus_common.routines import add_routine
from nexus_orchestrator import routines
from nexus_orchestrator.llm import private


@pytest.fixture
async def routine(sessionmaker, settings):
    from nexus_api.users import create_user

    async def make(role=RoleName.owner, **extra) -> tuple:
        async with sessionmaker() as db:
            user = await create_user(
                db,
                email=f"{role}@example.com",
                role=role,
                status=UserStatus.active,
                name="Priya",
            )
            user.timezone = "Asia/Kolkata"
            r = await add_routine(
                db,
                settings,
                user,
                name="Morning brief",
                prompt="Brief me on today.",
                schedule="FREQ=DAILY;BYHOUR=8;BYMINUTE=0",
                **extra,
            )
            await db.commit()
            return user.id, r.id

    return make


async def messages(sessionmaker, chat_id) -> list[Message]:
    async with sessionmaker() as db:
        return list(
            (
                await db.scalars(
                    select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
                )
            ).all()
        )


async def test_due_claims_queues_and_moves_on(deps, enqueued, routine, sessionmaker):
    _, rid = await routine()
    slot = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    async with sessionmaker() as db:
        r = await db.get(Routine, rid)
        r.next_run_at = slot
        await db.commit()
    assert await routines.due(deps) == 1
    assert enqueued.jobs == [("run_routine", str(rid), slot.isoformat())]
    async with sessionmaker() as db:
        r = await db.get(Routine, rid)
    assert r.next_run_at > datetime.now(UTC)
    # Nothing is due any more.
    assert await routines.due(deps) == 0


async def test_a_missed_run_is_skipped_not_run_late(deps, enqueued, routine, sessionmaker):
    _, rid = await routine()
    async with sessionmaker() as db:
        r = await db.get(Routine, rid)
        r.next_run_at = datetime.now(UTC) - timedelta(hours=3)
        await db.commit()
    assert await routines.due(deps) == 0
    assert enqueued.jobs == []


async def test_a_run_posts_in_its_chat_without_learning(deps, enqueued, routine, sessionmaker):
    _, rid = await routine()
    slot = datetime(2026, 10, 24, 2, 30, tzinfo=UTC)
    assert await routines.run_routine(deps, rid, slot) == "ok"
    async with sessionmaker() as db:
        r = await db.get(Routine, rid)
        run = await db.scalar(select(RoutineRun).where(RoutineRun.routine_id == rid))
    rows = await messages(sessionmaker, r.chat_id)
    assert rows[0].role == MessageRole.user
    assert rows[0].content.startswith('[Routine "Morning brief", ')
    assert "(Asia/Kolkata)" in rows[0].content and "Brief me on today." in rows[0].content
    assert rows[1].content == "Hello there, Shivam."
    assert run.status == "ok" and run.message_id == rows[1].id and run.output_tokens == 4
    assert r.last_run_at is not None and r.failures == 0
    assert not [j for j in enqueued.jobs if j[0] == "extract_memories"]
    # It ran in a hidden chat that stays out of the chat list.
    async with sessionmaker() as db:
        assert (await db.get(Chat, r.chat_id)).kind == "routine"
    # The same slot never runs twice.
    assert await routines.run_routine(deps, rid, slot) is None


async def test_nothing_new_is_quiet(deps, model, routine, sessionmaker):
    model.script = ["NOTHING_NEW"]
    _, rid = await routine(only_if_new=True)
    status = await routines.run_routine(deps, rid, datetime.now(UTC))
    assert status == "quiet"
    async with sessionmaker() as db:
        r = await db.get(Routine, rid)
    assert await messages(sessionmaker, r.chat_id) == []
    # The prompt asked for it.
    assert "reply with exactly NOTHING_NEW" in model.seen[0][-1].content


async def test_failures_pause_the_routine(deps, model, routine, sessionmaker):
    model.error = RuntimeError("boom")
    _, rid = await routine()
    base = datetime(2026, 10, 1, tzinfo=UTC)
    for i in range(3):
        assert await routines.run_routine(deps, rid, base + timedelta(days=i)) == "failed"
    async with sessionmaker() as db:
        r = await db.get(Routine, rid)
    assert not r.enabled and r.next_run_at is None and r.failures == 3
    # The pause is a notice, not a message in the hidden chat.
    async with sessionmaker() as db:
        notices = (await db.scalars(select(Notification))).all()
    assert [n.title for n in notices] == ["Paused Morning brief"]
    assert "3 failed runs in a row" in notices[0].body
    assert notices[0].url == f"/routines?open={rid}"


async def test_skipped_without_budget(deps, gateway, routine, sessionmaker, settings):
    from nexus_common.litellm import ensure_user_key

    uid, rid = await routine()
    async with sessionmaker() as db:
        user = await db.get(User, uid)
        key = await ensure_user_key(db, settings, gateway, user)
        await db.commit()
    gateway.keys[key]["spend"] = 49.99  # owner budget is 50
    assert await routines.run_routine(deps, rid, datetime.now(UTC)) == "skipped"


async def test_delivery_by_email_and_whatsapp(deps, routine, sessionmaker, monkeypatch):
    sent = {}

    async def fake_mail(settings, msg):
        sent["email"] = msg

    async def fake_whatsapp(settings, phone, params, http=None):
        sent["whatsapp"] = (phone, params)
        return "wamid.1"

    monkeypatch.setattr(routines.mail, "send", fake_mail)
    monkeypatch.setattr(routines.whatsapp, "send_template", fake_whatsapp)
    deps.settings.smtp_host, deps.settings.smtp_from = "smtp.test", "nexus@test"
    deps.settings.whatsapp_token, deps.settings.whatsapp_phone_number_id = "t", "123"
    try:
        uid, rid = await routine(delivery=["email", "whatsapp"])
        async with sessionmaker() as db:
            (await db.get(User, uid)).phone = "+919812345678"
            await db.commit()
        assert await routines.run_routine(deps, rid, datetime.now(UTC)) == "ok"
    finally:
        deps.settings.smtp_host = deps.settings.smtp_from = ""
        deps.settings.whatsapp_token = deps.settings.whatsapp_phone_number_id = ""
    assert sent["email"]["Subject"] == "Morning brief"
    assert "Hello there, Shivam." in sent["email"].get_content()
    assert f"/routines?open={rid}" in sent["email"].get_content()
    assert sent["whatsapp"] == ("+919812345678", ["Morning brief", "Hello there, Shivam."])
    async with sessionmaker() as db:
        run = await db.scalar(select(RoutineRun))
    assert run.delivered == ["email", "whatsapp"]


async def test_routine_tools_in_chat(deps, routine, sessionmaker):
    from nexus_orchestrator.tools import ToolContext, build_tools

    uid, rid = await routine()

    async def emit(*a, **k):
        pass

    tools = {
        t.name: t
        for t in build_tools(
            ToolContext(deps=deps, user_id=uid, chat_id=None, emit=emit),
            ["create_routine", "list_routines", "update_routine"],
        )
    }
    out = await tools["create_routine"].ainvoke(
        {
            "name": "Weekly papers",
            "prompt": "Find new papers on retrieval.",
            "schedule": "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0",
            "only_if_new": True,
        }
    )
    assert out.startswith("Saved routine Weekly papers") and "every Monday at 09:00" in out
    bad = await tools["create_routine"].ainvoke(
        {"name": "x", "prompt": "y", "schedule": "FREQ=MINUTELY"}
    )
    assert bad.startswith("Not saved")
    listed = await tools["list_routines"].ainvoke({})
    assert "Morning brief" in listed and "Weekly papers" in listed
    out = await tools["update_routine"].ainvoke({"routine_id": str(rid), "action": "pause"})
    assert "paused" in out
    async with sessionmaker() as db:
        assert (await db.get(Routine, rid)).next_run_at is None


async def test_langfuse_masks_everyone_but_owners(settings, routine, sessionmaker):
    owner_id, _ = await routine()
    member_id, _ = await routine(role=RoleName.member)
    async with sessionmaker() as db:
        owner = await db.get(User, owner_id)
        member = await db.get(User, member_id)
        assert private(settings, owner) == {}
        assert private(settings, member) == {"mask_input": True, "mask_output": True}
        settings.trace_masking = False
        try:
            assert private(settings, member) == {}
        finally:
            settings.trace_masking = True


def test_whatsapp_preview_is_one_line():
    text = "## Today\n\n- **Standup** at 09:00\n- Reply to [Anna](https://x.y)\n\n\tDone"
    assert whatsapp.one_line(text) == "Today Standup at 09:00 Reply to Anna Done"
    assert len(whatsapp.one_line("word " * 100)) <= whatsapp.PREVIEW_CHARS


async def test_whatsapp_sends_the_template(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200, json={"messages": [{"id": "wamid.9"}]})

    s = settings.model_copy(update={"whatsapp_token": "tok", "whatsapp_phone_number_id": "555"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        mid = await whatsapp.send_template(s, "+919812345678", ["Brief", "All\nclear"], http=http)
    assert mid == "wamid.9"
    assert seen["url"].endswith("/555/messages")
    assert b'"to":"919812345678"' in seen["body"].replace(b" ", b"")
    assert b"All clear" in seen["body"]
    with pytest.raises(whatsapp.WhatsAppError):
        await whatsapp.send_template(s, "12345", ["a", "b"])


async def test_routine_runs_skip_the_team_router(deps, routine):
    """A team for every scheduled run would use up the person's budget in days."""
    _, rid = await routine()
    deps.settings.route_model = "nexus-nano"
    try:
        assert await routines.run_routine(deps, rid, datetime.now(UTC)) == "ok"
    finally:
        deps.settings.route_model = ""
    assert [c["model"] for c in deps.factory_calls] == ["nexus-mini"]


async def test_push_delivery(deps, routine, sessionmaker, monkeypatch):
    from nexus_common.models import PushSubscription

    sent = []
    monkeypatch.setattr(routines.push, "_send", lambda s, sub, data: sent.append(data))
    deps.settings.vapid_public_key, deps.settings.vapid_private_key = "pub", "priv"
    deps.settings.vapid_subject = "mailto:o@x.y"
    try:
        uid, rid = await routine(delivery=["push"])
        async with sessionmaker() as db:
            db.add(PushSubscription(user_id=uid, endpoint="https://p/1", p256dh="k", auth="a"))
            await db.commit()
        assert await routines.run_routine(deps, rid, datetime.now(UTC)) == "ok"
    finally:
        deps.settings.vapid_public_key = deps.settings.vapid_private_key = ""
        deps.settings.vapid_subject = ""
    assert '"title": "Morning brief"' in sent[0] and f"/routines?open={rid}" in sent[0]
    async with sessionmaker() as db:
        run = await db.scalar(select(RoutineRun))
    assert run.delivered == ["push"]
