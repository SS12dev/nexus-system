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

from nexus_api.deps import (
    CurrentUser,
    DbDep,
    DeployerDep,
    OwnerUser,
    QueueDep,
    RedisDep,
    SettingsDep,
)
from nexus_common import apps
from nexus_common.deployer import DeployerError
from nexus_common.events import Event, publish
from nexus_common.models import Project, RoleName, User, utcnow
from nexus_common.settings import Settings
from nexus_common.trash import add as add_to_trash
from nexus_common.trash import visible

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
    public: bool
    screenshot_file_id: uuid.UUID | None


class InternetIn(BaseModel):
    internet: bool


class VisibilityIn(BaseModel):
    public: bool


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
        public=p.public,
        screenshot_file_id=p.screenshot_file_id,
    )


async def own_app(db: AsyncSession, user: User, app_id: uuid.UUID) -> tuple[Project, User]:
    row = await db.scalar(
        select(Project).where(
            Project.id == app_id,
            Project.app_slug.is_not(None),
            visible("app", Project.id),
            visible("project", Project.id),
        )
    )
    if row is None or (row.user_id != user.id and user.role != RoleName.owner):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "App not found")
    return row, await db.get(User, row.user_id)


async def check_limit(db: AsyncSession, settings: Settings, row: Project, owner: User) -> None:
    """A member's apps running (or being deployed) besides this one must leave room for it."""
    if owner.role == RoleName.owner:
        return
    running = await db.scalar(
        select(func.count()).where(
            Project.user_id == owner.id,
            Project.app_state.in_(LIVE),
            Project.id != row.id,
            visible("app", Project.id),
            visible("project", Project.id),
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


async def announce_project(redis, row: Project) -> None:
    await publish(redis, row.user_id, Event(type="project.updated", data={"id": str(row.id)}))


@router.get("", response_model=list[AppOut])
async def list_apps(user: CurrentUser, db: DbDep, deployer: DeployerDep):
    q = select(Project, User).join(User, User.id == Project.user_id)
    q = q.where(
        Project.app_slug.is_not(None),
        visible("app", Project.id),
        visible("project", Project.id),
    )
    if user.role != RoleName.owner:
        q = q.where(Project.user_id == user.id)
    rows = (await db.execute(q.order_by(Project.updated_at.desc()))).all()
    try:
        live = {a["slug"]: a["state"] for a in await deployer.apps()}
    except DeployerError:
        live = {}
    return [out(p, owner, live) for p, owner in rows]


@router.post("/{app_id}/stop", response_model=AppOut)
async def stop(
    app_id: uuid.UUID, user: CurrentUser, db: DbDep, deployer: DeployerDep, redis: RedisDep
):
    row, owner = await own_app(db, user, app_id)
    await deployer_call(deployer.stop(row.app_slug))
    row.app_state, row.updated_at = "stopped", utcnow()
    await db.commit()
    await announce_project(redis, row)
    return out(row, owner, {})


@router.post("/{app_id}/restart", response_model=AppOut)
async def restart(
    app_id: uuid.UUID,
    user: CurrentUser,
    db: DbDep,
    deployer: DeployerDep,
    settings: SettingsDep,
    redis: RedisDep,
):
    row, owner = await own_app(db, user, app_id)
    await check_limit(db, settings, row, owner)
    await deployer_call(deployer.restart(row.app_slug))
    row.app_state, row.updated_at = "running", utcnow()
    await db.commit()
    await announce_project(redis, row)
    return out(row, owner, {})


@router.post("/{app_id}/redeploy", response_model=AppOut, status_code=status.HTTP_202_ACCEPTED)
async def redeploy(
    app_id: uuid.UUID,
    user: CurrentUser,
    db: DbDep,
    queue: QueueDep,
    settings: SettingsDep,
    redis: RedisDep,
):
    """Build and start the app again from its project folder."""
    row, owner = await own_app(db, user, app_id)
    await check_limit(db, settings, row, owner)
    row.app_state, row.updated_at = "building", utcnow()
    await db.commit()
    await queue.enqueue_deploy(row.id)
    await announce_project(redis, row)
    return out(row, owner, {})


@router.patch("/{app_id}", response_model=AppOut)
async def set_internet(
    app_id: uuid.UUID, body: InternetIn, user: OwnerUser, db: DbDep, redis: RedisDep
):
    """Let an app reach the internet, or not, from its next deploy. Owner only."""
    row, owner = await own_app(db, user, app_id)
    row.internet, row.updated_at = body.internet, utcnow()
    await db.commit()
    await announce_project(redis, row)
    return out(row, owner, {})


@router.post("/{app_id}/visibility", response_model=AppOut)
async def set_visibility(
    app_id: uuid.UUID,
    body: VisibilityIn,
    user: CurrentUser,
    db: DbDep,
    deployer: DeployerDep,
    redis: RedisDep,
):
    """Public: anyone with the link. Private: only its owner and Nexus's owner, after signing
    in. Takes effect at once; the app isn't rebuilt."""
    row, owner = await own_app(db, user, app_id)
    try:
        result = await apps.set_visibility(db, deployer, row, body.public)
    except DeployerError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    if result is not None and not result.ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, result.error or "It didn't restart.")
    await announce_project(redis, row)
    return out(row, owner, {})


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    app_id: uuid.UUID, user: CurrentUser, db: DbDep, deployer: DeployerDep, redis: RedisDep
):
    """Move the app to Trash. It is stopped now and permanently removed after seven days."""
    row, _ = await own_app(db, user, app_id)
    if row.app_state == "building":
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for the app build to finish first.")
    app_state = row.app_state
    if row.app_state == "running":
        await deployer_call(deployer.stop(row.app_slug))
        row.app_state = "stopped"
    row.updated_at = utcnow()
    await add_to_trash(
        db,
        row.user_id,
        "app",
        row.id,
        row.name,
        data={"app_state": app_state},
    )
    await db.commit()
    await announce_project(redis, row)
