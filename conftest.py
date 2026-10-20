"""Shared test fixtures. Tests need a real Postgres and Redis (CI provides both as services):
set TEST_DATABASE_URL and TEST_REDIS_URL to point elsewhere."""

import asyncio
import hashlib
import math
import os
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text

from jarvis_common.db import make_engine, make_sessionmaker
from jarvis_common.embeddings import CachedEmbedder, EmbeddingError
from jarvis_common.models import EMBEDDING_DIM
from jarvis_common.settings import Settings
from jarvis_common.storage import MemoryStorage

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/jarvis_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
DEV_TOKEN = "test-dev-token"
TABLES = (
    "projects, approvals, run_events, agent_runs, agent_systems, skill_usages, skill_versions, "
    "skills, instructions, tool_grants, memory_events, memories, episodes, profiles, attachments, "
    "file_chunks, files, backups, app_settings, "
    "embedding_cache, audit_log, messages, chats, budgets, google_grants, api_keys, sessions, "
    "user_roles, users"
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        secret_key="test-secret-key",
        dev_login_enabled=True,
        dev_login_token=DEV_TOKEN,
        litellm_base_url="http://litellm.invalid",
        litellm_master_key="sk-test-master",
        deployer_token="test-deployer-token",
        library_path=str(Path(__file__).parent / "library"),
        # Off by default so scripted models aren't asked to route; routing tests turn it on.
        route_model="",
    )


@pytest.fixture(scope="session")
async def engine(settings):
    engine = make_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.execute(text("drop schema public cascade"))
        await conn.execute(text("create schema public"))
    cfg = Config(str(Path(__file__).parent / "services/api/alembic.ini"))
    cfg.attributes["database_url"] = settings.database_url
    cfg.attributes["configure_logger"] = False
    # env.py calls asyncio.run, which can't nest inside the test loop.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessionmaker(engine):
    return make_sessionmaker(engine)


@pytest.fixture(autouse=True)
async def clean_db(engine):
    yield
    async with engine.begin() as conn:
        await conn.execute(text(f"truncate {TABLES} cascade"))


@pytest.fixture
async def redis(settings):
    client = Redis.from_url(settings.redis_url)
    yield client
    await client.aclose()


class FakeGateway:
    """Stands in for the LiteLLM admin API."""

    def __init__(self):
        self.keys: dict[str, dict] = {}
        self.calls: list[tuple] = []

    async def generate_key(self, user, max_usd: Decimal, duration: str) -> str:
        key = f"sk-{uuid.uuid4().hex[:12]}"
        self.keys[key] = {"user_id": str(user.id), "max_usd": max_usd, "blocked": False}
        self.calls.append(("generate", str(user.id), max_usd))
        return key

    async def update_budget(self, key: str, max_usd: Decimal, duration: str) -> None:
        self.keys[key]["max_usd"] = max_usd
        self.calls.append(("update_budget", key, max_usd))

    async def set_blocked(self, key: str, blocked: bool) -> None:
        self.keys[key]["blocked"] = blocked
        self.calls.append(("set_blocked", key, blocked))

    async def generate_run_key(self, user, run_id, max_usd, models) -> str:
        key = f"sk-run-{uuid.uuid4().hex[:12]}"
        self.keys[key] = {"user_id": str(user.id), "max_usd": max_usd, "run_id": str(run_id)}
        self.calls.append(("generate_run", str(run_id), max_usd))
        return key

    async def delete_key(self, key: str) -> None:
        self.keys.pop(key, None)
        self.calls.append(("delete", key))

    async def spend(self, key: str) -> float | None:
        if key not in self.keys:
            return None
        return self.keys[key].get("spend", 0.0)

    async def healthy(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


class FakeEmbedder:
    """Bag-of-words vectors: texts sharing words point the same way, so similarity search
    behaves sensibly in tests without a model."""

    model = "fake-embed"

    def __init__(self):
        self.calls = 0
        self.down = False

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], str]:
        if self.down:
            raise EmbeddingError("embedder down")
        self.calls += 1
        out = []
        for t in texts:
            vec = [0.0] * EMBEDDING_DIM
            for word in re.findall(r"[a-z0-9]+", t.lower()):
                h = int(hashlib.md5(word.encode()).hexdigest(), 16)
                vec[h % EMBEDDING_DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            if not any(vec):
                vec[0] = 1.0
            out.append(vec)
        return out, self.model

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def embedder(fake_embedder) -> CachedEmbedder:
    return CachedEmbedder(fake_embedder)


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()
