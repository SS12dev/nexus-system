import hashlib
import hmac

import httpx
import pytest

import jarvis_deployer.app as module

BODY = {"app_id": "p1", "owner": "u1", "user": "u1", "project": "w1"}


def static_project(tmp_path):
    root = tmp_path / "u1" / "w1"
    root.mkdir(parents=True)
    (root / "index.html").write_text("<h1>2048</h1>")
    return root


def node_project(tmp_path):
    root = tmp_path / "u1" / "w1"
    (root / "web").mkdir(parents=True)
    (root / "web" / "index.html").write_text("<h1>2048</h1>")
    (root / "server").mkdir()
    (root / "server" / "index.js").write_text("require('express')")
    return root


async def test_static_app_is_built_offline_and_run_boxed_in(
    deployer, fake_docker, traefik, tmp_path
):
    static_project(tmp_path)
    resp = await deployer.post("/apps/2048/deploy", json={**BODY, "stack": "static"})
    out = resp.json()
    assert resp.status_code == 200 and out["ok"], out
    assert out["url"] == "http://2048.apps.localhost:8080" and out["stack"] == "static"
    assert out["screenshot"] == "iVBORw0KGgo="
    build = fake_docker.builds[0]
    assert build["networkmode"] == "none" and build["t"] == "jarvis-app-2048-web:latest"
    c = fake_docker.containers["jarvis-app-2048-web"]
    host = c["HostConfig"]
    assert host["Memory"] == 512 * 1024 * 1024 and host["NanoCpus"] == 500_000_000
    assert host["NetworkMode"] == "jarvis_apps" and host["CapDrop"] == ["ALL"]
    assert host["RestartPolicy"] == {"Name": "unless-stopped"} and host["ReadonlyRootfs"]
    assert "Binds" not in host and c["User"] == "101"
    labels = c["Labels"]
    assert labels["jarvis.app"] == "p1" and labels["jarvis.owner"] == "u1"
    assert labels["traefik.docker.network"] == "jarvis_apps"
    assert labels["traefik.http.routers.app-2048-web.rule"] == "Host(`2048.apps.localhost`)"
    assert labels["traefik.http.services.app-2048-web.loadbalancer.server.port"] == "8080"
    # No internet unless asked for; checked through Traefik by host.
    assert fake_docker.networks == {}
    assert ("2048.apps.localhost", "/") in traefik
    # The screenshot container is gone again.
    assert "jarvis-shot-2048" not in fake_docker.containers


async def test_backends_get_api_routes_and_internet_on_request(
    deployer, fake_docker, traefik, tmp_path
):
    node_project(tmp_path)
    resp = await deployer.post("/apps/2048/deploy", json={**BODY, "internet": True})
    assert resp.json()["ok"] and resp.json()["stack"] == "node"
    api = fake_docker.containers["jarvis-app-2048-api"]["Labels"]
    assert api["traefik.http.routers.app-2048-api.rule"] == (
        "Host(`2048.apps.localhost`) && PathPrefix(`/api`)"
    )
    assert api["traefik.http.routers.app-2048-api.priority"] == "1100"
    assert fake_docker.containers["jarvis-app-2048-api"]["User"] == "1000"
    assert sorted(fake_docker.networks["jarvis_apps_egress"]) == [
        "jarvis-app-2048-api", "jarvis-app-2048-web",
    ]  # fmt: skip
    assert ("2048.apps.localhost", "/api/health") in traefik


async def test_redeploy_replaces_in_place_and_drops_the_old_image(
    deployer, fake_docker, traefik, tmp_path
):
    static_project(tmp_path)
    await deployer.post("/apps/2048/deploy", json=BODY)
    await deployer.post("/apps/2048/deploy", json=BODY)
    assert list(fake_docker.containers) == ["jarvis-app-2048-web"]
    deletes = [p for m, p, _ in fake_docker.calls if m == "DELETE" and p.startswith("/images/")]
    assert deletes == ["/images/sha256:1"]


async def test_a_failed_build_or_health_check_comes_back_with_logs(
    deployer, fake_docker, traefik, tmp_path
):
    node_project(tmp_path)
    fake_docker.build_error = "npm run build exited 1"
    out = (await deployer.post("/apps/2048/deploy", json=BODY)).json()
    assert not out["ok"] and out["error"] == "The web didn't build."
    assert "npm run build exited 1" in out["logs"]

    fake_docker.build_error = None
    fake_docker.crash.add("jarvis-app-2048-api")
    out = (await deployer.post("/apps/2048/deploy", json=BODY)).json()
    assert not out["ok"] and "the api stopped" in out["error"]
    assert "cannot find module 'express'" in out["logs"]


