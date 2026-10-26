from sqlalchemy import select

from nexus_common.models import Notification, NotificationKind, User


async def _add(sessionmaker, email: str, title: str) -> None:
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        db.add(Notification(user_id=user.id, kind=NotificationKind.result, title=title))
        await db.commit()


async def test_tray_lists_own_notices_and_marks_them_read(owner, client_for, invite, sessionmaker):
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    await owner.get("/api/me")
    await _add(sessionmaker, "owner@example.com", "Rocket team finished")
    await _add(sessionmaker, "owner@example.com", "Essay team finished")
    await _add(sessionmaker, "friend@example.com", "Friend's team finished")

    tray = (await owner.get("/api/notifications")).json()
    assert tray["unread"] == 3
    assert {n["title"] for n in tray["items"]} == {
        "Rocket team finished",
        "Essay team finished",
        "Invitation created for friend@example.com",
    }
    first = tray["items"][0]["id"]

    assert (await owner.post("/api/notifications/read", json={"ids": [first]})).status_code == 204
    assert (await owner.get("/api/notifications")).json()["unread"] == 2
    # Someone else's ids are ignored; "all" only reaches the caller's own.
    await friend.post("/api/notifications/read", json={})
    assert (await owner.get("/api/notifications")).json()["unread"] == 2
    await owner.post("/api/notifications/read", json={})
    assert (await owner.get("/api/notifications")).json()["unread"] == 0
