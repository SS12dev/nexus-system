"""arq worker entry point: `arq jarvis_orchestrator.worker.WorkerSettings`."""

import logging
import uuid
from functools import partial

from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis

from jarvis_common.db import make_engine, make_sessionmaker
from jarvis_common.embeddings import CachedEmbedder, LiteLLMEmbedder
from jarvis_common.litellm import LiteLLMAdmin
from jarvis_common.settings import get_settings
from jarvis_orchestrator import memory
from jarvis_orchestrator.llm import chat_model
from jarvis_orchestrator.turn import TurnDeps
from jarvis_orchestrator.turn import run_turn as _run_turn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)
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
        embedder=CachedEmbedder(LiteLLMEmbedder(settings), settings.embedding_batch_size),
        # arq's own pool, already connected when startup runs.
        enqueue=ctx["redis"].enqueue_job,
    )


async def shutdown(ctx: dict) -> None:
    deps: TurnDeps = ctx["deps"]
    await deps.embedder.inner.aclose()
    await deps.gateway.aclose()
    await deps.redis.aclose()
    await ctx["engine"].dispose()


async def run_turn(ctx: dict, chat_id: str, assistant_message_id: str) -> None:
    await _run_turn(ctx["deps"], uuid.UUID(chat_id), uuid.UUID(assistant_message_id))


async def extract_memories(ctx: dict, chat_id: str, assistant_message_id: str) -> None:
    await memory.extract_memories(ctx["deps"], uuid.UUID(chat_id), uuid.UUID(assistant_message_id))


async def rebuild_profile(ctx: dict, user_id: str) -> None:
    await memory.rebuild_profile(ctx["deps"], uuid.UUID(user_id))


async def every_ten_minutes(ctx: dict) -> None:
    deps: TurnDeps = ctx["deps"]
    summarised = await memory.summarize_chats(deps)
    embedded = await memory.embed_missing(deps)
    if summarised or embedded:
        log.info("Summarised %d chats, embedded %d rows", summarised, embedded)


async def nightly(ctx: dict) -> None:
    await memory.nightly(ctx["deps"])


class WorkerSettings:
    functions = [run_turn, extract_memories, rebuild_profile]
    cron_jobs = [
        cron(every_ten_minutes, minute=set(range(0, 60, 10)), unique=True),
        cron(
            nightly,
            hour=settings.memory_nightly_hour_utc,
            minute=settings.memory_nightly_minute_utc,
            unique=True,
            timeout=1800,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 20
    job_timeout = 300
    # A turn streams to the user as it runs, so a retry would duplicate output.
    max_tries = 1
