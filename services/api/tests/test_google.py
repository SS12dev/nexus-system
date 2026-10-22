import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from nexus_common import google
from nexus_common.models import GoogleGrant, User, utcnow

GMAIL = google.SERVICE_SCOPES["gmail"]
CALENDAR = google.SERVICE_SCOPES["calendar"]


def token(scope: str, refresh: str | None = "refresh-1", sub: str = "google-1", **extra) -> dict:
    t = {
        "access_token": "access-1",
        "expires_in": 3600,
        "scope": scope,
        "userinfo": {"email": "owner@example.com", "email_verified": True, "sub": sub},
        **extra,
    }
    if refresh:
        t["refresh_token"] = refresh
    return t


class FakeGoogle:
    """Stands in for authlib's registered Google client."""

    def __init__(self):
        self.next_token: dict | None = None
        self.redirects: list[dict] = []

    async def authorize_redirect(self, request, redirect_uri, **params):
        self.redirects.append(params)
        request.session["state"] = "x"
        return RedirectResponse("https://accounts.google.com/o/oauth2/auth", 302)

    async def authorize_access_token(self, request):
        return self.next_token


@pytest.fixture
def google_app(app, settings):
    oidc_settings = settings.model_copy(
        update={"oidc_client_id": "client", "oidc_client_secret": "secret"}
    )
    fake = FakeGoogle()
    app.state.settings = oidc_settings
    app.state.oauth = SimpleNamespace(oidc=fake)
    calls: list[httpx.Request] = []
    responses: list[httpx.Response] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses.pop(0) if responses else httpx.Response(200, json={})

    app.state.google_http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    yield SimpleNamespace(fake=fake, settings=oidc_settings, calls=calls, responses=responses)
    app.state.settings = settings
    app.state.google_http = None


async def sign_in(client_for, fake, tok: dict) -> httpx.Response:
    client = await client_for()
    await client.get("/auth/login")
    fake.next_token = tok
    resp = await client.get("/auth/callback")
    resp.client = client
    return resp


def test_sign_in_asks_for_services_with_offline_access(settings):
    scope = google.sign_in_scope(settings)
    assert scope.startswith("openid email profile ")
    assert GMAIL in scope and CALENDAR in scope
    assert google.SERVICE_SCOPES["drive"] not in scope


def test_services_are_only_asked_of_google(settings):
    other = settings.model_copy(update={"oidc_issuer": "https://login.example.com"})
    assert google.sign_in_scope(other) == "openid email profile"


async def test_sign_in_stores_granted_services(client_for, google_app, sessionmaker):
    resp = await sign_in(client_for, google_app.fake, token(f"openid email {GMAIL}"))
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    assert google_app.fake.redirects[0] == {
        "access_type": "offline",
        "include_granted_scopes": "true",
    }
    access = (await resp.client.get("/api/me/google")).json()
    assert access["available"] is True
    assert access["granted"] == ["gmail"]
    assert access["requested"] == ["gmail", "calendar", "contacts", "tasks"]
    async with sessionmaker() as db:
        grant = (await db.scalars(select(GoogleGrant))).one()
        # Encrypted at rest.
        assert "refresh-1" not in grant.refresh_token
        assert google.decrypt(google_app.settings, grant.refresh_token) == "refresh-1"


async def test_declining_every_service_still_signs_in(client_for, google_app, sessionmaker):
    resp = await sign_in(client_for, google_app.fake, token("openid email profile", refresh=None))
    assert resp.headers["location"] == "/"
    assert (await resp.client.get("/api/me")).status_code == 200
    assert (await resp.client.get("/api/me/google")).json()["granted"] == []


async def test_missing_refresh_token_goes_through_consent_once(client_for, google_app):
    resp = await sign_in(client_for, google_app.fake, token(GMAIL, refresh=None))
    assert resp.headers["location"] == "/auth/google/connect"
    client = resp.client
    await client.get("/auth/google/connect")
    assert google_app.fake.redirects[-1]["prompt"] == "consent"
    assert google_app.fake.redirects[-1]["login_hint"] == "owner@example.com"
    google_app.fake.next_token = token(f"{GMAIL} {CALENDAR}", refresh="refresh-2")
    resp = await client.get("/auth/callback")
    assert resp.headers["location"] == "/settings"
    assert (await client.get("/api/me/google")).json()["granted"] == ["gmail", "calendar"]


