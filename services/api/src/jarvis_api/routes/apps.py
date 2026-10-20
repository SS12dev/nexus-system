"""The Apps page: the running apps software teams built. The owner sees and manages everyone's;
a member sees and manages only their own, and may have MEMBER_APP_LIMIT running at once (the
owner has no limit). Stop, restart and delete go straight to the deployer; a redeploy is a job
for the orchestrator, since it builds."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_api.deps import CurrentUser, DbDep, DeployerDep, OwnerUser, QueueDep, SettingsDep
from jarvis_common.deployer import DeployerError
from jarvis_common.models import Project, RoleName, User, utcnow
from jarvis_common.settings import Settings

router = APIRouter(prefix="/api/apps", tags=["apps"])

LIVE = ("running", "building")


class AppOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str | None
    owner_id: uuid.UUID
    owner: str
    state: str | None
    live_url: str | None
    repo_url: str | None
    stack: str | None
    deployed_at: datetime | None
    deploy_error: str | None
    internet: bool


class InternetIn(BaseModel):
    internet: bool


def out(p: Project, owner: User, live: dict[str, str]) -> AppOut:
    state = p.app_state
    # The containers' own state wins, unless a deploy is under way.
    if state != "building" and p.app_slug in live:
        state = live[p.app_slug]
    return AppOut(
        id=p.id,
        name=p.name,
        slug=p.app_slug,
        owner_id=owner.id,
        owner=owner.name or owner.email,
        state=state,
        live_url=p.live_url,
        repo_url=p.repo_url,
        stack=p.stack,
        deployed_at=p.deployed_at,
        deploy_error=p.deploy_error,
        internet=p.internet,
    )


async def own_app(db: AsyncSession, user: User, app_id: uuid.UUID) -> tuple[Project, User]:
    row = await db.get(Project, app_id)
    if (
        row is None
        or row.app_slug is None
        or (row.user_id != user.id and user.role != RoleName.owner)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "App not found")
    return row, await db.get(User, row.user_id)


async def check_limit(db: AsyncSession, settings: Settings, row: Project, owner: User) -> None:
    """A member's apps running (or being deployed) besides this one must leave room for it."""
    if owner.role == RoleName.owner:
        return
    running = await db.scalar(
        select(func.count()).where(
            Project.user_id == owner.id, Project.app_state.in_(LIVE), Project.id != row.id
        )
    )
    if running >= settings.member_app_limit:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Members can have {settings.member_app_limit} apps running at once; stop one first.",
        )


async def deployer_call(call) -> None:
    try:
        await call
    except DeployerError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("", response_model=list[AppOut])
async def list_apps(user: CurrentUser, db: DbDep, deployer: DeployerDep):
    q = select(Project, User).join(User, User.id == Project.user_id)
    q = q.where(Project.app_slug.is_not(None))
    if user.role != RoleName.owner:
        q = q.where(Project.user_id == user.id)
    rows = (await db.execute(q.order_by(Project.updated_at.desc()))).all()
    try:
        live = {a["slug"]: a["state"] for a in await deployer.apps()}
    except DeployerError:
        live = {}
    return [out(p, owner, live) for p, owner in rows]


@router.post("/{app_id}/stop", response_model=AppOut)
async def stop(app_id: uuid.UUID, user: CurrentUser, db: DbDep, deployer: DeployerDep):
    row, owner = await own_app(db, user, app_id)
    await deployer_call(deployer.stop(row.app_slug))
    row.app_state, row.updated_at = "stopped", utcnow()
    await db.commit()
    return out(row, owner, {})


@router.post("/{app_id}/restart", response_model=AppOut)
async def restart(
    app_id: uuid.UUID, user: CurrentUser, db: DbDep, deployer: DeployerDep, settings: SettingsDep
):
    row, owner = await own_app(db, user, app_id)
    await check_limit(db, settings, row, owner)
    await deployer_call(deployer.restart(row.app_slug))
    row.app_state, row.updated_at = "running", utcnow()
    await db.commit()
    return out(row, owner, {})


@router.post("/{app_id}/redeploy", response_model=AppOut, status_code=status.HTTP_202_ACCEPTED)
async def redeploy(
    app_id: uuid.UUID, user: CurrentUser, db: DbDep, queue: QueueDep, settings: SettingsDep
):
    """Build and start the app again from its project folder."""
    row, owner = await own_app(db, user, app_id)
    await check_limit(db, settings, row, owner)
    row.app_state, row.updated_at = "building", utcnow()
    await db.commit()
    await queue.enqueue_deploy(row.id)
    return out(row, owner, {})


@router.patch("/{app_id}", response_model=AppOut)
async def set_internet(app_id: uuid.UUID, body: InternetIn, user: OwnerUser, db: DbDep):
    """Let an app reach the internet, or not, from its next deploy. Owner only."""
    row, owner = await own_app(db, user, app_id)
    row.internet, row.updated_at = body.internet, utcnow()
    await db.commit()
    return out(row, owner, {})


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(app_id: uuid.UUID, user: CurrentUser, db: DbDep, deployer: DeployerDep):
    """Remove the app's containers and image. The project, its folder and repo stay, so it
    can be deployed again."""
    row, _ = await own_app(db, user, app_id)
    await deployer_call(deployer.delete(row.app_slug))
    row.app_state = row.live_url = row.deployed_at = row.deploy_error = None
    row.app_slug, row.updated_at = None, utcnow()
    await db.commit()
