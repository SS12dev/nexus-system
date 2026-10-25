"""The main screen's saved layout: switching it by hand saves it for every device and marks it
as the person's own choice; closing a file keeps it closed; moving a widget by hand renumbers
the dock."""

from sqlalchemy import select

from nexus_common import screen as sc
from nexus_common.models import AuditLog, ScreenState, User


async def test_switching_mode_by_hand_is_saved_and_wins(owner, sessionmaker):
    r = await owner.get("/api/screen")
    assert r.status_code == 200 and r.json()["mode"] == "orb" and r.json()["focus"] is None

    r = await owner.put("/api/screen/mode", json={"mode": "dashboard"})
    assert r.json()["mode"] == "dashboard"
    assert (await owner.get("/api/screen")).json()["mode"] == "dashboard"
    assert (await owner.put("/api/screen/mode", json={"mode": "grid"})).status_code == 422

    async with sessionmaker() as db:
        u = await db.scalar(select(User).where(User.email == "owner@example.com"))
        state = await db.get(ScreenState, u.id)
        assert sc.overridden(state, "mode")
        log = await db.scalar(select(AuditLog).where(AuditLog.action == "screen.mode"))
        assert log.data["by"] == "user" and log.target == "dashboard"


async def test_closing_a_file_keeps_it_closed(owner, sessionmaker):
    await owner.get("/api/screen")
    async with sessionmaker() as db:
        u = await db.scalar(select(User).where(User.email == "owner@example.com"))
        state = await db.get(ScreenState, u.id)
        state.focus = {"kind": "file", "id": "f1", "chat_id": None}
        await db.commit()
    r = await owner.post("/api/screen/close", json={"kind": "file", "id": "f1"})
    assert r.json()["focus"] is None
    async with sessionmaker() as db:
        state = await db.get(ScreenState, u.id)
        assert sc.overridden(state, sc.override_key("focus", "file", "f1"))


async def test_moving_a_widget_by_hand_renumbers_the_dock(owner, sessionmaker):
    ids = []
    for text in ("a", "b", "c"):
        r = await owner.post("/api/widgets", json={"kind": "note", "text": text})
        ids.append(r.json()["id"])
    r = await owner.patch(f"/api/widgets/{ids[2]}", json={"slot": 1})
    assert r.status_code == 200 and r.json()["slot"] == 0
    listed = [w["state"]["text"] for w in (await owner.get("/api/widgets")).json()]
    assert listed == ["c", "a", "b"]
    assert (await owner.patch(f"/api/widgets/{ids[0]}", json={"slot": 0})).status_code == 422
    async with sessionmaker() as db:
        u = await db.scalar(select(User).where(User.email == "owner@example.com"))
        state = await db.get(ScreenState, u.id)
        assert sc.overridden(state, sc.override_key("move", ids[2]))
