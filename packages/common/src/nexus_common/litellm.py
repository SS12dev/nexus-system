"""Admin client for the LiteLLM proxy: one virtual key per user carries that user's budget."""

import logging
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import ApiKey, Budget, RoleName, User, utcnow
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

PROVIDER = "litellm"
MODEL_ALIASES = ["nexus-nano", "nexus-mini", "nexus-pro", "nexus-max"]
# What a person's own key may call: the chat tiers plus speech to text (Telegram voice notes).
USER_MODELS = [*MODEL_ALIASES, "nexus-transcribe"]


class GatewayError(RuntimeError):
    pass


class LiteLLMAdmin:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.litellm_base_url,
            headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
            timeout=15,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: dict) -> dict:
        try:
            resp = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise GatewayError(f"LiteLLM unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise GatewayError(f"LiteLLM {path} failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    async def generate_key(self, user: User, max_usd: Decimal, duration: str) -> str:
        data = await self._post(
            "/key/generate",
            {
                "key_alias": f"nexus-user-{user.id}",
                "user_id": str(user.id),
                "models": USER_MODELS,
                "max_budget": float(max_usd),
                "budget_duration": duration,
                "metadata": {"email": user.email, "source": "nexus-api"},
            },
        )
        return data["key"]

    async def generate_run_key(
        self, user: User, run_id: uuid.UUID, max_usd: Decimal, models: list[str]
    ) -> str:
        """A key for one agent-system run, capped at the run's budget. Calls with it are still
        attributed to the user in LiteLLM and Langfuse."""
        data = await self._post(
            "/key/generate",
            {
                "key_alias": f"nexus-run-{run_id}",
                "user_id": str(user.id),
                "models": models,
                "max_budget": float(max_usd),
                "metadata": {"run_id": str(run_id), "source": "nexus-orchestrator"},
            },
        )
        return data["key"]

    async def delete_key(self, key: str) -> None:
        await self._post("/key/delete", {"keys": [key]})

    async def update_budget(self, key: str, max_usd: Decimal, duration: str) -> None:
        await self._post(
            "/key/update",
            {"key": key, "max_budget": float(max_usd), "budget_duration": duration},
        )

    async def set_models(self, key: str, models: list[str]) -> None:
        await self._post("/key/update", {"key": key, "models": models})

    async def set_blocked(self, key: str, blocked: bool) -> None:
        await self._post("/key/block" if blocked else "/key/unblock", {"key": key})

    async def healthy(self) -> bool:
        try:
            resp = await self._client.get("/health/liveliness", timeout=5)
        except httpx.HTTPError:
            return False
        return resp.status_code < 400

    async def model_info(self) -> dict | None:
        """LiteLLM's model list with the real model behind each alias, or None when down."""
        try:
            resp = await self._client.get("/model/info", timeout=5)
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        return resp.json()

    async def spend(self, key: str) -> float | None:
        try:
            resp = await self._client.get("/key/info", params={"key": key})
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        return resp.json().get("info", {}).get("spend")


def default_budget(settings: Settings, role: RoleName) -> Decimal:
    usd = settings.owner_budget_usd if role == RoleName.owner else settings.default_budget_usd
    return Decimal(str(usd)).quantize(Decimal("0.01"))


async def get_budget(session: AsyncSession, settings: Settings, user: User) -> Budget:
    budget = await session.get(Budget, user.id)
    if budget is None:
        budget = Budget(
            user_id=user.id,
            max_usd=default_budget(settings, user.role),
            duration=settings.litellm_budget_duration,
        )
        session.add(budget)
        await session.flush()
    return budget


async def active_key(session: AsyncSession, user_id: uuid.UUID) -> ApiKey | None:
    return await session.scalar(
        select(ApiKey).where(
            ApiKey.user_id == user_id,
            ApiKey.provider == PROVIDER,
            ApiKey.revoked_at.is_(None),
        )
    )


async def ensure_user_key(
    session: AsyncSession, settings: Settings, admin: LiteLLMAdmin, user: User
) -> str:
    """Return the key the user's model calls should use, minting a virtual key on first use."""
    if not settings.litellm_virtual_keys:
        return settings.litellm_api_key
    existing = await active_key(session, user.id)
    if existing:
        return existing.secret
    budget = await get_budget(session, settings, user)
    await session.commit()
    secret = await admin.generate_key(user, budget.max_usd, budget.duration)
    session.add(ApiKey(user_id=user.id, provider=PROVIDER, secret=secret, created_at=utcnow()))
    try:
        await session.commit()
    except IntegrityError:
        # Another worker minted one first; use theirs.
        await session.rollback()
        existing = await active_key(session, user.id)
        if existing is None:
            raise
        return existing.secret
    return secret


async def widen_user_keys(session: AsyncSession, admin: LiteLLMAdmin) -> int:
    """Give every person's key the models in USER_MODELS. Keys made before an alias was added
    (nexus-transcribe, 2026-11) can't call it otherwise. Safe to repeat; run at worker start."""
    keys = (
        await session.scalars(
            select(ApiKey).where(ApiKey.provider == PROVIDER, ApiKey.revoked_at.is_(None))
        )
    ).all()
    done = 0
    for key in keys:
        try:
            await admin.set_models(key.secret, USER_MODELS)
            done += 1
        except GatewayError as exc:
            log.warning(
                "litellm.widen_failed", extra={"user_id": str(key.user_id), "error": str(exc)[:200]}
            )
    return done
