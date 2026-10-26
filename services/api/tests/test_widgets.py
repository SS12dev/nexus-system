"""Widgets: adding from the dock, a timer's buttons, alarms and reminders in the person's own
time zone, the worker ringing what's due, and nobody touching another person's widgets."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from nexus_common.models import Notification, NotificationKind, User, Widget
from nexus_common.widgets import WidgetError, act, add, fire_due, next_at_time

NOW = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)  # 23:30 in Kolkata


def user(tz="Asia/Kolkata") -> User:
    import uuid

    return User(id=uuid.uuid4(), email="x@y.z", timezone=tz)


def test_an_alarm_rings_at_the_next_local_clock_time():
    # 07:00 in Kolkata is tomorrow, 01:30 UTC.
    assert next_at_time("07:00", "Asia/Kolkata", NOW) == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)
    assert next_at_time("23:45", "Asia/Kolkata", NOW) == datetime(2026, 10, 25, 18, 15, tzinfo=UTC)
    with pytest.raises(WidgetError):
        next_at_time("25:00", "UTC", NOW)


async def test_timer_pause_resume_add_time_and_snooze(owner, sessionmaker):
    await owner.get("/api/me")
    async with sessionmaker() as db:
        u = await db.scalar(select(User).where(User.email == "owner@example.com"))
        w = await add(db, u, "timer", minutes=5, now=NOW)
        assert w.title == "5 min timer" and w.fires_at == NOW + timedelta(minutes=5)

        act(w, "pause", now=NOW + timedelta(minutes=2))
        assert w.state["status"] == "paused" and w.state["remaining_s"] == 180 and not w.fires_at
        act(w, "resume", now=NOW + timedelta(minutes=10))
        assert w.fires_at == NOW + timedelta(minutes=13)
        act(w, "add_time", minutes=1, now=NOW + timedelta(minutes=11))
        assert w.fires_at == NOW + timedelta(minutes=14)
        with pytest.raises(WidgetError):
            act(w, "snooze", now=NOW)  # not ringing
        w.state = {**w.state, "status": "ringing"}
        act(w, "snooze", now=NOW + timedelta(minutes=14))
        assert w.fires_at == NOW + timedelta(minutes=19) and w.state["status"] == "running"
        act(w, "dismiss", now=NOW)
        assert w.state["status"] == "done" and w.fires_at is None


async def test_reminders_need_a_time_and_a_daily_one_repeats(owner, sessionmaker):
    await owner.get("/api/me")
    async with sessionmaker() as db:
        u = await db.scalar(select(User).where(User.email == "owner@example.com"))
        u.timezone = "Asia/Kolkata"
        with pytest.raises(WidgetError):
            await add(db, u, "reminder", text="Job hunting", now=NOW)
        with pytest.raises(WidgetError):
            await add(db, u, "reminder", text="Job hunting", when="2026-10-25T10:00", now=NOW)
        w = await add(db, u, "reminder", text="Job hunting", time="21:00", days="daily", now=NOW)
        # 21:00 Kolkata = 15:30 UTC, and today's has passed.
        assert w.fires_at == datetime(2026, 10, 26, 15, 30, tzinfo=UTC)
        assert w.state["repeat"] == "FREQ=DAILY;BYHOUR=21;BYMINUTE=0"


async def test_the_worker_rings_due_widgets_and_moves_repeats_on(
    owner, sessionmaker, redis, settings
):
    await owner.get("/api/me")
    async with sessionmaker() as db:
        u = await db.scalar(select(User).where(User.email == "owner@example.com"))
        t = await add(db, u, "timer", minutes=1, now=NOW)
        r = await add(db, u, "reminder", text="Stretch", time="21:00", days="daily", now=NOW)
        later = await add(db, u, "timer", minutes=60, now=NOW)
        await db.commit()
        r_first = r.fires_at

    async with sessionmaker() as db:
        assert await fire_due(db, redis, settings, now=NOW + timedelta(minutes=2)) == 1
        assert await fire_due(db, redis, settings, now=NOW + timedelta(minutes=2)) == 0
    async with sessionmaker() as db:
        t, later = await db.get(Widget, t.id), await db.get(Widget, later.id)
        assert t.state["status"] == "ringing" and t.fires_at is None
        assert later.state["status"] == "running"
        assert await fire_due(db, redis, settings, now=r_first + timedelta(seconds=5)) == 2
    async with sessionmaker() as db:
        r = await db.get(Widget, r.id)
        assert r.state["status"] == "ringing" and r.fires_at == r_first + timedelta(days=1)
        kinds = sorted(n.kind for n in (await db.scalars(select(Notification))).all())
    assert kinds == [NotificationKind.reminder, NotificationKind.timer, NotificationKind.timer]


async def test_dock_api_and_isolation(owner, client_for, invite):
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    resp = await owner.post("/api/widgets", json={"kind": "timer", "minutes": 5, "title": "Pasta"})
    assert resp.status_code == 201, resp.text
    w = resp.json()
    assert w["title"] == "Pasta" and w["state"]["status"] == "running"
    note = (await owner.post("/api/widgets", json={"kind": "note", "text": "milk"})).json()
    assert (await owner.post("/api/widgets", json={"kind": "timer"})).status_code == 422

    paused = await owner.post(f"/api/widgets/{w['id']}/action", json={"action": "pause"})
    assert paused.json()["state"]["status"] == "paused"
    edited = await owner.patch(f"/api/widgets/{note['id']}", json={"text": "milk, eggs"})
    assert edited.json()["state"]["text"] == "milk, eggs"
    assert (await owner.patch(f"/api/widgets/{w['id']}", json={"text": "x"})).status_code == 409

    assert (await friend.get("/api/widgets")).json() == []
    stolen = await friend.post(f"/api/widgets/{w['id']}/action", json={"action": "remove"})
    assert stolen.status_code == 404
    await owner.post(f"/api/widgets/{w['id']}/action", json={"action": "remove"})
    assert [x["kind"] for x in (await owner.get("/api/widgets")).json()] == ["note"]


async def test_a_widget_dragged_anywhere_keeps_its_place(owner, client_for, invite):
    w = (await owner.post("/api/widgets", json={"kind": "note", "text": "milk"})).json()
    assert w["pos"] is None
    placed = await owner.patch(f"/api/widgets/{w['id']}", json={"pos": {"x": 0.25, "y": 1}})
    assert placed.json()["pos"] == {"x": 0.25, "y": 1}
    assert (await owner.get("/api/widgets")).json()[0]["pos"] == {"x": 0.25, "y": 1}
    off = await owner.patch(f"/api/widgets/{w['id']}", json={"pos": {"x": 1.5, "y": 0}})
    assert off.status_code == 422
    docked = await owner.patch(f"/api/widgets/{w['id']}", json={"dock": True})
    assert docked.json()["pos"] is None

    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    moved = await friend.patch(f"/api/widgets/{w['id']}", json={"pos": {"x": 0, "y": 0}})
    assert moved.status_code == 404


async def test_a_widget_dropped_from_the_menu_lands_where_it_was_dropped(owner):
    resp = await owner.post(
        "/api/widgets", json={"kind": "stopwatch", "pos": {"x": 0.4, "y": 0.12345678}}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["pos"] == {"x": 0.4, "y": 0.1235}
    off = await owner.post("/api/widgets", json={"kind": "note", "pos": {"x": 2, "y": 0}})
    assert off.status_code == 422


async def test_alarms_ring_with_a_tune_and_can_be_moved_to_a_new_time(owner):
    alarm = await owner.post(
        "/api/widgets", json={"kind": "alarm", "time": "07:00", "tune": "birdsong"}
    )
    w = alarm.json()
    assert w["state"]["tune"] == "birdsong" and w["title"] == "Alarm 07:00"
    bad = await owner.post("/api/widgets", json={"kind": "alarm", "time": "07:00", "tune": "kazoo"})
    assert bad.status_code == 422

    retuned = await owner.patch(f"/api/widgets/{w['id']}", json={"tune": "harp"})
    assert retuned.json()["state"]["tune"] == "harp"
    await owner.post(f"/api/widgets/{w['id']}/action", json={"action": "pause"})
    moved = await owner.patch(
        f"/api/widgets/{w['id']}", json={"time": "06:30", "days": ["MO", "WE", "FR"]}
    )
    got = moved.json()
    assert got["title"] == "Alarm 06:30" and got["state"]["status"] == "set"
    assert got["state"]["repeat"] == "FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=6;BYMINUTE=30"
    assert got["fires_at"] is not None
    once = await owner.patch(f"/api/widgets/{w['id']}", json={"time": "06:45", "days": None})
    assert once.json()["state"]["repeat"] is None
    assert (await owner.patch(f"/api/widgets/{w['id']}", json={"time": "6pm"})).status_code == 422

    note = (await owner.post("/api/widgets", json={"kind": "note"})).json()
    assert (
        await owner.patch(f"/api/widgets/{note['id']}", json={"tune": "bell"})
    ).status_code == 409
    assert (
        await owner.patch(f"/api/widgets/{note['id']}", json={"time": "07:00"})
    ).status_code == 422


def test_the_app_and_the_server_know_the_same_tunes():
    import re
    from pathlib import Path

    from nexus_common.widgets import DEFAULT_TUNE, TUNES

    web = Path(__file__).parents[3] / "apps/web/src/lib/tunes.ts"
    ids = re.findall(r'\{ id: "(\w+)"', web.read_text())
    assert tuple(ids) == TUNES and DEFAULT_TUNE == TUNES[0]
