"""Software projects the dev teams built, for the Library's project cards. Each belongs to one
user; the code itself lives in the workbench and the project's GitHub repo."""

from fastapi import APIRouter
from sqlalchemy import select

from nexus_api.deps import CurrentUser, DbDep
from nexus_api.schemas import ProjectOut
from nexus_common.models import Project

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(user: CurrentUser, db: DbDep):
    rows = await db.scalars(
        select(Project).where(Project.user_id == user.id).order_by(Project.updated_at.desc())
    )
    return rows.all()
