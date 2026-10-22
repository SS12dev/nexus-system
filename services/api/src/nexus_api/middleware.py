"""The API's outer layer: cross-site request checks, the per-IP and sign-in rate limits,
security headers, and per-minute request counters for the Admin page.

A plain ASGI middleware rather than BaseHTTPMiddleware, so streamed downloads pass straight
through. WebSockets check their own Origin (see ws.py).
"""

import json
import logging
import re
import time
import uuid
from urllib.parse import urlsplit

from redis.exceptions import RedisError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexus_api.ratelimit import METRICS_TTL_S, hit, metrics_key
from nexus_api.sessions import client_ip
from nexus_common.logs import request_id
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Sign-in endpoints get their own, much lower, per-IP limit.
AUTH_PATHS = (
    "/auth/login",
    "/auth/callback",
    "/auth/dev-login",
    "/auth/google/connect",
    "/auth/access-request",
)
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), geolocation=(), payment=()"),
]
HSTS = (b"strict-transport-security", b"max-age=31536000")
# An id passed in by a proxy in front (X-Request-ID) is kept if it looks like one.
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
# Requests slower than this are logged, to spot what needs work.
SLOW_MS = 5000


def same_origin(origin: str, host: str | None, settings: Settings) -> bool:
    origin = origin.rstrip("/")
    return origin in settings.allowed_origin_list or urlsplit(origin).netloc == host


class GuardMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        if path in ("/healthz", "/readyz"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        started = time.monotonic()
        redis = getattr(scope["app"].state, "redis", None)
        given = headers.get("x-request-id", "")
        rid = given if REQUEST_ID_RE.match(given) else uuid.uuid4().hex
        rid_header = (b"x-request-id", rid.encode())
        token = request_id.set(rid)
        try:
            await self._handle(scope, receive, send, path, headers, started, redis, rid_header)
        finally:
            request_id.reset(token)

    async def _handle(self, scope, receive, send, path, headers, started, redis, rid_header):
        refusal = self._cross_site(scope["method"], headers)
        if refusal is None and redis is not None and self.settings.rate_limit_enabled:
            refusal = await self._limited(redis, path, headers, scope.get("client"))
        if refusal is not None:
            code, detail, extra = refusal
            log.warning(
                "http.refused",
                extra={"status": code, "method": scope["method"], "path": path, "why": detail},
            )
            await self._reject(send, code, detail, extra, rid_header)
            await self._count(redis, code, started)
            return

        https = scope.get("scheme") == "https"
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                have = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in SECURITY_HEADERS if k not in have]
                if https:
                    extra.append(HSTS)
                if path.startswith("/api") and b"cache-control" not in have:
                    # Responses carry private data; never let a shared cache keep them.
                    extra.append((b"cache-control", b"no-store"))
                extra.append(rid_header)
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            ms = int((time.monotonic() - started) * 1000)
            if status_code >= 500 or ms >= SLOW_MS:
                fields = {"status": status_code, "method": scope["method"], "path": path, "ms": ms}
                log.warning("http.error" if status_code >= 500 else "http.slow", extra=fields)
            await self._count(redis, status_code, started)

    def _cross_site(self, method: str, headers: dict[str, str]):
        """Session cookies are SameSite=Lax, which already keeps them off cross-site POSTs in
        current browsers; checking Origin as well covers older browsers and same-site
        subdomains (servhub.cc hosts other apps)."""
        if method not in UNSAFE_METHODS:
            return None
        origin = headers.get("origin")
        if origin and origin != "null":
            if same_origin(origin, headers.get("host"), self.settings):
                return None
            return 403, "Cross-site request refused", None
        if headers.get("sec-fetch-site") == "cross-site" or origin == "null":
            return 403, "Cross-site request refused", None
        return None  # Not a browser (curl, tests); the session cookie still has to be valid.

    async def _limited(self, redis, path: str, headers: dict[str, str], client):
        ip = client_ip(headers, client[0] if client else None)
        if path.startswith(AUTH_PATHS):
            wait = await hit(redis, "auth", ip, self.settings.rate_limit_auth_per_minute, 60)
            if wait is not None:
                return 429, f"Too many sign-in attempts. Try again in {wait} s.", wait
        # Traefik asks /auth/app/check for every request to a private app, assets included.
        if path.startswith(("/api", "/auth")) and path != "/auth/app/check":
            wait = await hit(redis, "ip", ip, self.settings.rate_limit_api_per_minute, 60)
            if wait is not None:
                return 429, f"Too many requests. Try again in {wait} s.", wait
        return None

    async def _reject(self, send: Send, code: int, detail: str, retry_after, rid_header) -> None:
        body = json.dumps({"detail": detail}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            *SECURITY_HEADERS,
            rid_header,
        ]
        if retry_after:
            headers.append((b"retry-after", str(retry_after).encode()))
        await send({"type": "http.response.start", "status": code, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _count(self, redis, status_code: int, started: float) -> None:
        if redis is None:
            return
        key = metrics_key(int(time.time() // 60))
        ms = int((time.monotonic() - started) * 1000)
        try:
            async with redis.pipeline(transaction=False) as pipe:
                pipe.hincrby(key, "requests", 1)
                pipe.hincrby(key, "ms", ms)
                if status_code >= 500:
                    pipe.hincrby(key, "errors", 1)
                pipe.expire(key, METRICS_TTL_S)
                await pipe.execute()
        except RedisError:
            pass
