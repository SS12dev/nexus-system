"""Deploying a software project as a running app: at the end of a dev team's run (dev.py) and
for the Apps page's redeploy (the deploy_app job). The deployer builds and starts it; this keeps
the project's row up to date, enforces how many apps a member may run, and saves the screenshot
so it comes with the team's result."""

import base64
import binascii
import logging
import uuid

from sqlalchemy import func, select

from jarvis_common.deployer import DeployerError, DeployResult
from jarvis_common.models import Project, RoleName, User, utcnow
from jarvis_orchestrator.artifacts import save_generated
from jarvis_orchestrator.systems.release import base_repo_name

log = logging.getLogger(__name__)

SLUG_TRIES = 20


async def _slug(db, row: Project, user: User) -> str:
    """The app's name in its URL: its repo's name if it has one, else one made the same way;
    -2, -3... when another project has it."""
    if row.app_slug:
        return row.app_slug
    base = (row.repo_name or base_repo_name(user, row.name))[:36].strip("-") or "app"
    for n in range(1, SLUG_TRIES + 1):
        slug = base if n == 1 else f"{base}-{n}"
        taken = await db.scalar(
            select(Project.id).where(Project.app_slug == slug, Project.id != row.id)
        )
        if taken is None:
            return slug
    return f"{base[:27]}-{uuid.uuid4().hex[:8]}"


async def deploy_project(
    deps, project_id: uuid.UUID, *, run_id: uuid.UUID | None = None, chat_id=None
) -> DeployResult:
    async with deps.sessionmaker() as db:
        row = await db.get(Project, project_id)
        user = await db.get(User, row.user_id)
        if user.role != RoleName.owner:
            running = await db.scalar(
                select(func.count()).where(
                    Project.user_id == user.id,
                    Project.app_state.in_(("running", "building")),
                    Project.id != row.id,
                )
            )
            limit = deps.settings.member_app_limit
            if running >= limit:
                error = (
                    f"{user.name or user.email} already has {limit} apps running; stop one on "
                    "the Apps page first."
                )
                row.deploy_error = error
                await db.commit()
                return DeployResult(ok=False, url=row.live_url or "", error=error)
        row.app_slug = await _slug(db, row, user)
        row.app_state = "building"
        await db.commit()
        slug, internet = row.app_slug, row.internet
        args = {
            "app_id": str(row.id),
            "owner": str(user.id),
            "user": str(user.id),
            "project": row.workbench_id,
            "stack": row.stack,
        }
    try:
        result = await deps.deployer.deploy(slug, internet=internet, **args)
    except DeployerError as exc:
        result = DeployResult(ok=False, url="", error=str(exc))
    if not result.ok:
        try:
            # Don't leave a broken app restarting over and over.
            await deps.deployer.stop(slug)
        except DeployerError:
            pass
    async with deps.sessionmaker() as db:
        row = await db.get(Project, project_id)
        row.app_state = "running" if result.ok else "failed"
        row.deploy_error = None if result.ok else result.error
        if result.ok:
            row.live_url, row.deployed_at = result.url, utcnow()
        row.updated_at = utcnow()
        await db.commit()
    if result.ok and result.screenshot and deps.storage is not None:
        try:
            await save_generated(
                deps,
                row.user_id,
                f"{slug}-screenshot.png",
                base64.b64decode(result.screenshot),
                chat_id=chat_id,
                run_id=run_id,
            )
        except (binascii.Error, ValueError):
            log.warning("Bad screenshot for %s", slug)
    return result
