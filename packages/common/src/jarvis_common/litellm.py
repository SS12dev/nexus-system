"""Admin client for the LiteLLM proxy: one virtual key per user carries that user's budget."""

import uuid
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_common.models import ApiKey, Budget, RoleName, User, utcnow
from jarvis_common.settings import Settings

PROVIDER = "litellm"
MODEL_ALIASES = ["jarvis-nano", "jarvis-mini", "jarvis-pro", "jarvis-max"]


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
                "key_alias": f"jarvis-user-{user.id}",
                "user_id": str(user.id),
                "models": MODEL_ALIASES,
                "max_budget": float(max_usd),
                "budget_duration": duration,
                "metadata": {"email": user.email, "source": "jarvis-api"},
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
                "key_alias": f"jarvis-run-{run_id}",
                "user_id": str(user.id),
                "models": models,
                "max_budget": float(max_usd),
                "metadata": {"run_id": str(run_id), "source": "jarvis-orchestrator"},
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

    async def set_blocked(self, key: str, blocked: bool) -> None:
        await self._post("/key/block" if blocked else "/key/unblock", {"key": key})

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
