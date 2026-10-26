"""Delete software projects and their managed external resources."""

import uuid

from sqlalchemy import or_, select

from nexus_common.events import Event, publish
from nexus_common.models import AgentRun, File, Folder, Link, Project, RunStatus
from nexus_orchestrator.turn import TurnDeps

FAILURE_MESSAGE = (
    "Project deletion failed. The project is still listed; try again or contact the Nexus owner."
)


async def delete_project(deps: TurnDeps, project_id: uuid.UUID) -> None:
    async with deps.sessionmaker() as db:
        project = await db.get(Project, project_id)
        if project is None:
            return

        project_id = project.id
        user_id = project.user_id
        owner_id = str(user_id)
        workbench_id = project.workbench_id
        app_slug = project.app_slug
        repo_name = project.repo_name
        repo_owner = project.repo_owner
        transferred_to = project.transferred_to
        repo_url = project.repo_url
        screenshot_id = project.screenshot_file_id
        app_state = project.app_state
        run_id = project.run_id

        folders = await db.scalars(select(Folder).where(Folder.project_id == project_id))
        folder_rows = folders.all()
        folder_ids = [folder.id for folder in folder_rows]
        file_query = select(File).where(
            File.user_id == user_id,
            or_(
                File.folder_id.in_(folder_ids) if folder_ids else False,
                File.id == screenshot_id if screenshot_id else False,
            ),
        )
        files = (await db.scalars(file_query)).all()
        link_rows = await db.scalars(select(Link).where(Link.project_id == project_id))
        link_ids = [link.id for link in link_rows.all()]
        file_ids = [file.id for file in files]
        file_keys = [file.storage_key for file in files]

        active = app_state == "building"
        if run_id and not active:
            run = await db.get(AgentRun, run_id)
            active = bool(
                run
                and run.status
                in {
                    RunStatus.queued,
                    RunStatus.running,
                    RunStatus.awaiting_approval,
                    RunStatus.paused,
                }
            )

    try:
        if active:
            raise RuntimeError("The project still has an active build or team run.")
        if app_slug:
            if deps.deployer is None:
                raise RuntimeError("The app deployer is not configured.")
            await deps.deployer.delete(app_slug)

        if workbench_id:
            if deps.workbench is None:
                raise RuntimeError("The project workbench is not configured.")
            await deps.workbench.delete_project(owner_id, workbench_id)

        if (repo_url or repo_name) and not transferred_to:
            if not repo_name:
                raise RuntimeError("The managed repository name is missing.")
            if deps.github is None:
                raise RuntimeError("The GitHub App is not configured.")
            owner = repo_owner or deps.github.org
            if owner.casefold() != deps.github.org.casefold():
                raise RuntimeError("The repository is outside the managed GitHub organization.")
            await deps.github.delete_repo(repo_name, owner)

        if file_keys and deps.storage is None:
            raise RuntimeError("File storage is not configured.")
        if deps.storage is not None:
            for key in file_keys:
                await deps.storage.delete(key)

        async with deps.sessionmaker() as db:
            project = await db.get(Project, project_id)
            if project is None:
                return
            for link_id in link_ids:
                link = await db.get(Link, link_id)
                if link is not None:
                    await db.delete(link)
            for file_id in file_ids:
                file = await db.get(File, file_id)
                if file is not None:
                    await db.delete(file)
            for folder_id in folder_ids:
                folder = await db.get(Folder, folder_id)
                if folder is not None:
                    await db.delete(folder)
            await db.delete(project)
            await db.commit()
    except Exception:
        await publish(
            deps.redis,
            user_id,
            Event(
                type="project.delete_failed",
                data={"id": str(project_id), "error": FAILURE_MESSAGE},
            ),
        )
        raise

    for file_id in file_ids:
        await publish(deps.redis, user_id, Event(type="file.deleted", data={"id": str(file_id)}))
    for folder_id in folder_ids:
        await publish(
            deps.redis, user_id, Event(type="folder.deleted", data={"id": str(folder_id)})
        )
    for link_id in link_ids:
        await publish(deps.redis, user_id, Event(type="link.deleted", data={"id": str(link_id)}))
    await publish(deps.redis, user_id, Event(type="project.deleted", data={"id": str(project_id)}))
