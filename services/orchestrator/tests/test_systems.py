"""Agent systems end to end with scripted models: each topology, approvals (approve, edit,
reject), pause/resume, cancel, budget checks, tier policy and Nexus's build tool."""

import asyncio
import base64
import json
import time
import uuid
from decimal import Decimal

import pytest
from langchain_core.messages import ToolMessage
from sqlalchemy import select

from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Approval,
    ApprovalStatus,
    Attachment,
    AuditLog,
    File,
    FileStatus,
    Link,
    Message,
    Notification,
    NotificationKind,
    RunEvent,
    RunStatus,
    Skill,
    SkillStatus,
    SystemLifetime,
    SystemStatus,
)
from nexus_common.notices import featured
from nexus_common.spec import AgentSystemSpec
from nexus_common.systems import BudgetProblem, control_run, decide_approval, new_run
from nexus_orchestrator.artifacts import save_generated
from nexus_orchestrator.systems import runner, runtime
from nexus_orchestrator.systems.learn import draft_skill
from nexus_orchestrator.systems.manage import system_tools
from nexus_orchestrator.tools import ToolContext


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


async def test_answer_left_in_a_file_is_posted_in_the_chat(sdeps, owner, scripts):
    guide = "# Buying guide\n\n**Pick: Bambu Lab A1 Mini.**\n\n| Model | Price |\n|---|---|\n" + (
        "| Example printer | 20,000 |\n" * 20
    )
    scripts["solo"] = [
        {"tool": "save_file", "args": {"name": "guide.md", "content": guide}},
        "Done. I saved guide.md. It contains a recommendation and a comparison table.",
    ]
    _, run_id = await make_run(sdeps, owner, "single", [agent("solo", tools=["save_file"])])
    await runner.run_system(sdeps, run_id)

    run, _ = await load(sdeps, run_id)
    assert run.output == guide.strip()
    async with sdeps.sessionmaker() as db:
        msg = await db.get(Message, run.message_id)
        # The answer itself, with the file still attached for whoever wants it.
        assert "**Pick: Bambu Lab A1 Mini.**" in msg.content
        assert "It contains" not in msg.content and "`guide.md`" in msg.content
        assert await db.scalar(select(Attachment).where(Attachment.message_id == msg.id))


