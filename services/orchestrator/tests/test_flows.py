"""Flows the architect designs (research first, side-by-side steps, checkers that send work
back) and reusing a past result instead of building it again."""

import json
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from nexus_common.models import AgentRun, AgentSystem, File, RunStatus
from nexus_common.spec import AgentSystemSpec
from nexus_orchestrator.systems import runner
from nexus_orchestrator.systems.manage import system_tools
from nexus_orchestrator.tools import ToolContext

from .test_systems import agent, human_texts, load, make_run


def flow(*agents, **kw) -> AgentSystemSpec:
    return AgentSystemSpec.model_validate(
        {"name": "f", "goal": "g", "topology": "flow", "agents": list(agents), **kw}
    )


# A Minion: two researchers side by side, a modeller after both, a checker on the modeller.
MINION = [
    agent("looks", tools=["web_search"]),
    agent("sizes", tools=["web_search"]),
    agent("modeller", after=["looks", "sizes"]),
    agent("checker", checks="modeller"),
]


def test_a_flow_is_wired_from_after_and_checks():
    spec = flow(*MINION)
    assert spec.agent("checker").after == ["modeller"]
    assert spec.flow_end == "checker"
    levels = {n["id"]: n["level"] for n in spec.nodes()}
    assert levels == {"looks": 0, "sizes": 0, "modeller": 1, "checker": 2}
    assert [n["id"] for n in spec.nodes() if n["lead"]] == ["checker"]
    edges = {(e["from"], e["to"], e["kind"]) for e in spec.edges()}
    assert edges == {
        ("looks", "modeller", "handoff"),
        ("sizes", "modeller", "handoff"),
        ("modeller", "checker", "review"),
        ("checker", "modeller", "feedback"),
    }
    # The checker and the work it checks run once per round.
    once = flow(*MINION[:3]).estimate_usd(10, 5)
    assert spec.estimate_usd(10, 5) > once


@pytest.mark.parametrize(
    ("agents", "problem"),
    [
        ([agent("a"), agent("b")], "exactly one last agent"),
        ([agent("a", after=["b"]), agent("b", after=["a"])], "circle"),
        ([agent("a"), agent("b", after=["ghost"])], "unknown agents"),
        ([agent("a"), agent("b", checks="a"), agent("c", after=["a", "b"])], "checker"),
        ([agent("a"), agent("b", checks="a"), agent("c", checks="a")], "one checker"),
        ([agent("a"), agent("b", checks="a"), agent("c", checks="b")], "checked in turn"),
        ([agent("a")], "at least 2"),
    ],
)
def test_a_badly_wired_flow_says_what_to_fix(agents, problem):
    with pytest.raises(ValidationError, match=problem):
        flow(*agents)


