"""Agent systems end to end with scripted models: each topology, approvals (approve, edit,
reject), pause/resume, cancel, budget checks, tier policy and Jarvis's build tool."""

import json
import uuid
from decimal import Decimal

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from jarvis_common.models import (
    AgentRun,
    AgentSystem,
    Approval,
    ApprovalStatus,
    Attachment,
    AuditLog,
    Chat,
    File,
    Message,
    RoleName,
    RunEvent,
    RunStatus,
    SystemLifetime,
    SystemStatus,
    UserStatus,
)
from jarvis_common.spec import AgentSystemSpec
from jarvis_common.systems import BudgetProblem, control_run, decide_approval, new_run
from jarvis_orchestrator.systems import runner
from jarvis_orchestrator.systems.manage import system_tools
from jarvis_orchestrator.tools import ToolContext
from jarvis_orchestrator.turn import TurnDeps


class AgentModel(BaseChatModel):
    """Plays a per-agent script: a string is the reply, a dict {"tool", "args"} (or a list of
    them) is a tool call. Records the messages and tool names it was given."""

    agent: str = ""
    model_name: str = ""
    # (scripts, seen), shared with the test; a tuple so pydantic doesn't copy the dicts.
    box: tuple = ({}, {})

    @property
    def scripts(self) -> dict:
        return self.box[0]

    @property
    def seen(self) -> dict:
        return self.box[1]

    @property
    def _llm_type(self) -> str:
        return "agent-scripted"

    def bind_tools(self, tools, **kwargs):
        self.seen.setdefault(f"{self.agent}:tools", []).append([t.name for t in tools])
        return self

    def bind(self, **kwargs):
        return self

    def _generate(self, *a, **k):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.seen.setdefault(self.agent, []).append(messages)
        self.seen.setdefault("models", []).append((self.agent, self.model_name))
        queue = self.scripts.setdefault(self.agent, [])
        step = queue.pop(0) if queue else f"{self.agent} done"
        if isinstance(step, Exception):
            raise step
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        if isinstance(step, dict | list):
            calls = [step] if isinstance(step, dict) else step
            msg = AIMessage(
                "",
                tool_calls=[
                    {"name": c["tool"], "args": c.get("args", {}), "id": uuid.uuid4().hex}
                    for c in calls
                ],
                usage_metadata=usage,
            )
        else:
            msg = AIMessage(step, usage_metadata=usage)
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.fixture
def scripts() -> dict:
    return {}


@pytest.fixture
def seen() -> dict:
    return {}


@pytest.fixture
def sdeps(settings, sessionmaker, redis, gateway, embedder, enqueued, storage, scripts, seen):
    def factory(*, model, api_key, metadata):
        name = metadata.get("generation_name", "")
        agent = name.removeprefix("agent.") if name.startswith("agent.") else name
        seen.setdefault("keys", []).append(api_key)
        return AgentModel(agent=agent, model_name=model, box=(scripts, seen))

    return TurnDeps(
        settings=settings,
        sessionmaker=sessionmaker,
        redis=redis,
        gateway=gateway,
        model_factory=factory,
        embedder=embedder,
        enqueue=enqueued,
        storage=storage,
        checkpointer=InMemorySaver(),
    )


@pytest.fixture
async def owner(sessionmaker):
    from jarvis_api.users import create_user

    async with sessionmaker() as db:
        user = await create_user(
            db,
            email="shivam@example.com",
            role=RoleName.owner,
            status=UserStatus.active,
            name="Shivam",
        )
        chat = Chat(user_id=user.id, title="t")
        db.add(chat)
        await db.commit()
        return user, chat.id


def agent(id_, **kw) -> dict:
    return {"id": id_, "name": id_.title(), "instructions": f"Be {id_}.", **kw}


