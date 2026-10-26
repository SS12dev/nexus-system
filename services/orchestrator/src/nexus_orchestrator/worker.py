"""arq worker entry point: `arq nexus_orchestrator.worker.WorkerSettings`."""

import asyncio
import logging
import uuid
from functools import partial
from pathlib import Path

import httpx
from arq import cron, func
from arq.connections import RedisSettings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from nexus_common import config, widgets
from nexus_common.db import make_engine, make_sessionmaker
from nexus_common.deployer import DeployerClient
from nexus_common.embeddings import CachedEmbedder, LiteLLMEmbedder
from nexus_common.litellm import LiteLLMAdmin
from nexus_common.logs import configure_logging, traced_job, with_request_id
from nexus_common.settings import get_settings
from nexus_common.skills import sync_library
from nexus_common.storage import S3Storage
from nexus_orchestrator import deploy, improve, links, memory, notify, recall_eval, routines
from nexus_orchestrator.github import GitHubApp
from nexus_orchestrator.llm import chat_model
from nexus_orchestrator.project_cleanup import delete_project as _delete_project
from nexus_orchestrator.retry import TRIES, retry_transient
from nexus_orchestrator.sandbox import SandboxClient
from nexus_orchestrator.systems import learn, runner
from nexus_orchestrator.turn import TurnDeps
from nexus_orchestrator.turn import run_turn as _run_turn
from nexus_orchestrator.web import WebClient
from nexus_orchestrator.workbench import WorkbenchClient

settings = get_settings()
# Before WorkerSettings below reads job timeouts and cron times: the owner's stored changes.
config.preload(settings, "orchestrator")
configure_logging(settings, "orchestrator")
log = logging.getLogger(__name__)


