"""Phase 5: rate limits, cross-site checks, headers, the Admin page APIs and device sign-out."""

from datetime import timedelta

from sqlalchemy import select

from jarvis_common.models import (
    AuditLog,
    Backup,
    BackupStatus,
    Session,
    utcnow,
)


async def _chat(client) -> str:
    return (await client.post("/api/chats", json={})).json()["id"]


async def test_security_headers(owner):
    resp = await owner.get("/api/me")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in resp.headers  # plain http here


async def test_cross_site_writes_are_refused(owner):
    evil = {"origin": "https://evil.example"}
    assert (await owner.post("/api/chats", json={}, headers=evil)).status_code == 403
    same = {"origin": "http://testserver"}
    assert (await owner.post("/api/chats", json={}, headers=same)).status_code == 201
    fetch = {"sec-fetch-site": "cross-site"}
    assert (await owner.post("/api/chats", json={}, headers=fetch)).status_code == 403
    # Reads aren't affected.
    assert (await owner.get("/api/chats", headers=evil)).status_code == 200


async def test_message_rate_limit(app, owner, queue):
    app.state.settings = app.state.settings.model_copy(update={"rate_limit_messages_per_minute": 2})
    chat = await _chat(owner)
    for _ in range(2):
        resp = await owner.post(f"/api/chats/{chat}/messages", json={"content": "hi"})
        assert resp.status_code == 202, resp.text
        # Let the next one through the "still answering" check.
        await _finish_turns(app)
    resp = await owner.post(f"/api/chats/{chat}/messages", json={"content": "hi"})
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1
    assert len(queue.turns) == 2


async def _finish_turns(app):
    from sqlalchemy import update

    from jarvis_common.models import Message, MessageStatus

    async with app.state.sessionmaker() as db:
        await db.execute(update(Message).values(status=MessageStatus.complete))
        await db.commit()


