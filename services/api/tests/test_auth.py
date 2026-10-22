from sqlalchemy import select

from nexus_common.models import AuditLog


async def test_first_user_becomes_owner(client_for):
    owner = await client_for("Owner@Example.com")
    me = (await owner.get("/api/me")).json()
    assert me["email"] == "owner@example.com"
    assert me["role"] == "owner"
    assert me["status"] == "active"
    assert me["budget"]["max_usd"] == "50.00"


async def test_uninvited_user_is_refused(owner, client_for):
    stranger = await client_for("stranger@example.com", expect=403)
    assert (await stranger.get("/api/me")).status_code == 401


async def test_wrong_dev_token_is_rejected(app, client_for):
    client = await client_for()
    resp = await client.post("/auth/dev-login", json={"email": "a@example.com", "token": "nope"})
    assert resp.status_code == 401


async def test_dev_login_disabled_returns_404(app, client_for, settings):
    client = await client_for()
    app.state.settings = settings.model_copy(update={"dev_login_enabled": False})
    try:
        resp = await client.post(
            "/auth/dev-login", json={"email": "a@example.com", "token": settings.dev_login_token}
        )
    finally:
        app.state.settings = settings
    assert resp.status_code == 404


async def test_auth_config(client_for):
    client = await client_for()
    assert (await client.get("/auth/config")).json() == {
        "oidc_enabled": False,
        "dev_login_enabled": True,
    }


async def test_logout_revokes_session(owner):
    assert (await owner.post("/auth/logout")).status_code == 204
    assert (await owner.get("/api/me")).status_code == 401


async def test_session_cookie_is_httponly(client_for, settings, app):
    client = await client_for()
    resp = await client.post(
        "/auth/dev-login", json={"email": "o@example.com", "token": settings.dev_login_token}
    )
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


async def test_oidc_identity_mismatch_is_refused(sessionmaker, settings):
    from nexus_api.users import SignInRefused, resolve_sign_in

    async with sessionmaker() as db:
        await resolve_sign_in(db, settings, email="o@example.com", sub="google-1")
        await db.commit()
    async with sessionmaker() as db:
        try:
            await resolve_sign_in(db, settings, email="o@example.com", sub="google-2")
        except SignInRefused as exc:
            assert exc.reason == "identity_mismatch"
        else:
            raise AssertionError("expected SignInRefused")


async def test_open_signup_joins_as_member(sessionmaker, settings):
    from nexus_api.users import resolve_sign_in

    open_settings = settings.model_copy(update={"signup_policy": "open"})
    async with sessionmaker() as db:
        await resolve_sign_in(db, open_settings, email="o@example.com")
        newcomer = await resolve_sign_in(db, open_settings, email="n@example.com")
        assert newcomer.role == "member"
        actions = (await db.scalars(select(AuditLog.action))).all()
        assert actions.count("user.joined") == 2


async def test_users_name_their_own_assistant(owner, client_for, invite):
    assert (await owner.get("/api/me")).json()["assistant_name"] is None
    resp = await owner.patch("/api/me", json={"assistant_name": "  Orion   Prime "})
    assert resp.status_code == 200
    assert resp.json()["assistant_name"] == "Orion Prime"

    await invite(owner, "ada@example.com")
    ada = await client_for("ada@example.com")
    assert (await ada.get("/api/me")).json()["assistant_name"] is None
    await ada.patch("/api/me", json={"assistant_name": "Friday"})
    assert (await owner.get("/api/me")).json()["assistant_name"] == "Orion Prime"

    for bad in ["", "   ", "<b>x</b>", "-dash", "x" * 25]:
        assert (await owner.patch("/api/me", json={"assistant_name": bad})).status_code == 422
