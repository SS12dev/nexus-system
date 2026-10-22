"""The workbench: a persistent project folder per dev-team project, where agents read, write
and edit files, run commands (npm, uv, git, headless Chromium) and commit to git.

Unlike the sandbox, nothing here is thrown away after a call: each project is a git repository
at <root>/<user_id>/<project_id> on a Docker volume, kept until it is deleted. Isolation comes
in layers. Compose puts this container on an internal network whose only way out is the
egress-proxy allowlist (npm, PyPI, GitHub), with a read-only root, no Linux capabilities and
memory, CPU and process caps. Inside, every command runs in its own process group with a clean
environment, CPU-time, file-size and core limits and a wall-clock timeout that kills the whole
group, and (where the kernel has Landlock) it can only see its own project folder.

    PUT    /projects/{user}/{project}                 create (idempotent)
    GET    /projects/{user}                           list
    DELETE /projects/{user}/{project}                 delete
    GET    /projects/{user}/{project}/tree?path=&depth=
    GET    /projects/{user}/{project}/file?path=
    PUT    /projects/{user}/{project}/file            {"path", "content", "encoding"}
    POST   /projects/{user}/{project}/edit            {"path", "old", "new", "replace_all"}
    DELETE /projects/{user}/{project}/file?path=
    POST   /projects/{user}/{project}/run             {"command", "timeout_s"}
    GET    /projects/{user}/{project}/git/status
    GET    /projects/{user}/{project}/git/diff?ref=
    POST   /projects/{user}/{project}/git/commit      {"message"}
    PUT    /projects/{user}/{project}/git/identity    {"name", "email"}
    POST   /projects/{user}/{project}/git/push        {"url", "branch", "token"}
"""

import asyncio
import base64
import ctypes
import logging
import os
import re
import resource
import shutil
import signal
import stat
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from nexus_workbench import landlock

log = logging.getLogger("nexus_workbench")

TOKEN = os.environ.get("WORKBENCH_TOKEN", "")
ROOT = Path(os.environ.get("WORKBENCH_ROOT", "/workspaces"))
MAX_TIMEOUT_S = int(os.environ.get("WORKBENCH_MAX_TIMEOUT_S", "900"))
# Per-process cap on private memory (RLIMIT_DATA, which Node's and Chromium's large address
# space reservations don't count against); the container's own limit caps the total. 0 is off.
MEMORY_MB = int(os.environ.get("WORKBENCH_MEMORY_MB", "3072"))
MAX_FILE_MB = int(os.environ.get("WORKBENCH_MAX_FILE_MB", "200"))
CONCURRENCY = int(os.environ.get("WORKBENCH_CONCURRENCY", "4"))
GIT_NAME = os.environ.get("WORKBENCH_GIT_NAME", "Nexus")
GIT_EMAIL = os.environ.get("WORKBENCH_GIT_EMAIL", "nexus@localhost")
MAX_OUTPUT_CHARS = 20_000
MAX_READ_BYTES = 2_000_000
MAX_TREE_ENTRIES = 2_000
GIT_TIMEOUT_S = 60

# Left out of a tree: git's own folder and the commands' home (caches).
HIDDEN = {".git", ".home"}
# Shown in a tree but never opened: generated and often huge.
SKIP_DIRS = {"node_modules", ".venv", "__pycache__", ".pytest_cache", "dist"}
# Folders a command may read but not change, besides its own project and /dev. Its temporary
# files go in the project's .home/tmp, not the shared /tmp.
SYSTEM_DIRS = ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt", "/proc", "/sys"]
# Settings passed through to commands; everything else (the token above all) stays out.
PASS_ENV = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "PLAYWRIGHT_BROWSERS_PATH",
    "PLAYWRIGHT_VERSION",
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
)
GITIGNORE = (
    "node_modules/\n.venv/\n.home/\n__pycache__/\n.pytest_cache/\ndist/\n"
    "test-results/\nplaywright-report/\n.env\n"
)
ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
REF = re.compile(r"^[A-Za-z0-9._/~^@{}-]{1,100}$")
# Where a project may be pushed: a GitHub repository over HTTPS (through the egress proxy).
PUSH_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\.git$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
PUSH_TIMEOUT_S = 180

