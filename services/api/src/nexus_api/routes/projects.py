"""Software projects the dev teams built, for the Library's project cards. Each belongs to one
user; the code itself lives in the workbench and the project's GitHub repo."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from nexus_api.deps import CurrentUser, DbDep, MemberUser, QueueDep, RedisDep, SettingsDep
from nexus_api.schemas import ProjectOut
from nexus_common.events import Event, publish
from nexus_common.models import AgentRun, Project, RoleName, RunStatus

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(user: CurrentUser, db: DbDep):
    rows = await db.scalars(
        select(Project).where(Project.user_id == user.id).order_by(Project.updated_at.desc())
    )
    return rows.all()


@router.delete("/{project_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_project(
    project_id: uuid.UUID,
    user: MemberUser,
    db: DbDep,
    queue: QueueDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> dict[str, bool]:
    """Queue a permanent deletion of the software project and its managed resources."""
    project = await db.get(Project, project_id)
    if project is None or (project.user_id != user.id and user.role != RoleName.owner):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if project.app_state == "building":
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for the app build to finish first.")
    if project.run_id:
        run = await db.get(AgentRun, project.run_id)
        if run and run.status in {
            RunStatus.queued,
            RunStatus.running,
            RunStatus.awaiting_approval,
            RunStatus.paused,
        }:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cancel or finish the active team run before deleting this project.",
            )
    if (project.repo_url or project.repo_name) and not project.transferred_to:
        if not project.repo_name:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "The repository name is missing, so Nexus cannot safely delete "
                "the GitHub repository.",
            )
        if not settings.github_enabled:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "GitHub App integration is not configured; the repository was not deleted.",
            )
        if (
            project.repo_owner
            and project.repo_owner.casefold() != settings.github_apps_org.casefold()
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This repository is outside Nexus's managed GitHub organization "
                "and was not deleted.",
            )
    await queue.enqueue_project_delete(project.id)
    await publish(
        redis,
        project.user_id,
        Event(type="project.delete_requested", data={"id": str(project.id)}),
    )
    return {"queued": True}
