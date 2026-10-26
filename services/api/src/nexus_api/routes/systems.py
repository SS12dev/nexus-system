"""The Agents page and chat cards: agent systems Nexus built, their runs (live status, timeline,
cost), pause/resume/stop, templates, and the approvals queue for risky steps. Every approval
decision is written to the audit log."""

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.appsettings import refuse_during_maintenance
from nexus_api.deps import (
    CurrentUser,
    DbDep,
    GatewayDep,
    MemberUser,
    QueueDep,
    RedisDep,
    SettingsDep,
)
from nexus_api.ratelimit import runs_limit
from nexus_api.schemas import DecisionIn, RunIn, TemplateIn
from nexus_common.events import Event, publish
from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Approval,
    ApprovalStatus,
    Chat,
    File,
    RunEvent,
    SystemLifetime,
    SystemStatus,
    User,
    utcnow,
)
from nexus_common.spec import AgentSystemSpec
from nexus_common.systems import (
    ACTIVE_RUN,
    BudgetProblem,
    ControlProblem,
    approval_payload,
    control_run,
    decide_approval,
    granted_capabilities,
    new_run,
    run_payload,
    system_payload,
)
from nexus_common.toolinfo import TOOLS, allowed_tools, needs_approval
from nexus_common.trash import add as add_to_trash
from nexus_common.trash import visible

router = APIRouter(prefix="/api", tags=["agents"])


async def own_system(db: AsyncSession, user: User, system_id: uuid.UUID) -> AgentSystem:
    s = await db.scalar(
        select(AgentSystem).where(
            AgentSystem.id == system_id,
            AgentSystem.user_id == user.id,
            visible("system", AgentSystem.id),
        )
    )
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent system not found")
    return s


async def own_run(db: AsyncSession, user: User, run_id: uuid.UUID) -> AgentRun:
    r = await db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return r


async def latest_runs(db: AsyncSession, system_ids: list[uuid.UUID]) -> dict:
    if not system_ids:
        return {}
    rows = await db.scalars(
        select(AgentRun)
        .where(AgentRun.system_id.in_(system_ids))
        .order_by(AgentRun.created_at.desc())
    )
    out: dict = {}
    for r in rows.all():
        out.setdefault(r.system_id, r)
    return out


@router.get("/tools")
async def list_tools(user: CurrentUser, db: DbDep):
    """The Tool Registry, with what this user may use."""
    allowed = allowed_tools(user, await granted_capabilities(db, user.id))
    return [
        {
            "name": t.name,
            "title": t.title,
            "risk": t.risk,
            "description": t.description,
            "allowed": t.name in allowed,
            "asks_first": needs_approval(t.name),
        }
        for t in TOOLS
    ]


@router.get("/systems")
async def list_systems(
    user: CurrentUser,
    db: DbDep,
    chat_id: uuid.UUID | None = None,
    archived: bool = Query(default=False),
):
    q = select(AgentSystem).where(AgentSystem.user_id == user.id, visible("system", AgentSystem.id))
    if chat_id is not None:
        # Systems that ran in this chat, archived or not, for the chat's cards.
        ran_here = select(AgentRun.system_id).where(AgentRun.chat_id == chat_id)
        q = q.where(AgentSystem.id.in_(ran_here))
    elif not archived:
        q = q.where(AgentSystem.status != SystemStatus.archived)
    systems = (await db.scalars(q.order_by(AgentSystem.updated_at.desc()).limit(200))).all()
    runs = await latest_runs(db, [s.id for s in systems])
    return [system_payload(s, runs.get(s.id)) for s in systems]


@router.get("/systems/{system_id}")
async def get_system(system_id: uuid.UUID, user: CurrentUser, db: DbDep):
    s = await own_system(db, user, system_id)
    runs = (
        await db.scalars(
            select(AgentRun)
            .where(AgentRun.system_id == s.id)
            .order_by(AgentRun.created_at.desc())
            .limit(50)
        )
    ).all()
    return {
        **system_payload(s, runs[0] if runs else None),
        "spec": s.spec,
        "runs": [run_payload(r) for r in runs],
    }


