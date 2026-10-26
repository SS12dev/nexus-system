"""Software-project deletion is authorized and queued with repository safety checks."""

import uuid

from nexus_common.models import Project


async def add_project(sessionmaker, user_id: str, **changes) -> uuid.UUID:
    async with sessionmaker() as db:
        project = Project(
            user_id=uuid.UUID(user_id),
            workbench_id="workbench-project",
            name="Project",
            **changes,
        )
        db.add(project)
        await db.commit()
        return project.id


async def test_delete_queues_managed_project_cleanup(owner, sessionmaker, queue):
    user_id = (await owner.get("/api/me")).json()["id"]
    project_id = await add_project(sessionmaker, user_id)

    response = await owner.delete(f"/api/projects/{project_id}")

    assert response.status_code == 202
    assert response.json() == {"queued": True}
    assert queue.project_deletes == [project_id]
    async with sessionmaker() as db:
        assert await db.get(Project, project_id) is not None


async def test_delete_rejects_another_users_project(owner, client_for, invite, sessionmaker):
    user_id = (await owner.get("/api/me")).json()["id"]
    await invite(owner, "member@example.com")
    member = await client_for("member@example.com")
    project_id = await add_project(sessionmaker, user_id)

    assert (await member.delete(f"/api/projects/{project_id}")).status_code == 404


async def test_delete_requires_managed_github_repository(owner, sessionmaker, settings, queue):
    user_id = (await owner.get("/api/me")).json()["id"]
    project_id = await add_project(
        sessionmaker,
        user_id,
        repo_owner="outside-org",
        repo_name="remote-project",
        repo_url="https://github.com/outside-org/remote-project",
    )
    settings.github_apps_org = "nexus-org"
    settings.github_app_id = "12345"
    settings.github_app_installation_id = "678"
    settings.github_app_private_key_b64 = "test-key"

    response = await owner.delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert "outside Nexus's managed" in response.json()["detail"]
    assert queue.project_deletes == []


async def test_delete_waits_for_active_runs(owner, sessionmaker, queue):
    user_id = (await owner.get("/api/me")).json()["id"]
    project_id = await add_project(sessionmaker, user_id, app_state="building")

    response = await owner.delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert queue.project_deletes == []
