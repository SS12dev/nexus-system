"""Run agent systems: the run_system and continue_run jobs.

A run starts as a queued row. run_system mints the run's own LiteLLM key (capped at the run's
budget), compiles the spec and runs the graph with the run id as its checkpoint thread. When an
agent needs approval, or the user pauses, the graph is interrupted and the job ends; once the
user decides (or resumes), the API queues continue_run, which picks the graph up where it
stopped. At the end the key is revoked, the result is posted in the chat with the files the run
made, and ephemeral systems are archived.
"""

import logging
import uuid
from pathlib import Path

import openai
from langgraph.types import Command
from sqlalchemy import func, select, update

from nexus_common import logs
from nexus_common.events import Event, publish
from nexus_common.library import file_payload
from nexus_common.links import link_payload
from nexus_common.litellm import MODEL_ALIASES, GatewayError
from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Approval,
    ApprovalStatus,
    Attachment,
    Chat,
    File,
    Link,
    Message,
    MessageLink,
    MessageRole,
    MessageStatus,
    RunEvent,
    RunStatus,
    Skill,
    SkillUsage,
    SystemLifetime,
    SystemStatus,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.skills import find_skill, instruction_layers, usable_skills
from nexus_common.spec import AgentSystemSpec
from nexus_common.systems import (
    granted_capabilities,
    money,
    run_payload,
    system_payload,
)
from nexus_common.toolinfo import BY_NAME, allowed_tools
from nexus_orchestrator.systems.compile import compile_system
from nexus_orchestrator.systems.prompts import agent_prompt
from nexus_orchestrator.systems.runtime import RunCancelled, RunContext
from nexus_orchestrator.turn import message_payload

log = logging.getLogger(__name__)

RECURSION_LIMIT = 400
ACTIVE = (RunStatus.running, RunStatus.awaiting_approval, RunStatus.paused)


async def run_system(deps, run_id: uuid.UUID) -> None:
    await _drive(deps, run_id, resume=False)


async def continue_run(deps, run_id: uuid.UUID) -> None:
    """After approvals are decided, a pause is lifted, or a cancel: carry on or wrap up."""
    await _drive(deps, run_id, resume=True)


async def _announce(deps, system: AgentSystem, run: AgentRun, deleted: bool = False) -> None:
    payload = system_payload(system, run)
    await publish(
        deps.redis,
        system.user_id,
        Event(type="run.updated", chat_id=run.chat_id, data={"run": run_payload(run)}),
    )
    await publish(
        deps.redis,
        system.user_id,
        Event(
            type="system.deleted" if deleted else "system.updated",
            chat_id=run.chat_id,
            data={"system": payload},
        ),
    )


