"""Jarvis's tools for agent systems: design and start one, run a saved one again, change it, keep
it as a template, pause/resume/stop a run, delete it. A run itself happens on the orchestrator
queue (runner.py); these tools return straight away so Jarvis can tell the user what started."""

import logging
import uuid

from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import select

from jarvis_common.events import Event, publish
from jarvis_common.models import (
    AgentRun,
    AgentSystem,
    File,
    SystemLifetime,
    SystemStatus,
    User,
    utcnow,
)
from jarvis_common.spec import AgentSystemSpec
from jarvis_common.systems import (
    ACTIVE_RUN,
    BudgetProblem,
    ControlProblem,
    control_run,
    new_run,
    run_payload,
    system_payload,
)
from jarvis_orchestrator.systems.design import DesignFailed, describe, design
from jarvis_orchestrator.tools import ToolContext

log = logging.getLogger(__name__)


def _uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def enqueue_run(deps, function: str, run_id: uuid.UUID) -> None:
    """One job per run at a time: the shared job id makes a second enqueue a no-op while one is
    queued or running (the running job picks up decisions itself before it parks)."""
    if deps.enqueue is not None:
        await deps.enqueue(function, str(run_id), _job_id=f"run:{run_id}")


async def latest_run(db, system_id: uuid.UUID) -> AgentRun | None:
    return await db.scalar(
        select(AgentRun)
        .where(AgentRun.system_id == system_id)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )


async def active_run(db, system_id: uuid.UUID) -> AgentRun | None:
    return await db.scalar(
        select(AgentRun).where(AgentRun.system_id == system_id, AgentRun.status.in_(ACTIVE_RUN))
    )


