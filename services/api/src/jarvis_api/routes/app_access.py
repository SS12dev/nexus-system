"""Private apps. Traefik asks /auth/app/check before every request to a private app (its
forwardAuth middleware, app-auth). Without a valid app cookie the visitor goes to Jarvis's own
host to sign in (/auth/app/start); Jarvis sends them back to the app with a signed one-time
token on /_jarvis/auth, which check swaps for a cookie on that app's host alone.

The Jarvis session cookie never leaves Jarvis's host: it has no Domain, so browsers keep it to
jarvis.servhub.cc, and check only ever reads the app cookie. An app's cookie opens that one app,
for its owner and Jarvis's owner, for APP_COOKIE_HOURS; the app (which sees it) can't use it
anywhere else.
"""

import hashlib
import hmac
import secrets
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_api.deps import DbDep, RedisDep, SettingsDep
from jarvis_api.sessions import user_for_token
from jarvis_common.models import Project, RoleName, User, UserStatus
from jarvis_common.settings import Settings

router = APIRouter(prefix="/auth/app", tags=["apps"])

COOKIE = "jarvis_app"
CALLBACK = "/_jarvis/auth"
TOKEN_SECONDS = 120


def signer(settings: Settings, what: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=f"jarvis-app-{what}")


def preview_key(settings: Settings, host: str) -> str:
    """The deployer's key for its own health check and screenshot of this host."""
    message = f"preview:{host}".encode()
    return hmac.new(settings.deployer_token.encode(), message, hashlib.sha256).hexdigest()


def allowed(user: User | None, row: Project) -> bool:
    return (
        user is not None
        and user.status == UserStatus.active
        and (user.id == row.user_id or user.role == RoleName.owner)
    )


def safe_next(path: str | None) -> str:
    """Only a path on the app itself, never another site or the callback."""
    path = path or "/"
    if not path.startswith("/") or path.startswith("//") or path.startswith(CALLBACK):
        return "/"
    return path


async def app_for_host(db: AsyncSession, settings: Settings, host: str) -> Project | None:
    host = host.split(":")[0].strip().lower()
    suffix = "." + settings.apps_domain
    if not host.endswith(suffix):
        return None
    return await db.scalar(select(Project).where(Project.app_slug == host[: -len(suffix)]))


def origin(row: Project) -> str:
    """The app's own scheme://host[:port], from its stored link rather than request headers."""
    base = urlsplit(row.live_url or "")
    return f"{base.scheme}://{base.netloc}"


def refuse(message: str, code: int = status.HTTP_403_FORBIDDEN) -> PlainTextResponse:
    return PlainTextResponse(message, status_code=code)


@router.get("/check")
async def check(request: Request, db: DbDep, settings: SettingsDep, redis: RedisDep):
    """Traefik's question for every request to a private app: 200 lets it through; anything
    else goes back to the visitor as it is (a redirect to sign in, or a refusal)."""
    host = request.headers.get("x-forwarded-host", "")
    uri = request.headers.get("x-forwarded-uri", "/")
    row = await app_for_host(db, settings, host)
    if row is None:
        return refuse("Not found", status.HTTP_404_NOT_FOUND)
    secure = request.headers.get("x-forwarded-proto") == "https" or (row.live_url or "").startswith(
        "https://"
    )
    if row.public:
        return Response(status_code=status.HTTP_200_OK)
    preview = request.headers.get("x-jarvis-preview", "")
    # Never without DEPLOYER_TOKEN: a key from an empty secret would be anyone's to work out.
    if (
        preview
        and settings.deployer_token
        and hmac.compare_digest(preview, preview_key(settings, host.split(":")[0]))
    ):
        return Response(status_code=status.HTTP_200_OK)
    path, _, query = uri.partition("?")
    if path == CALLBACK:
        return await exchange(db, settings, redis, row, query, secure)
    cookie = request.cookies.get(COOKIE)
    if cookie:
        try:
            data = signer(settings, "cookie").loads(
                cookie, max_age=settings.app_cookie_hours * 3600
            )
        except BadSignature:
            data = {}
        if data.get("a") == str(row.id):
            user = await db.get(User, uuid.UUID(data["u"]))
            if allowed(user, row):
                return Response(status_code=status.HTTP_200_OK)
    if request.headers.get("x-forwarded-method", "GET") not in ("GET", "HEAD"):
        return refuse("Sign in to Jarvis to use this app.", status.HTTP_401_UNAUTHORIZED)
    target = urlencode({"app": str(row.id), "next": safe_next(uri)})
    return RedirectResponse(f"{settings.jarvis_url.rstrip('/')}/auth/app/start?{target}", 302)


async def exchange(db, settings: Settings, redis, row: Project, query: str, secure: bool):
    """The visitor is back from signing in: check the one-time token and give them this app's
    own cookie."""
    token = (parse_qs(query).get("token") or [""])[0]
    try:
        data = signer(settings, "token").loads(token, max_age=TOKEN_SECONDS)
    except SignatureExpired:
        return refuse("This sign-in link has expired. Open the app again.")
    except BadSignature:
        return refuse("This sign-in link isn't valid. Open the app again.")
    if data.get("a") != str(row.id):
        return refuse("This sign-in link is for another app.")
    if not await redis.set(f"jarvis:apptoken:{data['n']}", 1, nx=True, ex=TOKEN_SECONDS * 2):
        return refuse("This sign-in link was already used. Open the app again.")
    user = await db.get(User, uuid.UUID(data["u"]))
    if not allowed(user, row):
        return refuse("You don't have access to this app.")
    # Absolute: Traefik would read a bare path as one on the API's own address.
    response = RedirectResponse(
        f"{origin(row)}{safe_next(data.get('next'))}", status.HTTP_302_FOUND
    )
    # No Domain: the cookie stays on this app's host.
    response.set_cookie(
        COOKIE,
        signer(settings, "cookie").dumps({"a": str(row.id), "u": str(user.id)}),
        max_age=settings.app_cookie_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    return response


@router.get("/start")
async def start(
    request: Request, db: DbDep, settings: SettingsDep, app: uuid.UUID, next: str = "/"
):
    """On Jarvis's own host: sign in if needed, then back to the app with a one-time token."""
    user = await user_for_token(db, request.cookies.get(settings.session_cookie_name))
    if user is None:
        # Come back here after signing in (the OIDC callback reads it).
        request.session["after_login"] = f"/auth/app/start?{request.url.query}"
        return RedirectResponse(
            "/auth/login" if settings.oidc_enabled else "/login", status.HTTP_303_SEE_OTHER
        )
    row = await db.get(Project, app)
    if row is None or not row.app_slug or not row.live_url:
        return refuse("No such app.", status.HTTP_404_NOT_FOUND)
    if not allowed(user, row):
        return refuse("You don't have access to this app. Ask its owner to make it public.")
    token = signer(settings, "token").dumps(
        {
            "a": str(row.id),
            "u": str(user.id),
            "n": secrets.token_urlsafe(16),
            "next": safe_next(next),
        }
    )
    return RedirectResponse(f"{origin(row)}{CALLBACK}?token={token}", status.HTTP_303_SEE_OTHER)
