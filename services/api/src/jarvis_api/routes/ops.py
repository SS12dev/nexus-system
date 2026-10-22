"""The Admin page: health of every service, traffic, usage per person, the audit log, backups
and maintenance mode. Owners only. They see counts, costs and error messages, never chat
content.

Status, usage and the audit log take `user_id` (repeatable) to narrow every per-person figure
to those people. Service health, queues, API traffic and backups are Jarvis-wide and ignore it."""

import asyncio
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import Date, cast, func, or_, select, text
from sqlalchemy.orm import aliased

from jarvis_api.appsettings import MAINTENANCE, get_setting, maintenance_notice, put_setting
from jarvis_api.cache import cached_spend
from jarvis_api.deps import DbDep, GatewayDep, OwnerUser, RedisDep, SettingsDep, StorageDep
from jarvis_api.ratelimit import metrics_key
from jarvis_api.schemas import MaintenanceIn
from jarvis_common.litellm import PROVIDER
from jarvis_common.models import (
    AgentRun,
    ApiKey,
    AuditLog,
    Backup,
    BackupStatus,
    Budget,
    Chat,
    File,
    FileStatus,
    Message,
    MessageRole,
    MessageStatus,
    RunStatus,
    Session,
    User,
    UserStatus,
    utcnow,
)
from jarvis_common.queues import DOCS_QUEUE, HEALTH_SUFFIX, ORCHESTRATOR_QUEUE

router = APIRouter(prefix="/api/admin", tags=["admin"])

LIVE_RUNS = (RunStatus.queued, RunStatus.running, RunStatus.awaiting_approval, RunStatus.paused)

UsersFilter = Query(default=[], alias="user_id", max_length=200)


def _only(q, column, user_ids: list[uuid.UUID]):
    """Narrow a query to the chosen people; no choice means everyone."""
    return q.where(column.in_(user_ids)) if user_ids else q


class ServiceOut(BaseModel):
    name: str
    ok: bool
    detail: str = ""


CHECK_TIMEOUT_S = 3


async def _check(name: str, coro) -> ServiceOut:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S):
            detail = await coro
    except TimeoutError:
        return ServiceOut(name=name, ok=False, detail=f"no answer within {CHECK_TIMEOUT_S} s")
    except Exception as exc:  # noqa: BLE001 - any failure means the service is down
        return ServiceOut(name=name, ok=False, detail=str(exc)[:200] or type(exc).__name__)
    if detail is False:
        return ServiceOut(name=name, ok=False, detail="not responding")
    return ServiceOut(name=name, ok=True, detail=detail if isinstance(detail, str) else "")


async def _services(db, redis, gateway, storage) -> list[ServiceOut]:
    async def postgres():
        size = await db.scalar(text("select pg_database_size(current_database())"))
        conns = await db.scalar(text("select count(*) from pg_stat_activity"))
        return f"{size / 1e6:.0f} MB, {conns} connections"

    async def valkey():
        info = await redis.info("memory")
        return f"{info.get('used_memory_human', '?')} used"

    async def worker(queue: str):
        info = await redis.get(queue + HEALTH_SUFFIX)
        if info is None:
            raise RuntimeError("no heartbeat in the last minute")
        info = info.decode() if isinstance(info, bytes) else info
        # "Oct-20 11:00:00 j_complete=3 j_failed=0 j_retried=0 j_ongoing=1 queued=0"
        stats = dict(p.split("=", 1) for p in info.split() if "=" in p)
        return f"{stats.get('j_ongoing', '0')} running, {stats.get('j_failed', '0')} failed"

    async def object_store():
        await storage.ping()
        return ""

    # Postgres first, alone: the session can't run two queries at once.
    checks = [await _check("Postgres", postgres())]
    checks += await asyncio.gather(
        _check("Valkey (cache and queues)", valkey()),
        _check("LiteLLM (model gateway)", gateway.healthy()),
        _check("MinIO (files)", object_store()),
        _check("Orchestrator worker", worker(ORCHESTRATOR_QUEUE)),
        _check("Document worker", worker(DOCS_QUEUE)),
    )
    return checks


async def _traffic(redis, minutes: int = 60) -> list[dict]:
    now = int(time.time() // 60)
    keys = [metrics_key(m) for m in range(now - minutes + 1, now + 1)]
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for k in keys:
                pipe.hgetall(k)
            rows = await pipe.execute()
    except RedisError:
        rows = [{} for _ in keys]
    out = []
    for i, row in enumerate(rows):
        row = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in row.items()}
        n = row.get("requests", 0)
        out.append(
            {
                "minute": datetime.fromtimestamp((now - minutes + 1 + i) * 60, UTC),
                "requests": n,
                "errors": row.get("errors", 0),
                "limited": row.get("limited", 0),
                "avg_ms": round(row.get("ms", 0) / n) if n else None,
            }
        )
    return out


