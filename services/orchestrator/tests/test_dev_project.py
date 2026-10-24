"""The dev_project team with scripted agents and a fake workbench: the house spec, a full run,
the review loop cap, plan-first pausing, routing of software requests and follow-ups that
reuse a project."""

import json
import uuid
from decimal import Decimal

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from nexus_common.devteam import DEV_IDS
from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Approval,
    Message,
    RunEvent,
    RunStatus,
    SystemLifetime,
    SystemStatus,
)
from nexus_common.spec import AgentSystemSpec
from nexus_common.systems import control_run, decide_approval, new_run
from nexus_orchestrator.route import Route, route
from nexus_orchestrator.systems import runner
from nexus_orchestrator.systems.dev import plan_first
from nexus_orchestrator.systems.manage import system_tools
from nexus_orchestrator.tools import ToolContext
from nexus_orchestrator.workbench import CommandResult

PLAN = "# Plan\n\n1. A 4x4 grid\n2. Arrow keys merge tiles\n3. Score"
DEV_TOOLS = {
    "web_search", "web_fetch", "ws_tree", "ws_read", "ws_write", "ws_edit", "ws_run",
    "ws_diff", "ws_commit",
}  # fmt: skip


class FakeWorkbench:
    def __init__(self):
        self.calls: list[tuple] = []

    async def run(self, user, project, command, timeout_s=None):
        self.calls.append(("run", user, project, command))
        return CommandResult(0, "abc1234\n", "", False, 3)

    async def read(self, user, project, path):
        self.calls.append(("read", user, project, path))
        return {"path": path, "size": len(PLAN), "encoding": "text", "content": PLAN,
                "truncated": False}  # fmt: skip

    async def commit(self, user, project, message):
        self.calls.append(("commit", user, project, message))
        return {"committed": True, "commit": "def5678", "summary": "1 file changed"}


@pytest.fixture
def wb(sdeps) -> FakeWorkbench:
    sdeps.workbench = FakeWorkbench()
    return sdeps.workbench


def done(tool: str, **args) -> dict:
    return {"tool": tool, "args": args}


def happy(scripts: dict, stack: str = "static", approved: bool = True) -> None:
    scripts["researcher"] = [done("submit_brief", stack=stack, summary="A 2048 game.")]
    scripts["architect"] = [done("submit_plan", summary="Grid, merges and a score.")]
    scripts["tester"] = [done("submit_tests", passed=14, failed=0, summary="All green.")]
    scripts["reviewer"] = [done("submit_review", approved=approved, summary="A playable 2048.")]


async def make_dev_run(sdeps, owner, request="Build me a 2048 game", project=None, **spec_kw):
    user, chat_id = owner
    spec = AgentSystemSpec.model_validate(
        {"name": "2048", "goal": "A 2048 game", "topology": "dev_project", "agents": [], **spec_kw}
    )
    async with sdeps.sessionmaker() as db:
        system = AgentSystem(
            id=uuid.uuid4(),
            user_id=user.id,
            chat_id=chat_id,
            name=spec.name,
            goal=spec.goal,
            spec={},
            topology=spec.topology,
            lifetime=SystemLifetime.ephemeral,
            status=SystemStatus.idle,
        )
        spec.project = project or str(system.id)
        system.spec = spec.model_dump()
        db.add(system)
        await db.flush()
        u = await db.merge(user)
        run = await new_run(db, sdeps.settings, sdeps.gateway, u, system, request, chat_id=chat_id)
        await db.commit()
        return system.id, run.id


async def load(sdeps, run_id):
    async with sdeps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        messages = (
            await db.scalars(
                select(Message).where(Message.chat_id == run.chat_id).order_by(Message.created_at)
            )
        ).all()
        events = (await db.scalars(select(RunEvent).where(RunEvent.run_id == run_id))).all()
        return run, [m.content for m in messages], events