async def test_a_missing_entry_point_is_explained(deployer, fake_docker, traefik, tmp_path):
    root = tmp_path / "u1" / "w1"
    (root / "web").mkdir(parents=True)
    (root / "api").mkdir()
    out = (await deployer.post("/apps/2048/deploy", json={**BODY, "stack": "web_fastapi"})).json()
    assert not out["ok"] and "api/main.py" in out["error"] and fake_docker.builds == []


async def test_stop_restart_list_and_delete(deployer, fake_docker, traefik, tmp_path):
    static_project(tmp_path)
    await deployer.post("/apps/2048/deploy", json=BODY)
    listed = (await deployer.get("/apps")).json()
    assert listed == [{"slug": "2048", "app_id": "p1", "owner": "u1", "state": "running",
                       "parts": [{"part": "web", "state": "running", "status": "Up"}]}]  # fmt: skip
    assert (await deployer.post("/apps/2048/stop")).status_code == 200
    assert (await deployer.get("/apps")).json()[0]["state"] == "stopped"
    assert (await deployer.post("/apps/2048/restart")).status_code == 200
    assert (await deployer.delete("/apps/2048")).status_code == 200
    assert fake_docker.containers == {}
    assert (await deployer.post("/apps/2048/stop")).status_code == 404


@pytest.mark.parametrize("slug", ["../x", "UPPER", "a" * 64, "jarvis", "traefik"])
async def test_bad_names_and_paths_are_refused(deployer, fake_docker, slug):
    resp = await deployer.post(f"/apps/{slug}/deploy", json=BODY)
    assert resp.status_code in (400, 404)
    resp = await deployer.post("/apps/ok/deploy", json={**BODY, "project": "../../etc"})
    assert resp.status_code == 422


async def test_token_is_checked(deployer, monkeypatch, fake_docker):
    monkeypatch.setattr(module, "TOKEN", "s3cret")
    assert (await deployer.get("/apps")).status_code == 401
    assert (
        await deployer.get("/apps", headers={"Authorization": "Bearer s3cret"})
    ).status_code == 200
    assert (await deployer.get("/healthz")).status_code == 200


async def test_a_backend_route_answered_by_the_front_end_is_not_healthy(
    deployer, fake_docker, monkeypatch, tmp_path
):
    """Before Traefik routes /api to the back end, the front end must not pass for it."""
    import httpx

    def handler(request):
        return httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"})

    monkeypatch.setattr(module, "probe", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(module, "HEALTH_TIMEOUT_S", 1)
    node_project(tmp_path)
    out = (await deployer.post("/apps/2048/deploy", json=BODY)).json()
    assert not out["ok"] and "/api/health answered 200" in out["error"]


async def test_apps_are_private_unless_asked_and_the_checks_get_past_sign_in(
    deployer, fake_docker, monkeypatch, tmp_path
):
    static_project(tmp_path)
    monkeypatch.setattr(module, "TOKEN", "s3cret")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-jarvis-preview", ""))
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(module, "probe", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    auth = {"Authorization": "Bearer s3cret"}
    assert (await deployer.post("/apps/2048/deploy", json=BODY, headers=auth)).json()["ok"]
    labels = fake_docker.containers["jarvis-app-2048-web"]["Labels"]
    assert labels["traefik.http.routers.app-2048-web.middlewares"] == "app-auth@docker"
    # The health check and the screenshot carry the key for this host alone.
    key = hmac.new(b"s3cret", b"preview:2048.apps.localhost", hashlib.sha256).hexdigest()
    assert seen and set(seen) == {key}
    assert fake_docker.created["jarvis-shot-2048"]["Cmd"][-2:] == ["2048.apps.localhost", key]
    resp = await deployer.post("/apps/2048/deploy", json={**BODY, "private": False}, headers=auth)
    assert resp.json()["ok"]
    labels = fake_docker.containers["jarvis-app-2048-web"]["Labels"]
    assert not any(k.endswith(".middlewares") for k in labels)


async def test_making_public_or_private_reuses_the_images(deployer, fake_docker, traefik, tmp_path):
    static_project(tmp_path)
    # Nothing built yet: nothing to reuse.
    out = (await deployer.post("/apps/2048/deploy", json={**BODY, "build": False})).json()
    assert not out["ok"] and "no image yet" in out["error"]
    await deployer.post("/apps/2048/deploy", json=BODY)
    fake_docker.containers.pop("jarvis-shot-2048", None)
    out = (
        await deployer.post("/apps/2048/deploy", json={**BODY, "build": False, "private": False})
    ).json()
    assert out["ok"] and out["screenshot"] is None
    assert len(fake_docker.builds) == 1
    labels = fake_docker.containers["jarvis-app-2048-web"]["Labels"]
    assert "traefik.http.routers.app-2048-web.middlewares" not in labels