app = FastAPI(title="Nexus workbench", version="0.1.0")
slots = asyncio.Semaphore(CONCURRENCY)
# One git operation per project at a time, so two agents committing don't fight over the index.
git_locks: defaultdict[Path, asyncio.Lock] = defaultdict(asyncio.Lock)
LANDLOCK = landlock.abi_version() if sys.platform == "linux" else 0
if sys.platform == "linux":
    # Hide this process's environment (the token) from commands running as the same user: a
    # non-dumpable process's /proc/<pid>/environ belongs to root. Commands start dumpable again.
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE


def check_token(authorization: str = Header(default="")) -> None:
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad workbench token")


# Everything but /healthz needs the token.
api = APIRouter(dependencies=[Depends(check_token)])
P = "/projects/{user}/{project}"


def bad(detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, detail)


def project_dir(user: str, project: str, must_exist: bool = True) -> Path:
    if not ID.match(user) or not ID.match(project):
        raise bad("Project and user ids are lowercase letters, digits, - and _.")
    root = ROOT / user / project
    if must_exist and not root.is_dir():
        raise bad("No such project.", status.HTTP_404_NOT_FOUND)
    return root


def safe_path(root: Path, rel: str, allow_root: bool = False) -> Path:
    """A path inside the project, refusing absolute paths, "..", git's own folder and
    symlinks that lead outside it."""
    p = PurePosixPath(rel.replace("\\", "/").strip())
    if p.is_absolute() or ".." in p.parts:
        raise bad(f"Bad file path: {rel}")
    if not p.parts or p.parts == (".",):
        if allow_root:
            return root
        raise bad("A file path is needed.")
    if p.parts[0] == ".git":
        raise bad("The .git folder is off limits; use the git endpoints.")
    target = root.joinpath(*p.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise bad(f"{rel} leads outside the project.")
    return target


def _limits(cpu_s: int, rw: list[str]) -> None:
    """Runs in the child before exec."""
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
    if MEMORY_MB:
        mem = MEMORY_MB * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_DATA, (mem, mem))
        except (ValueError, OSError):
            pass  # macOS; the container's memory limit still applies
    size = MAX_FILE_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # If memory runs out, the kernel kills a command before the service.
    try:
        Path("/proc/self/oom_score_adj").write_text("1000")
    except OSError:
        pass
    if LANDLOCK:
        landlock.restrict(rw, SYSTEM_DIRS + [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")])


class _Tail:
    """Keeps the start and the end of a stream, so a long build log still shows its errors."""

    def __init__(self, keep: int = MAX_OUTPUT_CHARS * 2):
        self.head = bytearray()
        self.tail = bytearray()
        self.keep = keep
        self.dropped = 0

    def add(self, chunk: bytes) -> None:
        room = self.keep - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        self.tail += chunk
        if len(self.tail) > self.keep:
            self.dropped += len(self.tail) - self.keep
            del self.tail[: len(self.tail) - self.keep]

    def text(self) -> str:
        data = bytes(self.head) + (b"\n... cut ...\n" if self.dropped else b"") + bytes(self.tail)
        text = data.decode("utf-8", errors="replace")
        if len(text) > MAX_OUTPUT_CHARS:
            half = MAX_OUTPUT_CHARS // 2
            cut = len(text) - MAX_OUTPUT_CHARS
            text = f"{text[:half]}\n… [{cut} characters cut] …\n{text[-half:]}"
        return text


async def _pump(stream: asyncio.StreamReader, sink: _Tail) -> None:
    while chunk := await stream.read(65536):
        sink.add(chunk)


def command_env(root: Path) -> dict[str, str]:
    home = root / ".home"
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    # The default commit identity. A project's own git config (set through git/identity, e.g.
    # the GitHub App's bot) wins over it, which environment variables would not allow.
    gitconfig = home / ".gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text(f"[user]\n\tname = {GIT_NAME}\n\temail = {GIT_EMAIL}\n")
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "CI": "1",
        "TMPDIR": str(home / "tmp"),
        "PYTHONUNBUFFERED": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_LINK_MODE": "copy",
        "npm_config_update_notifier": "false",
        "GIT_TERMINAL_PROMPT": "0",
    }
    env.update({k: os.environ[k] for k in PASS_ENV if k in os.environ})
    return env


async def execute(
    root: Path, argv: list[str], limit_s: int, extra_env: dict[str, str] | None = None
) -> dict:
    """Run argv in the project folder. When the command ends (or times out) anything it left
    running in its process group is killed too, so nothing outlives the call."""
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=root,
        env={**command_env(root), **(extra_env or {})},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=lambda: _limits(limit_s, [str(root), "/dev"]),
    )
    out, err = _Tail(), _Tail()
    pumps = asyncio.gather(_pump(proc.stdout, out), _pump(proc.stderr, err))
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), limit_s)
    except TimeoutError:
        timed_out = True
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await proc.wait()
    try:
        # Grandchildren that left the group may still hold the pipes; don't wait on them.
        await asyncio.wait_for(pumps, 5)
    except TimeoutError:
        pumps.cancel()
    return {
        "exit_code": None if timed_out else proc.returncode,
        "stdout": out.text(),
        "stderr": err.text() + (f"\nTimed out after {limit_s}s." if timed_out else ""),
        "timed_out": timed_out,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


async def git(
    root: Path, *args: str, env: dict[str, str] | None = None, limit_s: int = GIT_TIMEOUT_S
) -> dict:
    # Hooks and fsmonitor would run commands the project chose, outside a command's limits.
    argv = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args]
    return await execute(root, argv, limit_s, env)


