import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from nexus_api import ws
from nexus_api.middleware import GuardMiddleware
from nexus_api.queue import ArqJobQueue, JobQueue
from nexus_api.routes import (
    admin,
    app_access,
    apps,
    auth,
    chats,
    files,
    instructions,
    links,
    me,
    memories,
    ops,
    projects,
    skills,
    systems,
)
from nexus_common.db import make_engine, make_sessionmaker
from nexus_common.deployer import DeployerClient
from nexus_common.embeddings import CachedEmbedder, Embedder, LiteLLMEmbedder
from nexus_common.litellm import LiteLLMAdmin
from nexus_common.settings import Settings, get_settings
from nexus_common.storage import S3Storage, Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(
    settings: Settings | None = None,
    queue: JobQueue | None = None,
    storage: Storage | None = None,
    embedder: Embedder | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.redis = Redis.from_url(settings.redis_url)
        app.state.gateway = LiteLLMAdmin(settings)
        app.state.storage = storage or S3Storage(settings)
        inner = embedder or LiteLLMEmbedder(settings)
        app.state.embedder = CachedEmbedder(inner, settings.embedding_batch_size)
        app.state.deployer = DeployerClient(settings)
        pool = None
        if queue is None:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            app.state.queue = ArqJobQueue(pool)
        else:
            app.state.queue = queue
        yield
        if pool is not None:
            await pool.aclose()
        await inner.aclose()
        await app.state.storage.aclose()
        await app.state.gateway.aclose()
        await app.state.redis.aclose()
        await app.state.deployer.aclose()
        await engine.dispose()

    app = FastAPI(title="Nexus API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.oauth = auth.make_oauth(settings)
    # Client for Google's token endpoints; None makes a short-lived one per call. Tests swap it.
    app.state.google_http = None
    # Holds only the OIDC state and nonce during the login redirect; sign-in uses its own cookie.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="nexus_oauth",
        same_site="lax",
        https_only=settings.session_cookie_secure,
        max_age=600,
    )
    # Outermost: refuses cross-site writes and over-limit callers before anything else runs.
    app.add_middleware(GuardMiddleware, settings=settings)

    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(chats.router)
    app.include_router(files.router)
    app.include_router(links.router)
    app.include_router(projects.router)
    app.include_router(apps.router)
    app.include_router(app_access.router)
    app.include_router(memories.router)
    app.include_router(skills.router)
    app.include_router(instructions.router)
    app.include_router(systems.router)
    app.include_router(admin.router)
    app.include_router(ops.router)
    app.include_router(ws.router)

    @app.get("/healthz", tags=["ops"])
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz():
        async with app.state.engine.connect() as conn:
            await conn.execute(text("select 1"))
        await app.state.redis.ping()
        return {"status": "ready"}

    return app
