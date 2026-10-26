import hmac
import logging

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api import access, account_recovery
from nexus_api.deps import CurrentUser, DbDep, QueueDep, RedisDep, SettingsDep
from nexus_api.schemas import (
    AccessRequestIn,
    AccessStateOut,
    AccountRecoveryIn,
    AuthConfigOut,
    DevLoginIn,
    UserOut,
)
from nexus_api.sessions import revoke_token, set_session_cookie, start_session, user_for_token
from nexus_api.users import SignInRefused, normalize_email, resolve_sign_in
from nexus_common import google
from nexus_common.settings import Settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def make_oauth(settings: Settings) -> OAuth:
    oauth = OAuth()
    if settings.oidc_enabled:
        oauth.register(
            "oidc",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            server_metadata_url=f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
            # Google services in GOOGLE_SERVICES are asked for here too, so a user who grants
            # them at sign-in needs no second consent screen.
            client_kwargs={"scope": google.sign_in_scope(settings)},
        )
    return oauth


def _google_params(settings: Settings, *, consent: bool) -> dict:
    """Extra authorisation parameters when sign-in also asks for Google services."""
    if not settings.google_service_list:
        return {}
    params = {
        # A refresh token, so agents can use the services after the user has gone.
        "access_type": "offline",
        # Keep services granted earlier when a later consent asks for a subset.
        "include_granted_scopes": "true",
    }
    if consent:
        # Google sends a refresh token only on first consent unless asked again.
        params["prompt"] = "consent"
    return params


@router.get("/config", response_model=AuthConfigOut)
async def auth_config(settings: SettingsDep) -> AuthConfigOut:
    return AuthConfigOut(
        oidc_enabled=settings.oidc_enabled,
        dev_login_enabled=settings.dev_login_active,
        access_requests=settings.access_requests_open,
    )


@router.get("/login")
async def login(request: Request, settings: SettingsDep, account: str | None = None):
    """Start sign-in. account=choose shows the provider's account picker even when the browser
    is signed in to one account, for someone who was refused with the wrong one."""
    if not settings.oidc_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OIDC sign-in is not configured")
    # Built from the request so the same deployment works on localhost and the public hostname;
    # register every such callback URL with the identity provider.
    redirect_uri = str(request.url_for("auth_callback"))
    request.session.pop("google_connect", None)
    extra = _google_params(settings, consent=False)
    if account == "choose":
        extra["prompt"] = "select_account"
    return await request.app.state.oauth.oidc.authorize_redirect(request, redirect_uri, **extra)


@router.get("/google/connect")
async def google_connect(request: Request, user: CurrentUser, settings: SettingsDep):
    """Show Google's consent screen again, for a signed-in user who skipped some services or
    whose grant lapsed."""
    if not settings.oidc_enabled or not settings.google_service_list:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Google services are not configured")
    redirect_uri = str(request.url_for("auth_callback"))
    request.session["google_connect"] = str(user.id)
    return await request.app.state.oauth.oidc.authorize_redirect(
        request,
        redirect_uri,
        login_hint=user.email,
        **_google_params(settings, consent=True),
    )


@router.get("/callback", name="auth_callback")
async def callback(request: Request, db: DbDep, settings: SettingsDep):
    if not settings.oidc_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OIDC sign-in is not configured")
    # Set when a signed-in user came from /auth/google/connect rather than the sign-in page.
    connecting = request.session.pop("google_connect", None)
    try:
        token = await request.app.state.oauth.oidc.authorize_access_token(request)
    except OAuthError as exc:
        log.warning("OIDC callback failed: %s", exc)
        if connecting:
            return RedirectResponse("/settings?error=google_cancelled", status.HTTP_303_SEE_OTHER)
        return RedirectResponse("/login?error=oidc_failed", status.HTTP_303_SEE_OTHER)
    info = token.get("userinfo") or {}
    if not info.get("email") or not info.get("email_verified", False):
        return RedirectResponse("/login?error=email_unverified", status.HTTP_303_SEE_OTHER)
    if connecting:
        return await _finish_connect(request, db, settings, connecting, token, info)
    try:
        user = await resolve_sign_in(
            db,
            settings,
            email=info["email"],
            sub=info.get("sub"),
            name=info.get("name"),
            avatar_url=info.get("picture"),
        )
    except SignInRefused as exc:
        await db.rollback()
        log.info("auth.refused", extra={"reason": exc.reason, "method": "oidc"})
        if exc.reason in ("not_invited", "account_deleted"):
            access.remember_identity(
                request, info["email"], info.get("name"), info.get("picture"), info.get("sub")
            )
        return RedirectResponse(f"/login?error={exc.reason}", status.HTTP_303_SEE_OTHER)
    access.forget_identity(request)
    complete = True
    if settings.google_service_list:
        complete = await google.save_from_sign_in(db, settings, user.id, token)
    session_token = await start_session(db, settings, user, request)
    await db.commit()
    # Services were granted but Google kept the refresh token back (it sends one only on first
    # consent): go once through the consent screen to get it. The connect flow never loops.
    next_url = "/" if complete else "/auth/google/connect"
    # Signing in on the way to a private app: back to it (app_access.start set this).
    after = request.session.pop("after_login", None)
    if complete and isinstance(after, str) and after.startswith("/auth/app/start?"):
        next_url = after
    response = RedirectResponse(next_url, status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, settings, request, session_token)
    return response


