"""The dev team's deploy stage with a fake deployer: a deployed app's link and screenshot in the
reply, a failed health check going back to the builders first, the reviewer's and the tests'
gate, the member limit, app names, and Nexus's tool for making an app public or private."""

import base64
import uuid

import pytest
from sqlalchemy import select

from nexus_common.deployer import DeployResult
from nexus_common.models import (
    AgentRun,
    AgentSystem,
    File,
    Link,
    Message,
    MessageLink,
    Project,
    RoleName,
    RunStatus,
    SystemLifetime,
    SystemStatus,
    UserStatus,
)
from nexus_common.spec import AgentSystemSpec
from nexus_common.systems import new_run
from nexus_orchestrator.deploy import deploy_project
from nexus_orchestrator.systems import runner
from nexus_orchestrator.tools import ToolContext, build_tools
from nexus_orchestrator.workbench import CommandResult

PNG = base64.b64encode(b"\x89PNG fake").decode()
URL = "http://2048.apps.localhost:8080"


class FakeWorkbench:
    async def run(self, user, project, command, timeout_s=None):
        if command.startswith("git log"):
            return CommandResult(0, "abc1234\x1fAdd the game\x1f2026-10-21T10:00:00+00:00\n",
                                 "", False, 3)  # fmt: skip
        return CommandResult(0, "abc1234\n", "", False, 3)

    async def read(self, user, project, path):
        return {"path": path, "size": 6, "encoding": "text", "content": "# Plan",
                "truncated": False}  # fmt: skip

    async def commit(self, user, project, message):
        return {"committed": True, "commit": "abc1234", "summary": "1 file changed"}


class FakeDeployer:
    def __init__(self, results: list[DeployResult] | None = None):
        self.results = results or []
        self.deploys: list[tuple[str, dict]] = []
        self.stopped: list[str] = []

    async def deploy(self, slug, **kw):
        self.deploys.append((slug, kw))
        if self.results:
            return self.results.pop(0)
        return DeployResult(ok=True, url=URL, stack="static", screenshot=PNG)

    async def stop(self, slug):
        self.stopped.append(slug)


def done(tool, **args):
    return {"tool": tool, "args": args}


def scripts_for(scripts: dict, approved=True, failed=0, reviews=1, passed=14) -> None:
    scripts["researcher"] = [done("submit_brief", stack="static", summary="A 2048 game.")]
    scripts["architect"] = [done("submit_plan", summary="Grid and score.")]
    scripts["tester"] = [done("submit_tests", passed=passed, failed=failed, summary="Ran.")] * 4
    review = done("submit_review", approved=approved, fixes="1. fix", summary="A 2048 game.")
    scripts["reviewer"] = [review] * (reviews + 3)


async def dev_run(sdeps, user, chat_id):
    spec = AgentSystemSpec.model_validate(
        {"name": "2048", "goal": "A 2048 game", "topology": "dev_project", "agents": []}
    )
    async with sdeps.sessionmaker() as db:
        system = AgentSystem(
            id=uuid.uuid4(), user_id=user.id, chat_id=chat_id, name=spec.name, goal=spec.goal,
            spec={}, topology=spec.topology, lifetime=SystemLifetime.ephemeral,
            status=SystemStatus.idle,
        )  # fmt: skip
        spec.project = str(system.id)
        system.spec = spec.model_dump()
        db.add(system)
        await db.flush()
        run = await new_run(
            db, sdeps.settings, sdeps.gateway, await db.merge(user), system, "Build 2048",
            chat_id=chat_id,
        )  # fmt: skip
        await db.commit()
        return run.id


@pytest.fixture
def ddeps(sdeps):
    sdeps.workbench = FakeWorkbench()
    sdeps.deployer = FakeDeployer()
    return sdeps


