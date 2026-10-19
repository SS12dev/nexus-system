import hmac
import logging

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from jarvis_api.deps import DbDep, SettingsDep
from jarvis_api.schemas import AuthConfigOut, DevLoginIn, UserOut
from jarvis_api.sessions import revoke_token, set_session_cookie, start_session
from jarvis_api.users import SignInRefused, resolve_sign_in
from jarvis_common.settings import Settings

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
            client_kwargs={"scope": "openid email profile"},
        )
    return oauth


@router.get("/config", response_model=AuthConfigOut)
async def auth_config(settings: SettingsDep) -> AuthConfigOut:
    return AuthConfigOut(
        oidc_enabled=settings.oidc_enabled, dev_login_enabled=settings.dev_login_active
    )


@router.get("/login")
async def login(request: Request, settings: SettingsDep):
    if not settings.oidc_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OIDC sign-in is not configured")
    # Built from the request so the same deployment works on localhost and the public hostname;
    # register every such callback URL with the identity provider.
    redirect_uri = str(request.url_for("auth_callback"))
    return await request.app.state.oauth.oidc.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="auth_callback")
async def callback(request: Request, db: DbDep, settings: SettingsDep):
    if not settings.oidc_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OIDC sign-in is not configured")
    try:
        token = await request.app.state.oauth.oidc.authorize_access_token(request)
    except OAuthError as exc:
        log.warning("OIDC callback failed: %s", exc)
        return RedirectResponse("/login?error=oidc_failed", status.HTTP_303_SEE_OTHER)
    info = token.get("userinfo") or {}
    if not info.get("email") or not info.get("email_verified", False):
        return RedirectResponse("/login?error=email_unverified", status.HTTP_303_SEE_OTHER)
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
        return RedirectResponse(f"/login?error={exc.reason}", status.HTTP_303_SEE_OTHER)
    session_token = await start_session(db, settings, user, request)
    await db.commit()
    response = RedirectResponse("/", status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, settings, request, session_token)
    return response


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
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.reason) from exc
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
