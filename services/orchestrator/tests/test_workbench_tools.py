import uuid

import httpx
import pytest

import jarvis_workbench.app as workbench_app
from jarvis_common.models import Capability
from jarvis_common.toolinfo import BY_NAME, RISK_CAPABILITY
from jarvis_orchestrator.tools import ToolContext, build_tools
from jarvis_orchestrator.workbench import CommandResult, WorkbenchClient, WorkbenchError

WS_TOOLS = ["ws_tree", "ws_read", "ws_write", "ws_edit", "ws_run", "ws_diff", "ws_commit"]


class FakeWorkbench:
    """Holds one folder per (user, project) in memory and records who asked for what."""

    def __init__(self):
        self.files: dict[tuple[str, str], dict[str, str]] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.fail: str | None = None

    def _project(self, op: str, user: str, project: str) -> dict[str, str]:
        self.calls.append((op, user, project))
        if self.fail:
            raise WorkbenchError(self.fail)
        return self.files.setdefault((user, project), {})

    async def tree(self, user, project, path="", depth=3):
        files = self._project("tree", user, project)
        entries = [{"path": p, "type": "file", "size": len(c)} for p, c in sorted(files.items())]
        return {"entries": entries, "truncated": False}

    async def read(self, user, project, path):
        files = self._project("read", user, project)
        if path not in files:
            raise WorkbenchError(f"No file {path}.")
        content = files[path]
        return {
            "path": path,
            "size": len(content),
            "encoding": "text",
            "content": content,
            "truncated": False,
        }

    async def write(self, user, project, path, content):
        files = self._project("write", user, project)
        created = path not in files
        files[path] = content
        return {"path": path, "size": len(content.encode()), "created": created}

    async def edit(self, user, project, path, old, new, replace_all=False):
        files = self._project("edit", user, project)
        files[path] = files[path].replace(old, new)
        return {"path": path, "replaced": 1}

    async def run(self, user, project, command, timeout_s=None):
        self._project("run", user, project)
        if command == "slow":
            return CommandResult(None, "", "Timed out after 1s.", True, 1000)
        return CommandResult(0, f"ran {command}\n", "", False, 5)

    async def diff(self, user, project, ref="HEAD"):
        files = self._project("diff", user, project)
        diff = "".join(f"+++ b/{p}\n+{c}\n" for p, c in files.items())
        return {"ref": ref, "stat": f" {len(files)} files changed", "diff": diff}

    async def commit(self, user, project, message):
        self._project("commit", user, project)
        return {"committed": True, "commit": "abc1234", "summary": "1 file changed"}


@pytest.fixture
def fake() -> FakeWorkbench:
    return FakeWorkbench()


def ws_tools(deps, *, run_id=None, user_id=None, events=None, project=None) -> dict:
    async def emit(type_, **data):
        if events is not None:
            events.append((type_, data))

    ctx = ToolContext(
        deps=deps,
        user_id=user_id or uuid.uuid4(),
        chat_id=None,
        emit=emit,
        run_id=run_id,
        agent="builder",
        project=project,
    )
    return {t.name: t for t in build_tools(ctx, WS_TOOLS)}


async def test_tools_act_on_the_runs_project_for_its_user(deps, fake):
    deps.workbench = fake
    run_id, user_id = uuid.uuid4(), uuid.uuid4()
    tools = ws_tools(deps, run_id=run_id, user_id=user_id)
    out = await tools["ws_write"].ainvoke({"path": "src/main.js", "content": "let a = 1;\n"})
    assert out == "Created src/main.js (11 bytes)."
    await tools["ws_edit"].ainvoke({"path": "src/main.js", "old": "1", "new": "2"})
    assert "src/main.js (11 bytes)" in await tools["ws_tree"].ainvoke({})
    assert "ran npm test" in await tools["ws_run"].ainvoke({"command": "npm test"})
    assert "+let a = 2;" in await tools["ws_diff"].ainvoke({})
    assert "abc1234" in await tools["ws_commit"].ainvoke({"message": "Add main"})
    assert {(u, p) for _, u, p in fake.calls} == {(str(user_id), str(run_id))}
    # Another run gets its own folder.
    other = ws_tools(deps, run_id=uuid.uuid4(), user_id=user_id)
    assert "empty" in await other["ws_tree"].ainvoke({})


async def test_a_named_project_wins_over_the_run(deps, fake):
    deps.workbench = fake
    tools = ws_tools(deps, run_id=uuid.uuid4(), project="2048")
    await tools["ws_tree"].ainvoke({})
    assert fake.calls[-1][2] == "2048"


