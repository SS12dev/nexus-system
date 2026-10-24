"""Alerts: a check every ALERT_INTERVAL_S that tells the owner when something needs them.

One API replica at a time runs it (a Valkey lock), so there is one email per problem however
many replicas run. A problem has to show in two checks in a row before it is raised, so a single
slow answer doesn't page anyone; when it clears, a "resolved" note follows. Active alerts and
the last 50 changes live in Valkey and show on the Admin page; with SMTP set up
(SMTP_HOST, SMTP_FROM) and ALERT_EMAIL=true, every owner also gets an email.

What is checked: each service on the Status tab (Postgres, Valkey, LiteLLM, MinIO, both
workers), a stale backup, API errors (5xx) in the last 5 minutes, failed jobs in the last 15,
and, from LiteLLM's spend logs, failed model calls in the last 15 minutes and model spend since
midnight UTC; then anyone who has spent most of their budget. RULES lists them for the page.
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from fastapi import FastAPI
from redis.exceptions import RedisError
from sqlalchemy import select

from nexus_api.cache import cached_spend
from nexus_api.ratelimit import metrics_key
from nexus_common import mail, metrics
from nexus_common.litellm import PROVIDER, get_budget
from nexus_common.metrics import ALERTS_HISTORY_KEY, ALERTS_KEY
from nexus_common.models import (
    ApiKey,
    Backup,
    BackupStatus,
    Role,
    RoleName,
    User,
    UserRole,
    UserStatus,
    utcnow,
)
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

LOCK_KEY = "nexus:alerts:lock"
PENDING_KEY = "nexus:alerts:pending"
HISTORY_KEEP = 50


@dataclass
class Problem:
    key: str
    title: str
    detail: str


async def find_problems(app: FastAPI) -> list[Problem]:
    from nexus_api.routes.ops import _services  # routes import this module's neighbours

    state = app.state
    settings: Settings = state.settings
    problems: list[Problem] = []
    async with state.sessionmaker() as db:
        for s in await _services(db, state.redis, state.gateway, state.storage):
            if not s.ok:
                problems.append(Problem(f"service:{s.name}", f"{s.name} is down", s.detail))
        last_ok = await db.scalar(
            select(Backup.finished_at)
            .where(Backup.status == BackupStatus.ok)
            .order_by(Backup.finished_at.desc())
            .limit(1)
        )
    if last_ok is None or last_ok < utcnow() - timedelta(hours=settings.backup_stale_hours):
        when = last_ok.strftime("%d %b %H:%M UTC") if last_ok else "never"
        problems.append(Problem("backup", "Backups are out of date", f"Last good backup: {when}."))

    now = int(time.time() // 60)
    try:
        async with state.redis.pipeline(transaction=False) as pipe:
            for m in range(now - 4, now + 1):
                pipe.hget(metrics_key(m), "errors")
            errors = sum(int(v or 0) for v in await pipe.execute())
    except RedisError:
        errors = 0
    if settings.alert_api_errors_5m and errors >= settings.alert_api_errors_5m:
        problems.append(
            Problem(
                "api-errors", "The API is failing requests", f"{errors} errors (5xx) in 5 minutes."
            )
        )

    problems += await model_problems(app)

    jobs = (await metrics.summary(state.redis, 15))["jobs"]
    failed = {j["name"]: j["failed"] for j in jobs if j["failed"]}
    if settings.alert_job_failures_15m and sum(failed.values()) >= settings.alert_job_failures_15m:
        names = ", ".join(f"{n} ×{c}" for n, c in sorted(failed.items()))
        problems.append(
            Problem("job-failures", "Background jobs are failing", f"In 15 minutes: {names}.")
        )
    return problems


async def model_problems(app: FastAPI) -> list[Problem]:
    state = app.state
    settings: Settings = state.settings
    problems: list[Problem] = []
    now = utcnow()
    if settings.alert_llm_failures_15m:
        recent = await state.spend_logs.quick(now - timedelta(minutes=15), now)
        if recent and recent["failed"] >= settings.alert_llm_failures_15m:
            problems.append(
                Problem(
                    "llm-failures",
                    "Model calls are failing",
                    f"{recent['failed']} of {recent['calls']} calls failed in 15 minutes.",
                )
            )
    if settings.alert_daily_spend_usd:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today = await state.spend_logs.quick(midnight, now)
        if today and today["spend_usd"] > settings.alert_daily_spend_usd:
            problems.append(
                Problem(
                    "daily-spend",
                    "Model spend is high today",
                    f"${today['spend_usd']:.2f} since midnight UTC, over the "
                    f"${settings.alert_daily_spend_usd:.2f} limit.",
                )
            )
    if settings.alert_budget_percent:
        async with state.sessionmaker() as db:
            rows = (
                await db.execute(
                    select(User, ApiKey.secret)
                    .join(ApiKey, ApiKey.user_id == User.id)
                    .where(
                        User.status == UserStatus.active,
                        ApiKey.provider == PROVIDER,
                        ApiKey.revoked_at.is_(None),
                    )
                )
            ).all()
            budgets = {u.id: await get_budget(db, settings, u) for u, _ in rows}
            await db.commit()
        for user, secret in rows:
            spent = await cached_spend(state.redis, state.gateway, secret)
            cap = float(budgets[user.id].max_usd)
            if spent is None or cap <= 0:
                continue
            share = spent / cap * 100
            if share >= settings.alert_budget_percent:
                problems.append(
                    Problem(
                        f"budget:{user.id}",
                        f"{user.name or user.email} has used {min(share, 999):.0f}% of "
                        "their budget",
                        f"${spent:.2f} of ${cap:.2f} this period. Raise it on the People page "
                        "if that is expected.",
                    )
                )
    return problems


RULES = (
    ("Services", "Any service on the Status tab stops answering.", None),
    ("Backups", "The last good backup is older than the limit.", "backup_stale_hours"),
    (
        "API errors",
        "Requests fail with a server error (5xx) within 5 minutes.",
        "alert_api_errors_5m",
    ),
    ("Background jobs", "Jobs fail within 15 minutes.", "alert_job_failures_15m"),
    ("Model calls", "Calls to models fail within 15 minutes.", "alert_llm_failures_15m"),
    (
        "Daily spend",
        "Model calls since midnight UTC cost more than the limit.",
        "alert_daily_spend_usd",
    ),
    ("Budgets", "Someone has spent this share of their budget.", "alert_budget_percent"),
)


def rules(settings: Settings) -> list[dict]:
    """What is checked and its threshold now, for the Alerts tab."""
    return [
        {
            "name": name,
            "what": what,
            "setting": setting,
            "threshold": getattr(settings, setting) if setting else None,
            "on": bool(getattr(settings, setting)) if setting else True,
        }
        for name, what, setting in RULES
    ]


async def check_once(app: FastAPI) -> tuple[list[Problem], list[dict]]:
    """Run the checks and update the active alerts. Returns (raised, resolved)."""
    redis = app.state.redis
    problems = {p.key: p for p in await find_problems(app)}
    active = {k.decode(): json.loads(v) for k, v in (await redis.hgetall(ALERTS_KEY)).items()}
    pending = {k.decode() for k in await redis.hkeys(PENDING_KEY)}
    now = utcnow().isoformat(timespec="seconds")

    raised = [p for k, p in problems.items() if k not in active and k in pending]
    resolved = [dict(a, key=k) for k, a in active.items() if k not in problems]
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(PENDING_KEY)
        for k in problems:
            if k not in active and k not in pending:
                pipe.hset(PENDING_KEY, k, now)
        for p in raised:
            pipe.hset(
                ALERTS_KEY, p.key, json.dumps({"title": p.title, "detail": p.detail, "since": now})
            )
        for k, p in problems.items():
            if k in active and active[k].get("detail") != p.detail:
                pipe.hset(ALERTS_KEY, k, json.dumps(dict(active[k], detail=p.detail)))
        for a in resolved:
            pipe.hdel(ALERTS_KEY, a["key"])
        for p in raised:
            entry = {
                "key": p.key,
                "title": p.title,
                "detail": p.detail,
                "state": "raised",
                "at": now,
            }
            pipe.lpush(ALERTS_HISTORY_KEY, json.dumps(entry))
        for a in resolved:
            entry = {"key": a["key"], "title": a["title"], "state": "resolved", "at": now}
            pipe.lpush(ALERTS_HISTORY_KEY, json.dumps(entry))
        pipe.ltrim(ALERTS_HISTORY_KEY, 0, HISTORY_KEEP - 1)
        await pipe.execute()
    for p in raised:
        log.warning("Alert raised: %s", p.title, extra={"alert": p.key, "detail": p.detail})
    for a in resolved:
        log.info("Alert resolved: %s", a["title"], extra={"alert": a["key"]})
    if raised or resolved:
        await notify(app, raised, resolved)
    return raised, resolved


async def owner_emails(app: FastAPI) -> list[str]:
    async with app.state.sessionmaker() as db:
        rows = await db.scalars(
            select(User.email)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.name == RoleName.owner, User.status == UserStatus.active)
        )
        return list(rows.all())


async def notify(app: FastAPI, raised: list[Problem], resolved: list[dict]) -> None:
    settings: Settings = app.state.settings
    if not (settings.alert_email and settings.smtp_enabled):
        return
    to = await owner_emails(app)
    if not to:
        return
    lines = [f"Problem: {p.title}. {p.detail}" for p in raised]
    lines += [f"Resolved: {a['title']}." for a in resolved]
    subject = f"Nexus: {raised[0].title}" if raised else f"Nexus: resolved, {resolved[0]['title']}"
    body = (
        "\n".join(lines)
        + f"\n\nDetails on the Admin page: {settings.nexus_url.rstrip('/')}/admin\n"
    )
    try:
        await mail.send(settings, mail.message(settings, to, subject, body))
    except mail.MailError as exc:
        log.error("Could not email the alert: %s", exc)


async def run(app: FastAPI) -> None:
    """The loop the API's lifespan starts. Never raises; one failed check waits for the next."""
    interval = app.state.settings.alert_interval_s
    while True:
        await asyncio.sleep(interval)
        try:
            if await app.state.redis.set(LOCK_KEY, "1", nx=True, ex=max(1, interval - 5)):
                await check_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Alert check failed")


@contextlib.asynccontextmanager
async def running(app: FastAPI):
    if app.state.settings.alert_interval_s <= 0 or app.state.settings.env == "test":
        yield
        return
    task = asyncio.create_task(run(app))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def active_and_recent(redis) -> dict:
    try:
        active = await redis.hgetall(ALERTS_KEY)
        history = await redis.lrange(ALERTS_HISTORY_KEY, 0, HISTORY_KEEP - 1)
    except RedisError:
        return {"active": [], "recent": []}
    return {
        "active": sorted(
            (dict(json.loads(v), key=k.decode()) for k, v in active.items()),
            key=lambda a: a["since"],
            reverse=True,
        ),
        "recent": [json.loads(h) for h in history],
    }