async def outcome(sdeps, run_id) -> tuple[AgentRun, str, Project, list[File]]:
    async with sdeps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        msgs = (
            await db.scalars(
                select(Message).where(Message.chat_id == run.chat_id).order_by(Message.created_at)
            )
        ).all()
        project = await db.scalar(select(Project))
        files = (await db.scalars(select(File).where(File.run_id == run_id))).all()
        return run, msgs[-1].content, project, files


async def test_an_approved_green_app_is_deployed_with_a_screenshot(ddeps, owner, scripts):
    user, chat_id = owner
    scripts_for(scripts)
    run_id = await dev_run(ddeps, user, chat_id)
    await runner.run_system(ddeps, run_id)

    run, final, project, files = await outcome(ddeps, run_id)
    assert run.status == RunStatus.completed, run.error
    slug, args = ddeps.deployer.deploys[0]
    assert slug == "2048"
    assert args == {
        "app_id": str(project.id),
        "owner": str(user.id),
        "user": str(user.id),
        "project": project.workbench_id,
        "stack": "static",
        "internet": False,
        "private": True,
    }
    assert f"- Live link: {URL} (private: only you can open it" in final
    assert (project.app_slug, project.app_state, project.live_url) == ("2048", "running", URL)
    assert project.deployed_at is not None and project.deploy_error is None
    # The screenshot comes with the team's result.
    assert [f.name for f in files] == ["2048-screenshot.png"]
    assert "`2048-screenshot.png`" in final
    # And a link to the running app, in the library and on the answer.
    async with ddeps.sessionmaker() as db:
        link = await db.scalar(select(Link))
        shown = await db.scalar(
            select(MessageLink.message_id).where(MessageLink.link_id == link.id)
        )
    assert (link.url, link.kind, link.title) == (URL, "app", "2048 (live app)")
    assert link.project_id == project.id and shown is not None


async def test_a_failed_health_check_goes_back_to_the_builders_first(ddeps, owner, scripts, seen):
    user, chat_id = owner
    scripts_for(scripts)
    ddeps.deployer.results = [
        DeployResult(
            ok=False,
            url=URL,
            error="The app started but failed its health check.",
            logs="--- web ---\nnginx: [emerg] no dist",
        ),  # fmt: skip
    ]
    run_id = await dev_run(ddeps, user, chat_id)
    await runner.run_system(ddeps, run_id)

    run, final, project, _ = await outcome(ddeps, run_id)
    assert run.status == RunStatus.completed
    # The builders saw the failure and its logs; the second deploy worked.
    assert len(seen["frontend"]) == 2
    retry = seen["frontend"][1][-1].content
    assert "The deploy failed: The app started but failed its health check." in retry
    assert "nginx: [emerg] no dist" in retry
    assert len(ddeps.deployer.deploys) == 2 and ddeps.deployer.stopped == ["2048"]
    assert f"- Live link: {URL}" in final and project.app_state == "running"


async def test_a_second_failure_is_reported(ddeps, owner, scripts):
    user, chat_id = owner
    scripts_for(scripts)
    bad = DeployResult(ok=False, url=URL, error="The web didn't build.", logs="npm ERR!")
    ddeps.deployer.results = [bad, bad]
    run_id = await dev_run(ddeps, user, chat_id)
    await runner.run_system(ddeps, run_id)

    _, final, project, files = await outcome(ddeps, run_id)
    assert "- Live link: not deployed: The web didn't build." in final
    assert project.app_state == "failed" and project.deploy_error == "The web didn't build."
    assert files == []


@pytest.mark.parametrize(
    "approved,passed,failed,why",
    [
        (False, 14, 0, "the reviewer hasn't approved it"),
        (True, 14, 3, "not every test passed"),
        (True, 0, 0, "no tests ran"),
    ],
)
async def test_only_approved_green_apps_are_deployed(
    ddeps, owner, scripts, approved, passed, failed, why
):
    user, chat_id = owner
    scripts_for(scripts, approved=approved, passed=passed, failed=failed)
    run_id = await dev_run(ddeps, user, chat_id)
    await runner.run_system(ddeps, run_id)

    _, final, _, _ = await outcome(ddeps, run_id)
    assert ddeps.deployer.deploys == []
    assert f"- Live link: not deployed, as {why}" in final


