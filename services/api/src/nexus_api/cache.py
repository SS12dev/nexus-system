"""Short-lived Redis caches for slow lookups the web app repeats on every page load."""

import hashlib
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from nexus_common.litellm import LiteLLMAdmin
from nexus_common.modelnames import cached_names

log = logging.getLogger(__name__)

SPEND_TTL_S = 60


def _spend_key(secret: str) -> str:
    # Never store the key itself, only a hash of it.
    return "nexus:cache:spend:" + hashlib.sha256(secret.encode()).hexdigest()[:32]


async def cached_spend(redis: Redis, gateway: LiteLLMAdmin, secret: str) -> float | None:
    """A key's spend from LiteLLM, at most a minute old. LiteLLM itself only updates spend
    every few seconds, and /api/me runs on every page load."""
    key = _spend_key(secret)
    try:
        raw = await redis.get(key)
        if raw is not None:
            return json.loads(raw)
    except RedisError as exc:
        log.warning("Spend cache unavailable: %s", exc)
    spend = await gateway.spend(secret)
    if spend is not None:
        try:
            await redis.set(key, json.dumps(spend), ex=SPEND_TTL_S)
        except RedisError:
            pass
    return spend


async def cached_model_names(redis: Redis, gateway: LiteLLMAdmin) -> dict[str, dict[str, str]]:
    """The real model behind each alias ({"nexus-pro": {"model", "label", "provider"}}), at
    most five minutes old. Empty when LiteLLM can't be reached; the app then shows aliases."""
    return await cached_names(redis, gateway)