async def _drive(deps, run_id: uuid.UUID, resume: bool) -> None:
    settings = deps.settings
    async with deps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        if run is None or run.status in (RunStatus.completed, RunStatus.failed):
            return
        if run.status == RunStatus.cancelled:
            if run.finished_at is None:
                await finish(deps, run_id, RunStatus.cancelled, error="Stopped by the user.")
            return
        if resume != (run.status in ACTIVE) or (not resume and run.status != RunStatus.queued):
            log.info("Run %s is %s; nothing to do", run_id, run.status)
            return
        if resume:
            pending = await db.scalar(
                select(func.count()).where(
                    Approval.run_id == run_id, Approval.status == ApprovalStatus.pending
                )
            )
            if pending:
                return
        system = await db.get(AgentSystem, run.system_id)
        user = await db.get(User, run.user_id)
        if user is None or user.status != UserStatus.active:
            await db.commit()
            await finish(deps, run_id, RunStatus.failed, error="The account is not active.")
            return
        spec = AgentSystemSpec.model_validate(system.spec)

        key = run.key_secret or ""
        if settings.litellm_virtual_keys and not key:
            try:
                key = await deps.gateway.generate_run_key(
                    user, run.id, run.budget_usd, MODEL_ALIASES
                )
            except GatewayError as exc:
                log.warning("No run key for %s: %s", run_id, exc)
                await db.commit()
                await finish(
                    deps, run_id, RunStatus.failed, error="Couldn't reach the model gateway."
                )
                return
            run.key_secret = key
        elif not settings.litellm_virtual_keys:
            key = settings.litellm_api_key

        run.status = RunStatus.running
        run.started_at = run.started_at or utcnow()
        system.status = SystemStatus.running
        system.updated_at = utcnow()

        granted = await granted_capabilities(db, user.id)
        allowed = allowed_tools(user, granted)
        file_ids = [uuid.UUID(f) for f in run.file_ids or []]
        files = []
        if file_ids:
            rows = await db.scalars(
                select(File).where(File.id.in_(file_ids), File.user_id == user.id)
            )
            files = [(str(f.id), f.name) for f in rows.all()]
        library = Path(settings.library_path)
        visible = {s.slug: s for s in await usable_skills(db, user.id)}
        prompts = {}
        for a in spec.agents:
            layers = await instruction_layers(db, library, user, a.role)
            granted_skills = [visible[s] for s in a.skills if s in visible]
            for s in a.skills:
                if s not in visible and (found := await find_skill(db, user.id, s)):
                    granted_skills.append(found)
            infos = [BY_NAME[t] for t in a.tools if t in allowed and t in BY_NAME]
            prompts[a.id] = agent_prompt(
                spec, a, layers, granted_skills, infos, user.name, user.assistant
            )
        await db.commit()
        chat_id, request, trace_id = run.chat_id, run.input, run.trace_id
        logs.bind(user_id=run.user_id, chat_id=chat_id, trace_id=trace_id)
        budget = float(run.budget_usd)
        await _announce(deps, system, run)

    rc = RunContext(
        deps=deps,
        run_id=run_id,
        system_id=system.id,
        user=user,
        chat_id=chat_id,
        spec=spec,
        trace_id=trace_id,
        budget_usd=budget,
        api_key=key,
        allowed=allowed,
        allow_max=str(user.role) in settings.max_tier_role_list,
        file_ids=file_ids,
        prompts=prompts,
    )
    app = compile_system(rc, files, request).compile(checkpointer=deps.checkpointer)
    config = {"configurable": {"thread_id": str(run_id)}, "recursion_limit": RECURSION_LIMIT}
    try:
        result = await _invoke(app, config, resume)
        # Parked on approvals or a pause. A decision or resume may have landed while this job
        # was still running (its own continue_run is then deduplicated), so check before
        # parking and carry on in this job if nothing is outstanding any more.
        while interrupts := result.get("__interrupt__") or []:
            async with deps.sessionmaker() as db:
                run = await db.get(AgentRun, run_id)
                system = await db.get(AgentSystem, run.system_id)
                pending = await db.scalar(
                    select(func.count()).where(
                        Approval.run_id == run_id, Approval.status == ApprovalStatus.pending
                    )
                )
                if run.status == RunStatus.cancelled:
                    raise RunCancelled
                kinds = {(i.value or {}).get("kind") for i in interrupts}
                if pending:
                    status = RunStatus.awaiting_approval
                elif "pause" in kinds and run.status == RunStatus.paused:
                    status = RunStatus.paused
                else:
                    status = None
                if status is not None:
                    run.status = status
                    system.status = SystemStatus(status.value)
                    await db.commit()
                    await _announce(deps, system, run)
                    return
            result = await _invoke(app, config, True)
    except RunCancelled:
        await finish(deps, run_id, RunStatus.cancelled, error="Stopped by the user.")
        return
    except openai.APIStatusError as exc:
        text = str(exc).lower()
        if "budget" in text or "exceeded" in text:
            await finish(
                deps, run_id, RunStatus.failed, error="The run reached its budget before finishing."
            )
        else:
            log.exception("Run %s failed", run_id)
            await finish(deps, run_id, RunStatus.failed, error="A model call failed.")
        return
    except Exception:
        log.exception("Run %s failed", run_id)
        await finish(deps, run_id, RunStatus.failed, error="Something went wrong in the run.")
        return

    await finish(deps, run_id, RunStatus.completed, output=result.get("final") or "")


async def _invoke(app, config: dict, resume: bool) -> dict:
    if not resume:
        start = {"outputs": {}, "transcripts": {}, "counts": {}, "results": [], "dev": {}}
        return await app.ainvoke(start, config)
    snapshot = await app.aget_state(config)
    interrupts = [i for t in snapshot.tasks for i in t.interrupts]
    if not interrupts and not snapshot.next:
        return snapshot.values
    return await app.ainvoke(Command(resume={i.id: True for i in interrupts}), config)