async def make_run(sdeps, owner, topology, agents, request="Do the thing", **spec_kw):
    user, chat_id = owner
    spec = AgentSystemSpec.model_validate(
        {
            "name": f"{topology} team",
            "goal": "Do the thing well",
            "topology": topology,
            "agents": agents,
            **spec_kw,
        }
    )
    async with sdeps.sessionmaker() as db:
        system = AgentSystem(
            id=uuid.uuid4(),
            user_id=user.id,
            chat_id=chat_id,
            name=spec.name,
            goal=spec.goal,
            spec=spec.model_dump(),
            topology=spec.topology,
            lifetime=SystemLifetime(spec.lifetime),
            status=SystemStatus.idle,
        )
        db.add(system)
        await db.flush()
        u = await db.merge(user)
        run = await new_run(db, sdeps.settings, sdeps.gateway, u, system, request, chat_id=chat_id)
        await db.commit()
        return system.id, run.id


async def load(sdeps, run_id):
    async with sdeps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        system = await db.get(AgentSystem, run.system_id)
        return run, system


def human_texts(messages) -> str:
    return "\n".join(m.content for m in messages if m.type == "human")


async def test_single_agent_saves_file_and_posts_result(sdeps, owner, scripts, seen, gateway):
    scripts["solo"] = [
        {"tool": "save_file", "args": {"name": "notes.md", "content": "# Notes\nhi"}},
        "Here are the notes. Saved notes.md.",
    ]
    _, run_id = await make_run(sdeps, owner, "single", [agent("solo", tools=["save_file"])])
    await runner.run_system(sdeps, run_id)

    run, system = await load(sdeps, run_id)
    assert run.status == RunStatus.completed
    assert run.output == "Here are the notes. Saved notes.md."
    assert run.steps == 2 and run.input_tokens == 20
    assert run.finished_at is not None and run.key_secret is None
    # Ephemeral systems are archived after their run.
    assert system.status == SystemStatus.archived
    # The run had its own key, used for every model call and revoked at the end.
    run_keys = [c for c in gateway.calls if c[0] == "generate_run"]
    assert len(run_keys) == 1 and run_keys[0][2] == run.budget_usd
    assert all(k.startswith("sk-run-") for k in seen["keys"])
    assert ("delete", seen["keys"][0]) in gateway.calls
    assert seen["solo:tools"][0][:1] == ["save_file"]
    async with sdeps.sessionmaker() as db:
        msg = await db.get(Message, run.message_id)
        assert "finished" in msg.content and "notes.md" in msg.content
        assert "keep this team as a template" in msg.content
        files = (await db.scalars(select(File).where(File.run_id == run_id))).all()
        assert [f.name for f in files] == ["notes.md"] and files[0].origin == "generated"
        att = await db.scalar(select(Attachment).where(Attachment.message_id == msg.id))
        assert att.file_id == files[0].id
        events = (await db.scalars(select(RunEvent).where(RunEvent.run_id == run_id))).all()
        assert {"tool.call", "agent.status"} <= {e.type for e in events}


