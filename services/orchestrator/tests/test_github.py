"""GitHub with a mocked API: the App's JWT and tokens, repos and main, a dev run's release
(Library row, push, merge only when approved and green), naming, and moving a repo to the
user's own GitHub."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import select

from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Link,
    Message,
    MessageLink,
    Project,
    RoleName,
    RunStatus,
    SystemLifetime,
    SystemStatus,
    User,
    UserStatus,
)
from nexus_common.spec import AgentSystemSpec
from nexus_common.systems import new_run
from nexus_common.toolinfo import BY_NAME, allowed_tools
from nexus_orchestrator.github import GitHubApp, GitHubError
from nexus_orchestrator.systems import runner
from nexus_orchestrator.systems.release import base_repo_name
from nexus_orchestrator.tools import ToolContext, build_tools
from nexus_orchestrator.workbench import CommandResult

ORG = "servhub-apps"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_B64 = base64.b64encode(
    KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
).decode()


class FakeGitHub:
    """Just enough of the REST API, with what was asked of it."""

    def __init__(self):
        self.repos: dict[str, dict] = {}
        self.refs: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.tokens = 0
        self.default_branch: dict[str, str] = {}
        self.fast_forward = True

    def repo_json(self, name: str, owner: str = ORG) -> dict:
        return {
            "name": name,
            "owner": {"login": owner},
            "html_url": f"https://github.com/{owner}/{name}",
            "clone_url": f"https://github.com/{owner}/{name}.git",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        self.calls.append((method, path, body))
        auth = request.headers.get("authorization", "")
        if path.endswith("/access_tokens"):
            assert auth.startswith("Bearer ey"), "token requests are signed with the App's JWT"
            self.tokens += 1
            expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            return httpx.Response(201, json={"token": f"ghs_{self.tokens}", "expires_at": expires})
        if path == "/app":
            return httpx.Response(200, json={"slug": "jarvis-servhub"})
        if path == "/users/jarvis-servhub[bot]":
            return httpx.Response(200, json={"id": 4242})
        if method == "POST" and path == f"/orgs/{ORG}/repos":
            assert body["private"] is True
            self.repos[body["name"]] = body
            return httpx.Response(201, json=self.repo_json(body["name"]))
        parts = path.strip("/").split("/")
        if parts[0] == "repos" and len(parts) >= 3:
            name = parts[2]
            rest = "/".join(parts[3:])
            if name not in self.repos:
                return httpx.Response(404, json={"message": "Not Found"})
            if method == "GET" and not rest:
                return httpx.Response(200, json=self.repo_json(name))
            if method == "PATCH" and not rest:
                self.default_branch[name] = body["default_branch"]
                return httpx.Response(200, json=self.repo_json(name))
            if rest == "git/ref/heads/main":
                if (name, "main") not in self.refs:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json={"object": {"sha": self.refs[(name, "main")]}})
            if method == "POST" and rest == "git/refs":
                self.refs[(name, body["ref"].removeprefix("refs/heads/"))] = body["sha"]
                return httpx.Response(201, json={})
            if method == "PATCH" and rest == "git/refs/heads/main":
                if not self.fast_forward:
                    return httpx.Response(422, json={"message": "Update is not a fast forward"})
                self.refs[(name, "main")] = body["sha"]
                return httpx.Response(200, json={})
            if method == "POST" and rest == "merges":
                return httpx.Response(201, json={"sha": "merged"})
            if method == "POST" and rest == "transfer":
                return httpx.Response(202, json={"name": name})
        return httpx.Response(500, json={"message": f"unexpected {method} {path}"})


@pytest.fixture
def fake_gh() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def gh_settings(settings):
    return settings.model_copy(
        update={
            "github_apps_org": ORG,
            "github_app_id": "12345",
            "github_app_installation_id": "678",
            "github_app_private_key_b64": KEY_B64,
            "github_api_url": "https://api.github.test",
        }
    )


@pytest.fixture
def gh(gh_settings, fake_gh) -> GitHubApp:
    return GitHubApp(gh_settings, httpx.AsyncClient(transport=httpx.MockTransport(fake_gh)))


def test_jwt_is_signed_by_the_app_key(gh, gh_settings):
    header, claims, signature = gh.jwt().split(".")

    def decode(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    assert json.loads(decode(header)) == {"alg": "RS256", "typ": "JWT"}
    data = json.loads(decode(claims))
    assert data["iss"] == "12345" and data["exp"] - data["iat"] == 600
    # Raises if the signature is wrong.
    KEY.public_key().verify(
        decode(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )


def test_a_bad_key_is_explained(gh_settings):
    app = GitHubApp(gh_settings.model_copy(update={"github_app_private_key_b64": "bm9wZQ=="}))
    with pytest.raises(GitHubError, match="PEM private key"):
        app.jwt()


async def test_installation_token_is_cached_until_near_expiry(gh, fake_gh):
    assert await gh.token() == "ghs_1"
    assert await gh.token() == "ghs_1" and fake_gh.tokens == 1
    # Within five minutes of expiring, a fresh one is fetched.
    gh._token = ("ghs_1", gh._clock() + 60)
    assert await gh.token() == "ghs_2"
    # A push token is new each time, and limited to one repo's contents.
    await gh.push_token("2048")
    assert fake_gh.calls[-1][2] == {
        "repositories": ["2048"],
        "permissions": {"contents": "write"},
    }


async def test_main_is_created_then_fast_forwarded_then_merged(gh, fake_gh):
    repo = await gh.create_repo("2048", "A game")
    assert await gh.update_main(repo, "jarvis/r1", "aaa") == "main created"
    assert fake_gh.refs[("2048", "main")] == "aaa" and fake_gh.default_branch["2048"] == "main"
    assert await gh.update_main(repo, "jarvis/r2", "bbb") == "main fast-forwarded"
    fake_gh.fast_forward = False
    assert await gh.update_main(repo, "jarvis/r3", "ccc") == "merged into main"
    assert fake_gh.calls[-1][2]["head"] == "jarvis/r3"


async def test_errors_name_the_problem(gh):
    with pytest.raises(GitHubError, match="404.*docs/github-app.md"):
        await gh.transfer("missing", "SS12dev")


def test_repo_names():
    owner = User(email="shivam@example.com", name="Shivam Singh")
    owner.roles = []
    member = User(email="priya@example.com", name="Priya Rao")
    member.roles = []
    from nexus_common.models import Role

    owner.roles = [Role(name=RoleName.owner)]
    member.roles = [Role(name=RoleName.member)]
    assert base_repo_name(owner, "2048") == "2048"
    assert base_repo_name(member, "2048") == "priya-2048"
    assert base_repo_name(owner, "Snake: The Game!") == "snake-the-game"
    nameless = User(email="sam.k@example.com", name=None)
    nameless.roles = [Role(name=RoleName.member)]
    assert base_repo_name(nameless, "Todo") == "sam-k-todo"


# --- A dev run's release -------------------------------------------------------------------


class FakeWorkbench:
    def __init__(self):
        self.calls: list[tuple] = []

    async def run(self, user, project, command, timeout_s=None):
        self.calls.append(("run", command))
        if command.startswith("git log"):
            return CommandResult(0, "abc1234full\x1fAdd the score\x1f2026-10-21T10:00:00+00:00\n",
                                 "", False, 3)  # fmt: skip
        return CommandResult(0, "abc1234\n", "", False, 3)

    async def read(self, user, project, path):
        return {"path": path, "size": 6, "encoding": "text", "content": "# Plan",
                "truncated": False}  # fmt: skip

    async def commit(self, user, project, message):
        return {"committed": True, "commit": "abc1234", "summary": "1 file changed"}

    async def identity(self, user, project, name, email):
        self.calls.append(("identity", name, email))
        return {"name": name, "email": email}

    async def push(self, user, project, url, branch, token):
        self.calls.append(("push", url, branch, token))
        return {"branch": branch, "commit": "abc1234full"}


def scripts_for(scripts: dict, approved=True, failed=0) -> None:
    def done(tool, **args):
        return {"tool": tool, "args": args}

    scripts["researcher"] = [done("submit_brief", stack="static", summary="A 2048 game.")]
    scripts["architect"] = [done("submit_plan", summary="Grid and score.")]
    scripts["tester"] = [done("submit_tests", passed=14, failed=failed, summary="Ran.")]
    scripts["reviewer"] = [
        done("submit_review", approved=approved, fixes="1. fix", summary="A 2048 game.")
    ] * 3


async def dev_run(sdeps, user, chat_id, project=None, name="2048"):
    spec = AgentSystemSpec.model_validate(
        {"name": name, "goal": "A 2048 game", "topology": "dev_project", "agents": []}
    )
    async with sdeps.sessionmaker() as db:
        system = AgentSystem(
            id=uuid.uuid4(), user_id=user.id, chat_id=chat_id, name=spec.name, goal=spec.goal,
            spec={}, topology=spec.topology, lifetime=SystemLifetime.ephemeral,
            status=SystemStatus.idle,
        )  # fmt: skip
        spec.project = project or str(system.id)
        system.spec = spec.model_dump()
        db.add(system)
        await db.flush()
        run = await new_run(
            db, sdeps.settings, sdeps.gateway, await db.merge(user), system, "Build 2048",
            chat_id=chat_id,
        )  # fmt: skip
        await db.commit()
        return spec.project, run.id


@pytest.fixture
def release_deps(sdeps, gh):
    sdeps.workbench = FakeWorkbench()
    sdeps.github = gh
    return sdeps


async def final_message(sdeps, chat_id) -> str:
    async with sdeps.sessionmaker() as db:
        rows = await db.scalars(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
        )
        return rows.all()[-1].content


async def test_release_pushes_and_merges_an_approved_green_run(
    release_deps, owner, scripts, fake_gh
):
    user, chat_id = owner
    scripts_for(scripts)
    project, run_id = await dev_run(release_deps, user, chat_id)
    await runner.run_system(release_deps, run_id)

    wb = release_deps.workbench
    # Commits are by the App's bot, set before any agent works.
    assert wb.calls[0] == (
        "identity", "jarvis-servhub[bot]", "4242+jarvis-servhub[bot]@users.noreply.github.com",
    )  # fmt: skip
    push = next(c for c in wb.calls if c[0] == "push")
    assert push[1] == f"https://github.com/{ORG}/2048.git" and push[2] == f"jarvis/{run_id}"
    # The workbench only ever gets a token limited to this repo.
    scoped = [c for c in fake_gh.calls if c[1].endswith("/access_tokens") and c[2]]
    assert scoped[-1][2]["repositories"] == ["2048"] and push[3] == f"ghs_{fake_gh.tokens}"
    assert fake_gh.refs[("2048", "main")] == "abc1234full"

    async with release_deps.sessionmaker() as db:
        p = await db.scalar(select(Project))
        run = await db.get(AgentRun, run_id)
    assert run.status == RunStatus.completed, run.error
    assert (p.workbench_id, p.name, p.stack) == (project, "2048", "static")
    assert (p.repo_owner, p.repo_name, p.merged) == (ORG, "2048", True)
    assert p.repo_url == f"https://github.com/{ORG}/2048"
    assert (p.last_commit, p.last_commit_message) == ("abc1234full", "Add the score")
    final = await final_message(release_deps, chat_id)
    assert f"- Repo: https://github.com/{ORG}/2048 (on main)" in final
    # The library keeps a link to the repo (the code isn't copied in), on the team's answer.
    async with release_deps.sessionmaker() as db:
        link = await db.scalar(select(Link))
        shown = await db.scalar(select(MessageLink).where(MessageLink.link_id == link.id))
        message = await db.get(Message, shown.message_id)
    assert (link.url, link.kind, link.title) == (p.repo_url, "github_repo", "2048")
    assert (link.project_id, link.run_id, link.source) == (p.id, run_id, "team")
    assert message.chat_id == chat_id
    # Read as the App (the repo is private); the fake has no README, so it's left out.
    assert "error" not in link.preview and "readme" not in link.preview


@pytest.mark.parametrize(
    "approved,failed,why",
    [(False, 0, "the reviewer hasn't approved it"), (True, 2, "not every test passed")],
)
async def test_release_leaves_main_alone_unless_approved_and_green(
    release_deps, owner, scripts, fake_gh, approved, failed, why
):
    user, chat_id = owner
    scripts_for(scripts, approved=approved, failed=failed)
    _, run_id = await dev_run(release_deps, user, chat_id)
    await runner.run_system(release_deps, run_id)

    assert ("2048", "main") not in fake_gh.refs
    async with release_deps.sessionmaker() as db:
        p = await db.scalar(select(Project))
    assert p.repo_name == "2048" and not p.merged and p.branch == f"jarvis/{run_id}"
    final = await final_message(release_deps, chat_id)
    assert f"(on branch jarvis/{run_id}, not merged: {why})" in final


async def test_follow_ups_reuse_the_repo_and_new_apps_get_a_free_name(
    release_deps, owner, scripts, fake_gh
):
    user, chat_id = owner
    scripts_for(scripts)
    project, run_id = await dev_run(release_deps, user, chat_id)
    await runner.run_system(release_deps, run_id)
    # A follow-up on the same project pushes to the same repo.
    scripts_for(scripts)
    _, again = await dev_run(release_deps, user, chat_id, project=project)
    await runner.run_system(release_deps, again)
    # A different app called 2048 gets 2048-2; so does one whose name is taken on GitHub.
    fake_gh.repos["snake"] = {"name": "snake"}
    for name in ("2048", "Snake"):
        scripts_for(scripts)
        _, other = await dev_run(release_deps, user, chat_id, name=name)
        await runner.run_system(release_deps, other)

    async with release_deps.sessionmaker() as db:
        rows = (await db.scalars(select(Project).order_by(Project.created_at))).all()
    assert [p.repo_name for p in rows] == ["2048", "2048-2", "snake-2"]
    creates = [c for c in fake_gh.calls if c[:2] == ("POST", f"/orgs/{ORG}/repos")]
    assert [c[2]["name"] for c in creates] == ["2048", "2048-2", "snake-2"]


async def test_a_github_failure_still_finishes_the_run(release_deps, owner, scripts, gh_settings):
    user, chat_id = owner
    scripts_for(scripts)

    def down(request):
        raise httpx.ConnectError("no route")

    release_deps.github = GitHubApp(
        gh_settings, httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    _, run_id = await dev_run(release_deps, user, chat_id)
    await runner.run_system(release_deps, run_id)

    async with release_deps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        p = await db.scalar(select(Project))
    assert run.status == RunStatus.completed
    # It is in the Library, without a repo.
    assert p.repo_url is None and p.last_commit == "abc1234full"
    assert "- Repo: not pushed: GitHub isn't reachable right now." in await final_message(
        release_deps, chat_id
    )


# --- Moving a repo to the user's own GitHub ------------------------------------------------


def test_transfer_is_owner_only_and_asks_first():
    info = BY_NAME["github_transfer"]
    assert info.risk == "outbound" and not info.jarvis and info.owner_only
    from nexus_common.models import Role

    owner, admin = User(email="o@x.com"), User(email="a@x.com")
    owner.roles, admin.roles = [Role(name=RoleName.owner)], [Role(name=RoleName.admin)]
    assert "github_transfer" in allowed_tools(owner, set())
    assert "github_transfer" not in allowed_tools(admin, set())
    assert "send_email" in allowed_tools(admin, set())


async def test_transfer_tool(release_deps, owner, fake_gh):
    user, chat_id = owner
    async with release_deps.sessionmaker() as db:
        db.add(
            Project(
                user_id=user.id, workbench_id="p1", name="2048", repo_owner=ORG,
                repo_name="2048", repo_url=f"https://github.com/{ORG}/2048",
            )
        )  # fmt: skip
        await db.commit()
    fake_gh.repos["2048"] = {"name": "2048"}

    async def emit(*a, **k):
        pass

    ctx = ToolContext(deps=release_deps, user_id=user.id, chat_id=chat_id, emit=emit)
    tool = {t.name: t for t in build_tools(ctx, ["github_transfer"])}["github_transfer"]
    assert "isn't a GitHub username" in await tool.ainvoke(
        {"project": "2048", "github_user": "-bad-"}
    )
    assert "No project called snake" in await tool.ainvoke(
        {"project": "snake", "github_user": "SS12dev"}
    )
    out = await tool.ainvoke({"project": "2048", "github_user": "SS12dev"})
    assert "GitHub emails SS12dev to accept it" in out
    assert fake_gh.calls[-1] == ("POST", f"/repos/{ORG}/2048/transfer", {"new_owner": "SS12dev"})
    async with release_deps.sessionmaker() as db:
        p = await db.scalar(select(Project))
    assert p.transferred_to == "SS12dev" and p.repo_url == "https://github.com/SS12dev/2048"
    assert "already moved" in await tool.ainvoke({"project": "2048", "github_user": "SS12dev"})


async def test_projects_api_lists_only_your_own(release_deps, owner, sessionmaker):
    from nexus_api.routes.projects import list_projects

    user, _ = owner
    async with sessionmaker() as db:
        db.add(Project(user_id=user.id, workbench_id="p1", name="2048"))
        other = User(email="other@example.com", status=UserStatus.active)
        db.add(other)
        await db.flush()
        db.add(Project(user_id=other.id, workbench_id="p2", name="Theirs"))
        await db.commit()
        mine = await list_projects(user, db)
    assert [p.name for p in mine] == ["2048"]
