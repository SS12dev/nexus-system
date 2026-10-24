"""Retries for background jobs that are safe to run again.

arq retries a job only when it raises arq's Retry, so a job that hits a passing problem (LiteLLM
or OpenAI busy or briefly unreachable, a dropped database or Valkey connection) would otherwise
just fail, and its work (a memory, a profile, a link preview) would be lost. `retry_transient`
turns those errors into a Retry after 10 s, then 30 s; the third failure is final. Other errors
(a bug, a budget that ran out) fail at once, as before.

Only for jobs that write nothing until they succeed, or that overwrite what they wrote: a chat
turn streams to the user as it runs and an agent run spends budget, so those are never retried.
"""

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import openai
from arq import Retry
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.exc import InterfaceError, OperationalError

log = logging.getLogger(__name__)

TRIES = 3
TRANSIENT: tuple[type[BaseException], ...] = (
    openai.APIConnectionError,  # includes APITimeoutError
    openai.RateLimitError,
    openai.InternalServerError,
    httpx.TransportError,
    RedisConnectionError,
    RedisTimeoutError,
    OperationalError,
    InterfaceError,
    ConnectionError,
    TimeoutError,
)


def is_transient(exc: BaseException) -> bool:
    """True if `exc`, or an error it was raised from, is one that may pass on its own."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, TRANSIENT):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def backoff_s(job_try: int) -> int:
    return 10 * 3 ** (job_try - 1)


def retry_transient(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Retry `fn` on transient errors. Register it with `max_tries=TRIES`."""

    @functools.wraps(fn)
    async def wrapper(ctx: dict, *args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(ctx, *args, **kwargs)
        except Exception as exc:
            job_try = ctx.get("job_try") or 1
            if job_try >= TRIES or not is_transient(exc):
                raise
            delay = backoff_s(job_try)
            log.warning(
                "%s hit a passing error, trying again in %d s: %s",
                fn.__name__,
                delay,
                exc,
                extra={"attempt": job_try, "error": type(exc).__name__},
            )
            raise Retry(defer=delay) from exc

    return wrapper