async def test_connect_refuses_another_google_account(client_for, google_app):
    resp = await sign_in(client_for, google_app.fake, token("openid"))
    client = resp.client
    await client.get("/auth/google/connect")
    google_app.fake.next_token = token(GMAIL, sub="google-other")
    resp = await client.get("/auth/callback")
    assert resp.headers["location"] == "/settings?error=google_other_account"
    assert (await client.get("/api/me/google")).json()["granted"] == []


async def test_disconnect_revokes_at_google(client_for, google_app):
    resp = await sign_in(client_for, google_app.fake, token(GMAIL))
    assert (await resp.client.delete("/api/me/google")).status_code == 204
    revoke = google_app.calls[-1]
    assert str(revoke.url) == google.REVOKE_URL
    assert b"token=refresh-1" in revoke.content
    assert (await resp.client.get("/api/me/google")).json()["granted"] == []


async def test_access_token_refreshes_when_expired(client_for, google_app, sessionmaker):
    await sign_in(client_for, google_app.fake, token(f"{GMAIL} {CALENDAR}"))
    settings = google_app.settings
    async with sessionmaker() as db:
        user = (await db.scalars(select(User))).one()
        assert await google.access_token(db, settings, user.id, "gmail") == "access-1"
        with pytest.raises(google.GoogleNotConnected):
            await google.access_token(db, settings, user.id, "tasks")
        grant = await db.get(GoogleGrant, user.id)
        grant.access_expires_at = utcnow() - timedelta(minutes=1)
        await db.commit()

    seen: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200, json={"access_token": "access-2", "expires_in": 3600, "scope": GMAIL}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with sessionmaker() as db:
            assert await google.access_token(db, settings, user.id, "gmail", http) == "access-2"
            await db.commit()
        assert seen[0]["refresh_token"] == "refresh-1"
        assert seen[0]["grant_type"] == "refresh_token"
        async with sessionmaker() as db:
            # Cached until it nears expiry; the refresh also dropped calendar.
            assert await google.access_token(db, settings, user.id, "gmail", http) == "access-2"
            with pytest.raises(google.GoogleNotConnected):
                await google.access_token(db, settings, user.id, "calendar", http)
        assert len(seen) == 1


async def test_revoked_grant_is_forgotten(client_for, google_app, sessionmaker):
    await sign_in(client_for, google_app.fake, token(GMAIL, expires_in=0))
    settings = google_app.settings

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=json.dumps({"error": "invalid_grant"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with sessionmaker() as db:
            user = (await db.scalars(select(User))).one()
            with pytest.raises(google.GoogleNotConnected):
                await google.access_token(db, settings, user.id, "gmail", http)
            await db.commit()
    async with sessionmaker() as db:
        assert (await db.scalars(select(GoogleGrant))).first() is None


async def test_grant_under_old_secret_key_is_dropped(client_for, google_app, sessionmaker):
    await sign_in(client_for, google_app.fake, token(GMAIL))
    rotated = google_app.settings.model_copy(update={"secret_key": "another-secret-key"})
    async with sessionmaker() as db:
        user = (await db.scalars(select(User))).one()
        assert await google.get_grant(db, rotated, user.id) is None
        await db.commit()
    async with sessionmaker() as db:
        assert (await db.scalars(select(GoogleGrant))).first() is None


async def test_google_access_without_oidc(owner):
    access = (await owner.get("/api/me/google")).json()
    assert access["available"] is False and access["granted"] == []


async def test_new_token_key_re_encrypts_old_grants(client_for, google_app, sessionmaker):
    await sign_in(client_for, google_app.fake, token(GMAIL))
    keyed = google_app.settings.model_copy(update={"token_encryption_keys": "new-key,old-key"})
    async with sessionmaker() as db:
        user = (await db.scalars(select(User))).one()
        grant = await google.get_grant(db, keyed, user.id)
        assert google.decrypt(keyed, grant.refresh_token) == "refresh-1"
        await db.commit()
    # Now under new-key alone: SECRET_KEY is no longer needed, and a removed key drops it.
    only_new = google_app.settings.model_copy(
        update={"token_encryption_keys": "new-key", "secret_key": "another-secret-key"}
    )
    async with sessionmaker() as db:
        grant = await google.get_grant(db, only_new, user.id)
        assert google.decrypt(only_new, grant.access_token) == "access-1"
    gone = only_new.model_copy(update={"token_encryption_keys": "newer-key"})
    async with sessionmaker() as db:
        assert await google.get_grant(db, gone, user.id) is None