async def _queues(redis) -> dict:
    try:
        turns, docs = await asyncio.gather(redis.zcard(ORCHESTRATOR_QUEUE), redis.zcard(DOCS_QUEUE))
    except RedisError:
        return {"orchestrator": None, "documents": None}
    return {"orchestrator": turns, "documents": docs}


@router.get("/status")
async def system_status(
    actor: OwnerUser,
    db: DbDep,
    redis: RedisDep,
    gateway: GatewayDep,
    storage: StorageDep,
    settings: SettingsDep,
    user_ids: list[uuid.UUID] = UsersFilter,
):
    since = utcnow() - timedelta(hours=24)
    services = await _services(db, redis, gateway, storage)

    people = dict(
        (
            await db.execute(
                _only(select(User.status, func.count()), User.id, user_ids).group_by(User.status)
            )
        ).all()
    )
    signed_in = await db.scalar(
        _only(
            select(func.count(func.distinct(Session.user_id))).where(Session.created_at >= since),
            Session.user_id,
            user_ids,
        )
    )
    turns = (
        await db.execute(
            _only(
                select(
                    func.count(),
                    func.count().filter(Message.status == MessageStatus.failed),
                    func.coalesce(func.sum(Message.input_tokens), 0),
                    func.coalesce(func.sum(Message.output_tokens), 0),
                )
                .join(Chat, Chat.id == Message.chat_id)
                .where(Message.role == MessageRole.assistant, Message.created_at >= since),
                Chat.user_id,
                user_ids,
            )
        )
    ).one()
    runs = (
        await db.execute(
            _only(
                select(
                    func.count().filter(AgentRun.status.in_(LIVE_RUNS)),
                    func.count().filter(
                        AgentRun.status == RunStatus.failed, AgentRun.created_at >= since
                    ),
                    func.coalesce(
                        func.sum(AgentRun.cost_usd).filter(AgentRun.created_at >= since), 0
                    ),
                ),
                AgentRun.user_id,
                user_ids,
            )
        )
    ).one()
    files = dict(
        (
            await db.execute(
                _only(
                    select(File.status, func.count()).where(
                        File.status.in_([FileStatus.processing, FileStatus.failed])
                    ),
                    File.user_id,
                    user_ids,
                ).group_by(File.status)
            )
        ).all()
    )

    # Spend this budget period, from LiteLLM (cached for a minute per key).
    keys = (
        await db.scalars(
            _only(
                select(ApiKey.secret).where(
                    ApiKey.provider == PROVIDER, ApiKey.revoked_at.is_(None)
                ),
                ApiKey.user_id,
                user_ids,
            )
        )
    ).all()
    spends = await asyncio.gather(*(cached_spend(redis, gateway, k) for k in keys))
    budget_total = await db.scalar(
        _only(
            select(func.coalesce(func.sum(Budget.max_usd), 0))
            .join(User, User.id == Budget.user_id)
            .where(User.status != UserStatus.disabled),
            User.id,
            user_ids,
        )
    )

    last_ok = await db.scalar(
        select(Backup).where(Backup.status == BackupStatus.ok).order_by(Backup.finished_at.desc())
    )
    last_any = await db.scalar(select(Backup).order_by(Backup.created_at.desc()))
    stale = last_ok is None or last_ok.finished_at < utcnow() - timedelta(
        hours=settings.backup_stale_hours
    )

    return {
        "services": services,
        "queues": await _queues(redis),
        "traffic": await _traffic(redis),
        "people": {
            "active": people.get(UserStatus.active, 0),
            "invited": people.get(UserStatus.invited, 0),
            "disabled": people.get(UserStatus.disabled, 0),
            "signed_in_24h": signed_in,
        },
        "last_24h": {
            "turns": turns[0],
            "failed_turns": turns[1],
            "input_tokens": turns[2],
            "output_tokens": turns[3],
            "failed_runs": runs[1],
            "run_cost_usd": float(runs[2]),
        },
        "runs_live": runs[0],
        "files": {
            "processing": files.get(FileStatus.processing, 0),
            "failed": files.get(FileStatus.failed, 0),
        },
        "spend": {
            "period_usd": round(sum(s or 0 for s in spends), 4),
            "budgets_usd": float(budget_total),
            "unknown": sum(s is None for s in spends),
        },
        "backup": {
            "last_ok": _backup_out(last_ok) if last_ok else None,
            "latest": _backup_out(last_any) if last_any else None,
            "stale": stale,
        },
        "maintenance": await maintenance_notice(db),
        "failures": await _recent_failures(db, user_ids),
        "links": {"langfuse": settings.langfuse_url},
    }


