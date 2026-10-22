import asyncio
import json
from datetime import timedelta

from sqlalchemy import select, update

from nexus_common.events import user_channel
from nexus_common.models import AccessRequest, AuditLog, User, UserStatus, utcnow


async def refused(client_for, email: str = "newcomer@example.com"):
    """A browser whose sign-in was just refused for lack of an invite."""
    return await client_for(email, expect=403)


async def ask(client, note: str | None = "I'm on the research team") -> dict:
    resp = await client.post("/auth/access-request", json={"note": note})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_refused_sign_in_can_ask_for_access(owner, client_for, queue, sessionmaker):
    guest = await refused(client_for)
    state = (await guest.get("/auth/access-request")).json()
    assert state["email"] == "newcomer@example.com" and state["status"] == "can_request"

    state = await ask(guest)
    assert state["status"] == "pending" and state["requested_at"]
    async with sessionmaker() as db:
        req = await db.scalar(select(AccessRequest))
        assert req.email == "newcomer@example.com" and req.note == "I'm on the research team"
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "access.requested"))
        assert audit.target == str(req.id)
    assert queue.access_notices == [("requested", req.id)]


async def test_asking_again_updates_the_note_without_a_second_request(
    owner, client_for, queue, sessionmaker
):
    guest = await refused(client_for)
    await ask(guest, "first")
    state = await ask(guest, "second")
    assert state["status"] == "pending"
    async with sessionmaker() as db:
        rows = (await db.scalars(select(AccessRequest))).all()
    assert [r.note for r in rows] == ["second"]
    assert len(queue.access_notices) == 1


async def test_no_request_without_a_verified_address(owner, client_for):
    anon = await client_for()
    assert (await anon.get("/auth/access-request")).status_code == 404
    resp = await anon.post("/auth/access-request", json={"email": "someone@example.com"})
    assert resp.status_code == 404


async def test_signed_in_address_is_the_one_used(owner, client_for, sessionmaker):
    """The body can't name a different address: only the verified one counts."""
    guest = await refused(client_for, "real@example.com")
    resp = await guest.post(
        "/auth/access-request", json={"note": "hi", "email": "victim@example.com"}
    )
    assert resp.status_code == 200
    async with sessionmaker() as db:
        assert (await db.scalar(select(AccessRequest.email))) == "real@example.com"


async def test_note_is_cleaned_and_capped(owner, client_for, sessionmaker):
    guest = await refused(client_for)
    too_long = await guest.post("/auth/access-request", json={"note": "x" * 501})
    assert too_long.status_code == 422
    await ask(guest, "  hello\x07 there\nsecond line  ")
    async with sessionmaker() as db:
        assert (await db.scalar(select(AccessRequest.note))) == "hello there\nsecond line"


