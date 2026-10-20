"""Private apps' sign-in (forwardAuth): the round trip through Jarvis, one-time tokens (valid,
expired, for another app, reused), the app's own cookie, who may open an app, and that the
Jarvis session cookie never reaches or opens an app."""

import hashlib
import hmac
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

import jarvis_api.routes.app_access as access
from jarvis_common.models import Project

HOST = "2048.apps.localhost"


async def add_app(sessionmaker, user_id, slug="2048", **kw) -> uuid.UUID:
    async with sessionmaker() as db:
        p = Project(user_id=uuid.UUID(user_id), workbench_id=f"w-{slug}", name=slug,
                    app_slug=slug, app_state="running",
                    live_url=f"http://{slug}.apps.localhost:8080", **kw)  # fmt: skip
        db.add(p)
        await db.commit()
        return p.id


def forwarded(host=HOST, uri="/", method="GET", **headers) -> dict:
    """What Traefik sends forwardAuth: the visitor's request, described."""
    return {"X-Forwarded-Host": host, "X-Forwarded-Uri": uri, "X-Forwarded-Method": method,
            "X-Forwarded-Proto": "https", **headers}  # fmt: skip


@pytest.fixture
async def visitor(client_for):
    """A browser on the app's host: it never has Jarvis's session cookie."""
    return await client_for(None)


@pytest.fixture
async def me(owner) -> str:
    return (await owner.get("/api/me")).json()["id"]


async def token_for(client, app_id, next_path="/play") -> str:
    resp = await client.get("/auth/app/start", params={"app": str(app_id), "next": next_path})
    assert resp.status_code == 303, resp.text
    location = urlsplit(resp.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        f"http://{HOST}:8080/_jarvis/auth"
    )
    return parse_qs(location.query)["token"][0]


async def test_the_round_trip(owner, me, visitor, sessionmaker, settings):
    app_id = await add_app(sessionmaker, me)
    # 1. No app cookie: off to Jarvis's own host, remembering where they were going.
    resp = await visitor.get("/auth/app/check", headers=forwarded(uri="/play?level=2"))
    assert resp.status_code == 302
    start = urlsplit(resp.headers["location"])
    assert f"{start.scheme}://{start.netloc}" == settings.jarvis_url
    assert parse_qs(start.query) == {"app": [str(app_id)], "next": ["/play?level=2"]}
    # 2. Signed in to Jarvis: back to the app with a one-time token.
    token = await token_for(owner, app_id, "/play?level=2")
    # 3. The app's host swaps it for its own cookie, and goes on to the page.
    uri = f"/_jarvis/auth?token={token}"
    resp = await visitor.get("/auth/app/check", headers=forwarded(uri=uri))
    assert resp.status_code == 302
    # Absolute, on the app: Traefik reads a bare path as one on the API's own address.
    assert resp.headers["location"] == f"http://{HOST}:8080/play?level=2"
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith("jarvis_app=") and "HttpOnly" in cookie and "Secure" in cookie
    # Host-only: no Domain, so it never reaches other apps or Jarvis.
    assert "domain" not in cookie.lower()
    value = resp.cookies["jarvis_app"]
    # 4. With the cookie, requests go through.
    ok = await visitor.get(
        "/auth/app/check", headers=forwarded(uri="/play"), cookies={"jarvis_app": value}
    )
    assert ok.status_code == 200
    # The same token can't be used twice.
    again = await visitor.get("/auth/app/check", headers=forwarded(uri=uri))
    assert again.status_code == 403 and "already used" in again.text


async def test_an_expired_token(owner, me, visitor, sessionmaker, monkeypatch):
    app_id = await add_app(sessionmaker, me)
    token = await token_for(owner, app_id)
    monkeypatch.setattr(access, "TOKEN_SECONDS", -1)
    resp = await visitor.get(
        "/auth/app/check", headers=forwarded(uri=f"/_jarvis/auth?token={token}")
    )
    assert resp.status_code == 403 and "expired" in resp.text
    assert "set-cookie" not in resp.headers


async def test_a_token_or_cookie_for_another_app(owner, me, visitor, sessionmaker):
    app_id = await add_app(sessionmaker, me)
    await add_app(sessionmaker, me, slug="snake")
    snake = "snake.apps.localhost"
    token = await token_for(owner, app_id)
    resp = await visitor.get(
        "/auth/app/check", headers=forwarded(host=snake, uri=f"/_jarvis/auth?token={token}")
    )
    assert resp.status_code == 403 and "another app" in resp.text
    # A cookie made for 2048 doesn't open snake.
    token = await token_for(owner, app_id)
    made = await visitor.get(
        "/auth/app/check", headers=forwarded(uri=f"/_jarvis/auth?token={token}")
    )
    cookie = {"jarvis_app": made.cookies["jarvis_app"]}
    assert (
        await visitor.get("/auth/app/check", headers=forwarded(host=snake), cookies=cookie)
    ).status_code == 302
    # A forged or tampered one opens nothing.
    forged = {"jarvis_app": made.cookies["jarvis_app"][:-2] + "xx"}
    assert (
        await visitor.get("/auth/app/check", headers=forwarded(), cookies=forged)
    ).status_code == 302


