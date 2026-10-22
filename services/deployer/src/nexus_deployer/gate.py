"""The Docker gate: the deployer's only door to Docker, and the only container that holds the
Docker socket. It passes on a short allowlist of API calls and refuses everything else (exec,
archive, attach, volumes, swarm, plugins, system...). Generic socket proxies allow or deny by
URL prefix, so allowing /containers would also allow /containers/<id>/exec and archive writes;
this gate checks each call:

- builds: offline only (networkmode=none), tagged nexus-app-*, labelled with the app;
- containers and images it touches must carry the nexus.app (or nexus.shot) label, so Nexus's
  own containers can't be stopped, deleted or read;
- a new container must be named nexus-app-* or nexus-shot-*, run as a non-root user on the
  apps network, with a memory and CPU cap, and no privileges, host mounts, devices, published
  ports or extra capabilities;
- networks: only joining or leaving the apps' internet network.

    GET  /_ping, /version
    POST /build                              offline builds of nexus-app-* images
    GET  /images/{ref}/json  DELETE /images/{ref}            managed images
    GET  /containers/json                                    list
    POST /containers/create?name=nexus-(app|shot)-*         checked body
    GET  /containers/{id}/json|logs   POST start|stop|restart|wait   DELETE   managed only
    POST /networks/{apps internet network}/connect|disconnect          managed only
"""

import json
import os
import re
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
APPS_NETWORK = os.environ.get("APPS_NETWORK", "nexus_apps")
EGRESS_NETWORK = os.environ.get("APPS_EGRESS_NETWORK", "nexus_apps_egress")
SHOT_IMAGE = os.environ.get("SCREENSHOT_IMAGE", "nexus-workbench:latest")
MAX_MEMORY = int(os.environ.get("GATE_MAX_MEMORY_MB", "1024")) * 1024 * 1024
MAX_NANO_CPUS = int(float(os.environ.get("GATE_MAX_CPUS", "1")) * 1e9)

VERSION = re.compile(r"^/v\d+(\.\d+)?(?=/)")
MANAGED_NAME = re.compile(r"^nexus-(app|shot)-[a-z0-9][a-z0-9-]{0,80}$")
APP_TAG = re.compile(r"^nexus-app-[a-z0-9][a-z0-9-]{0,80}(:[a-z0-9._-]{1,40})?$")
ID = r"[A-Za-z0-9_.:-]{1,128}"
BUILD_PARAMS = {
    "t",
    "dockerfile",
    "networkmode",
    "forcerm",
    "rm",
    "nocache",
    "labels",
    "q",
    "memory",
    "memswap",
    "cpuquota",
    "cpuperiod",
    "buildargs",
    "pull",
    "version",
}
# HostConfig settings a new container may use; anything else set to a non-empty value is refused.
HOST_CONFIG = {
    "Memory",
    "MemorySwap",
    "NanoCpus",
    "PidsLimit",
    "RestartPolicy",
    "NetworkMode",
    "ReadonlyRootfs",
    "Tmpfs",
    "ShmSize",
    "CapDrop",
    "SecurityOpt",
    "LogConfig",
    "Init",
    "AutoRemove",
}
CREATE_KEYS = {
    "Image",
    "User",
    "Cmd",
    "Env",
    "Labels",
    "HostConfig",
    "NetworkingConfig",
    "ExposedPorts",
    "WorkingDir",
    "Entrypoint",
    "StopTimeout",
    "Healthcheck",
    "Hostname",
}
HOP = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "host",
    "content-length",
}

app = FastAPI(title="Nexus Docker gate", version="0.1.0", docs_url=None, redoc_url=None)
docker = httpx.AsyncClient(
    transport=httpx.AsyncHTTPTransport(uds=SOCKET), base_url="http://docker", timeout=None
)


class Refused(Exception):
    pass


def deny(why: str) -> JSONResponse:
    return JSONResponse({"message": f"Refused by the Nexus Docker gate: {why}"}, status_code=403)


async def managed(kind: str, ref: str) -> bool:
    """Whether a container or image belongs to a Nexus app (by its labels). One that doesn't
    exist counts too: Docker then just answers 404, and there is nothing to harm."""
    resp = await docker.get(f"/{kind}/{ref}/json")
    if resp.status_code == 404:
        return True
    if resp.status_code != 200:
        return False
    data = resp.json()
    labels = (data.get("Config") or {}).get("Labels") or {}
    return "nexus.app" in labels or (kind == "containers" and "nexus.shot" in labels)


def check_build(query: dict) -> None:
    unknown = set(query) - BUILD_PARAMS
    if unknown:
        raise Refused(f"build option {sorted(unknown)[0]}")
    if query.get("networkmode") != ["none"]:
        raise Refused("builds must have no network (networkmode=none)")
    if query.get("version", ["1"]) != ["1"]:
        raise Refused("only the classic builder")
    tags = query.get("t") or []
    if not tags or not all(APP_TAG.match(t) for t in tags):
        raise Refused("images must be tagged nexus-app-*")
    try:
        labels = json.loads((query.get("labels") or ["{}"])[0])
    except ValueError as exc:
        raise Refused("bad labels") from exc
    if "nexus.app" not in labels:
        raise Refused("images must be labelled nexus.app")