async def test_a_full_answer_is_kept_over_its_file(sdeps, owner, scripts):
    answer = "**Pick: the Honda CB350.** " + "Reasons and a comparison table follow. " * 50
    scripts["solo"] = [
        {"tool": "save_file", "args": {"name": "bikes.md", "content": "# Bikes\n" + "row\n" * 600}},
        answer,
    ]
    _, run_id = await make_run(sdeps, owner, "single", [agent("solo", tools=["save_file"])])
    await runner.run_system(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.output == answer


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


async def test_finished_run_drafts_a_learned_skill(sdeps, owner, scripts):
    scripts["solo"] = ["Done."]
    _, run_id = await make_run(sdeps, owner, "single", [agent("solo")])
    await runner.run_system(sdeps, run_id)
    scripts["nexus.draft_skill"] = [
        json.dumps(
            {
                "name": "EV market report",
                "description": "Research EV sales: sources first, then a report",
                "body": "# EV report\n1. Search.\n2. Write.",
            }
        )
    ]
    skill = await draft_skill(sdeps, run_id)
    assert skill is not None
    async with sdeps.sessionmaker() as db:
        saved = await db.get(Skill, skill.id)
    assert saved.status == SkillStatus.draft and saved.source == f"run:{run_id}"
    assert saved.owner_id == owner[0].id
    # Once per run.
    assert await draft_skill(sdeps, run_id) is None


async def test_pipeline_hands_output_forward(sdeps, owner, scripts, seen):
    scripts["researcher"] = ["Fact: the sky is blue."]
    scripts["writer"] = ["Report: blue sky."]
    _, run_id = await make_run(sdeps, owner, "pipeline", [agent("researcher"), agent("writer")])
    await runner.run_system(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed and run.output == "Report: blue sky."
    assert "Fact: the sky is blue." in human_texts(seen["writer"][0])
    # Only the last stage reaches the chat, so it is told to carry every part.
    assert "You are the last stage" in human_texts(seen["writer"][0])
    assert "You are the last stage" not in human_texts(seen["researcher"][0])


async def test_a_second_draft_waits_for_the_first_in_the_same_chat(sdeps, owner, scripts):
    draft = json.dumps({"name": "notes", "description": "Take notes", "body": "1. Write."})
    scripts["solo"] = ["Done.", "Done again."]
    _, first = await make_run(sdeps, owner, "single", [agent("solo")])
    await runner.run_system(sdeps, first)
    scripts["nexus.draft_skill"] = [draft, draft]
    assert await draft_skill(sdeps, first) is not None
    _, second = await make_run(sdeps, owner, "single", [agent("solo")])
    await runner.run_system(sdeps, second)
    # The first draft is still waiting for review, so the follow-up doesn't add another.
    assert await draft_skill(sdeps, second) is None


def test_result_lists_each_file_once_and_rounds_tiny_costs():
    from datetime import timedelta

    from nexus_common.models import utcnow
    from nexus_orchestrator.systems.runner import latest_versions, result_text

    now = utcnow()
    files = [
        File(name="costs.md", version=1, created_at=now),
        File(name="plan.md", version=1, created_at=now + timedelta(seconds=1)),
        File(name="costs.md", version=2, created_at=now + timedelta(seconds=2)),
    ]
    kept = latest_versions(files)
    assert [(f.name, f.version) for f in kept] == [("plan.md", 1), ("costs.md", 2)]
    system = AgentSystem(name="t", topology="single", lifetime=SystemLifetime.persistent)
    run = AgentRun(
        status=RunStatus.completed, output="ok", cost_usd=Decimal("0.0021"),
        budget_usd=Decimal("0.25"),
    )  # fmt: skip
    text = result_text(system, run, kept)
    assert "Run cost under a cent" in text and "`plan.md`, `costs.md`" in text


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
    assert ("lead", "nexus-pro") in seen["models"]


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
    # The agent was told that calling send_email is itself the approval step.
    assert "Calling send_email pauses the run" in seen["mailer"][0][0].content


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
    from nexus_orchestrator.systems import runtime

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
    assert models[0] == ("lead", "nexus-mini") and models[1] == ("lead", "nexus-pro")
    # The helper asked for max but isn't the lead: capped at pro.
    assert ("helper", "nexus-pro") in models

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
    assert ("lead", "nexus-max") in seen["models"]
    assert ("helper", "nexus-mini") in seen["models"]
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
        "budget_usd": 40,  # the architect's guess is ignored
    }
    bad = {**spec, "agents": [agent("researcher", tools=["teleport"]), spec["agents"][1]]}
    scripts["nexus.design_system"] = [json.dumps({"spec": bad}), json.dumps({"spec": spec})]
    from nexus_common.skills import sync_library

    async with sdeps.sessionmaker() as db:
        from pathlib import Path

        await sync_library(db, Path(sdeps.settings.library_path))

    async def emit(*a, **k):
        pass

    ctx = ToolContext(deps=sdeps, user_id=user.id, chat_id=chat_id, emit=emit)
    allowed = {"web_search", "web_fetch", "run_code", "save_file"}
    asked = "Research EV sales and email the report to sam@example.com"
    tools = {t.name: t for t in system_tools(ctx, user, allowed, "trace1", asked=asked)}
    out = await tools["build_agent_system"].ainvoke({"request": "EV report please"})
    assert "Started Market report" in out and "researcher" in out
    async with sdeps.sessionmaker() as db:
        run = await db.scalar(select(AgentRun))
        system = await db.get(AgentSystem, run.system_id)
    assert system.lifetime == SystemLifetime.ephemeral and system.chat_id == chat_id
    assert run.status == RunStatus.queued and run.trace_id == "trace1"
    assert run.budget_usd == Decimal(str(sdeps.settings.default_run_budget_usd))
    # The team gets the user's own words, so a step Nexus left out (the email) isn't lost.
    assert run.input.startswith("EV report please") and asked in run.input
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


@pytest.mark.parametrize(
    "asked,budget,expected",
    [
        # Nexus's own small figure (the user named no sum): the owner's default stands.
        ("Make me a 3D printable rocket, keep it cheap", 0.25, "0.5000"),
        ("Make me a 3D printable rocket for $0.20", 0.2, "0.2000"),  # the user's sum
        ("yes, go up to 2 dollars", 2.0, "2.0000"),
        ("go ahead", 1.0, "1.0000"),  # more than the default is never cut back
    ],
)
async def test_a_team_budget_is_the_users_sum_or_the_owners_default(
    sdeps, owner, scripts, asked, budget, expected
):
    user, chat_id = owner
    spec = {
        "name": "Rocket",
        "goal": "A 3D printable rocket",
        "topology": "single",
        "agents": [agent("maker", role="maker", tools=["run_code"])],
    }
    scripts["nexus.design_system"] = [json.dumps({"spec": spec})]

    async def emit(*a, **k):
        pass

    ctx = ToolContext(deps=sdeps, user_id=user.id, chat_id=chat_id, emit=emit)
    tools = {t.name: t for t in system_tools(ctx, user, {"run_code"}, "trace1", asked=asked)}
    out = await tools["build_agent_system"].ainvoke(
        {"request": "A 3D printable rocket", "budget_usd": budget}
    )
    assert "Started Rocket" in out, out
    async with sdeps.sessionmaker() as db:
        run = await db.scalar(select(AgentRun))
    assert run.budget_usd == Decimal(expected)
    # Nexus is told where the figure came from, so it can say so.
    source = "the default from Admin" if expected == "0.5000" else "the user's figure"
    assert source in out


def test_keeping_a_team_takes_the_users_own_words():
    from nexus_orchestrator.systems.manage import asked_to_keep

    assert asked_to_keep("Research EV prices and keep this team for next month")
    assert asked_to_keep("save it as a template")
    assert asked_to_keep("Send me this digest every morning")
    # Budget talk is not a request to keep the team.
    assert not asked_to_keep("Email a two-line note about EV prices, keep the budget small")
    assert not asked_to_keep("keep it cheap")
    assert not asked_to_keep("Research the best-selling EVs in Europe")


class SlowWeb:
    """Searches that take a while, recording how many ran at once and the order they began."""

    def __init__(self):
        self.running = 0
        self.peak = 0
        self.log: list[str] = []

    async def search(self, query: str, limit: int = 8):
        from nexus_orchestrator.web import SearchResult

        self.running += 1
        self.peak = max(self.peak, self.running)
        self.log.append(f"start {query}")
        await asyncio.sleep(0.05)
        self.running -= 1
        self.log.append(f"end {query}")
        return [SearchResult(f"Result for {query}", f"https://example.com/{query}", "snippet")]


async def test_a_batch_of_searches_runs_side_by_side(sdeps, owner, scripts, seen):
    sdeps.web = SlowWeb()
    queries = [f"q{i}" for i in range(6)]
    scripts["solo"] = [
        [{"tool": "web_search", "args": {"query": q}} for q in queries]
        + [{"tool": "save_file", "args": {"name": "n.md", "content": "# N"}}],
        "Done.",
    ]
    _, run_id = await make_run(
        sdeps, owner, "single", [agent("solo", tools=["web_search", "save_file"])]
    )
    started = time.monotonic()
    await runner.run_system(sdeps, run_id)
    elapsed = time.monotonic() - started

    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed
    # Up to four at a time, not one after another.
    assert sdeps.web.peak == runtime.PARALLEL_READS
    assert elapsed < 0.05 * len(queries)
    # Results come back in the order the agent asked, and the write ran after every search.
    tool_msgs = [m for m in seen["solo"][1] if isinstance(m, ToolMessage)]
    assert [m.name for m in tool_msgs] == ["web_search"] * 6 + ["save_file"]
    assert [m.content.split("\n")[0] for m in tool_msgs[:6]] == [
        f"1. Result for {q}" for q in queries
    ]


# --- Notices when a team finishes -----------------------------------------------------


def _file(name: str, status: FileStatus = FileStatus.ready) -> File:
    return File(id=uuid.uuid4(), name=name, status=status)


def test_a_model_beats_the_script_and_the_step_file():
    files = [_file("rocket_model.py"), _file("rocket_model.step"), _file("rocket_model.stl")]
    assert featured(files, []).file.name == "rocket_model.stl"


def test_a_failed_preview_is_skipped_and_a_live_app_wins():
    files = [_file("rocket.stl"), _file("preview.png", FileStatus.failed)]
    assert featured(files, []).file.name == "rocket.stl"
    app = Link(id=uuid.uuid4(), url="https://game.servhub.cc", kind="app")
    assert featured(files, [app]).link is app


def test_the_agents_pick_wins_and_source_files_alone_give_nothing():
    files = [_file("render.png"), _file("report.md")]
    assert featured(files, [], {"file_id": str(files[1].id)}).file is files[1]
    assert featured([_file("main.py")], []).file is None


async def test_a_finished_run_presents_its_result(sdeps, owner, scripts):
    stl = "solid r\nendsolid r\n"
    scripts["solo"] = [
        {"tool": "save_file", "args": {"name": "notes.md", "content": "# Notes\n" + stl}},
        {"tool": "save_file", "args": {"name": "summary.txt", "content": "short"}},
        {"tool": "present_result", "args": {"name": "summary.txt"}},
        "## Rocket ready\nAll done.",
    ]
    _, run_id = await make_run(sdeps, owner, "single", [agent("solo", tools=["save_file"])])
    await runner.run_system(sdeps, run_id)

    run, system = await load(sdeps, run_id)
    async with sdeps.sessionmaker() as db:
        n = await db.scalar(select(Notification).where(Notification.run_id == run_id))
        f = await db.get(File, n.file_id)
    assert n.kind == NotificationKind.result
    assert n.title == f"{system.name} finished" and n.body == "Rocket ready"
    assert n.url == f"/c/{run.chat_id}" and f.name == "summary.txt"


async def test_presenting_something_the_run_did_not_make_says_so(sdeps, owner, scripts, seen):
    scripts["solo"] = [
        {"tool": "present_result", "args": {"name": "nope.stl"}},
        "Done.",
    ]
    _, run_id = await make_run(sdeps, owner, "single", [agent("solo", tools=[])])
    await runner.run_system(sdeps, run_id)
    async with sdeps.sessionmaker() as db:
        n = await db.scalar(select(Notification).where(Notification.run_id == run_id))
    assert n.file_id is None and n.kind == NotificationKind.result


async def test_a_stopped_run_gets_no_notice(sdeps, owner, scripts):
    _, run_id = await make_run(sdeps, owner, "single", [agent("solo", tools=[])])
    await runner.finish(sdeps, run_id, RunStatus.cancelled)
    async with sdeps.sessionmaker() as db:
        assert await db.scalar(select(Notification).where(Notification.run_id == run_id)) is None


# --- Looking at pictures ---------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


async def test_an_agent_sees_the_render_it_asks_for(sdeps, owner, scripts, seen):
    user, _ = owner
    _, run_id = await make_run(sdeps, owner, "single", [agent("solo", tools=["view_image"])])
    await save_generated(sdeps, user.id, "rocket.png", PNG, run_id=run_id)
    await save_generated(sdeps, user.id, "notes.md", b"# not a picture", run_id=run_id)
    scripts["solo"] = [
        {"tool": "view_image", "args": {"name": "out/rocket.png"}},
        {"tool": "view_image", "args": {"name": "notes.md"}},
        {"tool": "view_image", "args": {"name": "rocket.png"}},
        {"tool": "view_image", "args": {"name": "rocket.png"}},
        "The nose cone is there.",
    ]
    await runner.run_system(sdeps, run_id)

    calls = seen["solo"]
    first = calls[1][-1]
    assert first.name == "view_image" and first.content[0] == {
        "type": "text",
        "text": "rocket.png:",
    }
    image = first.content[1]["image_url"]["url"]
    assert image == "data:image/png;base64," + base64.b64encode(PNG).decode()
    # A file that isn't a picture is refused in words, and nothing is attached.
    assert "isn't a PNG" in calls[2][-1].content and calls[2][-1].name == "view_image"
    # Only the latest two pictures stay; earlier ones become a line of text.
    pictures = [m for m in calls[4] if m.name == "view_image" and not isinstance(m, ToolMessage)]
    assert [isinstance(m.content, list) for m in pictures] == [False, True, True]
    assert "no longer shown" in pictures[0].content
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed
