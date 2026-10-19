"""arq worker entry point: `arq jarvis_orchestrator.worker.WorkerSettings`."""

import logging
import uuid
from functools import partial

from arq.connections import RedisSettings
from redis.asyncio import Redis

from jarvis_common.db import make_engine, make_sessionmaker
from jarvis_common.litellm import LiteLLMAdmin
from jarvis_common.settings import get_settings
from jarvis_orchestrator.llm import chat_model
from jarvis_orchestrator.turn import TurnDeps
from jarvis_orchestrator.turn import run_turn as _run_turn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = get_settings()


async def startup(ctx: dict) -> None:
    engine = make_engine(settings.database_url)
    ctx["engine"] = engine
    ctx["deps"] = TurnDeps(
        settings=settings,
        sessionmaker=make_sessionmaker(engine),
        redis=Redis.from_url(settings.redis_url),
        gateway=LiteLLMAdmin(settings),
        model_factory=partial(chat_model, settings),
    )


async def shutdown(ctx: dict) -> None:
    deps: TurnDeps = ctx["deps"]
    await deps.gateway.aclose()
    await deps.redis.aclose()
    await ctx["engine"].dispose()


async def run_turn(ctx: dict, chat_id: str, assistant_message_id: str) -> None:
    await _run_turn(ctx["deps"], uuid.UUID(chat_id), uuid.UUID(assistant_message_id))


class WorkerSettings:
    functions = [run_turn]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 20
    job_timeout = 300
    # A turn streams to the user as it runs, so a retry would duplicate output.
    max_tries = 1
