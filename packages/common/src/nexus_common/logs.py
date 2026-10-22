"""One log format for every Python service, and the ids that tie a request to its work.

Each line is a JSON object (LOG_FORMAT=text for a human-readable line instead) with the time,
level, service, logger and message, plus whatever is bound to the current request or job:
request_id, user_id, chat_id, run_id, job, job_id and trace_id (the Langfuse trace). The API
gives each request an id, hands it to the jobs the request queues, and the workers bind it while
they run them, so one id finds every line, in every container, that one click caused:

    docker compose logs api orchestrator | grep 4f1c2a9e0b7d4e21

The fields live in a context variable holding a dict. A request or job starts a fresh dict with
`scope()`; `bind()` adds to it in place, so a field bound deep inside a dependency is still there
when the middleware writes the access line.
"""

import functools
import inspect
import json
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from nexus_common import metrics

_fields: ContextVar[dict[str, Any] | None] = ContextVar("nexus_log_fields", default=None)

# Ids callers may pass in X-Request-ID: short and plain, so nothing odd lands in the logs.
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
# Attributes every LogRecord has; anything else on a record came from `extra=`.
_RECORD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def clean_request_id(value: str | None) -> str | None:
    return value if value and _REQUEST_ID.match(value) else None


def current() -> dict[str, Any]:
    """The fields bound now (a copy)."""
    return dict(_fields.get() or {})


def get(name: str) -> Any:
    return (_fields.get() or {}).get(name)


def bind(**fields: Any) -> None:
    """Add fields to the current request's or job's log context. None values are skipped."""
    fields = {k: str(v) for k, v in fields.items() if v is not None}
    current_fields = _fields.get()
    if current_fields is None:
        _fields.set(fields)
    else:
        current_fields.update(fields)


@contextmanager
def scope(**fields: Any) -> Iterator[dict[str, Any]]:
    """A fresh log context for one request or job, dropped when it ends."""
    token = _fields.set({k: str(v) for k, v in fields.items() if v is not None})
    try:
        yield _fields.get()  # type: ignore[misc]
    finally:
        _fields.reset(token)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        out.update(_fields.get() or {})
        for key, value in vars(record).items():
            if key not in _RECORD_ATTRS and not key.startswith("_"):
                out[key] = value
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """The old line, with the bound ids appended as key=value."""

    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extra = {k: v for k, v in vars(record).items() if k not in _RECORD_ATTRS}
        fields = {**(_fields.get() or {}), **extra}
        if not fields:
            return line
        tail = " ".join(f"{k}={v}" for k, v in fields.items())
        first, sep, rest = line.partition("\n")
        return f"{first} [{tail}]{sep}{rest}"


def configure_logging(service: str, level: str | None = None, fmt: str | None = None) -> None:
    """Send every logger, uvicorn's and arq's included, through one stdout handler."""
    level = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    fmt = (fmt or os.environ.get("LOG_FORMAT") or "json").lower()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service) if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    # Errors also go to the Admin page's recent-errors list (metrics.py).
    if sink := metrics.error_sink(service):
        root.addHandler(sink)
    root.setLevel(level)
    # uvicorn and arq install their own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "arq"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    # The API writes its own access line with the request id (middleware.py).
    logging.getLogger("uvicorn.access").disabled = True
    # One line per HTTP call to LiteLLM, MinIO or Google is noise at INFO.
    for name in ("httpx", "httpcore", "botocore", "aiobotocore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _queue_ms(ctx: dict) -> int | None:
    enqueued = ctx.get("enqueue_time")
    if not isinstance(enqueued, datetime):
        return None
    return max(0, int((datetime.now(UTC) - enqueued).total_seconds() * 1000))


job_log = logging.getLogger("nexus.jobs")


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def traced_job(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Wrap an arq job function: it takes the `request_id` its enqueuer passed (see
    `with_request_id`) and runs in a log scope with that id, its job name and its *_id args."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    async def wrapper(ctx: dict, *args: Any, request_id: str | None = None, **kwargs: Any) -> Any:
        bound = sig.bind_partial(ctx, *args, **kwargs).arguments
        ids = {k: v for k, v in bound.items() if k.endswith("_id")}
        wait_ms = _queue_ms(ctx)
        started = time.monotonic()
        failed = True
        with scope(
            job=fn.__name__,
            job_id=ctx.get("job_id"),
            request_id=clean_request_id(request_id),
            **ids,
        ):
            try:
                result = await fn(ctx, *args, **kwargs)
                failed = False
                return result
            except BaseException as exc:
                job_log.exception(
                    "job failed",
                    extra={
                        "duration_ms": _ms(started),
                        "queue_ms": wait_ms,
                        "error": type(exc).__name__,
                    },
                )
                raise
            finally:
                if not failed:
                    job_log.info(
                        "job done", extra={"duration_ms": _ms(started), "queue_ms": wait_ms}
                    )
                await metrics.record_job(
                    ctx.get("redis"), fn.__name__, _ms(started), failed, wait_ms
                )

    return wrapper


def with_request_id(enqueue: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """An arq `enqueue_job` that hands the current request id to the job it queues. Every job
    function on the other end must be wrapped in `traced_job`, which accepts it."""

    async def wrapped(function: str, *args: Any, **kwargs: Any) -> Any:
        request_id = get("request_id")
        if request_id and "request_id" not in kwargs:
            kwargs["request_id"] = request_id
        return await enqueue(function, *args, **kwargs)

    return wrapped