async def test_a_flow_researches_side_by_side_then_makes_and_is_checked(
    sdeps, owner, scripts, seen
):
    scripts["looks"] = ["Yellow capsule, one or two eyes, goggles, blue dungarees."]
    scripts["sizes"] = ["Body 60 mm tall, 30 mm wide."]
    scripts["modeller"] = ["Model v1", "Model v2 with goggles"]
    scripts["checker"] = [
        {"tool": "verdict", "args": {"approved": False, "feedback": "Add the goggles"}},
        {"tool": "verdict", "args": {"approved": True, "feedback": "Looks right"}},
    ]
    _, run_id = await make_run(sdeps, owner, "flow", MINION, max_rounds=3)
    await runner.run_system(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed
    # The answer is the work the checker passed, not its verdict.
    assert run.output == "Model v2 with goggles"
    first = human_texts(seen["modeller"][0])
    assert "blue dungarees" in first and "60 mm" in first
    # The revision continues the modeller's own conversation, with the feedback.
    assert "Add the goggles" in human_texts(seen["modeller"][1])
    assert "Model v1" in [m.content for m in seen["modeller"][1] if m.type == "ai"]
    # Each researcher ran once, even though the modeller ran twice.
    assert len(seen["looks"]) == 1 and len(seen["sizes"]) == 1
    assert len(seen["checker"]) == 2


async def test_work_after_a_checker_waits_for_the_pass(sdeps, owner, scripts, seen):
    scripts["writer"] = ["Draft 1", "Draft 2"]
    scripts["editor"] = [
        {"tool": "verdict", "args": {"approved": False, "feedback": "Cite sources"}},
        {"tool": "verdict", "args": {"approved": True, "feedback": "Fine"}},
    ]
    scripts["poster"] = ["Posted: Draft 2"]
    agents = [
        agent("writer"),
        agent("editor", checks="writer"),
        agent("poster", after=["editor"]),
    ]
    _, run_id = await make_run(sdeps, owner, "flow", agents)
    await runner.run_system(sdeps, run_id)
    run, _ = await load(sdeps, run_id)
    assert run.output == "Posted: Draft 2"
    # The poster ran once, after the pass, with the checked work.
    assert len(seen["poster"]) == 1
    text = human_texts(seen["poster"][0])
    assert "Draft 2" in text and "Draft 1" not in text and "You are the last stage" in text


async def _tools(sdeps, owner, asked="make a 3d model of a minion"):
    user, chat_id = owner

    async def emit(*a, **k):
        pass

    ctx = ToolContext(deps=sdeps, user_id=user.id, chat_id=chat_id, emit=emit)
    allowed = {"web_search", "web_fetch", "run_code", "save_file"}
    tools = {t.name: t for t in system_tools(ctx, user, allowed, "t", asked=asked)}
    return ctx, tools


async def _minion_made(sdeps, owner, scripts) -> str:
    scripts["solo"] = [
        {"tool": "save_file", "args": {"name": "minion.md", "content": "# Minion\nplan"}},
        "Minion done.",
    ]
    _, run_id = await make_run(
        sdeps, owner, "single", [agent("solo", tools=["save_file"])], request="minion"
    )
    await runner.run_system(sdeps, run_id)
    return str(run_id)


async def test_something_already_made_is_opened_not_rebuilt(sdeps, owner, scripts, seen):
    run_id = await _minion_made(sdeps, owner, scripts)
    scripts["nexus.design_system"] = [json.dumps({"existing": run_id})]
    ctx, tools = await _tools(sdeps, owner)
    out = await tools["build_agent_system"].ainvoke({"request": "Make a 3D model of a Minion"})
    assert out.startswith("Not started") and "minion.md" in out
    assert f"based_on={run_id}" in out and "fresh=true" in out
    # The earlier file goes on the reply, and is the one to open.
    assert [f.name for f in ctx.saved] == ["minion.md"]
    assert f"focus target {ctx.saved[0].id}" in out
    # The architect was shown the past result to choose from.
    prompt = human_texts(seen["nexus.design_system"][0])
    assert "PAST RESULTS" in prompt and run_id in prompt and "minion.md" in prompt
    async with sdeps.sessionmaker() as db:
        assert len((await db.scalars(select(AgentRun))).all()) == 1


async def test_a_change_starts_from_the_earlier_files(sdeps, owner, scripts, seen):
    run_id = await _minion_made(sdeps, owner, scripts)
    spec = {
        "name": "Minion v2",
        "goal": "Longer arms",
        "topology": "single",
        "agents": [agent("modeller", tools=["run_code"])],
    }
    scripts["nexus.design_system"] = [json.dumps({"plan": "one modeller", "spec": spec})]
    ctx, tools = await _tools(sdeps, owner, asked="make its arms longer")
    out = await tools["build_agent_system"].ainvoke(
        {"request": "make its arms longer", "based_on": run_id}
    )
    assert "Started Minion v2" in out
    prompt = human_texts(seen["nexus.design_system"][0])
    assert "CHANGE TO A PAST RESULT" in prompt and "PAST RESULTS" not in prompt
    async with sdeps.sessionmaker() as db:
        new = await db.scalar(select(AgentRun).where(AgentRun.status == RunStatus.queued))
        md = await db.scalar(select(File).where(File.run_id == uuid.UUID(run_id)))
    assert str(md.id) in [str(x) for x in new.file_ids]
    assert "changes an earlier result" in new.input


async def test_fresh_skips_past_results(sdeps, owner, scripts, seen):
    await _minion_made(sdeps, owner, scripts)
    spec = {
        "name": "Minion",
        "goal": "A new minion",
        "topology": "single",
        "agents": [agent("modeller", tools=["run_code"])],
    }
    scripts["nexus.design_system"] = [json.dumps({"spec": spec})]
    _, tools = await _tools(sdeps, owner, asked="make me another minion from scratch")
    out = await tools["build_agent_system"].ainvoke({"request": "another minion", "fresh": True})
    assert "Started Minion" in out
    assert "PAST RESULTS" not in human_texts(seen["nexus.design_system"][0])
    async with sdeps.sessionmaker() as db:
        assert len((await db.scalars(select(AgentSystem))).all()) == 2


async def _bury(sdeps, owner, n: int) -> None:
    """n newer finished results, so an older one falls out of the newest ones."""
    from datetime import timedelta

    from nexus_common.models import SystemLifetime, SystemStatus, utcnow

    user, chat_id = owner
    async with sdeps.sessionmaker() as db:
        for i in range(n):
            system = AgentSystem(
                id=uuid.uuid4(), user_id=user.id, chat_id=chat_id, name=f"Digest {i}",
                goal="A news digest", spec={}, topology="single",
                lifetime=SystemLifetime.ephemeral, status=SystemStatus.archived,
            )  # fmt: skip
            db.add(system)
            await db.flush()
            db.add(
                AgentRun(
                    system_id=system.id, user_id=user.id, chat_id=chat_id, input="news",
                    status=RunStatus.completed, budget_usd=1, trace_id="t",
                    finished_at=utcnow() + timedelta(minutes=i + 1),
                )
            )  # fmt: skip
        await db.commit()


async def test_an_old_result_named_in_chat_is_found_and_fixed(sdeps, owner, scripts, seen):
    """ "The minion model is wrong": Nexus finds it among older results however many came
    after, and the team starts by looking at the old files."""
    run_id = await _minion_made(sdeps, owner, scripts)
    async with sdeps.sessionmaker() as db:
        run = await db.get(AgentRun, uuid.UUID(run_id))
        system = await db.get(AgentSystem, run.system_id)
        system.name, system.goal = "Minion 3D model", "A 3D model of a Minion figurine"
        await db.commit()
    await _bury(sdeps, owner, 20)
    spec = {
        "name": "Minion fix",
        "goal": "Fix the Minion model",
        "topology": "single",
        "agents": [agent("modeller", tools=["run_code"])],
    }
    scripts["nexus.design_system"] = [json.dumps({"plan": "p", "spec": spec, "based_on": run_id})]
    _, tools = await _tools(sdeps, owner, asked="the minion model looks wrong, fix it")
    out = await tools["build_agent_system"].ainvoke({"request": "The minion model looks wrong"})
    assert "Started Minion fix" in out
    prompt = human_texts(seen["nexus.design_system"][0])
    past = prompt.split("PAST RESULTS", 1)[1]
    assert past.split("\n")[1].startswith(f"- {run_id}: Minion 3D model")
    async with sdeps.sessionmaker() as db:
        new = await db.scalar(select(AgentRun).where(AgentRun.status == RunStatus.queued))
        md = await db.scalar(select(File).where(File.run_id == uuid.UUID(run_id)))
    assert str(md.id) in [str(x) for x in new.file_ids]
    assert "changes an earlier result" in new.input and "minion.md" in new.input
    assert "view_image" in new.input and "don't start over" in new.input


async def test_a_based_on_that_isnt_a_past_result_is_ignored(sdeps, owner, scripts, seen):
    await _minion_made(sdeps, owner, scripts)
    spec = {"name": "M", "goal": "g", "topology": "single", "agents": [agent("m")]}
    scripts["nexus.design_system"] = [json.dumps({"spec": spec, "based_on": str(uuid.uuid4())})]
    _, tools = await _tools(sdeps, owner, asked="a minion")
    out = await tools["build_agent_system"].ainvoke({"request": "a minion"})
    assert "Started M" in out
    async with sdeps.sessionmaker() as db:
        new = await db.scalar(select(AgentRun).where(AgentRun.status == RunStatus.queued))
    assert new.file_ids == [] and "earlier result" not in new.input
