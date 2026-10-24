"""arq worker entry point: `arq nexus_doc_worker.worker.WorkerSettings`."""

import asyncio
import logging
import uuid

from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis

from nexus_common import config
from nexus_common.db import make_engine, make_sessionmaker
from nexus_common.embeddings import CachedEmbedder, LiteLLMEmbedder
from nexus_common.logs import configure_logging, traced_job
from nexus_common.queues import DOCS_QUEUE
from nexus_common.settings import get_settings
from nexus_common.storage import S3Storage
from nexus_doc_worker.ingest import DocDeps, embed_missing, ingest_file
from nexus_doc_worker.parse import docling_available

settings = get_settings()
configure_logging(settings, "doc-worker")


async def startup(ctx: dict) -> None:
    engine = make_engine(settings.database_url)
    ctx["engine"] = engine
    sessionmaker = make_sessionmaker(engine)
    # The owner's changes from the Admin page's Configuration tab, kept up to date while running.
    await config.load_and_apply(settings, sessionmaker, "doc-worker", starting=True)
    ctx["config_watch"] = asyncio.create_task(
        config.watch(settings, sessionmaker, ctx["redis"], "doc-worker")
    )
    ctx["deps"] = DocDeps(
        settings=settings,
        sessionmaker=sessionmaker,
        redis=Redis.from_url(settings.redis_url),
        storage=S3Storage(settings),
        embedder=CachedEmbedder(LiteLLMEmbedder(settings), settings.embedding_batch_size),
    )
    logging.getLogger(__name__).info("Docling available: %s", docling_available())


async def shutdown(ctx: dict) -> None:
    ctx["config_watch"].cancel()
    deps: DocDeps = ctx["deps"]
    await deps.embedder.inner.aclose()
    await deps.storage.aclose()
    await deps.redis.aclose()
    await ctx["engine"].dispose()


@traced_job
async def parse_file(ctx: dict, file_id: str) -> None:
    await ingest_file(ctx["deps"], uuid.UUID(file_id))


@traced_job
async def backfill_embeddings(ctx: dict) -> None:
    await embed_missing(ctx["deps"])


class WorkerSettings:
    functions = [parse_file]
    cron_jobs = [cron(backfill_embeddings, minute=set(range(0, 60, 10)), run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    queue_name = DOCS_QUEUE
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Parsing is CPU-bound; a few at a time keeps a laptop responsive.
    max_jobs = 2
    job_timeout = 900
    max_tries = 2
    # The Admin page reads <queue>:health-check to tell whether a worker is alive.
    health_check_interval = 30
