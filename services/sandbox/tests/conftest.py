import base64
from pathlib import Path

import httpx
import pytest

from jarvis_sandbox.app import app

LIBRARY = Path(__file__).resolve().parents[3] / "library"


@pytest.fixture
async def sandbox():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sandbox", timeout=120
    ) as client:
        yield client


def _b64(data: bytes | str) -> str:
    return base64.b64encode(data.encode() if isinstance(data, str) else data).decode()


def _skill_files(*names: str) -> dict[str, str]:
    """The files of core skills as the orchestrator sends them: skills/core/<name>/..."""
    out = {}
    for name in names:
        root = LIBRARY / "core" / name
        for p in root.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                out[f"skills/core/{name}/{p.relative_to(root).as_posix()}"] = _b64(p.read_bytes())
    return out


async def _run(client, code: str, files: dict | None = None, language: str = "python", **kw):
    resp = await client.post(
        "/run", json={"language": language, "code": code, "files": files or {}, **kw}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# Tests import nothing from here (pytest's importlib mode); the helpers come as fixtures.
@pytest.fixture
def b64():
    return _b64


@pytest.fixture
def skill_files():
    return _skill_files


@pytest.fixture
def run(sandbox):
    async def call(code: str, files: dict | None = None, language: str = "python", **kw):
        return await _run(sandbox, code, files, language, **kw)

    return call
