async def test_invited_user_joins_with_role_and_budget(invite, owner, client_for):
    invited = await invite(owner, "Ada@Example.com", "member", budget_usd="7.5")
    assert invited["status"] == "invited"
    assert invited["budget"]["max_usd"] == "7.50"

    ada = await client_for("ada@example.com")
    me = (await ada.get("/api/me")).json()
    assert me["role"] == "member" and me["status"] == "active"
    assert me["budget"]["max_usd"] == "7.50"


async def test_duplicate_invite_conflicts(invite, owner):
    await invite(owner, "ada@example.com")
    resp = await owner.post("/api/admin/users", json={"email": "ADA@example.com"})
    assert resp.status_code == 409


async def test_members_cannot_use_admin(invite, owner, client_for):
    await invite(owner, "m@example.com", "member")
    member = await client_for("m@example.com")
    assert (await member.get("/api/admin/users")).status_code == 403


async def test_admin_cannot_grant_admin_or_touch_owner(invite, owner, client_for):
    await invite(owner, "boss@example.com", "admin")
    admin = await client_for("boss@example.com")
    resp = await admin.post("/api/admin/users", json={"email": "x@example.com", "role": "admin"})
    assert resp.status_code == 403
    assert (await invite(admin, "y@example.com", "member"))["role"] == "member"

    users = (await admin.get("/api/admin/users")).json()
    owner_id = next(u["id"] for u in users if u["role"] == "owner")
    resp = await admin.patch(f"/api/admin/users/{owner_id}", json={"status": "disabled"})
    assert resp.status_code == 403


async def test_last_owner_cannot_be_demoted(invite, owner):
    me = (await owner.get("/api/me")).json()
    resp = await owner.patch(f"/api/admin/users/{me['id']}", json={"role": "admin"})
    assert resp.status_code == 409


async def test_disable_revokes_sessions_and_blocks_key(
    invite, owner, client_for, gateway, sessionmaker, settings
):
    from nexus_common.litellm import ensure_user_key
    from nexus_common.models import User

    target = await invite(owner, "m@example.com")
    member = await client_for("m@example.com")
    async with sessionmaker() as db:
        user = await db.get(User, target["id"])
        key = await ensure_user_key(db, settings, gateway, user)

    resp = await owner.patch(f"/api/admin/users/{target['id']}", json={"status": "disabled"})
    assert resp.status_code == 200 and resp.json()["status"] == "disabled"
    assert (await member.get("/api/me")).status_code == 401
    assert gateway.keys[key]["blocked"] is True

    resp = await owner.patch(f"/api/admin/users/{target['id']}", json={"budget_usd": "12"})
    assert resp.json()["budget"]["max_usd"] == "12.00"
    assert str(gateway.keys[key]["max_usd"]) == "12.00"


async def test_invited_user_cannot_be_activated_by_admin(invite, owner):
    target = await invite(owner, "m@example.com")
    resp = await owner.patch(f"/api/admin/users/{target['id']}", json={"status": "active"})
    assert resp.status_code == 422
