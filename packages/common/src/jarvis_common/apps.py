"""Running apps, shared by the orchestrator (deploys) and the API (the Apps page): naming,
what a deploy is asked for, and making an app public or private."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_common.deployer import DeployerClient, DeployResult
from jarvis_common.hostnames import base_name, pick
from jarvis_common.models import Project, RoleName, User
from jarvis_common.settings import Settings


def username(user: User) -> str | None:
    """How a person shows in their apps' names; None for the owner, whose apps go without."""
    if user.role == RoleName.owner:
        return None
    return (user.name or "").split(" ")[0] or user.email.split("@")[0]


async def app_name(db: AsyncSession, settings: Settings, row: Project, user: User) -> str:
    """The app's hostname label, picked once and kept across redeploys."""
    if row.app_slug:
        return row.app_slug

    async def taken(name: str) -> bool:
        other = await db.scalar(
            select(Project.id).where(Project.app_slug == name, Project.id != row.id)
        )
        return other is not None

    base = base_name(row.name, username(user))
    return await pick(base, taken, settings.reserved_hostname_set)


def host(settings: Settings, slug: str) -> str:
    return f"{slug}.{settings.apps_domain}"


def deploy_args(row: Project, user: User) -> dict:
    return {
        "app_id": str(row.id),
        "owner": str(user.id),
        "user": str(user.id),
        "project": row.workbench_id,
        "stack": row.stack,
        "internet": row.internet,
        "private": not row.public,
    }


async def set_visibility(
    db: AsyncSession, deployer: DeployerClient, row: Project, public: bool
) -> DeployResult | None:
    """Make an app public (anyone with the link) or private (its owner and Jarvis's owner,
    after signing in). A running app gets its containers again from the same images, with or
    without the sign-in check in front; nothing is rebuilt."""
    row.public = public
    await db.commit()
    if not row.app_slug or row.app_state not in ("running", "stopped", "degraded"):
        return None
    user = await db.get(User, row.user_id)
    return await deployer.deploy(row.app_slug, build=False, **deploy_args(row, user))