async def test_the_jarvis_session_cookie_never_opens_an_app(
    owner, me, visitor, sessionmaker, settings
):
    """The session cookie has no Domain, so app hosts never get it; and even sent by hand, the
    check ignores it and never hands one out."""
    app_id = await add_app(sessionmaker, me)
    session = owner.cookies.get(settings.session_cookie_name)
    assert session
    resp = await visitor.get(
        "/auth/app/check",
        headers=forwarded(),
        cookies={settings.session_cookie_name: session},
    )
    assert resp.status_code == 302 and "set-cookie" not in resp.headers
    token = await token_for(owner, app_id)
    resp = await visitor.get(
        "/auth/app/check", headers=forwarded(uri=f"/_jarvis/auth?token={token}")
    )
    assert settings.session_cookie_name not in resp.headers.get("set-cookie", "")


async def test_signing_in_sets_a_host_only_session_cookie(client_for, settings):
    client = await client_for(None)
    resp = await client.post(
        "/auth/dev-login", json={"email": "owner@example.com", "token": settings.dev_login_token}
    )
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith(f"{settings.session_cookie_name}=")
    assert "domain" not in cookie.lower()


async def test_who_may_open_an_app(owner, me, invite, client_for, sessionmaker):
    info = await invite(owner, "priya@example.com")
    priya = await client_for("priya@example.com")
    mine = await add_app(sessionmaker, me)
    hers = await add_app(sessionmaker, info["id"], slug="snake-priya")
    # A member can't get into someone else's app...
    resp = await priya.get("/auth/app/start", params={"app": str(mine)})
    assert resp.status_code == 403 and "make it public" in resp.text
    # ...but can into her own, and Jarvis's owner can into hers.
    assert (await priya.get("/auth/app/start", params={"app": str(hers)})).status_code == 303
    assert (await owner.get("/auth/app/start", params={"app": str(hers)})).status_code == 303


async def test_signed_out_visitors_sign_in_first(visitor, me, sessionmaker):
    app_id = await add_app(sessionmaker, me)
    resp = await visitor.get("/auth/app/start", params={"app": str(app_id), "next": "/x"})
    # Dev sign-in here (no OIDC in tests); with Google it is /auth/login, and back after.
    assert resp.status_code == 303 and resp.headers["location"] == "/login"


async def test_unknown_public_preview_and_non_get(visitor, me, sessionmaker, settings, monkeypatch):
    assert (
        await visitor.get("/auth/app/check", headers=forwarded(host="nope.apps.localhost"))
    ).status_code == 404
    assert (
        await visitor.get("/auth/app/check", headers=forwarded(host="jarvis.servhub.cc"))
    ).status_code == 404
    await add_app(sessionmaker, me, slug="open", public=True)
    resp = await visitor.get("/auth/app/check", headers=forwarded(host="open.apps.localhost"))
    assert resp.status_code == 200
    await add_app(sessionmaker, me)
    # The deployer's own health check and screenshot, with the key for this host only.
    key = hmac.new(settings.deployer_token.encode(), f"preview:{HOST}".encode(), hashlib.sha256)
    ok = await visitor.get(
        "/auth/app/check", headers=forwarded(**{"X-Jarvis-Preview": key.hexdigest()})
    )
    assert ok.status_code == 200
    other = hmac.new(settings.deployer_token.encode(), b"preview:x.apps.localhost", hashlib.sha256)
    bad = await visitor.get(
        "/auth/app/check", headers=forwarded(**{"X-Jarvis-Preview": other.hexdigest()})
    )
    assert bad.status_code == 302
    # Without DEPLOYER_TOKEN there is no preview key at all.
    monkeypatch.setattr(settings, "deployer_token", "")
    empty = hmac.new(b"", f"preview:{HOST}".encode(), hashlib.sha256).hexdigest()
    resp = await visitor.get("/auth/app/check", headers=forwarded(**{"X-Jarvis-Preview": empty}))
    assert resp.status_code == 302
    # An app's own fetch or form without a cookie: refused, not redirected.
    post = await visitor.get("/auth/app/check", headers=forwarded(method="POST"))
    assert post.status_code == 401


async def test_redirects_stay_on_the_app(owner, me, visitor, sessionmaker):
    app_id = await add_app(sessionmaker, me)
    for evil in ("https://evil.example/", "//evil.example/x", "/_jarvis/auth?token=x"):
        token = await token_for(owner, app_id, evil)
        resp = await visitor.get(
            "/auth/app/check", headers=forwarded(uri=f"/_jarvis/auth?token={token}")
        )
        assert resp.headers["location"] == f"http://{HOST}:8080/", evil