def test_spec_gets_the_house_team_with_stages():
    spec = AgentSystemSpec.model_validate(
        {"name": "2048", "goal": "g", "topology": "dev_project", "agents": [], "max_rounds": 9}
    )
    assert [a.id for a in spec.agents] == list(DEV_IDS)
    assert all(a.max_steps == 40 for a in spec.agents)
    assert {a.id: a.tier for a in spec.agents}["architect"] == "pro"
    assert spec.max_rounds == 3
    assert [n["stage"] for n in spec.nodes()] == [
        "Brief", "Plan", "Build", "Build", "Test", "Review",
    ]  # fmt: skip
    kinds = {(e["from"], e["to"]): e["kind"] for e in spec.edges()}
    assert kinds[("tester", "reviewer")] == "review"
    assert kinds[("reviewer", "frontend")] == kinds[("reviewer", "backend")] == "feedback"
    # Fits the $3 default for this template.
    assert spec.estimate_usd(12, 8) <= 2
    # Other topologies don't get stages, and bad project ids are dropped.
    assert (
        AgentSystemSpec.model_validate(
            {"name": "x", "goal": "g", "topology": "dev_project", "agents": [], "project": "../x"}
        ).project
        is None
    )


def test_plan_first_words():
    assert plan_first("Build me a todo app but show me the plan first")
    assert plan_first("Make a 2048 game, plan first")
    assert plan_first("Build it and wait for me before coding")
    assert not plan_first("Build me a 2048 game with a nice plan for levels")


async def test_static_app_end_to_end(sdeps, owner, scripts, seen, wb):
    happy(scripts)
    scripts["frontend"] = ["Built the game with Vite."]
    system_id, run_id = await make_dev_run(sdeps, owner)
    await runner.run_system(sdeps, run_id)

    run, messages, events = await load(sdeps, run_id)
    assert run.status == RunStatus.completed, run.error
    assert run.budget_usd == Decimal("3.0000")
    # The plan went to the chat before the result, and the run carried on.
    assert "**Plan for 2048**" in messages[0] and PLAN in messages[0]
    assert "Building it now" in messages[0]
    final = messages[-1]
    assert final.startswith("**2048** finished.\n\n2048 is built and ready to release.")
    for fact in ("A playable 2048.", "static web app with Vite", "14 passing",
                 "approved in round 1", "Repo: added here", "Live link: added here"):  # fmt: skip
        assert fact in final, fact
    # Software always gets the house team, so it is never offered as a template.
    assert "template" not in final
    # A static app needs no backend.
    assert "backend" not in seen and run.agents["backend"]["status"] == "skipped"
    # Every workbench call went to this system's project, for this user.
    user, _ = owner
    assert {(u, p) for _, u, p, *_ in wb.calls} == {(str(user.id), str(system_id))}
    assert ("commit", str(user.id), str(system_id), "Ready for release: 2048") in wb.calls
    ready = [e for e in events if e.type == "project.ready"]
    assert ready and ready[0].data["commit"] == "def5678" and ready[0].data["stack"] == "static"
    # Builders got the workbench tools; the reviewer's diff starts where the run started.
    assert "ws_run" in seen["frontend:tools"][0] and "submit_tests" in seen["tester:tools"][0]
    assert 'ws_diff(ref="abc1234")' in seen["reviewer"][0][-1].content


async def test_review_loop_is_capped_at_three_rounds(sdeps, owner, scripts, seen, wb):
    happy(scripts, stack="web_fastapi")
    reject = done("submit_review", approved=False, fixes="1. web/main.ts: arrows (frontend)",
                  summary="Arrows don't merge.")  # fmt: skip
    scripts["reviewer"] = [reject, reject, reject, reject]
    scripts["tester"] = [done("submit_tests", passed=3, failed=2, summary="2 fail")] * 4
    _, run_id = await make_dev_run(sdeps, owner)
    await runner.run_system(sdeps, run_id)

    run, messages, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed
    # Three build rounds with both builders, then release without approval.
    assert len(seen["frontend"]) == len(seen["backend"]) == len(seen["reviewer"]) == 3
    assert "Your folder: api/" in seen["backend"][0][-1].content
    assert "1. web/main.ts: arrows" in seen["frontend"][1][-1].content
    final = messages[-1]
    assert "the reviewer still wants changes" in final
    assert "not approved after 3 rounds" in final and "3 passing, 2 failing" in final
    assert "Still to fix:\n1. web/main.ts" in final