async def finish(
    deps, run_id: uuid.UUID, status: RunStatus, *, output: str = "", error: str | None = None
) -> None:
    """End a run: record cost, revoke its key, settle approvals and skill stats, post the result
    in the chat, archive an ephemeral system and drop the run's checkpoints."""
    async with deps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        system = await db.get(AgentSystem, run.system_id)
        if run.key_secret and deps.settings.litellm_virtual_keys:
            spent = await deps.gateway.spend(run.key_secret)
            if spent is not None:
                run.cost_usd = money(spent)
            try:
                await deps.gateway.delete_key(run.key_secret)
            except GatewayError as exc:
                log.warning("Couldn't revoke the key of run %s: %s", run_id, exc)
        run.key_secret = None
        run.status = status
        run.output = output or run.output
        run.error = error
        run.finished_at = utcnow()
        await db.execute(
            update(Approval)
            .where(Approval.run_id == run_id, Approval.status == ApprovalStatus.pending)
            .values(
                status=ApprovalStatus.rejected, note="The run ended first.", decided_at=utcnow()
            )
        )
        outcome = "success" if status == RunStatus.completed else "failure"
        usages = (await db.scalars(select(SkillUsage).where(SkillUsage.run_id == run_id))).all()
        for skill_id in {u.skill_id for u in usages}:
            column = Skill.successes if outcome == "success" else Skill.failures
            await db.execute(update(Skill).where(Skill.id == skill_id).values({column: column + 1}))
        for u in usages:
            u.outcome = outcome
        archived = system.lifetime == SystemLifetime.ephemeral
        system.status = SystemStatus.archived if archived else SystemStatus.idle
        system.updated_at = utcnow()
        files = latest_versions(
            (
                await db.scalars(
                    select(File).where(File.run_id == run_id).order_by(File.created_at)
                )
            ).all()
        )
        # Links the run saved: its repo and live app, and any its agents kept (save_link).
        links = (
            await db.scalars(select(Link).where(Link.run_id == run_id).order_by(Link.created_at))
        ).all()
        if status == RunStatus.completed:
            run.output = await answer_from_files(deps, run.output or "", files)
        message = None
        if run.chat_id is not None:
            chat = await db.get(Chat, run.chat_id)
            if chat is not None:
                message = Message(
                    chat_id=chat.id,
                    role=MessageRole.assistant,
                    content=result_text(system, run, files),
                    status=MessageStatus.complete,
                    model=None,
                    trace_id=run.trace_id,
                    error=None if status == RunStatus.completed else error,
                )
                db.add(message)
                await db.flush()
                for f in files:
                    db.add(Attachment(message_id=message.id, file_id=f.id))
                for link in links:
                    db.add(MessageLink(message_id=message.id, link_id=link.id))
                run.message_id = message.id
                chat.updated_at = utcnow()
        tool_calls = await db.scalar(
            select(func.count()).where(RunEvent.run_id == run_id, RunEvent.type == "tool.call")
        )
        await db.commit()
        await _announce(deps, system, run, deleted=archived)
        if message is not None:
            payload = message_payload(message)
            payload["attachments"] = [file_payload(f) for f in files]
            payload["links"] = [link_payload(x) for x in links]
            await publish(
                deps.redis,
                run.user_id,
                Event(type="message.completed", chat_id=run.chat_id, data={"message": payload}),
            )
    if deps.checkpointer is not None:
        try:
            await deps.checkpointer.adelete_thread(str(run_id))
        except Exception:
            log.warning("Couldn't delete checkpoints of run %s", run_id, exc_info=True)
    threshold = deps.settings.learn_after_tool_calls
    if (
        status == RunStatus.completed
        and threshold
        and (tool_calls or 0) >= threshold
        and deps.enqueue is not None
    ):
        await deps.enqueue("draft_skill", str(run_id), _job_id=f"learn:{run_id}")


# A result shorter than this that leaves the answer in a text file it made ("Saved guide.md. It
# contains...") is replaced in the chat by that file, so the user reads the answer itself.
POINTER_CHARS = 1500
INLINE_FILE_CHARS = 20000
TEXT_FILES = (".md", ".markdown", ".txt")


async def answer_from_files(deps, output: str, files: list[File]) -> str:
    if len(output.strip()) >= POINTER_CHARS or deps.storage is None:
        return output
    texts = [f for f in files if Path(f.name).suffix.lower() in TEXT_FILES]
    if not texts:
        return output
    # The newest version of the last text file the run wrote is its main deliverable.
    f = max(texts, key=lambda x: (x.created_at, x.version))
    if f.size > INLINE_FILE_CHARS * 4:
        return output
    try:
        text = (await deps.storage.get(f.storage_key)).decode("utf-8", errors="replace").strip()
    except Exception:
        log.warning("Couldn't read %s to put its answer in the chat", f.id, exc_info=True)
        return output
    if len(text) <= len(output.strip()) or len(text) > INLINE_FILE_CHARS:
        return output
    return text


def latest_versions(files: list[File]) -> list[File]:
    """One entry per file name, its newest version: a run that saved kyoto-costs.md three
    times attaches it once."""
    newest: dict[str, File] = {}
    for f in files:
        kept = newest.get(f.name)
        if kept is None or (f.version, f.created_at) >= (kept.version, kept.created_at):
            newest[f.name] = f
    return sorted(newest.values(), key=lambda f: f.created_at)


def result_text(system: AgentSystem, run: AgentRun, files: list[File]) -> str:
    spent = float(run.cost_usd or 0)
    cost = f"${spent:.2f}" if spent >= 0.005 else "under a cent"
    if run.status == RunStatus.completed:
        parts = [f"**{system.name}** finished.", (run.output or "").strip()]
    elif run.status == RunStatus.cancelled:
        parts = [f"**{system.name}** was stopped."]
    else:
        parts = [f"**{system.name}** stopped: {run.error}"]
    if files:
        parts.append("Files, if you want them: " + ", ".join(f"`{f.name}`" for f in files))
    parts.append(f"_Run cost {cost} of a ${float(run.budget_usd):.2f} budget._")
    # A software request always gets the house team, so a dev team is never kept as a template.
    if (
        run.status == RunStatus.completed
        and system.lifetime == SystemLifetime.ephemeral
        and system.topology != "dev_project"
    ):
        parts.append("Want me to keep this team as a template for next time?")
    return "\n\n".join(p for p in parts if p)
