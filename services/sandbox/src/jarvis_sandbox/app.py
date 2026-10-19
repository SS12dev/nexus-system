"""The sandbox: runs one piece of agent code per request in its own work folder.

Isolation comes in two layers. Compose puts this container on an internal network with no
route out, a read-only root filesystem, no Linux capabilities and memory, CPU and process
caps. Inside, every job runs as a child process in a fresh folder with CPU-time, memory,
file-size and open-file limits, a clean environment and a wall-clock timeout that kills its
whole process group.

A job's folder holds what the caller sent: in/ (input files), skills/<scope>/<name>/ (skill
scripts) and the code itself. Whatever the code writes to out/ comes back in the response.

    POST /run {"language": "python"|"bash", "code": "...", "files": {"in/a.pdf": "<base64>"},
               "timeout_s": 60}
"""

import asyncio
import base64
import os
import resource
import shutil
import signal
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

TOKEN = os.environ.get("SANDBOX_TOKEN", "")
WORK_ROOT = Path(os.environ.get("SANDBOX_WORK_DIR", tempfile.gettempdir()))
MAX_TIMEOUT_S = int(os.environ.get("SANDBOX_MAX_TIMEOUT_S", "300"))
MEMORY_MB = int(os.environ.get("SANDBOX_MEMORY_MB", "1536"))
MAX_FILE_MB = int(os.environ.get("SANDBOX_MAX_FILE_MB", "50"))
CONCURRENCY = int(os.environ.get("SANDBOX_CONCURRENCY", "4"))
MAX_OUTPUT_CHARS = 20_000
MAX_OUT_FILES = 20

app = FastAPI(title="Jarvis sandbox", version="0.1.0")
WORK_ROOT.mkdir(parents=True, exist_ok=True)
slots = asyncio.Semaphore(CONCURRENCY)


class RunIn(BaseModel):
    language: Literal["python", "bash"] = "python"
    code: str = Field(min_length=1, max_length=200_000)
    # Relative path -> base64 content.
    files: dict[str, str] = Field(default_factory=dict)
    timeout_s: int = Field(default=60, ge=1)


class RunOut(BaseModel):
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    # out/ files: relative path -> base64 content.
    files: dict[str, str]
    skipped_files: list[str] = []


def safe_path(root: Path, rel: str) -> Path:
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Bad file path: {rel}")
    return root.joinpath(*p.parts)


def _limits(cpu_s: int) -> None:
    """Runs in the child before exec."""
    os.setsid()
    mem = MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    size = MAX_FILE_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _clip(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_OUTPUT_CHARS:
        half = MAX_OUTPUT_CHARS // 2
        text = f"{text[:half]}\n… [{len(text) - MAX_OUTPUT_CHARS} characters cut] …\n{text[-half:]}"
    return text


async def execute(body: RunIn) -> RunOut:
    timeout = min(body.timeout_s, MAX_TIMEOUT_S)
    job = Path(tempfile.mkdtemp(prefix="job-", dir=WORK_ROOT))
    try:
        for rel, b64 in body.files.items():
            target = safe_path(job, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(b64))
        (job / "in").mkdir(exist_ok=True)
        (job / "out").mkdir(exist_ok=True)
        script = job / ("main.py" if body.language == "python" else "main.sh")
        script.write_text(body.code, "utf-8")
        argv = ["python", "-I", str(script)] if body.language == "python" else ["bash", str(script)]
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(job),
            "LANG": "C.UTF-8",
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(job / ".mpl"),
            "XDG_CACHE_HOME": str(job / ".cache"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=job,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=lambda: _limits(timeout),
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = await proc.communicate()
        duration = int((time.monotonic() - started) * 1000)

        files: dict[str, str] = {}
        skipped: list[str] = []
        limit = MAX_FILE_MB * 1024 * 1024
        for p in sorted((job / "out").rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(job).as_posix()
            if len(files) >= MAX_OUT_FILES or p.stat().st_size > limit:
                skipped.append(rel)
                continue
            files[rel] = base64.b64encode(p.read_bytes()).decode()
        return RunOut(
            exit_code=None if timed_out else proc.returncode,
            stdout=_clip(out),
            stderr=_clip(err) + (f"\nTimed out after {timeout}s." if timed_out else ""),
            timed_out=timed_out,
            duration_ms=duration,
            files=files,
            skipped_files=skipped,
        )
    finally:
        shutil.rmtree(job, ignore_errors=True)


@app.post("/run", response_model=RunOut)
async def run(body: RunIn, authorization: str = Header(default="")):
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad sandbox token")
    async with slots:
        return await execute(body)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
