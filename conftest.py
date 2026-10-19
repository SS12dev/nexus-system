"""Shared test fixtures. Tests need a real Postgres and Redis (CI provides both as services):
set TEST_DATABASE_URL and TEST_REDIS_URL to point elsewhere."""

import asyncio
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text

from jarvis_common.db import make_engine, make_sessionmaker
from jarvis_common.settings import Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/jarvis_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
DEV_TOKEN = "test-dev-token"
TABLES = "audit_log, messages, chats, budgets, api_keys, sessions, user_roles, users"


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

    async def spend(self, key: str) -> float | None:
        return 0.0 if key in self.keys else None

    async def aclose(self) -> None:
        pass


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()
