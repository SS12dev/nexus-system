"""Rate limits and request counters, kept in Redis so every API replica shares them.

Limits are fixed windows: a counter per bucket, caller and window that expires with the window.
That allows a burst of up to twice the limit across a window edge, which is fine for keeping one
person (or a script) from running up costs or hammering sign-in. If Redis is down, requests go
through: a limiter must not take Nexus down with it.
"""

import logging
import time
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from nexus_api.deps import CurrentUser
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

# Per-minute request counters for the Admin page, kept this long.
METRICS_TTL_S = 3 * 3600


def metrics_key(minute: int) -> str:
    return f"jarvis:metrics:{minute}"


async def hit(redis: Redis, bucket: str, who: str, limit: int, window_s: int) -> int | None:
    """Count one request. Returns the seconds to wait when the caller is over the limit."""
    if limit <= 0:
        return None
    now = time.time()
    window = int(now // window_s)
    key = f"jarvis:rl:{bucket}:{who}:{window}"
    try:
        async with redis.pipeline(transaction=False) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_s + 5)
            count, _ = await pipe.execute()
    except RedisError as exc:
        log.warning("Rate limiter unavailable: %s", exc)
        return None
    if count <= limit:
        return None
    try:
        await redis.hincrby(metrics_key(int(now // 60)), "limited", 1)
    except RedisError:
        pass
    return max(1, int((window + 1) * window_s - now))


def too_many(retry_after: int, what: str = "requests") -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Too many {what}. Try again in {retry_after} s.",
        headers={"Retry-After": str(retry_after)},
    )


def per_user(bucket: str, setting: str, window_s: int, what: str) -> Callable:
    """A route dependency limiting each signed-in user to `settings.<setting>` calls per window."""

    async def check(request: Request, user: CurrentUser) -> None:
        settings: Settings = request.app.state.settings
        if not settings.rate_limit_enabled:
            return
        wait = await hit(
            request.app.state.redis, bucket, str(user.id), getattr(settings, setting), window_s
        )
        if wait is not None:
            raise too_many(wait, what)

    return Depends(check)


messages_limit = per_user("messages", "rate_limit_messages_per_minute", 60, "messages")
uploads_limit = per_user("uploads", "rate_limit_uploads_per_minute", 60, "uploads")
runs_limit = per_user("runs", "rate_limit_runs_per_hour", 3600, "agent runs")
