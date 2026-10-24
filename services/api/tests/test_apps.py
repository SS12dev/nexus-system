"""The Apps page's API with a fake deployer: who sees which apps, the member limit, and stop,
restart, redeploy, internet access, public or private, and delete."""

import uuid

import pytest
from sqlalchemy import select

from nexus_common.apps import app_name
from nexus_common.deployer import DeployerError, DeployResult
from nexus_common.models import Project, User


class FakeDeployer:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.live: list[dict] = []
        self.down = False
        self.deploys: list[dict] = []

    async def _do(self, action: str, slug: str):
        if self.down:
            raise DeployerError("The deployer isn't reachable right now.")
        self.calls.append((action, slug))

    async def apps(self):
        if self.down:
            raise DeployerError("down")
        return self.live

    async def stop(self, slug):
        await self._do("stop", slug)

    async def restart(self, slug):
        await self._do("restart", slug)

    async def delete(self, slug):
        await self._do("delete", slug)

    async def deploy(self, slug, **kw):
        await self._do("deploy", slug)
        self.deploys.append(kw)
        return DeployResult(ok=True, url=f"http://{slug}.apps.localhost:8080")

    async def aclose(self):
        pass


@pytest.fixture
def deployer(app) -> FakeDeployer:
    app.state.deployer = FakeDeployer()
    return app.state.deployer


async def add_app(sessionmaker, user_id, slug, state="running", **kw) -> uuid.UUID:
    async with sessionmaker() as db:
        p = Project(user_id=uuid.UUID(user_id), workbench_id=f"w-{slug}", name=slug.title(),
                    app_slug=slug, app_state=state, live_url=f"http://{slug}.apps.localhost:8080",
                    **kw)  # fmt: skip
        db.add(p)
        await db.commit()
        return p.id


@pytest.fixture
async def people(owner, client_for, invite):
    me = (await owner.get("/api/me")).json()
    info = await invite(owner, "priya@example.com")
    member = await client_for("priya@example.com")
    return me["id"], info["id"], member


async def test_owner_sees_everyones_apps_and_members_their_own(
    owner, people, sessionmaker, deployer
):
    owner_id, member_id, member = people
    await add_app(sessionmaker, owner_id, "2048")
    await add_app(sessionmaker, member_id, "priya-snake", state="running")
    deployer.live = [{"slug": "priya-snake", "state": "stopped"}]
    everyone = (await owner.get("/api/apps")).json()
    assert {a["slug"]: a["owner"] for a in everyone} == {
        "2048": "owner@example.com",
        "priya-snake": "priya@example.com",
    }
    # The containers' own state wins over the stored one.
    assert {a["slug"]: a["state"] for a in everyone}["priya-snake"] == "stopped"
    mine = (await member.get("/api/apps")).json()
    assert [a["slug"] for a in mine] == ["priya-snake"]
    # Someone else's app is not found, not forbidden.
    other = everyone[[a["slug"] for a in everyone].index("2048")]["id"]
    assert (await member.post(f"/api/apps/{other}/stop")).status_code == 404
    # The deployer being down doesn't break the page.
    deployer.down = True
    assert len((await owner.get("/api/apps")).json()) == 2


async def test_stop_restart_redeploy_and_delete(owner, people, sessionmaker, deployer, queue):
    owner_id, _, _ = people
    app_id = await add_app(sessionmaker, owner_id, "2048")
    resp = await owner.post(f"/api/apps/{app_id}/stop")
    assert resp.status_code == 200 and resp.json()["state"] == "stopped"
    resp = await owner.post(f"/api/apps/{app_id}/restart")
    assert resp.status_code == 200 and resp.json()["state"] == "running"
    resp = await owner.post(f"/api/apps/{app_id}/redeploy")
    assert resp.status_code == 202 and resp.json()["state"] == "building"
    assert queue.deploys == [app_id]
    assert (await owner.delete(f"/api/apps/{app_id}")).status_code == 204
    assert deployer.calls == [("stop", "2048"), ("restart", "2048"), ("delete", "2048")]
    async with sessionmaker() as db:
        p = await db.get(Project, app_id)
    # The project stays, so it can be deployed again; the app is gone from the page.
    assert p is not None and p.app_slug is None and p.live_url is None
    assert (await owner.get("/api/apps")).json() == []


