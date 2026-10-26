"""Project deletion removes managed resources and keeps the Library intact on failure."""

import pytest

from nexus_common.models import File, FileStatus, Folder, Link, Project
from nexus_orchestrator.project_cleanup import delete_project


class FakeWorkbench:
    def __init__(self, fail=False):
        self.deleted = []
        self.fail = fail

    async def delete_project(self, user, project):
        if self.fail:
            raise RuntimeError("workbench unavailable")
        self.deleted.append((user, project))


class FakeGitHub:
    org = "nexus-org"

    def __init__(self):
        self.deleted = []

    async def delete_repo(self, name, owner):
        self.deleted.append((owner, name))


class FakeDeployer:
    def __init__(self):
        self.deleted = []

    async def delete(self, slug):
        self.deleted.append(slug)


async def test_delete_project_cleans_repo_workspace_app_and_library(deps, owner, storage):
    user, _ = owner
    project_id = None
    async with deps.sessionmaker() as db:
        project = Project(
            user_id=user.id,
            workbench_id="workbench-project",
            name="Project",
            repo_owner="nexus-org",
            repo_name="project-repo",
            repo_url="https://github.com/nexus-org/project-repo",
            app_slug="project-app",
            app_state="running",
        )
        db.add(project)
        await db.flush()
        folder = Folder(user_id=user.id, project_id=project.id, name="Project files")
        file = File(
            user_id=user.id,
            folder_id=None,
            name="notes.txt",
            content_type="text/plain",
            size=1,
            sha256="a" * 64,
            storage_key="project-file",
            status=FileStatus.ready,
        )
        db.add_all([folder, file])
        await db.flush()
        file.folder_id = folder.id
        link = Link(
            user_id=user.id,
            project_id=project.id,
            url="https://github.com/nexus-org/project-repo",
            kind="github_repo",
            title="Project",
        )
        db.add(link)
        project_id = project.id
        await db.commit()

    storage.objects["project-file"] = b"project"
    deps.storage = storage
    deps.workbench = FakeWorkbench()
    deps.github = FakeGitHub()
    deps.deployer = FakeDeployer()

    await delete_project(deps, project_id)

    assert deps.workbench.deleted == [(str(user.id), "workbench-project")]
    assert deps.github.deleted == [("nexus-org", "project-repo")]
    assert deps.deployer.deleted == ["project-app"]
    assert "project-file" not in storage.objects
    async with deps.sessionmaker() as db:
        assert await db.get(Project, project_id) is None
        assert await db.get(Folder, folder.id) is None
        assert await db.get(File, file.id) is None
        assert await db.get(Link, link.id) is None


async def test_transferred_repository_is_preserved(deps, owner):
    user, _ = owner
    async with deps.sessionmaker() as db:
        project = Project(
            user_id=user.id,
            workbench_id="workbench-project",
            name="Project",
            repo_owner="personal-account",
            repo_name="project-repo",
            repo_url="https://github.com/personal-account/project-repo",
            transferred_to="personal-account",
        )
        db.add(project)
        await db.commit()
        project_id = project.id

    deps.workbench = FakeWorkbench()
    deps.github = FakeGitHub()
    deps.deployer = FakeDeployer()

    await delete_project(deps, project_id)

    assert deps.github.deleted == []
    async with deps.sessionmaker() as db:
        assert await db.get(Project, project_id) is None


async def test_failed_workspace_cleanup_keeps_project_record(deps, owner):
    user, _ = owner
    async with deps.sessionmaker() as db:
        project = Project(user_id=user.id, workbench_id="workbench-project", name="Project")
        db.add(project)
        await db.commit()
        project_id = project.id

    deps.workbench = FakeWorkbench(fail=True)
    deps.github = FakeGitHub()
    deps.deployer = FakeDeployer()

    with pytest.raises(RuntimeError, match="workbench unavailable"):
        await delete_project(deps, project_id)

    async with deps.sessionmaker() as db:
        assert await db.get(Project, project_id) is not None
