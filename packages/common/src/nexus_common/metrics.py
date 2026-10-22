"""Request and job metrics, recent errors and alerts, kept in Valkey for the Admin Monitoring tab.

Counters go into one hash per five minutes (`nexus:mon:<bucket>`), kept for a day, so every API
replica and worker adds to the same figures and nothing new has to run. Per route and per job
there is a count, failures, total milliseconds and a latency histogram, from which the page
estimates p50 and p95. Errors logged anywhere (level ERROR and up) are pushed to a capped list
by a logging handler that writes from its own thread, so logging never waits on Valkey.

Nothing here may break a request or a job: every write swallows Valkey errors.
"""

import json
import logging
import logging.handlers
import os
import queue
import time
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError

BUCKET_S = 300
KEEP_S = 25 * 3600
# Latency histogram edges in ms; the last bucket is everything slower.
EDGES_MS = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 120000)
ERRORS_KEY = "nexus:mon:errors"
ERRORS_KEEP = 200
ALERTS_KEY = "nexus:alerts:active"
ALERTS_HISTORY_KEY = "nexus:alerts:history"


def bucket_key(bucket: int) -> str:
    return f"nexus:mon:{bucket}"


def _edge(ms: int) -> str:
    for edge in EDGES_MS:
        if ms <= edge:
            return str(edge)
    return "inf"


async def _record(redis, kind: str, name: str, ms: int, failed: bool, wait_ms: int | None) -> None:
    if redis is None:
        return
    key = bucket_key(int(time.time() // BUCKET_S))
    prefix = f"{kind}|{name}|"
    try:
        async with redis.pipeline(transaction=False) as pipe:
            pipe.hincrby(key, prefix + "n", 1)
            pipe.hincrby(key, prefix + "ms", ms)
            pipe.hincrby(key, prefix + "le" + _edge(ms), 1)
            if failed:
                pipe.hincrby(key, prefix + "fail", 1)
            if wait_ms is not None:
                pipe.hincrby(key, prefix + "wait", wait_ms)
            pipe.expire(key, KEEP_S)
            await pipe.execute()
    except (RedisError, OSError):
        pass


async def record_request(redis, method: str, route: str | None, status: int, ms: int) -> None:
    """One API request. `route` is the route template (/api/chats/{chat_id}), never the raw
    path, so ids don't each become a row; requests no route matched count as "unmatched"."""
    await _record(redis, "route", f"{method} {route or 'unmatched'}", ms, status >= 500, None)


async def record_job(redis, name: str, ms: int, failed: bool, wait_ms: int | None) -> None:
    await _record(redis, "job", name, ms, failed, wait_ms)


def _quantile(hist: dict[str, int], n: int, q: float) -> int | None:
    """The histogram edge at or below which a share q of calls finished (None if slower than
    every edge, which the page shows as "over 2 min")."""
    if not n:
        return None
    target = q * n
    seen = 0
    for edge in EDGES_MS:
        seen += hist.get(str(edge), 0)
        if seen >= target:
            return edge
    return None


async def summary(redis, minutes: int) -> dict[str, list[dict]]:
    """Per-route and per-job figures over the last `minutes`."""
    now = int(time.time() // BUCKET_S)
    count = max(1, -(-minutes * 60 // BUCKET_S))
    keys = [bucket_key(b) for b in range(now - count + 1, now + 1)]
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for k in keys:
                pipe.hgetall(k)
            rows = await pipe.execute()
    except (RedisError, OSError):
        rows = []
    totals: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        for field, value in row.items():
            field = field.decode() if isinstance(field, bytes) else field
            kind, name, stat = field.split("|", 2)
            slot = totals.setdefault((kind, name), {})
            slot[stat] = slot.get(stat, 0) + int(value)
    out: dict[str, list[dict]] = {"routes": [], "jobs": []}
    for (kind, name), s in totals.items():
        n = s.get("n", 0)
        hist = {k[2:]: v for k, v in s.items() if k.startswith("le")}
        item = {
            "name": name,
            "count": n,
            "failed": s.get("fail", 0),
            "avg_ms": round(s.get("ms", 0) / n) if n else None,
            "p50_ms": _quantile(hist, n, 0.5),
            "p95_ms": _quantile(hist, n, 0.95),
        }
        if kind == "job":
            item["avg_wait_ms"] = round(s.get("wait", 0) / n) if n else None
            out["jobs"].append(item)
        elif kind == "route":
            out["routes"].append(item)
    for items in out.values():
        items.sort(key=lambda i: (-i["failed"], -i["count"]))
    return out


async def recent_errors(redis, limit: int = 50) -> list[dict]:
    try:
        rows = await redis.lrange(ERRORS_KEY, 0, limit - 1)
    except (RedisError, OSError):
        return []
    out = []
    for row in rows:
        try:
            out.append(json.loads(row))
        except (TypeError, ValueError):
            continue
    return out


ENTRY_ATTR = "nexus_error_entry"


def error_entry(record: logging.LogRecord, service: str, ids: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
        "service": service,
        "level": record.levelname.lower(),
        "logger": record.name,
        "msg": record.getMessage()[:500],
        **ids,
    }
    if record.exc_info and record.exc_info[1] is not None:
        exc = record.exc_info[1]
        entry["error"] = f"{type(exc).__name__}: {exc}"[:500]
        entry["stack"] = logging.Formatter().formatException(record.exc_info)[-3000:]
    elif isinstance(stack := getattr(record, "stack", None), str) and stack:
        entry["stack"] = stack[-3000:]  # a browser's stack, from /api/me/client-error
    if isinstance(page := getattr(record, "page", None), str) and page:
        entry["page"] = page[:300]
    return entry


class ErrorSink(logging.Handler):
    """Runs on the listener's thread: pushes each prepared entry to the capped errors list."""

    def __init__(self, redis_url: str):
        super().__init__(level=logging.ERROR)
        import redis as sync_redis

        self.redis = sync_redis.Redis.from_url(
            redis_url, socket_timeout=1, socket_connect_timeout=1
        )

    def emit(self, record: logging.LogRecord) -> None:
        entry = getattr(record, ENTRY_ATTR, None)
        if entry is None:
            return
        try:
            with self.redis.pipeline(transaction=False) as pipe:
                pipe.lpush(ERRORS_KEY, json.dumps(entry, default=str))
                pipe.ltrim(ERRORS_KEY, 0, ERRORS_KEEP - 1)
                pipe.execute()
        except (RedisError, OSError):
            pass


class ErrorQueueHandler(logging.handlers.QueueHandler):
    """Builds the entry on the logging thread, where the bound ids (a context variable) and the
    exception are still there, then hands it to the listener thread."""

    def __init__(self, q, service: str):
        super().__init__(q)
        self.setLevel(logging.ERROR)
        self.service = service

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        from nexus_common.logs import current  # logs imports this module

        entry = error_entry(record, self.service, current())
        record = super().prepare(record)
        setattr(record, ENTRY_ATTR, entry)
        return record


def error_sink(service: str) -> logging.Handler | None:
    """A non-blocking handler for the errors list, or None without REDIS_URL. The listener
    thread is a daemon; entries still queued when the process exits are dropped."""
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    q: queue.SimpleQueue = queue.SimpleQueue()
    logging.handlers.QueueListener(q, ErrorSink(url)).start()
    return ErrorQueueHandler(q, service)