def system_tools(
    ctx: ToolContext, user: User, allowed: set[str], trace_id: str | None = None
) -> list[BaseTool]:
    deps = ctx.deps
    sessionmaker, user_id = deps.sessionmaker, ctx.user_id

    async def note(tool: str, /, **data) -> None:
        await ctx.emit("tool.call", name=tool, **data)

    async def owned(db, system_id: str) -> AgentSystem | None:
        sid = _uuid(system_id)
        if sid is None:
            return None
        return await db.scalar(
            select(AgentSystem).where(AgentSystem.id == sid, AgentSystem.user_id == user_id)
        )

    async def files_for(db, file_ids: list[str] | None) -> list[uuid.UUID]:
        wanted = [u for u in (_uuid(x) for x in file_ids or []) if u] or list(ctx.file_ids)
        if not wanted:
            return []
        rows = await db.scalars(select(File.id).where(File.id.in_(wanted), File.user_id == user_id))
        return list(rows.all())

    async def start(
        db, system: AgentSystem, request: str, file_ids: list[uuid.UUID], budget: float | None
    ) -> str:
        """Add the run, commit, announce and queue it. Returns what Jarvis tells the user."""
        live = await active_run(db, system.id)
        if live is not None:
            return f"{system.name} is already running (run {live.id}); wait for it or stop it."
        owner = await db.get(User, user_id)
        try:
            run = await new_run(
                db,
                deps.settings,
                deps.gateway,
                owner,
                system,
                request,
                file_ids=file_ids,
                budget_usd=budget,
                chat_id=ctx.chat_id,
                trace_id=trace_id,
            )
        except BudgetProblem as exc:
            await db.commit()
            return f"Not started: {exc} The system is saved as {system.id}."
        system.status = SystemStatus.idle
        system.chat_id = system.chat_id or ctx.chat_id
        system.updated_at = utcnow()
        await db.commit()
        await publish(
            deps.redis,
            user_id,
            Event(
                type="system.created",
                chat_id=ctx.chat_id,
                data={"system": system_payload(system, run)},
            ),
        )
        await enqueue_run(deps, "run_system", run.id)
        spec = AgentSystemSpec.model_validate(system.spec)
        return (
            f"Started {system.name} (system {system.id}, run {run.id}).\n{describe(spec)}\n"
            f"Estimated ${float(run.estimate_usd or 0):.2f} of a ${float(run.budget_usd):.2f} "
            "budget. The result and files will be posted in this chat when it finishes; risky "
            "steps will ask the user for approval first. The user can watch, pause or stop it "
            "from the card in this chat or the Agents page."
        )

    async def build_agent_system(
        request: str,
        deliverables: list[str] | None = None,
        file_ids: list[str] | None = None,
        budget_usd: float | None = None,
        best_model: bool = False,
        keep: bool = False,
    ) -> str:
        """Design a team of sub-agents for a big task and start it. Use for multi-step work
        that needs research, several documents or files made (reports, decks, spreadsheets),
        or many items handled in parallel; answer simple things yourself. request: the whole
        task in the user's words plus everything you know that matters. deliverables: files
        wanted, e.g. ["report.docx", "data.xlsx"]. file_ids: the user's files to work from
        (default: this chat's files). budget_usd: only when the user gave one. best_model:
        only when the user asked for the best model. keep: true when the user wants to reuse
        this team (otherwise it is discarded after the run unless they keep it)."""
        await note("build_agent_system")
        async with sessionmaker() as db:
            owner = await db.get(User, user_id)
            ids = await files_for(db, file_ids)
            names = {}
            if ids:
                rows = await db.execute(select(File.id, File.name).where(File.id.in_(ids)))
                names = {str(i): n for i, n in rows.all()}
            try:
                result = await design(
                    deps,
                    db,
                    owner,
                    request,
                    allowed=allowed,
                    deliverables=deliverables,
                    files=list(names.items()),
                    best_model=best_model,
                    trace_id=trace_id,
                    chat_id=ctx.chat_id,
                )
            except DesignFailed as exc:
                return str(exc)
            if result.template is not None:
                system = await db.get(AgentSystem, result.template.id)
                return f"Using your saved template {system.name}.\n" + await start(
                    db, system, request, ids, budget_usd
                )
            spec = result.spec
            spec.lifetime = "persistent" if keep else "ephemeral"
            if deliverables and not spec.deliverables:
                spec.deliverables = deliverables[:10]
            system = AgentSystem(
                id=uuid.uuid4(),
                user_id=user_id,
                chat_id=ctx.chat_id,
                name=spec.name,
                goal=spec.goal,
                spec=spec.model_dump(),
                topology=spec.topology,
                lifetime=SystemLifetime(spec.lifetime),
                status=SystemStatus.idle,
            )
            db.add(system)
            await db.flush()
            return await start(db, system, request, ids, budget_usd)

    async def run_agent_system(
        system_id: str,
        request: str,
        file_ids: list[str] | None = None,
        budget_usd: float | None = None,
    ) -> str:
        """Run one of the user's saved systems or templates again on a new request."""
        await note("run_agent_system", system_id=system_id)
        async with sessionmaker() as db:
            system = await owned(db, system_id)
            if system is None:
                return "No such agent system."
            if system.status == SystemStatus.archived:
                system.lifetime = SystemLifetime.ephemeral
            return await start(db, system, request, await files_for(db, file_ids), budget_usd)

    async def list_agent_systems() -> str:
        """List the user's saved agent systems and templates, and runs in progress, with ids."""
        await note("list_agent_systems")
        async with sessionmaker() as db:
            rows = await db.scalars(
                select(AgentSystem)
                .where(
                    AgentSystem.user_id == user_id,
                    AgentSystem.status != SystemStatus.archived,
                )
                .order_by(AgentSystem.updated_at.desc())
                .limit(30)
            )
            systems = rows.all()
            lines = []
            for s in systems:
                run = await latest_run(db, s.id)
                last = (
                    f"; last run {run.id} {run.status}, ${float(run.cost_usd or 0):.2f}"
                    if run
                    else ""
                )
                lines.append(f"- {s.name} (id {s.id}, {s.lifetime}, {s.topology}{last})")
        return "\n".join(lines) or "The user has no saved agent systems."

    async def update_agent_system(system_id: str, change: str) -> str:
        """Change a saved system as the user asks (add or drop an agent, switch model tiers,
        tools, instructions...). It must not be running."""
        await note("update_agent_system", system_id=system_id)
        async with sessionmaker() as db:
            system = await owned(db, system_id)
            if system is None:
                return "No such agent system."
            if await active_run(db, system.id) is not None:
                return f"{system.name} is running; change it after the run."
            owner = await db.get(User, user_id)
            current = AgentSystemSpec.model_validate(system.spec)
            try:
                result = await design(
                    deps,
                    db,
                    owner,
                    change,
                    allowed=allowed,
                    current=current,
                    trace_id=trace_id,
                    chat_id=ctx.chat_id,
                    best_model=current.lead.tier == "max",
                )
            except DesignFailed as exc:
                return str(exc)
            system = await db.get(AgentSystem, system.id)
            spec = result.spec
            spec.lifetime = current.lifetime
            system.spec = spec.model_dump()
            system.name, system.goal, system.topology = spec.name, spec.goal, spec.topology
            system.embedding = system.embedding_model = None
            system.updated_at = utcnow()
            await db.commit()
            await publish(
                deps.redis,
                user_id,
                Event(
                    type="system.updated",
                    chat_id=ctx.chat_id,
                    data={"system": system_payload(system)},
                ),
            )
        return f"Updated.\n{describe(spec)}"

    async def save_as_template(system_id: str, name: str | None = None) -> str:
        """Keep a system as a reusable template (the user said yes to keeping it)."""
        await note("save_as_template", system_id=system_id)
        async with sessionmaker() as db:
            system = await owned(db, system_id)
            if system is None:
                return "No such agent system."
            spec = AgentSystemSpec.model_validate(system.spec)
            spec.lifetime = "template"
            if name:
                spec.name = name[:120]
            system.spec = spec.model_dump()
            system.name = spec.name
            system.lifetime = SystemLifetime.template
            if system.status == SystemStatus.archived:
                system.status = SystemStatus.idle
            system.updated_at = utcnow()
            await db.commit()
            await publish(
                deps.redis,
                user_id,
                Event(
                    type="system.updated",
                    chat_id=ctx.chat_id,
                    data={"system": system_payload(system)},
                ),
            )
        return f"Saved {system.name} as a template (id {system.id})."

    async def delete_agent_system(system_id: str) -> str:
        """Delete a saved system or template, stopping any run of it. Only when the user asks."""
        await note("delete_agent_system", system_id=system_id)
        async with sessionmaker() as db:
            system = await owned(db, system_id)
            if system is None:
                return "No such agent system."
            if await active_run(db, system.id) is not None:
                return f"{system.name} is running; stop it with control_run first."
            name, sid = system.name, system.id
            await db.delete(system)
            await db.commit()
        await publish(
            deps.redis,
            user_id,
            Event(type="system.deleted", chat_id=ctx.chat_id, data={"system": {"id": str(sid)}}),
        )
        return f"Deleted {name}."

    async def control_agent_run(run_id: str, action: str) -> str:
        """Pause, resume or cancel a run. action: pause | resume | cancel."""
        await note("control_agent_run", run_id=run_id, action=action)
        rid = _uuid(run_id)
        async with sessionmaker() as db:
            run = (
                await db.scalar(
                    select(AgentRun).where(AgentRun.id == rid, AgentRun.user_id == user_id)
                )
                if rid
                else None
            )
            if run is None:
                return "No such run."
            system = await db.get(AgentSystem, run.system_id)
            try:
                wake = control_run(run, system, action)
            except ControlProblem as exc:
                return str(exc)
            await db.commit()
            await publish(
                deps.redis,
                user_id,
                Event(type="run.updated", chat_id=run.chat_id, data={"run": run_payload(run)}),
            )
        if wake:
            await enqueue_run(deps, "continue_run", run.id)
        return {
            "pause": "Pausing before the next step.",
            "resume": "Resumed.",
            "cancel": "Stopping.",
        }[action]

    fns = [
        build_agent_system,
        run_agent_system,
        list_agent_systems,
        update_agent_system,
        save_as_template,
        delete_agent_system,
        control_agent_run,
    ]
    return [StructuredTool.from_function(coroutine=fn) for fn in fns]