async def _recent_failures(db, user_ids: list[uuid.UUID], limit: int = 15) -> list[dict]:
    """The latest failed turns, runs and file parses: who, when and the error, not content."""
    turns = await db.execute(
        _only(
            select(Message.created_at, Message.error, User.email)
            .join(Chat, Chat.id == Message.chat_id)
            .join(User, User.id == Chat.user_id)
            .where(Message.status == MessageStatus.failed),
            User.id,
            user_ids,
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    runs = await db.execute(
        _only(
            select(AgentRun.created_at, AgentRun.error, User.email)
            .join(User, User.id == AgentRun.user_id)
            .where(AgentRun.status == RunStatus.failed),
            User.id,
            user_ids,
        )
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    files = await db.execute(
        _only(
            select(File.created_at, File.error, User.email)
            .join(User, User.id == File.user_id)
            .where(File.status == FileStatus.failed),
            User.id,
            user_ids,
        )
        .order_by(File.created_at.desc())
        .limit(limit)
    )
    out = [
        {"kind": kind, "at": at, "error": (error or "")[:300], "user": email}
        for kind, rows in (("turn", turns), ("run", runs), ("file", files))
        for at, error, email in rows.all()
    ]
    out.sort(key=lambda r: r["at"], reverse=True)
    return out[:limit]


@router.get("/usage")
async def usage(
    actor: OwnerUser,
    db: DbDep,
    days: int = Query(default=7, ge=1, le=90),
    user_ids: list[uuid.UUID] = UsersFilter,
):
    """Per person and per day over the last `days` days."""
    since = utcnow() - timedelta(days=days)
    msg_user = (
        select(
            Chat.user_id.label("user_id"),
            func.count().label("turns"),
            func.count().filter(Message.status == MessageStatus.failed).label("failed"),
            func.coalesce(func.sum(Message.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(Message.output_tokens), 0).label("output_tokens"),
            func.max(Message.created_at).label("last_active"),
        )
        .join(Chat, Chat.id == Message.chat_id)
        .where(Message.role == MessageRole.assistant, Message.created_at >= since)
        .group_by(Chat.user_id)
        .subquery()
    )
    run_user = (
        select(
            AgentRun.user_id.label("user_id"),
            func.count().label("runs"),
            func.coalesce(func.sum(AgentRun.cost_usd), 0).label("run_cost"),
        )
        .where(AgentRun.created_at >= since)
        .group_by(AgentRun.user_id)
        .subquery()
    )
    file_user = (
        select(File.user_id.label("user_id"), func.count().label("files"))
        .where(File.created_at >= since)
        .group_by(File.user_id)
        .subquery()
    )
    rows = await db.execute(
        _only(
            select(
                User.id,
                User.email,
                User.name,
                func.coalesce(msg_user.c.turns, 0),
                func.coalesce(msg_user.c.failed, 0),
                func.coalesce(msg_user.c.input_tokens, 0),
                func.coalesce(msg_user.c.output_tokens, 0),
                msg_user.c.last_active,
                func.coalesce(run_user.c.runs, 0),
                func.coalesce(run_user.c.run_cost, 0),
                func.coalesce(file_user.c.files, 0),
            )
            .outerjoin(msg_user, msg_user.c.user_id == User.id)
            .outerjoin(run_user, run_user.c.user_id == User.id)
            .outerjoin(file_user, file_user.c.user_id == User.id),
            User.id,
            user_ids,
        ).order_by(func.coalesce(msg_user.c.turns, 0).desc(), User.email)
    )
    people = [
        {
            "user_id": r[0],
            "email": r[1],
            "name": r[2],
            "turns": r[3],
            "failed_turns": r[4],
            "input_tokens": r[5],
            "output_tokens": r[6],
            "last_active": r[7],
            "runs": r[8],
            "run_cost_usd": float(r[9]),
            "files": r[10],
        }
        for r in rows.all()
    ]

    day = cast(func.timezone("UTC", Message.created_at), Date)
    msg_days = dict(
        (d, (n, f, t))
        for d, n, f, t in (
            await db.execute(
                _only(
                    select(
                        day,
                        func.count(),
                        func.count().filter(Message.status == MessageStatus.failed),
                        func.coalesce(func.sum(Message.input_tokens + Message.output_tokens), 0),
                    )
                    .join(Chat, Chat.id == Message.chat_id)
                    .where(Message.role == MessageRole.assistant, Message.created_at >= since),
                    Chat.user_id,
                    user_ids,
                ).group_by(day)
            )
        ).all()
    )
    run_day = cast(func.timezone("UTC", AgentRun.created_at), Date)
    run_days = dict(
        (
            await db.execute(
                _only(
                    select(run_day, func.coalesce(func.sum(AgentRun.cost_usd), 0)).where(
                        AgentRun.created_at >= since
                    ),
                    AgentRun.user_id,
                    user_ids,
                ).group_by(run_day)
            )
        ).all()
    )
    today = datetime.now(UTC).date()
    series = []
    for i in range(days - 1, -1, -1):
        d: date = today - timedelta(days=i)
        n, f, t = msg_days.get(d, (0, 0, 0))
        series.append(
            {
                "day": d.isoformat(),
                "turns": n,
                "failed_turns": f,
                "tokens": t,
                "run_cost_usd": float(run_days.get(d, Decimal(0))),
            }
        )
    return {"days": days, "people": people, "series": series}


@router.get("/audit")
async def audit_log(
    actor: OwnerUser,
    db: DbDep,
    action: str | None = Query(default=None, max_length=64),
    before: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    user_ids: list[uuid.UUID] = UsersFilter,
):
    """Admin actions, sign-ins, approvals and other recorded events, newest first. `action`
    matches a prefix, e.g. "user." or "approval.". With `user_id`, only events those people
    did or that were done to them."""
    Actor = aliased(User)
    q = select(AuditLog, Actor.email).outerjoin(Actor, Actor.id == AuditLog.actor_id)
    if action:
        escaped = action.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        q = q.where(AuditLog.action.like(escaped + "%", escape="\\"))
    if before:
        q = q.where(AuditLog.created_at < before)
    if user_ids:
        q = q.where(
            or_(
                AuditLog.actor_id.in_(user_ids),
                AuditLog.target.in_([str(u) for u in user_ids]),
            )
        )
    rows = await db.execute(q.order_by(AuditLog.created_at.desc()).limit(limit))
    out = []
    for entry, email in rows.all():
        out.append(
            {
                "id": entry.id,
                "at": entry.created_at,
                "action": entry.action,
                "actor": email,
                "target": entry.target,
                "data": entry.data,
            }
        )
    # Say who a user id points at, so "user.updated <uuid>" reads as a person.
    targets = {uuid.UUID(e["target"]) for e in out if _is_uuid(e["target"])}
    if targets:
        names = dict(
            (await db.execute(select(User.id, User.email).where(User.id.in_(targets)))).all()
        )
        for e in out:
            if _is_uuid(e["target"]):
                e["target_user"] = names.get(uuid.UUID(e["target"]))
    actions = (await db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action))).all()
    return {"entries": out, "actions": actions}


def _is_uuid(value: str | None) -> bool:
    try:
        uuid.UUID(value or "")
    except ValueError:
        return False
    return True


def _backup_out(b: Backup) -> dict:
    return {
        "id": b.id,
        "kind": b.kind,
        "status": b.status,
        "name": b.name,
        "size_bytes": b.size_bytes,
        "error": b.error,
        "created_at": b.created_at,
        "started_at": b.started_at,
        "finished_at": b.finished_at,
    }


@router.get("/backups")
async def list_backups(actor: OwnerUser, db: DbDep, limit: int = Query(default=30, le=200)):
    rows = await db.scalars(select(Backup).order_by(Backup.created_at.desc()).limit(limit))
    return [_backup_out(b) for b in rows.all()]


@router.post("/backups", status_code=status.HTTP_202_ACCEPTED)
async def request_backup(actor: OwnerUser, db: DbDep):
    """Ask the backup service for a backup now. It checks for requests every minute."""
    pending = await db.scalar(
        select(Backup).where(Backup.status.in_([BackupStatus.requested, BackupStatus.running]))
    )
    if pending is not None:
        return _backup_out(pending)
    b = Backup(kind="manual", status=BackupStatus.requested, requested_by=actor.id)
    db.add(b)
    db.add(AuditLog(actor_id=actor.id, action="backup.requested"))
    await db.commit()
    return _backup_out(b)


@router.get("/maintenance")
async def get_maintenance(actor: OwnerUser, db: DbDep):
    value = await get_setting(db, MAINTENANCE)
    return {"enabled": bool(value.get("enabled")), "message": value.get("message")}


@router.put("/maintenance")
async def set_maintenance(body: MaintenanceIn, actor: OwnerUser, db: DbDep):
    """While on, members and viewers can read but not start chat turns or agent runs; admins
    still can, to test. Runs already going carry on."""
    value = {"enabled": body.enabled, "message": (body.message or "").strip() or None}
    await put_setting(db, MAINTENANCE, value, actor.id)
    db.add(
        AuditLog(
            actor_id=actor.id,
            action="maintenance.on" if body.enabled else "maintenance.off",
            data={"message": value["message"]},
        )
    )
    await db.commit()
    return value