async def test_plan_first_waits_for_go(sdeps, owner, scripts, seen, wb):
    happy(scripts)
    _, run_id = await make_dev_run(sdeps, owner, "Build me a 2048 game, plan first")
    await runner.run_system(sdeps, run_id)

    run, messages, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.paused
    assert len(messages) == 1 and "Reply **go**" in messages[0]
    assert "frontend" not in seen

    # The user says go: Nexus resumes the run like any paused run.
    async with sdeps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        system = await db.get(AgentSystem, run.system_id)
        assert control_run(run, system, "resume")
        await db.commit()
    await runner.continue_run(sdeps, run_id)

    run, messages, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed
    assert len(seen["architect"]) == 1 and len(seen["frontend"]) == 1
    # The plan was posted once, then the result.
    assert sum("**Plan for 2048**" in m for m in messages) == 1
    assert "ready to release" in messages[-1]


async def test_an_approval_parks_one_builder_only(sdeps, owner, scripts, seen, wb):
    happy(scripts, stack="web_fastapi")
    scripts["backend"] = [done("ws_run", command="uv run pytest"), "Back end done."]
    scripts["frontend"] = ["Front end done."]
    _, run_id = await make_dev_run(sdeps, owner, approval={"required_for": ["ws_run"]})
    await runner.run_system(sdeps, run_id)

    run, _, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.awaiting_approval
    assert len(seen["frontend"]) == 1 and "tester" not in seen
    user, _ = owner
    async with sdeps.sessionmaker() as db:
        approval = await db.scalar(select(Approval))
        actor = await db.merge(user)
        assert approval.agent == "backend"
        assert await decide_approval(db, approval, actor, "approve")
        await db.commit()
    await runner.continue_run(sdeps, run_id)

    run, messages, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed, run.error
    # Only the waiting builder carried on; the tester ran once, after both.
    assert len(seen["frontend"]) == 1 and len(seen["tester"]) == 1
    assert ("run", str(user.id), wb.calls[0][2], "uv run pytest") in wb.calls
    assert "ready to release" in messages[-1]


class RouteModel(BaseChatModel):
    reply: str = ""

    @property
    def _llm_type(self) -> str:
        return "route"

    def bind(self, **kwargs):
        return self

    def _generate(self, *a, **k):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(self.reply))])


async def test_router_flags_software():
    reply = '{"route": "team", "software": true, "reason": "build an app"}'
    assert await route(RouteModel(reply=reply), "Build me a 2048 game") == Route(True, True)
    reply = '{"route": "team", "software": false, "reason": "research"}'
    assert await route(RouteModel(reply=reply), "Research EV sales") == Route(True, False)
    # Software only counts for team work.
    reply = '{"route": "self", "software": true, "reason": "explain code"}'
    assert await route(RouteModel(reply=reply), "What does this regex do?") == Route()


def build_tool(sdeps, owner, allowed=DEV_TOOLS, software=True, asked="Build me a 2048 game"):
    user, chat_id = owner

    async def emit(*a, **k):
        pass

    ctx = ToolContext(deps=sdeps, user_id=user.id, chat_id=chat_id, emit=emit)
    tools = system_tools(ctx, user, set(allowed), "trace1", asked=asked, software=software)
    return {t.name: t for t in tools}["build_agent_system"]


def dev_spec(**kw) -> str:
    return json.dumps({"spec": {"name": "2048", "goal": "A 2048 game", "topology": "dev_project",
                                "agents": [], **kw}})  # fmt: skip


async def test_software_requests_get_the_dev_team(sdeps, owner, scripts, seen, enqueued):
    other = {"name": "x", "goal": "g", "topology": "single",
             "agents": [{"id": "a", "name": "A", "instructions": "do"}]}  # fmt: skip
    scripts["nexus.design_system"] = [json.dumps({"spec": other}), dev_spec()]
    out = await build_tool(sdeps, owner).ainvoke({"request": "Build me a 2048 game"})
    assert "Started 2048" in out and "workbench project" in out

    prompt = seen["nexus.design_system"][0][-1].content
    assert "use dev_project" in prompt
    # The first, non-dev design was sent back.
    assert "use topology dev_project" in seen["nexus.design_system"][1][-1].content
    async with sdeps.sessionmaker() as db:
        run = await db.scalar(select(AgentRun))
        system = await db.get(AgentSystem, run.system_id)
    spec = AgentSystemSpec.model_validate(system.spec)
    assert system.topology == "dev_project" and [a.id for a in spec.agents] == list(DEV_IDS)
    assert spec.project == str(system.id)
    assert run.budget_usd == Decimal("3.0000")
    assert ("run_system", str(run.id)) in enqueued.jobs