async def test_admins_list_and_approve(owner, client_for, queue, invite):
    guest = await refused(client_for)
    await ask(guest)

    listing = (await owner.get("/api/admin/access-requests")).json()
    assert listing["pending"] == 1 and listing["enabled"] is True
    assert listing["email_notifications"] is False
    [req] = listing["requests"]
    assert req["email"] == "newcomer@example.com" and req["previous"] == 0

    resp = await owner.post(
        f"/api/admin/access-requests/{req['id']}/approve",
        json={"role": "viewer", "budget_usd": "2"},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["request"]["status"] == "approved"
    assert out["request"]["decided_by_name"] == "owner@example.com"
    assert out["user"]["status"] == "invited" and out["user"]["role"] == "viewer"
    assert out["user"]["budget"]["max_usd"] == "2.00"
    assert queue.access_notices[-1] == ("approved", queue.access_notices[0][1])

    assert (await guest.get("/auth/access-request")).json()["status"] == "approved"
    joined = await client_for("newcomer@example.com")
    me = (await joined.get("/api/me")).json()
    assert me["status"] == "active" and me["role"] == "viewer"

    again = await owner.post(f"/api/admin/access-requests/{req['id']}/approve", json={})
    assert again.status_code == 409
    decided = (await owner.get("/api/admin/access-requests?status=decided")).json()
    assert [r["status"] for r in decided["requests"]] == ["approved"]


async def test_admin_cannot_approve_as_admin_and_members_cannot_see(owner, client_for, invite):
    await invite(owner, "admin@example.com", "admin")
    await invite(owner, "member@example.com", "member")
    admin = await client_for("admin@example.com")
    member = await client_for("member@example.com")
    guest = await refused(client_for)
    await ask(guest)

    assert (await member.get("/api/admin/access-requests")).status_code == 403
    [req] = (await admin.get("/api/admin/access-requests")).json()["requests"]
    url = f"/api/admin/access-requests/{req['id']}"
    assert (await admin.post(f"{url}/approve", json={"role": "admin"})).status_code == 403
    assert (await member.post(f"{url}/deny")).status_code == 403
    assert (await admin.post(f"{url}/approve", json={"role": "member"})).status_code == 200


async def test_deny_then_cooldown(owner, client_for, queue, sessionmaker):
    guest = await refused(client_for)
    await ask(guest)
    [req] = (await owner.get("/api/admin/access-requests")).json()["requests"]
    resp = await owner.post(f"/api/admin/access-requests/{req['id']}/deny")
    assert resp.status_code == 200 and resp.json()["status"] == "denied"
    # Denials aren't emailed.
    assert [k for k, _ in queue.access_notices] == ["requested"]

    state = (await guest.get("/auth/access-request")).json()
    assert state["status"] == "denied" and state["retry_after"]
    assert (await ask(guest))["status"] == "denied"
    assert (await guest.get("/auth/access-request")).json()["status"] == "denied"

    async with sessionmaker() as db:
        await db.execute(update(AccessRequest).values(decided_at=utcnow() - timedelta(days=8)))
        await db.commit()
    assert (await ask(guest, "please reconsider"))["status"] == "pending"
    [again] = (await owner.get("/api/admin/access-requests")).json()["requests"]
    assert again["previous"] == 1


async def test_requests_off_with_open_signup_or_setting(app, settings, owner, client_for):
    guest = await refused(client_for)
    for change in ({"access_requests_enabled": False}, {"signup_policy": "open"}):
        app.state.settings = settings.model_copy(update=change)
        try:
            assert (await guest.get("/auth/config")).json()["access_requests"] is False
            assert (await guest.post("/auth/access-request", json={})).status_code == 404
        finally:
            app.state.settings = settings


async def test_too_many_pending_is_refused(app, settings, owner, client_for):
    app.state.settings = settings.model_copy(update={"access_request_max_pending": 1})
    try:
        await ask(await refused(client_for, "a@example.com"))
        resp = await (await refused(client_for, "b@example.com")).post(
            "/auth/access-request", json={}
        )
    finally:
        app.state.settings = settings
    assert resp.status_code == 503


async def test_pending_count_on_me_and_live_to_admins(owner, client_for, redis, invite):
    await invite(owner, "member@example.com")
    member = await client_for("member@example.com")
    assert (await owner.get("/api/me")).json()["pending_access"] == 0
    assert (await member.get("/api/me")).json()["pending_access"] is None

    owner_id = (await owner.get("/api/me")).json()["id"]
    pubsub = redis.pubsub()
    await pubsub.subscribe(user_channel(owner_id))
    try:
        await ask(await refused(client_for))
        event = None
        for _ in range(20):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg:
                event = json.loads(msg["data"])
                break
            await asyncio.sleep(0.05)
    finally:
        await pubsub.aclose()
    assert event == {"type": "access.pending", "chat_id": None, "data": {"count": 1}}
    assert (await owner.get("/api/me")).json()["pending_access"] == 1


async def test_queue_down_does_not_lose_the_request(owner, client_for, queue, sessionmaker):
    queue.fail = True
    state = await ask(await refused(client_for))
    assert state["status"] == "pending"
    async with sessionmaker() as db:
        assert await db.scalar(select(AccessRequest.id)) is not None


async def test_revoke_unused_invite(owner, client_for, invite, sessionmaker):
    pending = await invite(owner, "later@example.com")
    resp = await owner.delete(f"/api/admin/users/{pending['id']}")
    assert resp.status_code == 204
    async with sessionmaker() as db:
        assert await db.scalar(select(User).where(User.email == "later@example.com")) is None
        assert await db.scalar(select(AuditLog).where(AuditLog.action == "user.invite_revoked"))

    used = await invite(owner, "used@example.com")
    await client_for("used@example.com")
    assert (await owner.delete(f"/api/admin/users/{used['id']}")).status_code == 409
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "used@example.com"))
        assert user.status == UserStatus.active


async def test_request_id_header(owner):
    resp = await owner.get("/api/me")
    assert len(resp.headers["x-request-id"]) == 32
    resp = await owner.get("/api/me", headers={"x-request-id": "trace-abc-12345"})
    assert resp.headers["x-request-id"] == "trace-abc-12345"
    resp = await owner.get("/api/me", headers={"x-request-id": "bad id\n"})
    assert resp.headers["x-request-id"] != "bad id\n"