async def _finish_connect(
    request: Request, db: AsyncSession, settings: Settings, user_id: str, token: dict, info: dict
) -> RedirectResponse:
    """Store the grant from /auth/google/connect, if it came from the same Google account."""
    user = await user_for_token(db, request.cookies.get(settings.session_cookie_name))
    same_account = user is not None and (
        user.oidc_sub == info.get("sub")
        if user.oidc_sub
        else user.email == normalize_email(info["email"])
    )
    if not same_account or str(user.id) != user_id:
        return RedirectResponse("/settings?error=google_other_account", status.HTTP_303_SEE_OTHER)
    await google.save_from_sign_in(db, settings, user.id, token)
    await db.commit()
    return RedirectResponse("/settings", status.HTTP_303_SEE_OTHER)


@router.post("/dev-login", response_model=UserOut)
async def dev_login(
    body: DevLoginIn, request: Request, response: Response, db: DbDep, settings: SettingsDep
):
    if not settings.dev_login_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dev sign-in is disabled")
    if not hmac.compare_digest(body.token, settings.dev_login_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong dev sign-in token")
    try:
        user = await resolve_sign_in(db, settings, email=body.email)
    except SignInRefused as exc:
        await db.rollback()
        log.info("auth.refused", extra={"reason": exc.reason, "method": "dev"})
        if exc.reason in ("not_invited", "account_deleted"):
            access.remember_identity(request, body.email)
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.reason) from exc
    access.forget_identity(request)
    session_token = await start_session(db, settings, user, request)
    await db.commit()
    set_session_cookie(response, settings, request, session_token)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbDep, settings: SettingsDep):
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await revoke_token(db, token)
        await db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


def _identity_or_404(request: Request, settings: Settings) -> dict:
    who = access.identity(request)
    if who is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Sign in with Google first, then you can ask for access"
        )
    return who


@router.get("/access-request", response_model=AccessStateOut)
async def access_request_state(request: Request, db: DbDep, settings: SettingsDep):
    """For the login page after a refused sign-in: may this person ask for access, or have they
    already?"""
    return await access.state(db, settings, _identity_or_404(request, settings))


@router.post("/access-request", response_model=AccessStateOut)
async def request_access(
    body: AccessRequestIn,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    redis: RedisDep,
    queue: QueueDep,
):
    """Ask for an invite, as the address the identity provider just verified."""
    if not settings.access_requests_open:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access requests are turned off")
    who = _identity_or_404(request, settings)
    try:
        out, created = await access.submit(db, settings, request, who, body.note)
    except access.RequestsFull as exc:
        log.warning("access.requests_full", extra={"limit": settings.access_request_max_pending})
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Too many requests are waiting right now. Try again in a few days.",
        ) from exc
    if created is not None:
        log.info("access.requested", extra={"access_request_id": str(created.id)})
        await access.announce(db, redis, queue, "requested", created.id, settings)
    return out


@router.post("/account-recovery", response_model=AccessStateOut)
async def request_account_recovery(
    body: AccountRecoveryIn,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    redis: RedisDep,
    queue: QueueDep,
):
    who = access.identity(request)
    if who is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Sign in with the account you deleted to request recovery.",
        )
    return await account_recovery.submit(db, settings, request, redis, queue, who, body.action)
