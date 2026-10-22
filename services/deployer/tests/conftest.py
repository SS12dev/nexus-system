import json
import struct
from collections import defaultdict

import httpx
import pytest

import nexus_deployer.app as module
from nexus_deployer.docker import Docker


def frames(text: str) -> bytes:
    data = text.encode()
    return struct.pack(">BxxxI", 1, len(data)) + data


class FakeDocker:
    """The Docker API as the gate passes it on: builds, containers, images, networks."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.builds: list[dict] = []
        self.containers: dict[str, dict] = {}
        self.created: dict[str, dict] = {}
        self.images: dict[str, str] = {}
        self.networks: dict[str, list[str]] = defaultdict(list)
        self.build_error: str | None = None
        self.crash: set[str] = set()
        self.shot = {"png": "iVBORw0KGgo=", "errors": []}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1.44")
        method, params = request.method, dict(request.url.params)
        body = request.content
        self.calls.append((method, path, params))
        if path == "/build":
            self.builds.append({**params, "size": len(body)})
            if self.build_error:
                return httpx.Response(200, text=json.dumps({"error": self.build_error}) + "\n")
            tag = params["t"]
            self.images[tag] = f"sha256:{len(self.builds)}"
            return httpx.Response(200, text='{"stream": "Step 1/3"}\n{"stream": "done"}\n')
        if path.startswith("/images/"):
            ref = path.split("/")[2]
            if method == "GET":
                if ref in self.images:
                    return httpx.Response(200, json={"Id": self.images[ref]})
                return httpx.Response(404, json={"message": "no such image"})
            return httpx.Response(200, json=[])
        if path == "/containers/json":
            label = json.loads(params["filters"])["label"][0]
            key, _, value = label.partition("=")
            found = [
                {"Names": [f"/{n}"], "Labels": c["Labels"], "State": c["state"], "Status": "Up"}
                for n, c in self.containers.items()
                if key in c["Labels"] and (not value or c["Labels"][key] == value)
            ]
            return httpx.Response(200, json=found)
        if path == "/containers/create":
            spec = json.loads(body)
            self.containers[params["name"]] = {**spec, "state": "created"}
            self.created[params["name"]] = spec
            return httpx.Response(201, json={"Id": params["name"]})
        if path.startswith("/networks/"):
            self.networks[path.split("/")[2]].append(json.loads(body)["Container"])
            return httpx.Response(200)
        parts = path.split("/")
        name, action = parts[2], parts[3] if len(parts) > 3 else None
        c = self.containers.get(name)
        if c is None:
            return httpx.Response(404, json={"message": "No such container"})
        if method == "DELETE":
            del self.containers[name]
            return httpx.Response(204)
        if action == "start":
            c["state"] = "exited" if name in self.crash else "running"
            return httpx.Response(204)
        if action in ("stop", "restart"):
            c["state"] = "exited" if action == "stop" else "running"
            return httpx.Response(204)
        if action == "wait":
            return httpx.Response(200, json={"StatusCode": 0})
        if action == "logs":
            if name.startswith("nexus-shot-"):
                return httpx.Response(200, content=frames(json.dumps(self.shot) + "\n"))
            return httpx.Response(200, content=frames("Error: cannot find module 'express'\n"))
        if action == "json":
            running = c["state"] == "running"
            state = {"Running": running, "Status": c["state"], "ExitCode": 0 if running else 1}
            return httpx.Response(200, json={"State": state, "RestartCount": 0})
        return httpx.Response(500, json={"message": f"unexpected {method} {path}"})


@pytest.fixture
def fake_docker(monkeypatch) -> FakeDocker:
    fake = FakeDocker()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="http://gate")
    monkeypatch.setattr(module, "docker", Docker("http://gate", client))
    return fake


@pytest.fixture
def traefik(monkeypatch) -> list:
    """What the health check asked Traefik for (host, path); it always answers 200."""
    asked: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append((request.headers["host"], request.url.path))
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(module, "probe", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(module, "HEALTH_TIMEOUT_S", 2)
    return asked


@pytest.fixture
async def deployer(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "WORKSPACES", tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=module.app), base_url="http://deployer", timeout=30
    ) as client:
        yield client
