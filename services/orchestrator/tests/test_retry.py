"""Retries for background jobs that are safe to run again (retry.py)."""

import httpx
import openai
import pytest
from arq import Retry

from nexus_common import logs
from nexus_orchestrator.retry import TRIES, is_transient, retry_transient


def _openai_timeout() -> Exception:
    return openai.APITimeoutError(request=httpx.Request("POST", "http://litellm/v1/chat"))


def test_passing_errors_are_told_apart_from_bugs():
    assert is_transient(_openai_timeout())
    assert is_transient(httpx.ConnectError("refused"))
    assert not is_transient(ValueError("bad json"))
    # Wrapped: the cause counts.
    try:
        try:
            raise ConnectionResetError("reset")
        except ConnectionResetError as exc:
            raise RuntimeError("could not build the profile") from exc
    except RuntimeError as wrapped:
        assert is_transient(wrapped)


async def test_a_passing_error_is_retried_with_backoff_until_the_last_try():
    calls = []

    @retry_transient
    async def rebuild_profile(ctx, user_id):
        calls.append(ctx["job_try"])
        raise _openai_timeout()

    with pytest.raises(Retry) as first:
        await rebuild_profile({"job_try": 1}, "u")
    assert first.value.defer_score == 10_000
    with pytest.raises(Retry) as second:
        await rebuild_profile({"job_try": 2}, "u")
    assert second.value.defer_score == 30_000
    with pytest.raises(openai.APITimeoutError):
        await rebuild_profile({"job_try": TRIES}, "u")
    assert calls == [1, 2, 3]


async def test_a_bug_fails_at_once():
    @retry_transient
    async def link_preview(ctx, link_id):
        raise KeyError("title")

    with pytest.raises(KeyError):
        await link_preview({"job_try": 1}, "l")


async def test_a_retry_is_not_logged_or_counted_as_a_failure(caplog):
    @logs.traced_job
    @retry_transient
    async def extract_memories(ctx, chat_id, assistant_message_id):
        raise httpx.ConnectError("litellm down")

    with pytest.raises(Retry):
        await extract_memories({"job_try": 1, "job_id": "j"}, "c", "m")
    assert not [r for r in caplog.records if r.getMessage() == "job failed"]
    assert any("trying again in 10 s" in r.getMessage() for r in caplog.records)