async def test_follow_up_reuses_the_project(sdeps, owner, scripts, seen):
    scripts["nexus.design_system"] = [dev_spec()]
    await build_tool(sdeps, owner).ainvoke({"request": "Build me a 2048 game"})
    async with sdeps.sessionmaker() as db:
        first = await db.scalar(select(AgentSystem))
        # The run ended and its ephemeral system was archived; it still counts as a project.
        first.status = SystemStatus.archived
        for run in (await db.scalars(select(AgentRun))).all():
            run.status = RunStatus.completed
        await db.commit()
    project = first.spec["project"]

    scripts["nexus.design_system"] = [dev_spec(project=project)]
    out = await build_tool(sdeps, owner, asked="Add a high score table").ainvoke(
        {"request": "Add a high score table to the 2048 game"}
    )
    assert f"workbench project {project}" in out
    # The architect was shown the earlier project to pick.
    assert f"- {project}: 2048: A 2048 game" in seen["nexus.design_system"][-1][-1].content

    # A project id the user doesn't own is never continued.
    scripts["nexus.design_system"] = [dev_spec(project="someone-elses")]
    await build_tool(sdeps, owner).ainvoke({"request": "Build a snake game"})
    async with sdeps.sessionmaker() as db:
        systems = (await db.scalars(select(AgentSystem).order_by(AgentSystem.created_at))).all()
    assert [s.spec["project"] for s in systems[:2]] == [project, project]
    assert systems[2].spec["project"] == str(systems[2].id)


async def test_building_software_needs_the_exec_capability(sdeps, owner, scripts):
    out = await build_tool(sdeps, owner, allowed=DEV_TOOLS - {"ws_run"}).ainvoke(
        {"request": "Build me a 2048 game"}
    )
    assert "Run code permission" in out
    assert "nexus.design_system" not in scripts


async def test_run_against_the_real_workbench(
    sdeps, owner, scripts, settings, tmp_path, monkeypatch
):
    """The agents' ws_* calls and the graph's own reads and commits all land in one real
    project folder (the workbench app in-process)."""
    import httpx

    import nexus_workbench.app as workbench_app
    from nexus_orchestrator.workbench import WorkbenchClient

    monkeypatch.setattr(workbench_app, "ROOT", tmp_path)
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=workbench_app.app), base_url="http://wb", timeout=60
    )
    sdeps.workbench = WorkbenchClient(settings, http)
    happy(scripts)
    scripts["architect"] = [
        done("ws_write", path="PLAN.md", content=PLAN),
        done("submit_plan", summary="Grid, merges and a score."),
    ]
    scripts["frontend"] = [
        done("ws_write", path="index.html", content="<h1>2048</h1>\n"),
        done("ws_commit", message="Add the page"),
        "Built the page.",
    ]
    system_id, run_id = await make_dev_run(sdeps, owner)
    try:
        await runner.run_system(sdeps, run_id)
    finally:
        await http.aclose()

    run, messages, events = await load(sdeps, run_id)
    assert run.status == RunStatus.completed, run.error
    assert PLAN in messages[0]
    user, _ = owner
    folder = tmp_path / str(user.id) / str(system_id)
    assert (folder / "index.html").read_text() == "<h1>2048</h1>\n"
    log = (await workbench_app.git(folder, "log", "--format=%h %s"))["stdout"].splitlines()
    # The builder's commit already held everything, so the release is that commit.
    assert [line.split(" ", 1)[1] for line in log] == ["Add the page", "Start project"]
    ready = next(e for e in events if e.type == "project.ready")
    assert ready.data["commit"] == log[0].split(" ")[0]


def test_a_dev_team_always_gets_three_review_rounds():
    """Lowering the rounds to make the estimate look cheaper left every fix undone."""
    spec = AgentSystemSpec.model_validate(
        {"name": "x", "goal": "g", "topology": "dev_project", "agents": [], "max_rounds": 1}
    )
    assert spec.max_rounds == 3