async def test_members_get_three_running_apps(people, sessionmaker, deployer, queue):
    _, member_id, member = people
    for slug in ("a", "b", "c"):
        await add_app(sessionmaker, member_id, f"priya-{slug}")
    fourth = await add_app(sessionmaker, member_id, "priya-d", state="stopped")
    for action in ("restart", "redeploy"):
        resp = await member.post(f"/api/apps/{fourth}/{action}")
        assert resp.status_code == 409 and "3 apps running" in resp.json()["detail"]
    assert queue.deploys == [] and deployer.calls == []
    # Stopping one makes room.
    async with sessionmaker() as db:
        p = await db.scalar(select(Project).where(Project.app_slug == "priya-a"))
    assert (await member.post(f"/api/apps/{p.id}/stop")).status_code == 200
    assert (await member.post(f"/api/apps/{fourth}/restart")).status_code == 200


async def test_the_owner_has_no_limit(owner, people, sessionmaker, deployer):
    owner_id, _, _ = people
    for slug in ("a", "b", "c", "d"):
        await add_app(sessionmaker, owner_id, slug)
    extra = await add_app(sessionmaker, owner_id, "e", state="stopped")
    assert (await owner.post(f"/api/apps/{extra}/restart")).status_code == 200


async def test_internet_access_is_the_owners_call(owner, people, sessionmaker, deployer):
    _, member_id, member = people
    app_id = await add_app(sessionmaker, member_id, "priya-snake")
    assert (await member.patch(f"/api/apps/{app_id}", json={"internet": True})).status_code == 403
    resp = await owner.patch(f"/api/apps/{app_id}", json={"internet": True})
    assert resp.status_code == 200 and resp.json()["internet"] is True


async def test_deployer_errors_are_explained(owner, people, sessionmaker, deployer):
    owner_id, _, _ = people
    app_id = await add_app(sessionmaker, owner_id, "2048")
    deployer.down = True
    resp = await owner.post(f"/api/apps/{app_id}/stop")
    assert resp.status_code == 502 and "isn't reachable" in resp.json()["detail"]


async def test_public_and_private(owner, people, sessionmaker, deployer):
    owner_id, member_id, member = people
    app_id = await add_app(sessionmaker, member_id, "snake-priya")
    # New apps are public: links are for sharing.
    assert (await owner.get("/api/apps")).json()[0]["public"] is True
    resp = await member.post(f"/api/apps/{app_id}/visibility", json={"public": False})
    assert resp.status_code == 200 and resp.json()["public"] is False
    # The same images again, with the sign-in check in front.
    assert deployer.calls == [("deploy", "snake-priya")]
    assert deployer.deploys[0]["build"] is False and deployer.deploys[0]["private"] is True
    resp = await member.post(f"/api/apps/{app_id}/visibility", json={"public": True})
    assert resp.json()["public"] is True and deployer.deploys[1]["private"] is False
    # Only the app's owner (or Nexus's) may change it.
    mine = await add_app(sessionmaker, owner_id, "2048")
    assert (
        await member.post(f"/api/apps/{mine}/visibility", json={"public": False})
    ).status_code == 404
    deployer.down = True
    resp = await owner.post(f"/api/apps/{app_id}/visibility", json={"public": False})
    assert resp.status_code == 502


async def test_delete_frees_the_name(owner, people, sessionmaker, deployer, settings):
    owner_id, _, _ = people
    app_id = await add_app(sessionmaker, owner_id, "2048")
    async with sessionmaker() as db:
        other = Project(user_id=uuid.UUID(owner_id), workbench_id="w-other", name="2048")
        db.add(other)
        await db.commit()
        me = await db.get(User, uuid.UUID(owner_id))
        assert (await app_name(db, settings, other, me)) != "2048"
    assert (await owner.delete(f"/api/apps/{app_id}")).status_code == 204
    async with sessionmaker() as db:
        assert (await app_name(db, settings, other, me)) == "2048"
