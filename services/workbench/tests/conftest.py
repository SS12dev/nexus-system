import httpx
import pytest

import nexus_workbench.app as module
from nexus_workbench.app import app

USER = "u1"


@pytest.fixture
async def workbench(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ROOT", tmp_path / "workspaces")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://workbench", timeout=120
    ) as client:
        yield client


@pytest.fixture
async def project(workbench):
    """A fresh project; returns its URL prefix."""
    resp = await workbench.put(f"/projects/{USER}/p1")
    assert resp.status_code == 200, resp.text
    return f"/projects/{USER}/p1"


@pytest.fixture
def run(workbench, project):
    async def call(command: str, **kw) -> dict:
        resp = await workbench.post(f"{project}/run", json={"command": command, **kw})
        assert resp.status_code == 200, resp.text
        return resp.json()

    return call
