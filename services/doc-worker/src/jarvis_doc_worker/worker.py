"""arq worker entry point: `arq jarvis_doc_worker.worker.WorkerSettings`."""

import logging
import uuid

from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis

from jarvis_common.db import make_engine, make_sessionmaker
from jarvis_common.embeddings import CachedEmbedder, LiteLLMEmbedder
from jarvis_common.queues import DOCS_QUEUE
from jarvis_common.settings import get_settings
from jarvis_common.storage import S3Storage
from jarvis_doc_worker.ingest import DocDeps, embed_missing, ingest_file
from jarvis_doc_worker.parse import docling_available

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = get_settings()


async def startup(ctx: dict) -> None:
    engine = make_engine(settings.database_url)
    ctx["engine"] = engine
    ctx["deps"] = DocDeps(
        settings=settings,
        sessionmaker=make_sessionmaker(engine),
        redis=Redis.from_url(settings.redis_url),
        storage=S3Storage(settings),
        embedder=CachedEmbedder(LiteLLMEmbedder(settings), settings.embedding_batch_size),
    )
    logging.getLogger(__name__).info("Docling available: %s", docling_available())


async def shutdown(ctx: dict) -> None:
    deps: DocDeps = ctx["deps"]
    await deps.embedder.inner.aclose()
    await deps.storage.aclose()
    await deps.redis.aclose()
    await ctx["engine"].dispose()


async def parse_file(ctx: dict, file_id: str) -> None:
    await ingest_file(ctx["deps"], uuid.UUID(file_id))


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