class SuiteWorkbench(FakeWorkbench):
    """A project with a Vite front end and a FastAPI back end whose suites report counts."""

    def __init__(self):
        super().__init__()
        self.files: dict[str, str] = {}

    async def write(self, user, project, path, content):
        self.calls.append(("write", user, project, path))
        self.files[path] = content
        return {"path": path}

    async def run(self, user, project, command, timeout_s=None):
        self.calls.append(("run", user, project, command))
        if "NEXUS_YES" in command:
            out = "vite.config.ts\nNEXUS_YES\n" if "vite.config" in command else "NEXUS_YES\n"
            return CommandResult(0, out, "", False, 3)
        if "--outputFile.json" in command:
            return CommandResult(0, " Tests  5 passed (5)\nNEXUS_COUNTS 5 0\n", "", False, 3)
        if "playwright test --reporter" in command:
            return CommandResult(0, "  1 passed\nNEXUS_COUNTS 1 0\n", "", False, 3)
        if "uv run pytest" in command:
            out = "E   assert 404 == 200\n1 failed, 3 passed\nNEXUS_COUNTS 3 1\n"
            return CommandResult(1, out, "", False, 3)
        return CommandResult(0, "abc1234\n", "", False, 3)


async def test_nexus_wires_and_runs_the_tests_itself(sdeps, owner, scripts, seen):
    """The house test setup goes in before the tester, and the measured counts (not the
    tester's) reach the reviewer and the answer."""
    wb = sdeps.workbench = SuiteWorkbench()
    happy(scripts, stack="web_fastapi", approved=False)
    scripts["tester"] = [done("submit_tests", passed=14, failed=0, summary="All green.")] * 3
    scripts["reviewer"] = [
        done("submit_review", approved=False, fixes="1. api/main.py: SPA fallback (backend)",
             summary="Almost.")
    ] * 3  # fmt: skip
    _, run_id = await make_dev_run(sdeps, owner)
    await runner.run_system(sdeps, run_id)

    run, messages, _ = await load(sdeps, run_id)
    assert run.status == RunStatus.completed, run.error
    assert "import viteConfig from './vite.config.ts'" in wb.files["web/vitest.config.ts"]
    assert "tests/e2e/**" in wb.files["web/vitest.config.ts"]
    assert "uvicorn main:app" in wb.files["web/playwright.config.ts"]
    assert "pythonpath = ." in wb.files["api/pytest.ini"]
    setup = [c[3] for c in wb.calls if c[0] == "run" and "npm pkg set" in c[3]]
    assert setup and "@playwright/test@$PLAYWRIGHT_VERSION" in setup[0]
    assert "The test runners are set up" in seen["tester"][0][-1].content
    # Later rounds hand the reviewer's fixes to the tester too.
    assert "SPA fallback" in seen["tester"][1][-1].content
    review = seen["reviewer"][0][-1].content
    assert "Test report: 9 passed, 1 failed" in review and "assert 404 == 200" in review
    # The builders see the failing output with the fixes.
    assert "assert 404 == 200" in seen["backend"][1][-1].content
    final = messages[-1]
    assert "9 passing, 1 failing (unit 5 passed; smoke 1 passed; backend 3 passed, 1 failed)" in (
        final
    )
    assert "not approved after 3 rounds" in final


@pytest.mark.parametrize(
    "asked,budget,expected",
    [
        ("go ahead", 0.81, "3.0000"),  # Nexus's own figure: the default stands
        ("Build me a 2048 game, keep it cheap", 0.25, "3.0000"),
        ("Build me a 2048 game for $1.50", 1.5, "1.5000"),  # the user's sum
        ("yes, go up to 4 dollars", 4.0, "4.0000"),
    ],
)
async def test_a_software_budget_is_the_users_sum_or_the_default(
    sdeps, owner, scripts, asked, budget, expected
):
    scripts["nexus.design_system"] = [dev_spec()]
    out = await build_tool(sdeps, owner, asked=asked).ainvoke(
        {"request": "Build me a 2048 game", "budget_usd": budget}
    )
    assert "Started 2048" in out, out
    async with sdeps.sessionmaker() as db:
        run = await db.scalar(select(AgentRun))
    assert run.budget_usd == Decimal(expected)


async def test_skills_are_drafted_only_from_approved_software(sdeps, owner, scripts, wb):
    from nexus_orchestrator.systems.learn import draft_skill

    happy(scripts, approved=False)
    scripts["reviewer"] = [done("submit_review", approved=False, fixes="1. x", summary="s")] * 3
    _, run_id = await make_dev_run(sdeps, owner)
    await runner.run_system(sdeps, run_id)
    # An unapproved run is never learned from; the drafting model isn't even asked.
    assert await draft_skill(sdeps, run_id) is None
    assert "nexus.draft_skill" not in scripts
