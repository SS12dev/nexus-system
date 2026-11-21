"""People's LiteLLM keys: what they may call, and widening keys made before an alias existed."""

import json
from decimal import Decimal

import httpx
from sqlalchemy import select

from nexus_common.litellm import USER_MODELS, LiteLLMAdmin, widen_user_keys
from nexus_common.models import ApiKey, User, utcnow


def _admin(settings, seen: list[tuple[str, dict]], fail_for: str | None = None) -> LiteLLMAdmin:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        seen.append((request.url.path, body))
        if fail_for and body.get("key") == fail_for:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"key": "sk-new"})

    admin = LiteLLMAdmin(settings)
    admin._client = httpx.AsyncClient(
        base_url="http://litellm", transport=httpx.MockTransport(handler)
    )
    return admin


async def test_new_keys_can_transcribe_and_old_ones_are_widened(owner, sessionmaker, settings):
    seen: list[tuple[str, dict]] = []
    admin = _admin(settings, seen)
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        await admin.generate_key(user, Decimal("5"), "30d")
        assert seen[-1][0] == "/key/generate"
        assert "nexus-transcribe" in seen[-1][1]["models"]

        db.add(ApiKey(user_id=user.id, provider="litellm", secret="sk-old", created_at=utcnow()))
        db.add(
            ApiKey(user_id=user.id, provider="litellm", secret="sk-gone", created_at=utcnow(),
                   revoked_at=utcnow())
        )  # fmt: skip
        await db.commit()
        seen.clear()
        assert await widen_user_keys(db, admin) >= 1
    widened = {b["key"]: b["models"] for p, b in seen if p == "/key/update"}
    assert widened.get("sk-old") == USER_MODELS and "sk-gone" not in widened

    # One key LiteLLM refuses doesn't stop the rest.
    failing = _admin(settings, [], fail_for="sk-old")
    async with sessionmaker() as db:
        await widen_user_keys(db, failing)
    await admin.aclose()
    await failing.aclose()