async def git_ok(root: Path, *args: str) -> str:
    result = await git(root, *args)
    if result["exit_code"] != 0:
        raise bad(f"git {args[0]} failed: {(result['stderr'] or result['stdout']).strip()[:500]}")
    return result["stdout"]


def fs_error(path: str, exc: OSError) -> HTTPException:
    """A filesystem refusal (often from something a command did) the agent can act on."""
    return bad(f"Couldn't use {path}: {exc.strerror or exc}.")


def _rmtree(path: Path) -> None:
    def fix(func, p, _exc):
        # A command may have made files or folders read-only; make them writable and retry.
        os.chmod(os.path.dirname(p), stat.S_IRWXU)
        if os.path.isdir(p) and not os.path.islink(p):
            os.chmod(p, stat.S_IRWXU)
        func(p)

    shutil.rmtree(path, onexc=fix)


# --- Projects ----------------------------------------------------------------------------


class ProjectOut(BaseModel):
    id: str
    created: bool = False
    created_at: datetime | None = None


@api.put(P, response_model=ProjectOut)
async def create_project(user: str, project: str):
    root = project_dir(user, project, must_exist=False)
    async with git_locks[root]:
        if (root / ".git").is_dir():
            return ProjectOut(id=project)
        root.mkdir(parents=True, exist_ok=True)
        await git_ok(root, "init", "-q", "-b", "main")
        if not (root / ".gitignore").exists():
            (root / ".gitignore").write_text(GITIGNORE)
        await git_ok(root, "add", "-A")
        await git_ok(root, "commit", "-q", "--allow-empty", "-m", "Start project")
    return ProjectOut(id=project, created=True)


@api.get("/projects/{user}", response_model=list[ProjectOut])
async def list_projects(user: str):
    if not ID.match(user):
        raise bad("Bad user id.")
    base = ROOT / user
    if not base.is_dir():
        return []
    out = [
        ProjectOut(id=p.name, created_at=datetime.fromtimestamp(p.stat().st_mtime, UTC))
        for p in base.iterdir()
        if p.is_dir() and (p / ".git").is_dir()
    ]
    return sorted(out, key=lambda p: p.created_at, reverse=True)


@api.delete(P)
async def delete_project(user: str, project: str):
    root = project_dir(user, project)
    async with git_locks[root]:
        await asyncio.to_thread(_rmtree, root)
    git_locks.pop(root, None)
    return {"deleted": project}


# --- Files -------------------------------------------------------------------------------


class Entry(BaseModel):
    path: str
    type: Literal["file", "dir", "link"]
    size: int | None = None


class TreeOut(BaseModel):
    entries: list[Entry]
    truncated: bool


