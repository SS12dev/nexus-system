import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from jarvis_api import ws
from jarvis_api.queue import ArqTurnQueue, TurnQueue
from jarvis_api.routes import admin, auth, chats, me
from jarvis_common.db import make_engine, make_sessionmaker
from jarvis_common.litellm import LiteLLMAdmin
from jarvis_common.settings import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(settings: Settings | None = None, queue: TurnQueue | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.redis = Redis.from_url(settings.redis_url)
        app.state.gateway = LiteLLMAdmin(settings)
        pool = None
        if queue is None:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            app.state.queue = ArqTurnQueue(pool)
        else:
            app.state.queue = queue
        yield
        if pool is not None:
            await pool.aclose()
        await app.state.gateway.aclose()
        await app.state.redis.aclose()
        await engine.dispose()

    app = FastAPI(title="Jarvis API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.oauth = auth.make_oauth(settings)
    # Holds only the OIDC state and nonce during the login redirect; sign-in uses its own cookie.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="jarvis_oauth",
        same_site="lax",
        https_only=settings.session_cookie_secure,
        max_age=600,
    )

    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(chats.router)
    app.include_router(admin.router)
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
