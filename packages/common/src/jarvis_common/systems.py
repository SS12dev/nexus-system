"""Agent-system helpers shared by the API (Agents page, approvals) and the orchestrator."""

import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_common.litellm import LiteLLMAdmin, active_key, get_budget
from jarvis_common.models import (
    AgentRun,
    AgentSystem,
    Approval,
    ApprovalStatus,
    AuditLog,
    Capability,
    RunStatus,
    SystemStatus,
    ToolGrant,
    User,
    utcnow,
)
from jarvis_common.settings import Settings
from jarvis_common.spec import AgentSystemSpec


def system_payload(s: AgentSystem, run: AgentRun | None = None) -> dict:
    spec = AgentSystemSpec.model_validate(s.spec)
    out = {
        "id": str(s.id),
        "name": s.name,
        "goal": s.goal,
        "topology": s.topology,
        "lifetime": s.lifetime,
        "status": s.status,
        "chat_id": str(s.chat_id) if s.chat_id else None,
        "template_id": str(s.template_id) if s.template_id else None,
        "nodes": spec.nodes(),
        "edges": spec.edges(),
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }
    if run is not None:
        out["run"] = run_payload(run)
    return out


def run_payload(r: AgentRun) -> dict:
    return {
        "id": str(r.id),
        "system_id": str(r.system_id),
        "chat_id": str(r.chat_id) if r.chat_id else None,
        "status": r.status,
        "input": r.input,
        "budget_usd": float(r.budget_usd),
        "cost_usd": float(r.cost_usd or 0),
        "estimate_usd": float(r.estimate_usd) if r.estimate_usd is not None else None,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "steps": r.steps,
        "agents": r.agents or {},
        "output": r.output,
        "error": r.error,
        "trace_id": r.trace_id,
        "created_at": r.created_at.isoformat(),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


def approval_payload(a: Approval) -> dict:
    return {
        "id": str(a.id),
        "run_id": str(a.run_id),
        "system_id": str(a.system_id),
        "chat_id": str(a.chat_id) if a.chat_id else None,
        "agent": a.agent,
        "tool": a.tool,
        "args": a.args,
        "reason": a.reason,
        "status": a.status,
        "decided_args": a.decided_args,
        "note": a.note,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "created_at": a.created_at.isoformat(),
    }


async def granted_capabilities(db: AsyncSession, user_id: uuid.UUID) -> set[Capability]:
    rows = await db.scalars(select(ToolGrant.capability).where(ToolGrant.user_id == user_id))
    return set(rows.all())


def _period(duration: str) -> timedelta:
    """LiteLLM durations such as 30d, 24h, 7d."""
    try:
        n, unit = int(duration[:-1]), duration[-1]
    except (ValueError, IndexError):
        return timedelta(days=30)
    return {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}.get(
        unit, timedelta(days=30)
    )


async def spent_usd(
    db: AsyncSession, settings: Settings, gateway: LiteLLMAdmin, user: User
) -> float:
    """What the user spent this budget period: their own key (chat, memory, designing systems)
    plus every agent-system run (each run has its own key)."""
    key = await active_key(db, user.id)
    chat = (await gateway.spend(key.secret) if key else 0.0) or 0.0
    since = utcnow() - _period(settings.litellm_budget_duration)
    runs = await db.scalar(
        select(func.coalesce(func.sum(AgentRun.cost_usd), 0)).where(
            AgentRun.user_id == user.id, AgentRun.created_at >= since
        )
    )
    return float(chat) + float(runs or 0)


async def remaining_usd(
    db: AsyncSession, settings: Settings, gateway: LiteLLMAdmin, user: User
) -> float:
    budget = await get_budget(db, settings, user)
    return max(0.0, float(budget.max_usd) - await spent_usd(db, settings, gateway, user))


def money(v: float | Decimal) -> Decimal:
    return Decimal(str(round(float(v), 4)))


class BudgetProblem(ValueError):
    """Why a run can't start; worded for Jarvis to pass on."""


async def new_run(
    db: AsyncSession,
    settings: Settings,
    gateway: LiteLLMAdmin,
    user: User,
    system: AgentSystem,
    request: str,
    *,
    file_ids: list[uuid.UUID] | None = None,
    budget_usd: float | None = None,
    chat_id: uuid.UUID | None = None,
    trace_id: str | None = None,
) -> AgentRun:
    """Check the budget and add a queued run (not committed). The budget is what the user or
    spec asked for, else the default, capped by the per-run maximum and what the user has
    left this period; the estimate must fit in it."""
    spec = AgentSystemSpec.model_validate(system.spec)
    default = (
        settings.dev_run_budget_usd
        if spec.topology == "dev_project"
        else settings.default_run_budget_usd
    )
    wanted = budget_usd or spec.budget_usd or default
    budget = min(wanted, settings.max_run_budget_usd)
    left = await remaining_usd(db, settings, gateway, user)
    if left <= 0.01:
        raise BudgetProblem("The user's model budget for this period is used up.")
    budget = min(budget, left)
    estimate = spec.estimate_usd(settings.agent_max_steps, settings.max_map_items)
    if estimate > budget:
        raise BudgetProblem(
            f"This system is estimated to cost about ${estimate:.2f}, over its ${budget:.2f} "
            f"budget (per-run maximum ${settings.max_run_budget_usd:.2f}, ${left:.2f} left this "
            "period). Ask the user to confirm a higher budget, or design a cheaper system."
        )
    run = AgentRun(
        id=uuid.uuid4(),
        system_id=system.id,
        user_id=user.id,
        chat_id=chat_id,
        input=request,
        file_ids=[str(f) for f in file_ids or []],
        status=RunStatus.queued,
        budget_usd=money(budget),
        cost_usd=money(0),
        estimate_usd=money(estimate),
        input_tokens=0,
        output_tokens=0,
        steps=0,
        agents={a.id: {"status": "idle", "tier": a.tier} for a in spec.agents},
        trace_id=trace_id or uuid.uuid4().hex,
    )
    db.add(run)
    return run


# --- Run control and approvals (used by the API and by Jarvis's tools) ---------------------

ACTIVE_RUN = (RunStatus.queued, RunStatus.running, RunStatus.awaiting_approval, RunStatus.paused)


class ControlProblem(ValueError):
    """Why a pause, resume, cancel or decision can't be applied; safe to show."""


def control_run(run: AgentRun, system: AgentSystem, action: str) -> bool:
    """Apply pause, resume or cancel to a run (not committed). Returns True when the
    orchestrator must be asked to continue the run (a parked run resuming or being cancelled).
    A running job notices a pause or cancel itself before its next model call."""
    if action == "pause":
        if run.status != RunStatus.running:
            raise ControlProblem(f"Only a running run can be paused; this one is {run.status}.")
        run.status = RunStatus.paused
        system.status = SystemStatus.paused
        return False
    if action == "resume":
        if run.status != RunStatus.paused:
            raise ControlProblem(f"Only a paused run can be resumed; this one is {run.status}.")
        run.status = RunStatus.running
        system.status = SystemStatus.running
        return True
    if action == "cancel":
        if run.status not in ACTIVE_RUN:
            raise ControlProblem(f"This run already ended ({run.status}).")
        run.status = RunStatus.cancelled
        return True
    raise ControlProblem(f"Unknown action {action}.")


async def decide_approval(
    db: AsyncSession,
    approval: Approval,
    actor: User,
    decision: str,
    *,
    args: dict | None = None,
    note: str | None = None,
) -> bool:
    """Approve (optionally with edited arguments) or reject a paused tool call, and audit it
    (not committed). Returns True when nothing in the run is pending any more, so it can go on."""
    if approval.status != ApprovalStatus.pending:
        raise ControlProblem("This request was already decided.")
    if decision not in ("approve", "reject"):
        raise ControlProblem("Decide with approve or reject.")
    approval.status = ApprovalStatus.approved if decision == "approve" else ApprovalStatus.rejected
    if decision == "approve" and args is not None and args != approval.args:
        approval.decided_args = args
    approval.note = (note or "").strip()[:1000] or None
    approval.decided_by = actor.id
    approval.decided_at = utcnow()
    db.add(
        AuditLog(
            actor_id=actor.id,
            action=f"approval.{approval.status}",
            target=str(approval.id),
            data={
                "run_id": str(approval.run_id),
                "agent": approval.agent,
                "tool": approval.tool,
                "args": approval.args,
                "edited_args": approval.decided_args,
                "note": approval.note,
            },
        )
    )
    await db.flush()
    pending = await db.scalar(
        select(func.count()).where(
            Approval.run_id == approval.run_id, Approval.status == ApprovalStatus.pending
        )
    )
    if pending:
        return False
    run = await db.get(AgentRun, approval.run_id)
    if run is not None and run.status == RunStatus.awaiting_approval:
        run.status = RunStatus.running
        system = await db.get(AgentSystem, run.system_id)
        if system is not None:
            system.status = SystemStatus.running
    return True
