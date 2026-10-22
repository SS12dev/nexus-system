"""The deployer: turns a software project in the workbench into running containers on the apps
network, and stops, restarts and deletes them. Only the orchestrator and the API can reach it
(the deployer network, plus DEPLOYER_TOKEN); it reaches Docker only through the Docker gate.

A deploy builds each part of the project (a web front end, and a back end for web_fastapi and
node) from a house template with no network, using the dependencies the team installed in the
workbench. The new containers replace the old ones under the same names: non-root, read-only,
512 MB and half a CPU, on the internal apps network (no internet unless the project opts in),
labelled for Traefik to route <slug>.<APPS_DOMAIN> to them. Then it checks the app answers
through Traefik and takes a screenshot with the workbench image's Playwright.

    POST   /apps/{slug}/deploy    {"app_id", "owner", "user", "project", "stack", "internet"}
    GET    /apps                  every app container, by app
    POST   /apps/{slug}/stop      POST /apps/{slug}/restart      DELETE /apps/{slug}
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from nexus_deployer.build import BuildProblem, Part, context, detect_stack, parts
from nexus_deployer.docker import Docker, DockerError

log = logging.getLogger("nexus_deployer")

TOKEN = os.environ.get("DEPLOYER_TOKEN", "")
WORKSPACES = Path(os.environ.get("WORKSPACES", "/workspaces"))
APPS_NETWORK = os.environ.get("APPS_NETWORK", "jarvis_apps")
EGRESS_NETWORK = os.environ.get("APPS_EGRESS_NETWORK", "jarvis_apps_egress")
APPS_DOMAIN = os.environ.get("APPS_DOMAIN", "apps.localhost")
# Where people open an app: https on a real domain (the tunnel), Traefik's port on *.localhost.
APPS_URL = os.environ.get("APPS_URL") or (
    "http://{host}:" + os.environ.get("NEXUS_HTTP_PORT", "8080")
    if APPS_DOMAIN.endswith("localhost")
    else "https://{host}"
)
# Never an app's name: Nexus's own and the usual service names, plus RESERVED_HOSTNAMES.
RESERVED = {"nexus", "jarvis", "www", "mail", "api", "admin", "traefik"} | {
    h.strip().lower() for h in os.environ.get("RESERVED_HOSTNAMES", "").split(",") if h.strip()
}
# The Traefik middleware that lets only signed-in, allowed people into a private app.
AUTH_MIDDLEWARE = os.environ.get("APPS_AUTH_MIDDLEWARE", "app-auth@docker")
TRAEFIK_URL = os.environ.get("TRAEFIK_URL", "http://traefik")
SHOT_IMAGE = os.environ.get("SCREENSHOT_IMAGE", "jarvis-workbench:latest")
APP_MEMORY_MB = int(os.environ.get("APP_MEMORY_MB", "512"))
APP_CPUS = float(os.environ.get("APP_CPUS", "0.5"))
BUILD_TIMEOUT_S = int(os.environ.get("BUILD_TIMEOUT_S", "900"))
HEALTH_TIMEOUT_S = int(os.environ.get("HEALTH_TIMEOUT_S", "60"))
SHOT_TIMEOUT_S = int(os.environ.get("SCREENSHOT_TIMEOUT_S", "90"))
LOG_CHARS = 4000

ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# Each template's image runs as this user (none of them as root).
USERS = {"static": "101", "fastapi": "10010", "node": "1000"}
# Loads the app through Traefik (as a visitor would) and prints {"png", "errors"} as JSON.
SHOT_JS = """
const { chromium } = require('/usr/local/lib/node_modules/@playwright/test');
(async () => {
  const [url, host, preview] = process.argv.slice(1);
  const browser = await chromium.launch({ args: [`--host-resolver-rules=MAP ${host} traefik`] });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 800 },
    extraHTTPHeaders: { 'X-Nexus-Preview': preview },
  });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(500);
  const png = await page.screenshot();
  console.log(JSON.stringify({ png: png.toString('base64'), errors: errors.slice(0, 10) }));
  await browser.close();
})().catch((e) => { console.log(JSON.stringify({ error: String(e) })); process.exit(1); });
"""

app = FastAPI(title="Nexus deployer", version="0.1.0")
docker = Docker(os.environ.get("DOCKER_URL", "http://docker-gate:2375"))
probe = httpx.AsyncClient(timeout=5)


def check_token(authorization: str = Header(default="")) -> None:
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad deployer token")


api = APIRouter(dependencies=[Depends(check_token)])


def slug_ok(slug: str) -> str:
    if not SLUG.match(slug):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "App names are a-z, 0-9 and -.")
    if slug in RESERVED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{slug} is a reserved name.")
    return slug


def preview_key(host: str) -> str:
    """Lets the deployer's own health check and screenshot past a private app's sign-in check.
    The API works out the same value; each one opens only its own host."""
    return hmac.new(TOKEN.encode(), f"preview:{host}".encode(), hashlib.sha256).hexdigest()


class DeployIn(BaseModel):
    app_id: str = Field(pattern=ID.pattern)
    owner: str = Field(pattern=ID.pattern)
    # Where the project is: /workspaces/<user>/<project>.
    user: str = Field(pattern=ID.pattern)
    project: str = Field(pattern=ID.pattern)
    stack: str | None = None
    internet: bool = False
    # Behind the sign-in check: only its owner and Nexus's owner can open it.
    private: bool = True
    # False: start it again from its current images (a change of visibility), no build.
    build: bool = True


class DeployOut(BaseModel):
    ok: bool
    url: str
    stack: str | None = None
    error: str | None = None
    # The tail of the failing build or the containers' logs, for the builders.
    logs: str = ""
    screenshot: str | None = None  # base64 PNG
    console_errors: list[str] = []


def host_for(slug: str) -> str:
    return f"{slug}.{APPS_DOMAIN}"


def container_spec(body: DeployIn, slug: str, part: Part, tag: str) -> dict:
    router = f"app-{slug}-{part.name}"
    rule = f"Host(`{host_for(slug)}`)"
    if part.prefix:
        rule += f" && PathPrefix(`{part.prefix}`)"
    return {
        "Image": tag,
        "User": USERS[part.template],
        "Env": ["HOME=/tmp", "npm_config_cache=/tmp/.npm"],
        "Labels": {
            "jarvis.app": body.app_id,
            "jarvis.owner": body.owner,
            "jarvis.slug": slug,
            "jarvis.part": part.name,
            "traefik.enable": "true",
            "traefik.docker.network": APPS_NETWORK,
            f"traefik.http.routers.{router}.rule": rule,
            f"traefik.http.routers.{router}.entrypoints": "web",
            # Above Nexus's own routes, so /api on an app's host is the app's back end.
            f"traefik.http.routers.{router}.priority": "1100" if part.prefix else "1000",
            **(
                {f"traefik.http.routers.{router}.middlewares": AUTH_MIDDLEWARE}
                if body.private
                else {}
            ),  # fmt: skip
            f"traefik.http.services.{router}.loadbalancer.server.port": str(part.port),
        },
        "HostConfig": {
            "Memory": APP_MEMORY_MB * 1024 * 1024,
            "NanoCpus": int(APP_CPUS * 1e9),
            "PidsLimit": 256,
            "RestartPolicy": {"Name": "unless-stopped"},
            "NetworkMode": APPS_NETWORK,
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": "size=64m"},
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
        },
    }


async def healthy(slug: str, ps: list[Part]) -> tuple[bool, str]:
    """Every part running and answering through Traefik, within HEALTH_TIMEOUT_S."""
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    detail = "it didn't answer"
    while time.monotonic() < deadline:
        waiting = False
        for p in ps:
            info = await docker.inspect(f"jarvis-app-{slug}-{p.name}") or {}
            state = info.get("State") or {}
            if not state.get("Running") or state.get("Restarting"):
                if state.get("Status") in ("exited", "dead") or info.get("RestartCount", 0) > 1:
                    return False, f"the {p.name} stopped (exit code {state.get('ExitCode')})"
                waiting = True
                break
            try:
                headers = {"Host": host_for(slug), "X-Nexus-Preview": preview_key(host_for(slug))}
                resp = await probe.get(TRAEFIK_URL + p.health, headers=headers)
            except httpx.HTTPError as exc:
                detail = f"the {p.name} didn't answer ({exc.__class__.__name__})"
                waiting = True
                break
            # A back end must answer itself, not the front end's page.
            html = "text/html" in resp.headers.get("content-type", "")
            if resp.status_code >= 400 or (p.prefix and html):
                detail = f"{p.health} answered {resp.status_code}"
                waiting = True
                break
        if not waiting:
            return True, ""
        await asyncio.sleep(1)
    return False, detail


async def screenshot(body: DeployIn, slug: str) -> dict:
    """{"png", "errors"} from a throwaway container of the workbench image; {} if it failed."""
    name = f"jarvis-shot-{slug}"
    host = host_for(slug)
    spec = {
        "Image": SHOT_IMAGE,
        "User": "10003",
        "Cmd": ["node", "-e", SHOT_JS, f"http://{host}/", host, preview_key(host)],
        # The workbench image's proxy settings point at egress-proxy, which isn't on this network.
        "Env": [
            "HOME=/tmp",
            "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright",
            "HTTP_PROXY=",
            "HTTPS_PROXY=",
            "http_proxy=",
            "https_proxy=",
        ],
        "Labels": {"jarvis.shot": body.app_id},
        "HostConfig": {
            "Memory": 1024 * 1024 * 1024,
            "NanoCpus": int(1e9),
            "PidsLimit": 512,
            "NetworkMode": APPS_NETWORK,
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": "size=256m"},
            "ShmSize": 256 * 1024 * 1024,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
        },
    }
    try:
        await docker.remove(name)
        await docker.create(name, spec)
        await docker.start(name)
        await docker.wait(name, SHOT_TIMEOUT_S)
        out = await docker.logs(name, tail="all")
        return json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except (DockerError, ValueError, IndexError, httpx.HTTPError) as exc:
        log.warning("No screenshot of %s: %s", slug, exc)
        return {}
    finally:
        try:
            await docker.remove(name)
        except DockerError:
            pass


async def app_containers(slug: str | None = None) -> list[dict]:
    label = f"jarvis.slug={slug}" if slug else "jarvis.app"
    return await docker.containers(label)


@api.post("/apps/{slug}/deploy", response_model=DeployOut)
async def deploy(slug: str, body: DeployIn):
    slug = slug_ok(slug)
    url = APPS_URL.format(host=host_for(slug))
    root = WORKSPACES / body.user / body.project
    if not root.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such project in the workbench.")
    stack = detect_stack(root, body.stack)
    try:
        ps = parts(root, stack)
    except BuildProblem as exc:
        return DeployOut(ok=False, url=url, stack=stack, error=str(exc))
    labels = {"jarvis.app": body.app_id, "jarvis.owner": body.owner}
    if not body.build:
        for p in ps:
            if not await docker.image_id(f"jarvis-app-{slug}-{p.name}:latest"):
                return DeployOut(
                    ok=False, url=url, stack=stack, error="It has no image yet; redeploy it."
                )
    # Build everything first, so the running version stays up while the new one builds.
    old_images = []
    for p in ps if body.build else []:
        tag = f"jarvis-app-{slug}-{p.name}:latest"
        old = await docker.image_id(tag)
        try:
            ctx = await asyncio.to_thread(context, root, p)
            with ctx:
                await docker.build(tag, ctx, labels, p.build_args, limit_s=BUILD_TIMEOUT_S)
        except BuildProblem as exc:
            return DeployOut(ok=False, url=url, stack=stack, error=str(exc))
        except DockerError as exc:
            return DeployOut(
                ok=False,
                url=url,
                stack=stack,
                error=f"The {p.name} didn't build.",
                logs=str(exc)[-LOG_CHARS:],
            )
        if old and old != await docker.image_id(tag):
            old_images.append(old)
    # Swap the containers: same names, so Traefik's routes stay the same.
    wanted = {f"jarvis-app-{slug}-{p.name}" for p in ps}
    try:
        for c in await app_containers(slug):
            for n in c.get("Names", []):
                if n.lstrip("/") not in wanted:
                    await docker.remove(n.lstrip("/"))
        for p in ps:
            name = f"jarvis-app-{slug}-{p.name}"
            await docker.remove(name)
            await docker.create(
                name, container_spec(body, slug, p, f"jarvis-app-{slug}-{p.name}:latest")
            )
            if body.internet:
                await docker.connect(EGRESS_NETWORK, name)
            await docker.start(name)
        for image in old_images:
            await docker.remove_image(image)
    except DockerError as exc:
        return DeployOut(ok=False, url=url, stack=stack, error=f"The app didn't start: {exc}")
    ok, why = await healthy(slug, ps)
    if not ok:
        logs = ""
        for p in ps:
            logs += f"--- {p.name} ---\n{await docker.logs(f'jarvis-app-{slug}-{p.name}')}\n"
        return DeployOut(
            ok=False,
            url=url,
            stack=stack,
            error=f"The app started but failed its health check: {why}.",
            logs=logs[-LOG_CHARS:],
        )
    # A change of visibility shows the same app: no new screenshot.
    shot = await screenshot(body, slug) if body.build else {}
    return DeployOut(
        ok=True,
        url=url,
        stack=stack,
        screenshot=shot.get("png"),
        console_errors=shot.get("errors") or [],
    )


@api.get("/apps")
async def list_apps():
    """Each app with its parts' states: running only when every part runs."""
    apps: dict[str, dict] = {}
    for c in await app_containers():
        labels = c.get("Labels") or {}
        slug = labels.get("jarvis.slug")
        if not slug:
            continue
        a = apps.setdefault(
            slug,
            {
                "slug": slug,
                "app_id": labels.get("jarvis.app"),
                "owner": labels.get("jarvis.owner"),
                "parts": [],
            },
        )
        a["parts"].append(
            {"part": labels.get("jarvis.part"), "state": c.get("State"), "status": c.get("Status")}
        )
    for a in apps.values():
        states = {p["state"] for p in a["parts"]}
        a["state"] = (
            "running"
            if states == {"running"}
            else ("stopped" if "running" not in states else "degraded")
        )
    return list(apps.values())


async def each(slug: str, action) -> dict:
    found = await app_containers(slug_ok(slug))
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such app.")
    try:
        for c in found:
            await action(c["Names"][0].lstrip("/"))
    except DockerError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {"slug": slug, "containers": len(found)}


@api.post("/apps/{slug}/stop")
async def stop(slug: str):
    return await each(slug, docker.stop)


@api.post("/apps/{slug}/restart")
async def restart(slug: str):
    return await each(slug, docker.restart)


@api.delete("/apps/{slug}")
async def delete(slug: str):
    """Remove the app's containers and images; its project and repo stay."""
    found = await app_containers(slug_ok(slug))
    for c in found:
        await docker.remove(c["Names"][0].lstrip("/"))
    for part in ("web", "api"):
        await docker.remove_image(f"jarvis-app-{slug}-{part}:latest")
    return {"slug": slug, "containers": len(found)}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


app.include_router(api)