async def test_pipeline_hands_output_forward(sdeps, owner, scripts, seen):
    scripts["researcher"] = ["Fact: the sky is blue."]
    scripts["writer"] = ["Report: blue sky."]
    _, run_id = await make_run(sdeps, owner, "pipeline", [agent("researcher"), agent("writer")])
    await runner.run_system(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed and run.output == "Report: blue sky."
    assert "Fact: the sky is blue." in human_texts(seen["writer"][0])


async def test_supervisor_delegates_then_finishes(sdeps, owner, scripts, seen):
    scripts["lead"] = [
        {"tool": "delegate", "args": {"worker": "analyst", "task": "Count the apples"}},
        {"tool": "finish", "args": {"answer": "There are 3 apples."}},
    ]
    scripts["analyst"] = ["3 apples"]
    _, run_id = await make_run(
        sdeps, owner, "supervisor_workers", [agent("lead", tier="pro"), agent("analyst")]
    )
    await runner.run_system(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed and run.output == "There are 3 apples."
    assert "Count the apples" in human_texts(seen["analyst"][0])
    # The worker's result went back into the lead's transcript.
    assert '<result from="analyst">\n3 apples' in human_texts(seen["lead"][1])
    assert {"delegate", "finish"} <= set(seen["lead:tools"][0])
    assert ("lead", "jarvis-pro") in seen["models"]


async def test_map_reduce_fans_out(sdeps, owner, scripts, seen):
    scripts["splitter"] = [{"tool": "submit_items", "args": {"items": ["alpha", "beta"]}}]
    scripts["worker"] = ["result A", "result B"]
    scripts["combiner"] = ["Combined."]
    _, run_id = await make_run(
        sdeps,
        owner,
        "parallel_map_reduce",
        [agent("splitter"), agent("worker"), agent("combiner")],
    )
    await runner.run_system(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed and run.output == "Combined."
    assert len(seen["worker"]) == 2
    combined = human_texts(seen["combiner"][0])
    assert 'item="1" task="alpha"' in combined and 'item="2" task="beta"' in combined


async def test_critic_loop_revises_until_approved(sdeps, owner, scripts, seen):
    scripts["doer"] = ["Draft one", "Draft two"]
    scripts["critic"] = [
        {"tool": "verdict", "args": {"approved": False, "feedback": "Add a title"}},
        {"tool": "verdict", "args": {"approved": True, "feedback": "Good"}},
    ]
    _, run_id = await make_run(sdeps, owner, "critic_loop", [agent("doer"), agent("critic")])
    await runner.run_system(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed and run.output == "Draft two"
    # The doer kept its own transcript and got the feedback on the second turn.
    assert "Draft one" in [m.content for m in seen["doer"][1] if m.type == "ai"]
    assert "Add a title" in human_texts(seen["doer"][1])


async def email_run(sdeps, owner, scripts):
    scripts["mailer"] = [
        {"tool": "send_email", "args": {"to": ["a@example.com"], "subject": "Hi", "body": "x"}},
        "Done emailing.",
    ]
    return await make_run(sdeps, owner, "single", [agent("mailer", tools=["send_email"])])


async def decide(sdeps, owner, run_id, decision, **kw) -> bool:
    user, _ = owner
    async with sdeps.sessionmaker() as db:
        a = await db.scalar(select(Approval).where(Approval.run_id == run_id))
        actor = await db.merge(user)
        ready = await decide_approval(db, a, actor, decision, **kw)
        await db.commit()
        return ready


async def test_risky_tool_waits_for_approval_then_runs(sdeps, owner, scripts, seen):
    _, run_id = await email_run(sdeps, owner, scripts)
    await runner.run_system(sdeps, run_id)

    run, system = await load(sdeps, run_id)
    assert run.status == RunStatus.awaiting_approval
    assert system.status == SystemStatus.awaiting_approval
    assert run.key_secret  # kept for the rest of the run
    async with sdeps.sessionmaker() as db:
        a = await db.scalar(select(Approval).where(Approval.run_id == run_id))
        assert a.status == ApprovalStatus.pending and a.tool == "send_email"
        assert a.args["to"] == ["a@example.com"]
    calls_before = len(seen["mailer"])

    # Approving with edited arguments; nothing else is pending, so the run can go on.
    assert await decide(
        sdeps,
        owner,
        run_id,
        "approve",
        args={"to": ["b@example.com"], "subject": "Hi", "body": "x"},
    )
    await runner.continue_run(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed and run.output == "Done emailing."
    # The model call before the approval wasn't repeated on resume.
    assert len(seen["mailer"]) == calls_before + 1
    tool_msg = next(m for m in seen["mailer"][-1] if isinstance(m, ToolMessage))
    assert "edited the arguments" in tool_msg.content
    assert "Email isn't set up" in tool_msg.content
    async with sdeps.sessionmaker() as db:
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "approval.approved"))
        assert audit.data["edited_args"]["to"] == ["b@example.com"]


async def test_rejected_tool_is_skipped(sdeps, owner, scripts, seen):
    _, run_id = await email_run(sdeps, owner, scripts)
    await runner.run_system(sdeps, run_id)
    await decide(sdeps, owner, run_id, "reject", note="Not now")
    await runner.continue_run(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed
    tool_msg = next(m for m in seen["mailer"][-1] if isinstance(m, ToolMessage))
    assert "rejected" in tool_msg.content and "Not now" in tool_msg.content


async def test_decision_landing_before_park_continues_in_same_job(sdeps, owner, scripts):
    """If the user decides while the job is still finishing its turn, the job carries on
    instead of parking (its own continue_run would be deduplicated)."""
    _, run_id = await email_run(sdeps, owner, scripts)
    original = runner._invoke
    invocations = []

    async def invoke_then_decide(app, config, resume):
        result = await original(app, config, resume)
        invocations.append(resume)
        if len(invocations) == 1:
            assert result.get("__interrupt__")
            await decide(sdeps, owner, run_id, "approve")
        return result

    runner._invoke = invoke_then_decide
    try:
        await runner.run_system(sdeps, run_id)
    finally:
        runner._invoke = original
    run, _ = await load(sdeps, run_id)
    assert invocations == [False, True] and run.status == RunStatus.completed


async def test_pause_parks_and_resume_finishes(sdeps, owner, scripts):
    scripts["a"] = [{"tool": "list_documents"}, "Finished."]
    _, run_id = await make_run(sdeps, owner, "single", [agent("a", tools=["list_documents"])])
    calls = {"n": 0}
    original = sdeps.model_factory

    def pausing_factory(**kw):
        calls["n"] += 1
        return original(**kw)

    sdeps.model_factory = pausing_factory

    # Pause as soon as the first tool runs.
    from jarvis_orchestrator.systems import runtime

    real_status = runtime.RunContext.agent_status

    async def status_and_pause(self, agent_id, status, **extra):
        await real_status(self, agent_id, status, **extra)
        if status == "working":
            async with self.deps.sessionmaker() as db:
                run = await db.get(AgentRun, self.run_id)
                system = await db.get(AgentSystem, run.system_id)
                if run.status == RunStatus.running:
                    control_run(run, system, "pause")
                    await db.commit()

    runtime.RunContext.agent_status = status_and_pause
    try:
        await runner.run_system(sdeps, run_id)
    finally:
        runtime.RunContext.agent_status = real_status
    run, system = await load(sdeps, run_id)
    assert run.status == RunStatus.paused and system.status == SystemStatus.paused
    assert calls["n"] == 1

    async with sdeps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        system = await db.get(AgentSystem, run.system_id)
        assert control_run(run, system, "resume") is True
        await db.commit()
    await runner.continue_run(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed and run.output == "Finished."


async def test_cancel_parked_run(sdeps, owner, scripts):
    _, run_id = await email_run(sdeps, owner, scripts)
    await runner.run_system(sdeps, run_id)
    async with sdeps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        system = await db.get(AgentSystem, run.system_id)
        assert control_run(run, system, "cancel") is True
        await db.commit()
    await runner.continue_run(sdeps, run_id)
    run, system = await load(sdeps, run_id)
    assert run.status == RunStatus.cancelled and run.finished_at is not None
    async with sdeps.sessionmaker() as db:
        a = await db.scalar(select(Approval).where(Approval.run_id == run_id))
        assert a.status == ApprovalStatus.rejected
        msg = await db.get(Message, run.message_id)
        assert "was stopped" in msg.content


async def test_budget_check_refuses_expensive_spec(sdeps, owner):
    with pytest.raises(BudgetProblem, match="estimated"):
        await make_run(
            sdeps,
            owner,
            "critic_loop",
            [agent("doer", tier="max", max_steps=40), agent("critic", tier="max", max_steps=40)],
            max_rounds=10,
        )


async def test_tier_policy(sdeps, owner, scripts, seen, gateway):
    """max is only for the lead of an owner/admin; a model error retries one tier up; a
    non-lead agent drops a tier once 80% of the budget is spent."""
    import openai

    def error():
        import httpx

        req = httpx.Request("POST", "http://x")
        return openai.InternalServerError(
            "boom", response=httpx.Response(500, request=req), body=None
        )

    scripts["lead"] = [error(), "lead ok"]
    scripts["helper"] = ["helper ok"]
    _, run_id = await make_run(
        sdeps,
        owner,
        "pipeline",
        [agent("lead", tier="mini"), agent("helper", tier="max")],
    )
    run, _ = await load(sdeps, run_id)
    await runner.run_system(sdeps, run_id)
    models = seen["models"]
    assert models[0] == ("lead", "jarvis-mini") and models[1] == ("lead", "jarvis-pro")
    # The helper asked for max but isn't the lead: capped at pro.
    assert ("helper", "jarvis-pro") in models

    seen.clear()
    scripts["lead"] = ["lead ok"]
    scripts["helper"] = ["helper ok"]
    _, run_id = await make_run(
        sdeps, owner, "pipeline", [agent("lead", tier="max"), agent("helper", tier="pro")]
    )
    real = gateway.generate_run_key

    async def spent_key(*a, **k):
        key = await real(*a, **k)
        gateway.keys[key]["spend"] = 0.45  # of a $0.50 budget
        return key

    gateway.generate_run_key = spent_key
    await runner.run_system(sdeps, run_id)
    assert ("lead", "jarvis-max") in seen["models"]
    assert ("helper", "jarvis-mini") in seen["models"]
    run, _ = await load(sdeps, run_id)
    assert run.cost_usd == Decimal("0.45")


async def test_build_agent_system_designs_and_queues(sdeps, owner, scripts, enqueued):
    user, chat_id = owner
    spec = {
        "name": "Market report",
        "goal": "Research EV sales and write a report with a spreadsheet",
        "topology": "pipeline",
        "agents": [
            agent("researcher", role="researcher", tools=["web_search", "web_fetch"]),
            agent("writer", role="writer", tools=["run_code"], skills=["core/docx"]),
        ],
        "deliverables": ["report.docx", "data.xlsx"],
    }
    bad = {**spec, "agents": [agent("researcher", tools=["teleport"]), spec["agents"][1]]}
    scripts["jarvis.design_system"] = [json.dumps({"spec": bad}), json.dumps({"spec": spec})]
    from jarvis_common.skills import sync_library

    async with sdeps.sessionmaker() as db:
        from pathlib import Path

        await sync_library(db, Path(sdeps.settings.library_path))

    async def emit(*a, **k):
        pass

    ctx = ToolContext(deps=sdeps, user_id=user.id, chat_id=chat_id, emit=emit)
    allowed = {"web_search", "web_fetch", "run_code", "save_file"}
    tools = {t.name: t for t in system_tools(ctx, user, allowed, "trace1")}
    out = await tools["build_agent_system"].ainvoke({"request": "EV report please"})
    assert "Started Market report" in out and "researcher" in out
    async with sdeps.sessionmaker() as db:
        run = await db.scalar(select(AgentRun))
        system = await db.get(AgentSystem, run.system_id)
    assert system.lifetime == SystemLifetime.ephemeral and system.chat_id == chat_id
    assert run.status == RunStatus.queued and run.trace_id == "trace1"
    assert ("run_system", str(run.id)) in enqueued.jobs

    # Keeping it as a template, then listing it.
    out = await tools["save_as_template"].ainvoke({"system_id": str(system.id)})
    assert "template" in out
    listing = await tools["list_agent_systems"].ainvoke({})
    assert "Market report" in listing and "template" in listing
    # It is still running, so it can't be started twice.
    again = await tools["run_agent_system"].ainvoke(
        {"system_id": str(system.id), "request": "again"}
    )
    assert "already running" in again