async def test_tool_events_reach_the_chat(deps, fake):
    deps.workbench = fake
    events: list = []
    tools = ws_tools(deps, run_id=uuid.uuid4(), events=events)
    await tools["ws_run"].ainvoke({"command": "npm install"})
    (call_type, call), (done_type, done) = events
    assert call_type == "tool.call" and call["name"] == "ws_run"
    assert call["command"] == "npm install" and call["call_id"]
    assert done_type == "tool.done" and done["call_id"] == call["call_id"]
    assert done["ok"] and done["summary"] == "exit code: 0"


async def test_read_pages_with_line_numbers(deps, fake):
    deps.workbench = fake
    tools = ws_tools(deps, run_id=uuid.uuid4())
    body = "".join(f"line {i}\n" for i in range(1, 11))
    await tools["ws_write"].ainvoke({"path": "a.txt", "content": body})
    out = await tools["ws_read"].ainvoke({"path": "a.txt", "offset": 3, "limit": 2})
    assert out.splitlines()[0] == "    3  line 3" and "    4  line 4" in out
    assert "offset=5" in out
    assert "offset=" not in await tools["ws_read"].ainvoke({"path": "a.txt"})
    assert "nothing at line 50" in await tools["ws_read"].ainvoke({"path": "a.txt", "offset": 50})


async def test_timeouts_and_errors_go_back_to_the_agent(deps, fake):
    deps.workbench = fake
    tools = ws_tools(deps, run_id=uuid.uuid4())
    out = await tools["ws_run"].ainvoke({"command": "slow"})
    assert out.startswith("exit code: timeout") and "Timed out" in out
    assert await tools["ws_read"].ainvoke({"path": "nope.txt"}) == "No file nope.txt."
    fake.fail = "The workbench isn't reachable right now."
    assert await tools["ws_tree"].ainvoke({}) == fake.fail


async def test_outside_a_run_or_without_a_workbench(deps, fake):
    deps.workbench = fake
    tools = ws_tools(deps)
    assert "only for agent teams" in await tools["ws_tree"].ainvoke({})
    deps.workbench = None
    tools = ws_tools(deps, run_id=uuid.uuid4())
    assert await tools["ws_run"].ainvoke({"command": "ls"}) == "The workbench isn't available."
    assert fake.calls == []


def test_catalogue():
    for name in WS_TOOLS:
        assert not BY_NAME[name].jarvis, name
    assert BY_NAME["ws_run"].risk == "exec"
    assert RISK_CAPABILITY[BY_NAME["ws_run"].risk] == Capability.exec
    assert {BY_NAME[n].risk for n in ("ws_tree", "ws_read", "ws_diff")} == {"read"}
    assert {BY_NAME[n].risk for n in ("ws_write", "ws_edit", "ws_commit")} == {"write"}


async def test_client_against_the_real_workbench(deps, settings, tmp_path, monkeypatch):
    """The real client and service end to end: the project is made on first use, files and
    commits persist between calls, and a deleted project comes back empty."""
    monkeypatch.setattr(workbench_app, "ROOT", tmp_path)
    transport = httpx.ASGITransport(app=workbench_app.app)
    http = httpx.AsyncClient(transport=transport, base_url="http://workbench", timeout=60)
    deps.workbench = WorkbenchClient(settings, http)
    run_id, user_id = uuid.uuid4(), uuid.uuid4()
    tools = ws_tools(deps, run_id=run_id, user_id=user_id)
    try:
        await tools["ws_write"].ainvoke({"path": "app.py", "content": "print('hi')\n"})
        out = await tools["ws_run"].ainvoke({"command": "python3 app.py && git status --short"})
        assert "exit code: 0" in out and "hi" in out and "?? app.py" in out
        assert "+print('hi')" in await tools["ws_diff"].ainvoke({})
        assert "Committed" in await tools["ws_commit"].ainvoke({"message": "Say hi"})
        assert "No changes since HEAD" in await tools["ws_diff"].ainvoke({})
        assert (tmp_path / str(user_id) / str(run_id) / "app.py").exists()
        bad = await tools["ws_read"].ainvoke({"path": "../../etc/passwd"})
        assert bad.startswith("Bad file path")
        await http.delete(f"/projects/{user_id}/{run_id}")
        assert "app.py" not in await tools["ws_tree"].ainvoke({})
    finally:
        await http.aclose()
