import pytest
from sqlalchemy import select

from nexus_common import push
from nexus_common.models import PushSubscription

SUB = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
    "keys": {"p256dh": "BPkey", "auth": "authkey"},
}


@pytest.fixture
def push_on(app):
    s = app.state.settings
    public, private = push.generate_keys()
    s.vapid_public_key, s.vapid_private_key, s.vapid_subject = public, private, "mailto:o@x.y"
    yield s
    s.vapid_public_key = s.vapid_private_key = s.vapid_subject = ""


async def test_push_is_off_without_keys(owner):
    state = (await owner.get("/api/push")).json()
    assert state == {"enabled": False, "public_key": None, "devices": 0}
    assert (await owner.post("/api/push/subscriptions", json=SUB)).status_code == 409
    assert "push" not in (await owner.get("/api/me")).json()["channels"]


async def test_subscribe_test_and_unsubscribe(owner, push_on, sessionmaker, monkeypatch):
    sent = []

    def fake_send(settings, sub, data):
        sent.append((sub["endpoint"], data))
        return None

    monkeypatch.setattr(push, "_send", fake_send)
    assert (await owner.get("/api/push")).json()["public_key"] == push_on.vapid_public_key
    assert "push" in (await owner.get("/api/me")).json()["channels"]
    assert (await owner.post("/api/push/subscriptions", json=SUB)).status_code == 204
    # The same endpoint again updates the row.
    again = {**SUB, "keys": {"p256dh": "BPkey2", "auth": "a2"}}
    assert (await owner.post("/api/push/subscriptions", json=again)).status_code == 204
    assert (await owner.get("/api/push")).json()["devices"] == 1

    resp = await owner.post("/api/push/test")
    assert resp.json() == {"sent": 1}
    assert sent[0][0] == SUB["endpoint"] and "Notifications work" in sent[0][1]

    await owner.post("/api/push/unsubscribe", json={"endpoint": SUB["endpoint"]})
    assert (await owner.get("/api/push")).json()["devices"] == 0


async def test_gone_subscriptions_are_dropped(owner, push_on, sessionmaker, monkeypatch):
    monkeypatch.setattr(push, "_send", lambda settings, sub, data: 410)
    await owner.post("/api/push/subscriptions", json=SUB)
    assert (await owner.post("/api/push/test")).json() == {"sent": 0}
    async with sessionmaker() as db:
        assert (await db.scalars(select(PushSubscription))).all() == []


async def test_routines_accept_push_delivery(owner, push_on):
    resp = await owner.post(
        "/api/routines",
        json={
            "name": "Brief",
            "prompt": "Brief me.",
            "schedule": "FREQ=DAILY;BYHOUR=8;BYMINUTE=0",
            "delivery": ["email", "push"],
        },
    )
    assert resp.json()["delivery"] == ["push", "email"]


async def test_feedback_goes_to_owners(owner, client_for, invite, queue):
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    resp = await friend.post(
        "/api/feedback",
        json={"kind": "problem", "message": "The routine page is slow", "page": "/routines"},
    )
    assert resp.status_code == 201, resp.text
    fid = resp.json()["id"]
    assert [str(x) for x in queue.feedback] == [fid]
    assert (await friend.post("/api/feedback", json={"message": "x"})).status_code == 422
    assert (await friend.get("/api/admin/feedback")).status_code == 403

    listed = (await owner.get("/api/admin/feedback")).json()
    assert listed[0]["email"] == "friend@example.com" and listed[0]["kind"] == "problem"
    done = (await owner.post(f"/api/admin/feedback/{fid}/done")).json()
    assert done["done_at"]
    assert (await owner.get("/api/admin/feedback?open_only=true")).json() == []