def check_create(query: dict, body: dict) -> None:
    name = (query.get("name") or [""])[0]
    if not MANAGED_NAME.match(name):
        raise Refused("containers must be named nexus-app-* or nexus-shot-*")
    unknown = set(body) - CREATE_KEYS
    if unknown:
        raise Refused(f"container option {sorted(unknown)[0]}")
    image, labels = body.get("Image", ""), body.get("Labels") or {}
    if name.startswith("nexus-shot-"):
        if image != SHOT_IMAGE or "nexus.shot" not in labels:
            raise Refused("screenshots use the workbench image and the nexus.shot label")
    elif not APP_TAG.match(image) or "nexus.app" not in labels:
        raise Refused("apps use nexus-app-* images and the nexus.app label")
    user = str(body.get("User") or "").split(":")[0]
    if user in ("", "0", "root"):
        raise Refused("containers must run as a non-root user")
    host = body.get("HostConfig") or {}
    for key, value in host.items():
        if key not in HOST_CONFIG and value not in (None, "", 0, False, [], {}):
            raise Refused(f"HostConfig.{key}")
    if host.get("NetworkMode") != APPS_NETWORK:
        raise Refused(f"containers must be on {APPS_NETWORK}")
    endpoints = ((body.get("NetworkingConfig") or {}).get("EndpointsConfig") or {}).keys()
    if set(endpoints) - {APPS_NETWORK}:
        raise Refused(f"containers start on {APPS_NETWORK} only")
    if not 0 < int(host.get("Memory") or 0) <= MAX_MEMORY:
        raise Refused("a memory limit is required")
    if not 0 < int(host.get("NanoCpus") or 0) <= MAX_NANO_CPUS:
        raise Refused("a CPU limit is required")
    if "ALL" not in (host.get("CapDrop") or []):
        raise Refused("all capabilities must be dropped")
    if any(not str(o).startswith("no-new-privileges") for o in host.get("SecurityOpt") or []):
        raise Refused("security options other than no-new-privileges")


async def check(method: str, path: str, query: dict, body: bytes) -> None:
    """Raise Refused unless this call is on the allowlist."""
    if method == "GET" and path in ("/_ping", "/version"):
        return
    if method == "POST" and path == "/build":
        return check_build(query)
    if method == "GET" and path == "/containers/json":
        return
    if method == "POST" and path == "/containers/create":
        try:
            spec = json.loads(body or b"{}")
        except ValueError as exc:
            raise Refused("bad container body") from exc
        return check_create(query, spec)
    if m := re.fullmatch(rf"/images/({ID})(/json)?", path):
        if (method, bool(m.group(2))) in (("GET", True), ("DELETE", False)):
            if await managed("images", m.group(1)):
                return
            raise Refused("not a Nexus app image")
    if m := re.fullmatch(rf"/containers/({ID})(/(json|logs|start|stop|restart|wait))?", path):
        action = m.group(3)
        allowed = (
            (method == "GET" and action in ("json", "logs"))
            or (method == "POST" and action in ("start", "stop", "restart", "wait"))
            or (method == "DELETE" and action is None)
        )
        if allowed:
            if await managed("containers", m.group(1)):
                return
            raise Refused("not a Nexus app container")
    if m := re.fullmatch(rf"/networks/({ID})/(connect|disconnect)", path):
        if method == "POST" and m.group(1) == EGRESS_NETWORK:
            try:
                target = str(json.loads(body or b"{}").get("Container", ""))
            except (ValueError, AttributeError) as exc:
                raise Refused("bad network body") from exc
            if re.fullmatch(ID, target) and await managed("containers", target):
                return
            raise Refused("not a Nexus app container")
    raise Refused(f"{method} {path}")


@app.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH"])
async def gate(request: Request, rest: str):
    raw = "/" + rest
    path = VERSION.sub("", raw)
    query = parse_qs(request.url.query, keep_blank_values=True)
    # Bodies the gate reads are small; a build's tar is streamed straight through.
    streamed = path == "/build"
    body = b"" if streamed else await request.body()
    try:
        await check(request.method, path, query, body)
    except Refused as exc:
        return deny(str(exc))
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    upstream = docker.build_request(
        request.method,
        raw,
        params=request.url.query,
        headers=headers,
        content=request.stream() if streamed else body,
    )
    resp = await docker.send(upstream, stream=True)
    if request.method == "HEAD":
        await resp.aclose()
        return Response(status_code=resp.status_code)
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
    return StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        headers=out_headers,
        background=BackgroundTask(resp.aclose),
    )