@router.post(
    "/systems/{system_id}/runs", status_code=status.HTTP_202_ACCEPTED, dependencies=[runs_limit]
)
async def start_run(
    system_id: uuid.UUID,
    body: RunIn,
    user: MemberUser,
    db: DbDep,
    settings: SettingsDep,
    gateway: GatewayDep,
    queue: QueueDep,
    redis: RedisDep,
):
    s = await own_system(db, user, system_id)
    await refuse_during_maintenance(db, user)
    live = await db.scalar(
        select(AgentRun.id).where(AgentRun.system_id == s.id, AgentRun.status.in_(ACTIVE_RUN))
    )
    if live:
        raise HTTPException(status.HTTP_409_CONFLICT, "This system is already running")
    if body.chat_id is not None and not await db.scalar(
        select(Chat.id).where(Chat.id == body.chat_id, Chat.user_id == user.id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    file_ids = list(
        (
            await db.scalars(
                select(File.id).where(File.id.in_(body.file_ids), File.user_id == user.id)
            )
        ).all()
    )
    if s.status == SystemStatus.archived:
        s.status = SystemStatus.idle
    try:
        run = await new_run(
            db,
            settings,
            gateway,
            user,
            s,
            body.request,
            file_ids=file_ids,
            budget_usd=body.budget_usd,
            chat_id=body.chat_id,
        )
    except BudgetProblem as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc
    s.updated_at = utcnow()
    await db.commit()
    await queue.enqueue_run("run_system", run.id)
    payload = system_payload(s, run)
    await publish(
        redis, user.id, Event(type="system.updated", chat_id=body.chat_id, data={"system": payload})
    )
    return payload


@router.post("/systems/{system_id}/template")
async def keep_as_template(
    system_id: uuid.UUID, body: TemplateIn, user: MemberUser, db: DbDep, redis: RedisDep
):
    s = await own_system(db, user, system_id)
    spec = AgentSystemSpec.model_validate(s.spec)
    spec.lifetime = "template"
    if body.name:
        spec.name = body.name
    s.spec, s.name, s.lifetime = spec.model_dump(), spec.name, SystemLifetime.template
    if s.status == SystemStatus.archived:
        s.status = SystemStatus.idle
    s.updated_at = utcnow()
    await db.commit()
    payload = system_payload(s)
    await publish(redis, user.id, Event(type="system.updated", data={"system": payload}))
    return payload


@router.delete("/systems/{system_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_system(system_id: uuid.UUID, user: MemberUser, db: DbDep, redis: RedisDep):
    s = await own_system(db, user, system_id)
    if await db.scalar(
        select(AgentRun.id).where(AgentRun.system_id == s.id, AgentRun.status.in_(ACTIVE_RUN))
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Stop the running run first")
    await add_to_trash(db, user.id, "system", s.id, s.name)
    await db.commit()
    await publish(
        redis, user.id, Event(type="system.deleted", data={"system": {"id": str(system_id)}})
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, user: CurrentUser, db: DbDep):
    r = await own_run(db, user, run_id)
    s = await db.get(AgentSystem, r.system_id)
    events = (
        await db.scalars(
            select(RunEvent).where(RunEvent.run_id == r.id).order_by(RunEvent.created_at)
        )
    ).all()
    approvals = (
        await db.scalars(
            select(Approval).where(Approval.run_id == r.id).order_by(Approval.created_at)
        )
    ).all()
    files = (
        await db.scalars(select(File).where(File.run_id == r.id).order_by(File.created_at))
    ).all()
    return {
        "run": run_payload(r),
        "system": system_payload(s),
        "events": [
            {"type": e.type, "agent": e.agent, "data": e.data, "at": e.created_at.isoformat()}
            for e in events
        ],
        "approvals": [approval_payload(a) for a in approvals],
        "files": [
            {
                "id": str(f.id),
                "name": f.name,
                "content_type": f.content_type,
                "size": f.size,
                "version": f.version,
            }
            for f in files
        ],
    }


@router.post("/runs/{run_id}/{action}")
async def control(
    run_id: uuid.UUID, action: str, user: MemberUser, db: DbDep, queue: QueueDep, redis: RedisDep
):
    if action not in ("pause", "resume", "cancel"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown action")
    r = await own_run(db, user, run_id)
    s = await db.get(AgentSystem, r.system_id)
    try:
        wake = control_run(r, s, action)
    except ControlProblem as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.commit()
    if wake:
        await queue.enqueue_run("continue_run", r.id)
    payload = run_payload(r)
    await publish(
        redis, user.id, Event(type="run.updated", chat_id=r.chat_id, data={"run": payload})
    )
    return payload


@router.get("/approvals")
async def list_approvals(
    user: CurrentUser,
    db: DbDep,
    pending: bool = Query(default=True),
    chat_id: uuid.UUID | None = None,
):
    q = select(Approval).where(Approval.user_id == user.id)
    if pending:
        q = q.where(Approval.status == ApprovalStatus.pending)
    if chat_id is not None:
        q = q.where(Approval.chat_id == chat_id)
    rows = await db.scalars(q.order_by(Approval.created_at.desc()).limit(100))
    return [approval_payload(a) for a in rows.all()]


@router.post("/approvals/{approval_id}")
async def decide(
    approval_id: uuid.UUID,
    body: DecisionIn,
    user: MemberUser,
    db: DbDep,
    queue: QueueDep,
    redis: RedisDep,
):
    a = await db.scalar(
        select(Approval).where(Approval.id == approval_id, Approval.user_id == user.id)
    )
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
    try:
        ready = await decide_approval(db, a, user, body.decision, args=body.args, note=body.note)
    except ControlProblem as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.commit()
    if ready:
        await queue.enqueue_run("continue_run", a.run_id)
    payload = approval_payload(a)
    await publish(
        redis,
        user.id,
        Event(
            type="approval.resolved",
            chat_id=a.chat_id,
            data={"approval": payload, "run_continues": ready},
        ),
    )
    return payload
