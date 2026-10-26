"""Permanently delete expired or explicitly purged Trash items."""

import logging
import uuid

from sqlalchemy import delete, select

from nexus_common import graph
from nexus_common.memory import mark_profile_stale
from nexus_common.models import (
    AgentSystem,
    Chat,
    Entity,
    Episode,
    File,
    Folder,
    Link,
    Memory,
    Profile,
    Project,
    Routine,
    Skill,
    TrashItem,
    utcnow,
)
from nexus_common.routines import remove_routine
from nexus_orchestrator.project_cleanup import delete_project
from nexus_orchestrator.turn import TurnDeps

log = logging.getLogger(__name__)

_ROWS = {
    "chat": Chat,
    "entity": Entity,
    "file": File,
    "folder": Folder,
    "link": Link,
    "memory": Memory,
    "routine": Routine,
    "skill": Skill,
    "system": AgentSystem,
}


async def purge_item(deps: TurnDeps, item_id: uuid.UUID, *, force: bool = False) -> bool:
    """Purge one item only after expiry unless the owner explicitly requested it."""
    async with deps.sessionmaker() as db:
        item = await db.scalar(select(TrashItem).where(TrashItem.id == item_id).with_for_update())
        if item is None or (not force and item.expires_at > utcnow()):
            return False
        kind, entity_id, user_id = item.kind, item.entity_id, item.user_id

    if kind == "project":
        await delete_project(deps, entity_id)
    elif kind == "app":
        async with deps.sessionmaker() as db:
            project = await db.get(Project, entity_id)
            if project is not None:
                if project.app_slug:
                    if deps.deployer is None:
                        raise RuntimeError("App deployer is not configured.")
                    await deps.deployer.delete(project.app_slug)
                project.app_slug = project.app_state = project.live_url = None
                project.deployed_at = project.deploy_error = None
                await db.commit()
    elif kind == "memories_all":
        async with deps.sessionmaker() as db:
            await db.execute(delete(Entity).where(Entity.user_id == user_id))
            await db.execute(delete(Memory).where(Memory.user_id == user_id))
            await db.execute(delete(Episode).where(Episode.user_id == user_id))
            profile = await db.get(Profile, user_id)
            if profile is not None:
                profile.content, profile.built_at, profile.stale = "", None, False
            await db.commit()
    elif kind in ("file", "folder"):
        async with deps.sessionmaker() as db:
            if kind == "file":
                files = list(
                    (
                        await db.scalars(
                            select(File).where(File.id == entity_id, File.user_id == user_id)
                        )
                    ).all()
                )
            else:
                files = list(
                    (
                        await db.scalars(
                            select(File).where(File.folder_id == entity_id, File.user_id == user_id)
                        )
                    ).all()
                )
            if files and deps.storage is None:
                raise RuntimeError("File storage is not configured; Trash item was not purged.")
            if deps.storage is not None:
                for file in files:
                    await deps.storage.delete(file.storage_key)
            for file in files:
                await db.delete(file)
            if kind == "folder":
                folder = await db.get(Folder, entity_id)
                if folder is not None:
                    await db.delete(folder)
            await db.execute(
                delete(TrashItem).where(
                    TrashItem.user_id == user_id,
                    TrashItem.parent_id == item_id,
                )
            )
            await db.commit()
    else:
        async with deps.sessionmaker() as db:
            row_type = _ROWS.get(kind)
            if row_type is None:
                raise ValueError(f"Unsupported Trash item kind: {kind}")
            row = await db.get(row_type, entity_id)
            if kind == "routine" and row is not None:
                await remove_routine(db, row)
            elif row is not None:
                await db.delete(row)
            if kind == "memory":
                await graph.prune(db, user_id)
                await mark_profile_stale(db, user_id)
            elif kind == "entity":
                await graph.prune(db, user_id)
            await db.commit()

    async with deps.sessionmaker() as db:
        await db.execute(
            delete(TrashItem).where(
                TrashItem.id == item_id,
                TrashItem.user_id == user_id,
            )
        )
        await db.commit()
    log.info(
        "trash.purged",
        extra={"trash_item_id": str(item_id), "kind": kind, "entity_id": str(entity_id)},
    )
    return True


async def enqueue_expired(deps: TurnDeps) -> int:
    """Queue expired root entries. Idempotent job IDs make overlapping workers safe."""
    async with deps.sessionmaker() as db:
        ids = list(
            (
                await db.scalars(
                    select(TrashItem.id).where(
                        TrashItem.parent_id.is_(None), TrashItem.expires_at <= utcnow()
                    )
                )
            ).all()
        )
    queued = 0
    for item_id in ids:
        await deps.enqueue(
            "purge_trash_item",
            str(item_id),
            False,
            _job_id=f"trash-purge:{item_id}",
        )
        queued += 1
    return queued