@api.get(P + "/tree", response_model=TreeOut)
async def tree(user: str, project: str, path: str = "", depth: int = Query(3, ge=1, le=10)):
    root = project_dir(user, project)
    start = safe_path(root, path, allow_root=True)
    if not start.is_dir():
        raise bad(f"{path} isn't a folder.", status.HTTP_404_NOT_FOUND)
    entries: list[Entry] = []

    def walk(folder: Path, level: int) -> bool:
        for p in sorted(folder.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
            if len(entries) >= MAX_TREE_ENTRIES:
                return False
            rel = p.relative_to(root).as_posix()
            if p.name in HIDDEN and p.parent == root:
                continue
            if p.is_symlink():
                entries.append(Entry(path=rel, type="link"))
            elif p.is_dir():
                entries.append(Entry(path=rel + "/", type="dir"))
                if level < depth and p.name not in SKIP_DIRS and not walk(p, level + 1):
                    return False
            else:
                entries.append(Entry(path=rel, type="file", size=p.stat().st_size))
        return True

    complete = await asyncio.to_thread(walk, start, 1)
    return TreeOut(entries=entries, truncated=not complete)


class FileOut(BaseModel):
    path: str
    size: int
    # Text files come back as text; anything that isn't UTF-8 as base64.
    encoding: Literal["text", "base64"]
    content: str
    truncated: bool


@api.get(P + "/file", response_model=FileOut)
async def read_file(user: str, project: str, path: str):
    target = safe_path(project_dir(user, project), path)
    if not target.is_file():
        raise bad(f"No file {path}.", status.HTTP_404_NOT_FOUND)
    try:
        size = target.stat().st_size
        with target.open("rb") as f:
            data = f.read(MAX_READ_BYTES)
    except OSError as exc:
        raise fs_error(path, exc) from exc
    content, encoding = base64.b64encode(data).decode(), "base64"
    # A cut at MAX_READ_BYTES may split a character, so allow up to three bytes short.
    for short in range(4 if size > len(data) else 1):
        try:
            content, encoding = data[: len(data) - short].decode("utf-8"), "text"
            break
        except UnicodeDecodeError:
            continue
    return FileOut(
        path=path, size=size, encoding=encoding, content=content, truncated=size > len(data)
    )


class WriteIn(BaseModel):
    path: str
    content: str = Field(max_length=10_000_000)
    encoding: Literal["text", "base64"] = "text"


@api.put(P + "/file")
async def write_file(user: str, project: str, body: WriteIn):
    target = safe_path(project_dir(user, project), body.path)
    if target.is_dir():
        raise bad(f"{body.path} is a folder.")
    data = base64.b64decode(body.content) if body.encoding == "base64" else body.content.encode()
    existed = target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        raise fs_error(body.path, exc) from exc
    return {"path": body.path, "size": len(data), "created": not existed}


class EditIn(BaseModel):
    path: str
    old: str = Field(min_length=1)
    new: str
    replace_all: bool = False


@api.post(P + "/edit")
async def edit_file(user: str, project: str, body: EditIn):
    target = safe_path(project_dir(user, project), body.path)
    if not target.is_file():
        raise bad(f"No file {body.path}.", status.HTTP_404_NOT_FOUND)
    try:
        text = target.read_text("utf-8")
    except UnicodeDecodeError as exc:
        raise bad(f"{body.path} isn't a text file.") from exc
    except OSError as exc:
        raise fs_error(body.path, exc) from exc
    count = text.count(body.old)
    if count == 0:
        raise bad(f"The text to replace isn't in {body.path}; read the file and copy it exactly.")
    if count > 1 and not body.replace_all:
        raise bad(
            f"The text to replace is in {body.path} {count} times; include more of the "
            "surrounding lines so it matches once, or set replace_all."
        )
    try:
        target.write_text(text.replace(body.old, body.new), "utf-8")
    except OSError as exc:
        raise fs_error(body.path, exc) from exc
    return {"path": body.path, "replaced": count if body.replace_all else 1}


@api.delete(P + "/file")
async def delete_file(user: str, project: str, path: str):
    target = safe_path(project_dir(user, project), path)
    if target.is_dir() and not target.is_symlink():
        raise bad(f"{path} is a folder; remove folders with a command.")
    if not target.exists() and not target.is_symlink():
        raise bad(f"No file {path}.", status.HTTP_404_NOT_FOUND)
    target.unlink()
    return {"deleted": path}


# --- Commands ----------------------------------------------------------------------------


class RunIn(BaseModel):
    command: str = Field(min_length=1, max_length=100_000)
    timeout_s: int = Field(default=300, ge=1)


class RunOut(BaseModel):
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


@api.post(P + "/run", response_model=RunOut)
async def run(user: str, project: str, body: RunIn):
    root = project_dir(user, project)
    async with slots:
        return await execute(root, ["bash", "-c", body.command], min(body.timeout_s, MAX_TIMEOUT_S))


# --- Git ---------------------------------------------------------------------------------


@api.get(P + "/git/status")
async def git_status(user: str, project: str):
    root = project_dir(user, project)
    async with git_locks[root]:
        out = await git_ok(root, "status", "--short", "--branch")
    branch, _, changes = out.partition("\n")
    return {"branch": branch.removeprefix("## "), "changes": changes.rstrip()}


@api.get(P + "/git/diff")
async def git_diff(user: str, project: str, ref: str = "HEAD"):
    root = project_dir(user, project)
    if not REF.match(ref) or ref.startswith("-"):
        raise bad(f"Bad git ref: {ref}")
    async with git_locks[root]:
        # Intent-to-add makes new files show up in the diff without staging their content.
        await git_ok(root, "add", "-A", "--intent-to-add")
        stat_ = await git_ok(root, "diff", "--stat", ref, "--")
        diff = await git_ok(root, "diff", ref, "--")
    clipped = _Tail()
    clipped.add(diff.encode())
    return {"ref": ref, "stat": stat_.rstrip(), "diff": clipped.text()}


class CommitIn(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


@api.post(P + "/git/commit")
async def git_commit(user: str, project: str, body: CommitIn):
    root = project_dir(user, project)
    async with git_locks[root]:
        await git_ok(root, "add", "-A")
        if not (await git_ok(root, "status", "--porcelain")).strip():
            return {"committed": False, "commit": None, "summary": "Nothing to commit."}
        await git_ok(root, "commit", "-q", "-m", body.message)
        sha = (await git_ok(root, "rev-parse", "--short", "HEAD")).strip()
        summary = (await git_ok(root, "show", "--stat", "--format=", "HEAD")).strip()
    return {"committed": True, "commit": sha, "summary": summary.splitlines()[-1]}


class IdentityIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[^\n<>]+$")
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^\s<>]+@[^\s<>]+$")


@api.put(P + "/git/identity")
async def git_identity(user: str, project: str, body: IdentityIn):
    """Who this project's commits are by from now on, such as the GitHub App's bot."""
    root = project_dir(user, project)
    async with git_locks[root]:
        await git_ok(root, "config", "user.name", body.name)
        await git_ok(root, "config", "user.email", body.email)
    return {"name": body.name, "email": body.email}


class PushIn(BaseModel):
    url: str = Field(max_length=300)
    branch: str
    # A short-lived token for this one repository; used for this push only, never stored.
    token: str = Field(min_length=1, max_length=500)


@api.post(P + "/git/push")
async def git_push(user: str, project: str, body: PushIn):
    """Push the current commit to a branch of a GitHub repository."""
    root = project_dir(user, project)
    if not PUSH_URL.match(body.url):
        raise bad("Projects can only be pushed to a GitHub repository over HTTPS.")
    if not BRANCH.match(body.branch) or ".." in body.branch:
        raise bad(f"Bad branch name: {body.branch}")
    basic = base64.b64encode(f"x-access-token:{body.token}".encode()).decode()
    # In git's environment, not its arguments or the remote URL, so it never shows in a process
    # list, git's output or .git/config; Landlock keeps other commands out of /proc/<pid>/environ.
    env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
    }
    async with git_locks[root]:
        sha = (await git_ok(root, "rev-parse", "HEAD")).strip()
        result = await git(
            root,
            "push",
            "--force",
            "--porcelain",
            body.url,
            f"HEAD:refs/heads/{body.branch}",
            env=env,
            limit_s=PUSH_TIMEOUT_S,
        )
    if result["exit_code"] != 0:
        detail = (result["stderr"] or result["stdout"]).replace(body.token, "***").strip()
        raise bad(f"git push failed: {detail[:500]}", status.HTTP_502_BAD_GATEWAY)
    return {"branch": body.branch, "commit": sha}


@app.get("/healthz")
async def healthz():
    # isolation says whether commands are locked to their project folder (Landlock) or rely
    # on the container alone.
    return {"status": "ok", "isolation": f"landlock v{LANDLOCK}" if LANDLOCK else "container"}


app.include_router(api)
