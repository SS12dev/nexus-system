import json
import re

import httpx
import pytest

import nexus_deployer.gate as gate

APP_LABELS = {"jarvis.app": "p1"}


class Stream(httpx.AsyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        yield self.data


@pytest.fixture
def docker(monkeypatch):
    """Docker behind the gate: containers c-app (an app) and postgres (not one)."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        path = request.url.path
        if re.fullmatch(r"(/v1.44)?/containers/[^/]+/json", path):
            name = path.split("/")[-2]
            labels = APP_LABELS if name.startswith("jarvis-app") else {"com.docker.compose": "x"}
            if name == "missing":
                return httpx.Response(404, json={"message": "No such container"})
            return httpx.Response(200, json={"Config": {"Labels": labels}})
        if re.fullmatch(r"(/v1.44)?/images/[^/]+/json", path):
            labels = APP_LABELS if "jarvis-app" in path else {}
            return httpx.Response(200, json={"Config": {"Labels": labels}})
        # Streamed like the real socket, so the gate can pass it on as it comes.
        return httpx.Response(200, stream=Stream(json.dumps({"ok": True}).encode()))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://docker")
    monkeypatch.setattr(gate, "docker", client)
    return seen


@pytest.fixture
async def through(docker):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gate.app), base_url="http://gate"
    ) as c:
        yield c


def good_create() -> dict:
    return {
        "Image": "jarvis-app-2048-web:latest",
        "User": "101",
        "Labels": {"jarvis.app": "p1", "traefik.enable": "true"},
        "HostConfig": {
            "Memory": 512 * 1024 * 1024,
            "NanoCpus": 500_000_000,
            "NetworkMode": "jarvis_apps",
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "RestartPolicy": {"Name": "unless-stopped"},
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": "size=64m"},
        },
    }


async def test_allowed_calls_pass(through):
    assert (await through.get("/v1.44/_ping")).status_code == 200
    assert (await through.get("/v1.44/containers/json", params={"all": "1"})).status_code == 200
    resp = await through.post(
        "/v1.44/containers/create", params={"name": "jarvis-app-2048-web"}, json=good_create()
    )
    assert resp.status_code == 200, resp.text
    for action in ("start", "stop", "restart", "wait"):
        assert (
            await through.post(f"/v1.44/containers/jarvis-app-2048-web/{action}")
        ).status_code == 200
    assert (await through.delete("/v1.44/containers/jarvis-app-2048-web")).status_code == 200
    assert (await through.delete("/v1.44/images/jarvis-app-2048-web:latest")).status_code == 200
    params = {"t": "jarvis-app-2048-web:latest", "networkmode": "none",
              "labels": json.dumps(APP_LABELS)}  # fmt: skip
    assert (await through.post("/v1.44/build", params=params, content=b"tar")).status_code == 200
    body = {"Container": "jarvis-app-2048-web"}
    resp = await through.post("/v1.44/networks/jarvis_apps_egress/connect", json=body)
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1.44/containers/jarvis-app-2048-web/exec"),
        ("POST", "/v1.44/exec/abc/start"),
        ("PUT", "/v1.44/containers/jarvis-app-2048-web/archive"),
        ("GET", "/v1.44/containers/jarvis-app-2048-web/archive"),
        ("POST", "/v1.44/containers/jarvis-app-2048-web/attach"),
        ("GET", "/v1.44/volumes"),
        ("POST", "/v1.44/volumes/create"),
        ("GET", "/v1.44/swarm"),
        ("GET", "/v1.44/info"),
        ("POST", "/v1.44/images/create"),
        ("POST", "/v1.44/networks/create"),
        ("POST", "/v1.44/networks/jarvis_default/connect"),
        ("GET", "/v1.44/plugins"),
        ("POST", "/v1.44/session"),
    ],
)
async def test_everything_else_is_refused(through, docker, method, path):
    resp = await through.request(method, path, json={})
    assert resp.status_code == 403 and "Nexus Docker gate" in resp.json()["message"]
    assert all(p != path for _, p in docker)


async def test_jarvis_own_containers_are_off_limits(through, docker):
    for method, path in [
        ("POST", "/v1.44/containers/jarvis-postgres-1/stop"),
        ("DELETE", "/v1.44/containers/jarvis-postgres-1"),
        ("GET", "/v1.44/containers/jarvis-postgres-1/logs"),
        ("DELETE", "/v1.44/images/postgres:17"),
    ]:
        assert (await through.request(method, path)).status_code == 403, path
    resp = await through.post(
        "/v1.44/networks/jarvis_apps_egress/connect", json={"Container": "jarvis-postgres-1"}
    )
    assert resp.status_code == 403
    # The refused calls never reached Docker beyond the label check.
    assert not any(m != "GET" for m, _ in docker)


@pytest.mark.parametrize(
    "params",
    [
        {"t": "jarvis-app-x:latest", "labels": json.dumps(APP_LABELS)},  # with network
        {"t": "jarvis-app-x", "networkmode": "host", "labels": json.dumps(APP_LABELS)},
        {"t": "postgres:latest", "networkmode": "none", "labels": json.dumps(APP_LABELS)},
        {"t": "jarvis-app-x", "networkmode": "none"},  # no label
        {"t": "jarvis-app-x", "networkmode": "none", "labels": json.dumps(APP_LABELS),
         "remote": "https://evil.example/x.tar"},
        {"t": "jarvis-app-x", "networkmode": "none", "labels": json.dumps(APP_LABELS),
         "version": "2"},
    ],
)  # fmt: skip
async def test_builds_must_be_offline_app_images(through, params):
    assert (await through.post("/v1.44/build", params=params, content=b"tar")).status_code == 403


def bad(**changes) -> dict:
    body = good_create()
    for key, value in changes.items():
        if key.startswith("HostConfig."):
            body["HostConfig"][key.split(".", 1)[1]] = value
        else:
            body[key] = value
    return body


@pytest.mark.parametrize(
    "name,body",
    [
        ("postgres", good_create()),
        ("jarvis-app-x", bad(**{"HostConfig.Privileged": True})),
        ("jarvis-app-x", bad(**{"HostConfig.Binds": ["/:/host"]})),
        ("jarvis-app-x", bad(**{"HostConfig.Mounts": [{"Type": "bind", "Source": "/"}]})),
        ("jarvis-app-x", bad(**{"HostConfig.CapAdd": ["SYS_ADMIN"]})),
        ("jarvis-app-x", bad(**{"HostConfig.PortBindings": {"80/tcp": [{"HostPort": "80"}]}})),
        ("jarvis-app-x", bad(**{"HostConfig.NetworkMode": "jarvis_default"})),
        ("jarvis-app-x", bad(**{"HostConfig.NetworkMode": "host"})),
        ("jarvis-app-x", bad(**{"HostConfig.PidMode": "host"})),
        ("jarvis-app-x", bad(**{"HostConfig.Devices": [{"PathOnHost": "/dev/sda"}]})),
        ("jarvis-app-x", bad(**{"HostConfig.Memory": 0})),
        ("jarvis-app-x", bad(**{"HostConfig.Memory": 64 * 1024**3})),
        ("jarvis-app-x", bad(**{"HostConfig.NanoCpus": 0})),
        ("jarvis-app-x", bad(**{"HostConfig.CapDrop": []})),
        ("jarvis-app-x", bad(**{"HostConfig.SecurityOpt": ["seccomp=unconfined"]})),
        ("jarvis-app-x", bad(User="root")),
        ("jarvis-app-x", bad(User="")),
        ("jarvis-app-x", bad(Image="postgres:17")),
        ("jarvis-app-x", bad(Labels={})),
        ("jarvis-app-x", bad(Volumes={"/data": {}})),
        ("jarvis-shot-x", good_create()),  # a screenshot must use the workbench image
    ],
)
async def test_new_containers_are_boxed_in(through, docker, name, body):
    resp = await through.post("/v1.44/containers/create", params={"name": name}, json=body)
    assert resp.status_code == 403, (name, body)
    assert ("POST", "/v1.44/containers/create") not in docker


async def test_calls_on_missing_containers_reach_docker(through, docker):
    # The first deploy removes a container that isn't there yet; Docker answers 404 itself.
    assert (await through.delete("/v1.44/containers/missing")).status_code == 200
    assert ("DELETE", "/v1.44/containers/missing") in docker