async def test_members_can_run_three_apps(ddeps, sessionmaker):
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        member = await create_user(
            db, email="priya@example.com", role=RoleName.member, status=UserStatus.active,
            name="Priya Rao",
        )  # fmt: skip
        for i in range(3):
            db.add(Project(user_id=member.id, workbench_id=f"w{i}", name=f"App {i}",
                           app_slug=f"priya-app-{i}", app_state="running"))  # fmt: skip
        fourth = Project(user_id=member.id, workbench_id="w9", name="2048")
        db.add(fourth)
        await db.commit()
    result = await deploy_project(ddeps, fourth.id)
    assert not result.ok and "already has 3 apps running" in result.error
    assert ddeps.deployer.deploys == []

    # Stopping one makes room; the app is named after its owner.
    async with sessionmaker() as db:
        row = await db.scalar(select(Project).where(Project.workbench_id == "w0"))
        row.app_state = "stopped"
        await db.commit()
    assert (await deploy_project(ddeps, fourth.id)).ok
    assert ddeps.deployer.deploys[0][0] == "2048-priya"


async def test_names_are_kept_and_taken_ones_get_a_suffix(ddeps, owner, sessionmaker):
    user, _ = owner
    async with sessionmaker() as db:
        db.add(Project(user_id=user.id, workbench_id="old", name="2048", app_slug="2048"))
        new = Project(user_id=user.id, workbench_id="new", name="2048")
        db.add(new)
        await db.commit()
    assert (await deploy_project(ddeps, new.id)).ok
    first = ddeps.deployer.deploys[0][0]
    assert first.startswith("2048-") and len(first) == len("2048-k7")
    # A redeploy keeps the name it got.
    assert (await deploy_project(ddeps, new.id)).ok
    assert ddeps.deployer.deploys[1][0] == first


async def test_reserved_names_are_never_picked(ddeps, owner, sessionmaker, monkeypatch):
    user, _ = owner
    monkeypatch.setattr(ddeps.settings, "reserved_hostnames", "plex, Nas")
    async with sessionmaker() as db:
        rows = [Project(user_id=user.id, workbench_id=n, name=n) for n in ("Nexus", "plex")]
        db.add_all(rows)
        await db.commit()
    for row in rows:
        assert (await deploy_project(ddeps, row.id)).ok
    names = [slug for slug, _ in ddeps.deployer.deploys]
    assert names[0].startswith("nexus-") and names[1].startswith("plex-")


async def test_the_visibility_tool(ddeps, owner, sessionmaker):
    user, chat_id = owner
    async with sessionmaker() as db:
        db.add(Project(user_id=user.id, workbench_id="w1", name="2048", app_slug="2048",
                       app_state="running", live_url=URL))  # fmt: skip
        await db.commit()

    async def emit(*a, **k):
        pass

    ctx = ToolContext(deps=ddeps, user_id=user.id, chat_id=chat_id, emit=emit)
    tool = {t.name: t for t in build_tools(ctx, ["app_visibility"])}["app_visibility"]
    assert "No running app called snake" in await tool.ainvoke({"app": "snake", "public": True})
    out = await tool.ainvoke({"app": "https://2048.servhub.cc/", "public": True})
    assert out == f"2048 is now public: anyone with the link can open {URL}."
    slug, args = ddeps.deployer.deploys[-1]
    assert slug == "2048" and args["build"] is False and args["private"] is False
    out = await tool.ainvoke({"app": "2048", "public": False})
    assert "private again" in out and ddeps.deployer.deploys[-1][1]["private"] is True
    async with sessionmaker() as db:
        assert (await db.scalar(select(Project))).public is False
