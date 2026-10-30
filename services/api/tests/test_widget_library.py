"""The widget library and what came with it: removing a widget puts it away to come back as it
was, a search box, resizing, app actions a custom widget asks for, and forking a chat."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from nexus_common import custom_widgets as cw
from nexus_common.models import (
    Chat,
    CustomWidget,
    Message,
    MessageRole,
    MessageStatus,
    User,
    Widget,
)
from nexus_common.widgets import LIBRARY_KEEP, MAX_WIDGETS, add, prune_library, put_away, restore

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)  # 17:30 in Kolkata
HTML = '<!doctype html><html><head><meta charset="utf-8"></head><body><div>hi</div></body></html>'


async def me(owner, sessionmaker):
    await owner.get("/api/me")
    async with sessionmaker() as db:
        return await db.scalar(select(User).where(User.email == "owner@example.com"))


async def test_a_removed_note_waits_in_the_library_and_comes_back_as_it_was(owner):
    note = (await owner.post("/api/widgets", json={"kind": "note", "text": "Buy milk"})).json()
    r = await owner.post(f"/api/widgets/{note['id']}/action", json={"action": "remove"})
    assert r.status_code == 200 and r.json() is None
    assert (await owner.get("/api/widgets")).json() == []
    (away,) = (await owner.get("/api/widgets/library")).json()
    assert away["id"] == note["id"] and away["removed_at"] and away["state"]["text"] == "Buy milk"
    # Nothing can be pressed on it while it's put away.
    r = await owner.patch(f"/api/widgets/{note['id']}", json={"text": "x"})
    assert r.status_code == 404

    back = await owner.post(f"/api/widgets/{note['id']}/action", json={"action": "restore"})
    assert back.status_code == 200 and back.json()["removed_at"] is None
    assert [w["state"]["text"] for w in (await owner.get("/api/widgets")).json()] == ["Buy milk"]
    assert (await owner.get("/api/widgets/library")).json() == []
    r = await owner.post(f"/api/widgets/{note['id']}/action", json={"action": "restore"})
    assert r.status_code == 409


async def test_put_away_nothing_rings_and_putting_back_sets_it_again(owner, sessionmaker):
    async with sessionmaker() as db:
        u = await me(owner, sessionmaker)
        u = await db.merge(u)
        u.timezone = "Asia/Kolkata"
        alarm = await add(db, u, "alarm", time="07:00", days="weekdays", now=NOW)
        once = await add(db, u, "reminder", text="Call mum", minutes=30, now=NOW)
        timer = await add(db, u, "timer", minutes=10, now=NOW)
        for w in (alarm, once, timer):
            put_away(w, NOW + timedelta(minutes=5))
            assert w.fires_at is None and w.removed_at
        assert timer.state["status"] == "paused" and timer.state["remaining_s"] == 300

        # Back the next morning: the weekday alarm is set for its next time, the reminder's
        # half hour went by while it was away.
        later = NOW + timedelta(days=1)
        for w in (alarm, once, timer):
            await restore(db, u, w, later)
        assert alarm.state["status"] == "set" and alarm.fires_at > later
        assert once.state["status"] == "done" and once.fires_at is None
        assert timer.state["status"] == "paused" and timer.removed_at is None
        assert sorted([alarm.slot, once.slot, timer.slot]) == [alarm.slot, once.slot, timer.slot]


async def test_the_screen_limit_counts_only_what_is_on_it(owner, sessionmaker):
    async with sessionmaker() as db:
        u = await db.merge(await me(owner, sessionmaker))
        notes = [await add(db, u, "note", now=NOW) for _ in range(MAX_WIDGETS)]
        put_away(notes[0], NOW)
        await add(db, u, "note", now=NOW)  # room again
        await db.commit()
        # Full, so the one in the library can't come back until something else goes.
        r = await owner.post(f"/api/widgets/{notes[0].id}/action", json={"action": "restore"})
        assert r.status_code == 409 and "library" in r.json()["detail"]


async def test_the_library_keeps_every_custom_widget_but_only_the_newest_built_ins(
    owner, sessionmaker
):
    async with sessionmaker() as db:
        u = await db.merge(await me(owner, sessionmaker))
        mine = await cw.save(db, u, "Pet", HTML, cw.check_manifest())
        put_away(mine, NOW - timedelta(days=30))
        for i in range(LIBRARY_KEEP + 3):
            w = await add(db, u, "note", now=NOW)
            put_away(w, NOW + timedelta(seconds=i))
            await db.flush()
            await prune_library(db, u.id)
        await db.commit()
        left = await db.scalar(
            select(func.count()).where(Widget.user_id == u.id, Widget.removed_at.is_not(None))
        )
    assert left == LIBRARY_KEEP + 1  # the newest notes and the pet
    library = (await owner.get("/api/widgets/library")).json()
    assert any(w["kind"] == "custom" for w in library)


async def test_a_custom_widget_deleted_for_good_takes_its_versions(owner, sessionmaker):
    async with sessionmaker() as db:
        u = await db.merge(await me(owner, sessionmaker))
        w = await cw.save(db, u, "Pet", HTML, cw.check_manifest())
        await cw.save(db, u, "Pet", HTML.replace("hi", "hey"), cw.check_manifest(), widget=w)
        await db.commit()
        wid = w.id
    r = await owner.post(f"/api/widgets/{wid}/action", json={"action": "forget"})
    assert r.status_code == 409  # still on the screen
    await owner.post(f"/api/widgets/{wid}/action", json={"action": "remove"})
    assert (await owner.get(f"/api/widgets/{wid}/frame")).status_code == 404
    r = await owner.post(f"/api/widgets/{wid}/action", json={"action": "forget"})
    assert r.status_code == 200
    async with sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(CustomWidget)) == 0
        assert await db.get(Widget, wid) is None


async def test_saving_a_new_version_of_a_put_away_widget_brings_it_back(owner, sessionmaker):
    async with sessionmaker() as db:
        u = await db.merge(await me(owner, sessionmaker))
        w = await cw.save(db, u, "Pet", HTML, cw.check_manifest())
        put_away(w, NOW)
        await cw.save(db, u, "Pet", HTML, cw.check_manifest(), widget=w)
        assert w.removed_at is None and w.state["version"] == 2


async def test_a_search_box_and_its_engine(owner):
    r = await owner.post("/api/widgets", json={"kind": "search"})
    assert r.status_code == 201
    w = r.json()
    assert w["title"] == "Search" and w["state"]["engine"] == "google"
    r = await owner.patch(f"/api/widgets/{w['id']}", json={"engine": "assistant"})
    assert r.json()["state"]["engine"] == "assistant"
    r = await owner.patch(f"/api/widgets/{w['id']}", json={"engine": "altavista"})
    assert r.status_code == 422
    note = (await owner.post("/api/widgets", json={"kind": "note"})).json()
    r = await owner.patch(f"/api/widgets/{note['id']}", json={"engine": "bing"})
    assert r.status_code == 409


async def test_a_widget_keeps_the_size_it_was_dragged_to(owner):
    w = (await owner.post("/api/widgets", json={"kind": "note"})).json()
    assert w["size"] is None
    r = await owner.patch(f"/api/widgets/{w['id']}", json={"size": {"w": 320.4, "h": 240}})
    assert r.json()["size"] == {"w": 320, "h": 240}
    r = await owner.patch(f"/api/widgets/{w['id']}", json={"size": {"w": 5000, "h": 240}})
    assert r.status_code == 422
    r = await owner.patch(f"/api/widgets/{w['id']}", json={"reset_size": True})
    assert r.json()["size"] is None


def test_the_page_and_its_manifest_have_to_agree():
    page = HTML.replace(
        "hi",
        "<script>nexus.fetch('https://api.open-meteo.com/v1/x');nexus.data('calendar.today');"
        "btn.onclick=()=>nexus.chat.fork()</script>",
    )
    problems = cw.check_uses(page, cw.check_manifest())
    assert any("hosts is empty" in p for p in problems)
    assert any("api.open-meteo.com" in p for p in problems)
    assert any("calendar.today" in p for p in problems)
    assert any("chat.fork" in p for p in problems)
    fine = cw.check_manifest(
        hosts=["api.open-meteo.com"], data=["calendar.today"], actions=["chat.fork"]
    )
    assert cw.check_uses(page, fine) == []
    assert "chat.fork" in cw.SHIM


async def test_app_actions_wait_for_approval_like_hosts(owner, sessionmaker):
    async with sessionmaker() as db:
        u = await db.merge(await me(owner, sessionmaker))
        w = await cw.save(db, u, "Fork", HTML, cw.check_manifest(actions=["chat.fork", "open"]))
        await db.commit()
        wid = w.id
        assert cw.pending(w.state)["actions"] == ["chat.fork", "open"]
    r = await owner.post(
        f"/api/widgets/{wid}/approve", json={"actions": ["chat.fork", "ask"], "hosts": []}
    )
    # Only what the manifest asks for counts.
    assert r.json()["state"]["approved"]["actions"] == ["chat.fork"]


async def chat_with_messages(app, owner) -> tuple[str, list[uuid.UUID]]:
    chat = (await owner.post("/api/chats", json={"title": "Trip to Goa"})).json()["id"]
    ids = []
    async with app.state.sessionmaker() as db:
        t = NOW
        for role, text, st in [
            (MessageRole.user, "Plan a trip", MessageStatus.complete),
            (MessageRole.assistant, "Here's a plan", MessageStatus.complete),
            (MessageRole.user, "Make it cheaper", MessageStatus.complete),
            (MessageRole.assistant, "", MessageStatus.streaming),
        ]:
            t += timedelta(seconds=1)
            m = Message(chat_id=uuid.UUID(chat), role=role, content=text, status=st, created_at=t)
            db.add(m)
            await db.flush()
            ids.append(m.id)
        await db.commit()
    return chat, ids


async def test_a_fork_copies_the_conversation_so_far_into_its_own_chat(owner, app):
    chat, ids = await chat_with_messages(app, owner)
    r = await owner.post(f"/api/chats/{chat}/fork", json={})
    assert r.status_code == 201
    fork = r.json()
    assert fork["id"] != chat and fork["title"] == "Trip to Goa (fork)"
    copied = (await owner.get(f"/api/chats/{fork['id']}/messages")).json()
    # The reply still being written stays behind.
    assert [m["content"] for m in copied] == ["Plan a trip", "Here's a plan", "Make it cheaper"]
    assert len((await owner.get(f"/api/chats/{chat}/messages")).json()) == 4

    # Up to a message; a fork of a fork isn't "(fork) (fork)".
    r = await owner.post(f"/api/chats/{fork['id']}/fork", json={})
    assert r.json()["title"] == "Trip to Goa (fork)"
    r = await owner.post(f"/api/chats/{chat}/fork", json={"until": str(ids[0])})
    copied = (await owner.get(f"/api/chats/{r.json()['id']}/messages")).json()
    assert [m["content"] for m in copied] == ["Plan a trip"]
    r = await owner.post(f"/api/chats/{chat}/fork", json={"until": str(uuid.uuid4())})
    assert r.status_code == 404
    async with app.state.sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(Chat)) == 4


async def test_other_people_cannot_fork_a_chat(owner, app, client_for, invite):
    chat, _ = await chat_with_messages(app, owner)
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    assert (await friend.post(f"/api/chats/{chat}/fork", json={})).status_code == 404
