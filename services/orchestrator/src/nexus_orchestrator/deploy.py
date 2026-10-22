"""Deploying a software project as a running app: at the end of a dev team's run (dev.py) and
for the Apps page's redeploy (the deploy_app job). The deployer builds and starts it; this keeps
the project's row up to date, enforces how many apps a member may run, and saves the screenshot
so it comes with the team's result."""

import base64
import binascii
import logging
import uuid

from sqlalchemy import func, select

from nexus_common.apps import app_name, deploy_args
from nexus_common.deployer import DeployerError, DeployResult
from nexus_common.hostnames import NoFreeName
from nexus_common.models import Project, RoleName, User, utcnow
from nexus_orchestrator.artifacts import save_generated
from nexus_orchestrator.links import keep_release_link

log = logging.getLogger(__name__)


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
        try:
            row.app_slug = await app_name(db, deps.settings, row, user)
        except NoFreeName as exc:
            return DeployResult(ok=False, url="", error=str(exc))
        row.app_state = "building"
        await db.commit()
        slug, args = row.app_slug, deploy_args(row, user)
    try:
        result = await deps.deployer.deploy(slug, **args)
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
        name = row.name
    if result.ok:
        await keep_release_link(
            deps,
            row.user_id,
            result.url,
            title=f"{name} (live app)",
            note=f"{name}, running.",
            kind="app",
            chat_id=chat_id,
            run_id=run_id,
            project_id=project_id,
        )
    if result.ok and result.screenshot and deps.storage is not None:
        try:
            shot = await save_generated(
                deps,
                row.user_id,
                f"{slug}-screenshot.png",
                base64.b64decode(result.screenshot),
                chat_id=chat_id,
                run_id=run_id,
            )
        except (binascii.Error, ValueError):
            log.warning("Bad screenshot for %s", slug)
        else:
            async with deps.sessionmaker() as db:
                row = await db.get(Project, project_id)
                row.screenshot_file_id = shot.id
                await db.commit()
    return result