async def test_sign_in_rate_limit_per_ip(app):
    app.state.settings = app.state.settings.model_copy(update={"rate_limit_auth_per_minute": 3})
    # The middleware keeps the settings it was built with; rebuild with the lower limit.
    from jarvis_api.main import create_app

    strict = create_app(
        app.state.settings,
        queue=app.state.queue,
        storage=app.state.storage,
        embedder=app.state.embedder.inner,
    )
    import httpx

    async with strict.router.lifespan_context(strict):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=strict), base_url="http://testserver"
        ) as c:
            body = {"email": "owner@example.com", "token": "wrong"}
            codes = [(await c.post("/auth/dev-login", json=body)).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


async def test_maintenance_blocks_members_not_admins(owner, invite, client_for):
    await invite(owner, "m@example.com")
    member = await client_for("m@example.com")
    resp = await owner.put(
        "/api/admin/maintenance", json={"enabled": True, "message": "Upgrading, back at 6"}
    )
    assert resp.status_code == 200
    assert (await member.get("/api/me")).json()["notice"] == "Upgrading, back at 6"

    chat = await _chat(member)
    resp = await member.post(f"/api/chats/{chat}/messages", json={"content": "hi"})
    assert resp.status_code == 503 and resp.json()["detail"] == "Upgrading, back at 6"
    own = await _chat(owner)
    assert (
        await owner.post(f"/api/chats/{own}/messages", json={"content": "hi"})
    ).status_code == 202

    await owner.put("/api/admin/maintenance", json={"enabled": False})
    assert (await member.get("/api/me")).json()["notice"] is None
    assert (
        await member.post(f"/api/chats/{chat}/messages", json={"content": "hi"})
    ).status_code == 202
    assert (await member.put("/api/admin/maintenance", json={"enabled": True})).status_code == 403


async def test_status_page(app, owner, redis):
    # A live orchestrator writes this heartbeat; the doc-worker has none, so it shows as down.
    await redis.set("arq:queue:health-check", "Oct-20 j_complete=1 j_failed=0 j_ongoing=0")
    await owner.get("/api/me")
    data = (await owner.get("/api/admin/status")).json()
    by_name = {s["name"]: s for s in data["services"]}
    assert by_name["Postgres"]["ok"] and by_name["Valkey (cache and queues)"]["ok"]
    assert by_name["Orchestrator worker"]["ok"]
    assert not by_name["Document worker"]["ok"]
    assert data["people"]["active"] == 1
    assert data["backup"]["stale"] is True
    # The request above was counted.
    assert sum(m["requests"] for m in data["traffic"]) >= 1
    assert len(data["traffic"]) == 60


async def test_usage_and_audit(owner, invite, client_for, app):
    await invite(owner, "m@example.com")
    member = await client_for("m@example.com")
    chat = await _chat(member)
    await member.post(f"/api/chats/{chat}/messages", json={"content": "hello"})

    usage = (await owner.get("/api/admin/usage?days=7")).json()
    m = next(p for p in usage["people"] if p["email"] == "m@example.com")
    assert m["turns"] == 1
    assert len(usage["series"]) == 7 and usage["series"][-1]["turns"] == 1

    audit = (await owner.get("/api/admin/audit")).json()
    actions = [e["action"] for e in audit["entries"]]
    assert "user.invited" in actions and "user.signed_in" in actions
    invited = next(e for e in audit["entries"] if e["action"] == "user.invited")
    assert invited["actor"] == "owner@example.com" and invited["target_user"] == "m@example.com"
    only_users = (await owner.get("/api/admin/audit?action=user.inv")).json()["entries"]
    assert {e["action"] for e in only_users} == {"user.invited"}
    assert (await member.get("/api/admin/usage")).status_code == 403


async def test_backup_request_is_queued_once(owner, sessionmaker):
    first = await owner.post("/api/admin/backups")
    assert first.status_code == 202 and first.json()["status"] == "requested"
    again = await owner.post("/api/admin/backups")
    assert again.json()["id"] == first.json()["id"]

    async with sessionmaker() as db:
        db.add(
            Backup(
                kind="scheduled",
                status=BackupStatus.ok,
                name="2026-10-20T2130Z",
                size_bytes=1234,
                finished_at=utcnow() - timedelta(hours=1),
            )
        )
        await db.commit()
    listed = (await owner.get("/api/admin/backups")).json()
    assert len(listed) == 2
    status = (await owner.get("/api/admin/status")).json()
    assert status["backup"]["stale"] is False
    assert status["backup"]["last_ok"]["name"] == "2026-10-20T2130Z"


async def test_my_sessions_and_sign_out_others(client_for, sessionmaker):
    laptop = await client_for("owner@example.com")
    phone = await client_for("owner@example.com")
    sessions = (await laptop.get("/api/me/sessions")).json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1

    assert (await laptop.post("/api/me/sessions/sign-out-others")).status_code == 204
    assert (await phone.get("/api/me")).status_code == 401
    assert (await laptop.get("/api/me")).status_code == 200

    phone = await client_for("owner@example.com")
    other = next(s for s in (await laptop.get("/api/me/sessions")).json() if not s["current"])
    assert (await laptop.delete(f"/api/me/sessions/{other['id']}")).status_code == 204
    assert (await phone.get("/api/me")).status_code == 401


async def test_admin_signs_someone_out(owner, invite, client_for, sessionmaker):
    target = await invite(owner, "m@example.com")
    member = await client_for("m@example.com")
    assert (await owner.post(f"/api/admin/users/{target['id']}/sign-out")).status_code == 204
    assert (await member.get("/api/me")).status_code == 401
    async with sessionmaker() as db:
        assert await db.scalar(select(AuditLog).where(AuditLog.action == "user.signed_out"))
        member_id = target["id"]
        live = await db.scalar(
            select(Session).where(Session.revoked_at.is_(None), Session.user_id == member_id)
        )
        assert live is None


async def test_admin_page_is_owner_only(owner, invite, client_for):
    await invite(owner, "a@example.com", role="admin")
    admin = await client_for("a@example.com")
    # Admins still manage people...
    assert (await admin.get("/api/admin/users")).status_code == 200
    # ...but the Admin page is the owner's alone.
    for path in ("status", "usage", "audit", "backups", "maintenance"):
        assert (await admin.get(f"/api/admin/{path}")).status_code == 403, path
    assert (await admin.post("/api/admin/backups")).status_code == 403
    assert (await admin.put("/api/admin/maintenance", json={"enabled": True})).status_code == 403


async def test_admin_stats_filter_by_person(owner, invite, client_for):
    a = await invite(owner, "a@example.com")
    b = await invite(owner, "b@example.com")
    alice = await client_for("a@example.com")
    bob = await client_for("b@example.com")
    for c in (alice, bob):
        chat = await _chat(c)
        await c.post(f"/api/chats/{chat}/messages", json={"content": "hello"})

    def q(*users):
        return "&".join(f"user_id={u['id']}" for u in users)

    everyone = (await owner.get("/api/admin/status")).json()
    assert everyone["last_24h"]["turns"] == 2 and everyone["people"]["active"] == 3
    one = (await owner.get(f"/api/admin/status?{q(a)}")).json()
    assert one["last_24h"]["turns"] == 1 and one["people"]["active"] == 1

    usage = (await owner.get(f"/api/admin/usage?{q(a)}")).json()
    assert [p["email"] for p in usage["people"]] == ["a@example.com"]
    assert usage["series"][-1]["turns"] == 1
    both = (await owner.get(f"/api/admin/usage?{q(a, b)}")).json()
    assert {p["email"] for p in both["people"]} == {"a@example.com", "b@example.com"}
    assert both["series"][-1]["turns"] == 2

    audit = (await owner.get(f"/api/admin/audit?{q(b)}")).json()["entries"]
    assert audit and all(
        e["actor"] == "b@example.com" or e.get("target_user") == "b@example.com" for e in audit
    )