def psycopg_url(url: str) -> str:
    """The checkpointer uses psycopg; the app's URL names the asyncpg driver."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def startup(ctx: dict) -> None:
    engine = make_engine(settings.database_url)
    ctx["engine"] = engine
    # Agent-system runs keep their LangGraph checkpoints here, so a run parked on an approval
    # or a pause survives restarts.
    pool = AsyncConnectionPool(
        psycopg_url(settings.database_url),
        min_size=1,
        max_size=8,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    ctx["pool"] = pool
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    sessionmaker = make_sessionmaker(engine)
    # Changes made on the Admin page's Configuration tab while this worker runs.
    ctx["config_watch"] = asyncio.create_task(
        config.watch(settings, sessionmaker, ctx["redis"], "orchestrator")
    )
    ctx["deps"] = TurnDeps(
        settings=settings,
        sessionmaker=sessionmaker,
        redis=Redis.from_url(settings.redis_url),
        gateway=LiteLLMAdmin(settings),
        model_factory=partial(chat_model, settings),
        embedder=CachedEmbedder(LiteLLMEmbedder(settings), settings.embedding_batch_size),
        # arq's own pool, already connected when startup runs.
        enqueue=with_request_id(ctx["redis"].enqueue_job),
        storage=S3Storage(settings),
        sandbox=SandboxClient(settings),
        workbench=WorkbenchClient(settings),
        github=GitHubApp(settings) if settings.github_enabled else None,
        deployer=DeployerClient(settings),
        web=WebClient(settings),
        google_http=httpx.AsyncClient(timeout=30),
        checkpointer=checkpointer,
    )
    async with ctx["deps"].sessionmaker() as db:
        changed = await sync_library(db, Path(settings.library_path))
    if changed:
        log.info("Skills library: %d core skills added or updated", changed)


async def shutdown(ctx: dict) -> None:
    ctx["config_watch"].cancel()
    deps: TurnDeps = ctx["deps"]
    await deps.embedder.inner.aclose()
    await deps.gateway.aclose()
    await deps.redis.aclose()
    await deps.storage.aclose()
    await deps.sandbox.aclose()
    await deps.workbench.aclose()
    if deps.github is not None:
        await deps.github.aclose()
    await deps.deployer.aclose()
    await deps.web.aclose()
    await deps.google_http.aclose()
    await ctx["pool"].close()
    await ctx["engine"].dispose()


@traced_job
async def run_turn(ctx: dict, chat_id: str, assistant_message_id: str) -> None:
    await _run_turn(ctx["deps"], uuid.UUID(chat_id), uuid.UUID(assistant_message_id))


@traced_job
@retry_transient
async def extract_memories(ctx: dict, chat_id: str, assistant_message_id: str) -> None:
    await memory.extract_memories(ctx["deps"], uuid.UUID(chat_id), uuid.UUID(assistant_message_id))


@traced_job
@retry_transient
async def rebuild_profile(ctx: dict, user_id: str) -> None:
    await memory.rebuild_profile(ctx["deps"], uuid.UUID(user_id))


@traced_job
async def run_system(ctx: dict, run_id: str) -> None:
    await runner.run_system(ctx["deps"], uuid.UUID(run_id))


@traced_job
async def continue_run(ctx: dict, run_id: str) -> None:
    await runner.continue_run(ctx["deps"], uuid.UUID(run_id))


@traced_job
async def draft_skill(ctx: dict, run_id: str) -> None:
    await learn.draft_skill(ctx["deps"], uuid.UUID(run_id))


@traced_job
async def deploy_app(ctx: dict, project_id: str) -> None:
    """Redeploy from the Apps page."""
    await deploy.deploy_project(ctx["deps"], uuid.UUID(project_id))


@traced_job
async def delete_project(ctx: dict, project_id: str) -> None:
    """Permanently remove a software project and its owned resources."""
    await _delete_project(ctx["deps"], uuid.UUID(project_id))


@traced_job
@retry_transient
async def link_preview(ctx: dict, link_id: str) -> None:
    """Fetch the preview of a link the user added in the Library."""
    await links.refresh_preview(ctx["deps"], uuid.UUID(link_id))


@traced_job
async def notify_access(ctx: dict, kind: str, request_id: str) -> None:
    """Email admins about a new access request, or the person about their approval."""
    await notify.access(ctx["deps"], kind, uuid.UUID(request_id), attempt=ctx.get("job_try", 1))


@traced_job
async def notify_feedback(ctx: dict, feedback_id: str) -> None:
    """Email the owners about feedback someone sent from the app."""
    await notify.feedback(ctx["deps"], uuid.UUID(feedback_id), attempt=ctx.get("job_try", 1))


@traced_job
async def self_review(ctx: dict, review_id: str) -> None:
    """A self-review the owner asked for on the Admin page."""
    await improve.run_review(ctx["deps"], uuid.UUID(review_id))


@traced_job
async def proposal_pr(ctx: dict, proposal_id: str) -> None:
    """The pull request for an approved code or tool proposal."""
    await improve.open_pr(ctx["deps"], uuid.UUID(proposal_id))


@traced_job
async def recall_check(ctx: dict, eval_id: str) -> None:
    """A recall check the owner started on the Admin page."""
    await recall_eval.run_eval(ctx["deps"], uuid.UUID(eval_id))


@traced_job
async def run_routine(ctx: dict, routine_id: str, scheduled_for: str) -> None:
    """One run of a routine, for the slot it was due (or "Run now")."""
    await routines.run_routine(
        ctx["deps"], uuid.UUID(routine_id), routines.parse_slot(scheduled_for)
    )


async def routines_due(ctx: dict) -> None:
    """Every minute: queue the routines whose time has come."""
    if not settings.routines_enabled:
        return
    if queued := await routines.due(ctx["deps"]):
        log.info("Queued %d routines", queued)


async def widgets_due(ctx: dict) -> None:
    """Every 15 seconds: ring the timers, alarms and reminders whose time has come."""
    deps: TurnDeps = ctx["deps"]
    async with deps.sessionmaker() as db:
        if rang := await widgets.fire_due(db, deps.redis, deps.settings):
            log.info("Rang %d widgets", rang)


@traced_job
async def daily_review(ctx: dict) -> None:
    """Starts a self-review when the last one is IMPROVE_EVERY_DAYS old."""
    await improve.scheduled(ctx["deps"])


@traced_job
async def every_ten_minutes(ctx: dict) -> None:
    deps: TurnDeps = ctx["deps"]
    summarised = await memory.summarize_chats(deps)
    embedded = await memory.embed_missing(deps)
    linked = await memory.link_graph(deps)
    if summarised or embedded or linked:
        log.info(
            "Summarised %d chats, embedded %d rows, linked %d into the graph",
            summarised,
            embedded,
            linked,
        )


@traced_job
async def nightly(ctx: dict) -> None:
    await memory.nightly(ctx["deps"])


# A review reads up to IMPROVE_MAX_REQUESTS requests in batches, then drafts its proposals.
REVIEW_TIMEOUT_S = 900


class WorkerSettings:
    functions = [
        run_turn,
        # Safe to run again (retry.py): they write only once they have their answer.
        func(extract_memories, max_tries=TRIES),
        func(rebuild_profile, max_tries=TRIES),
        draft_skill,
        func(link_preview, timeout=60, keep_result=0, max_tries=TRIES),
        # A run can take a while; when it parks on an approval or a pause the job ends and a
        # later continue_run picks it up from its checkpoint. keep_result=0 so the shared
        # run:<id> job id only blocks a second job while one is queued or running.
        func(run_system, timeout=settings.run_job_timeout_s, keep_result=0),
        func(continue_run, timeout=settings.run_job_timeout_s, keep_result=0),
        func(deploy_app, timeout=settings.deploy_timeout_s + 60, keep_result=0),
        func(delete_project, timeout=600, keep_result=0),
        # Emails are safe to retry: the job checks the request is still in the state it's about.
        func(notify_access, timeout=90, keep_result=0, max_tries=notify.MAX_TRIES),
        func(notify_feedback, timeout=90, keep_result=0, max_tries=notify.MAX_TRIES),
        func(self_review, timeout=REVIEW_TIMEOUT_S, keep_result=0),
        func(proposal_pr, timeout=120, keep_result=0),
        func(recall_check, timeout=900, keep_result=0),
        # A routine's turn is a chat turn; a team it starts runs as its own job.
        func(run_routine, timeout=600, keep_result=0),
    ]
    cron_jobs = [
        cron(routines_due, second=5, unique=True, timeout=60),
        cron(widgets_due, second={0, 15, 30, 45}, unique=True, timeout=30),
        cron(every_ten_minutes, minute=set(range(0, 60, 10)), unique=True),
        cron(
            nightly,
            hour=settings.memory_nightly_hour_utc,
            minute=settings.memory_nightly_minute_utc,
            unique=True,
            timeout=1800,
        ),
        # An hour after the nightly memory jobs; it does nothing unless a review is due.
        cron(
            daily_review,
            hour=(settings.memory_nightly_hour_utc + 1) % 24,
            minute=settings.memory_nightly_minute_utc,
            unique=True,
            timeout=REVIEW_TIMEOUT_S,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
    # The Admin page reads <queue>:health-check to tell whether a worker is alive.
    health_check_interval = 30
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 20
    # arq looks for new jobs every 0.5 s by default, which a chat turn waits on; checking
    # Redis every 50 ms costs next to nothing.
    poll_delay = 0.05
    job_timeout = 300
    # A turn streams to the user as it runs, so a retry would duplicate output.
    max_tries = 1
