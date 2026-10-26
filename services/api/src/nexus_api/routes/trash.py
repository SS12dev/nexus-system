"""List, restore and permanently remove items held for seven days."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from nexus_api.deps import CurrentUser, DbDep, DeployerDep, MemberUser, QueueDep, RedisDep
from nexus_api.schemas import TrashItemOut
from nexus_common.deployer import DeployerError
from nexus_common.events import Event, publish
from nexus_common.models import AuditLog, Profile, Project, Routine, TrashItem, User, utcnow
from nexus_common.routines import reschedule

router = APIRouter(prefix="/api/trash", tags=["trash"])


async def own_item(db, user: User, item_id: uuid.UUID) -> TrashItem:
    item = await db.scalar(
        select(TrashItem)
        .where(TrashItem.id == item_id, TrashItem.user_id == user.id, TrashItem.parent_id.is_(None))
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trash item not found")
    return item


@router.get("", response_model=list[TrashItemOut])
async def list_trash(user: CurrentUser, db: DbDep):
    rows = await db.scalars(
        select(TrashItem)
        .where(TrashItem.user_id == user.id, TrashItem.parent_id.is_(None))
        .order_by(TrashItem.expires_at, TrashItem.created_at.desc())
        .limit(500)
    )
    return rows.all()


@router.post("/{item_id}/restore", response_model=TrashItemOut)
async def restore(
    item_id: uuid.UUID,
    user: MemberUser,
    db: DbDep,
    deployer: DeployerDep,
    redis: RedisDep,
):
    item = await own_item(db, user, item_id)
    if item.expires_at <= utcnow():
        raise HTTPException(
            status.HTTP_410_GONE, "This item has expired and is being permanently removed."
        )

    if item.kind in ("app", "project"):
        project = await db.get(Project, item.entity_id)
        if project is not None and item.data.get("app_state") == "running":
            if item.kind == "project" and await db.scalar(
                select(TrashItem.id).where(
                    TrashItem.kind == "app", TrashItem.entity_id == item.entity_id
                )
            ):
                project = None
        if project is not None and item.data.get("app_state") == "running":
            if not project.app_slug:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "This app no longer has a deployed version to restore.",
                )
            try:
                await deployer.restart(project.app_slug)
            except DeployerError as exc:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
            project.app_state = "running"
    if item.kind == "routine":
        routine = await db.get(Routine, item.entity_id)
        if routine is not None:
            routine.enabled = bool(item.data.get("enabled", False))
            if routine.enabled:
                reschedule(routine, utcnow())
    if item.kind == "memory":
        from nexus_common.memory import mark_profile_stale

        await mark_profile_stale(db, user.id)
    if item.kind == "memories_all":
        profile = await db.get(Profile, user.id)
        if profile is not None:
            profile.content = item.data.get("content", "")
            profile.built_at = (
                datetime.fromisoformat(item.data["built_at"]) if item.data.get("built_at") else None
            )
            profile.stale = bool(item.data.get("stale", False))
            profile.memory_enabled = bool(item.data.get("memory_enabled", True))
    kind = item.kind

    children = await db.scalars(
        select(TrashItem).where(TrashItem.user_id == user.id, TrashItem.parent_id == item.id)
    )
    for child in children:
        await db.delete(child)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="trash.restored",
            target=str(item.entity_id),
            data={"kind": item.kind},
        )
    )
    out = TrashItemOut.model_validate(item)
    await db.delete(item)
    await db.commit()
    await publish(
        redis,
        user.id,
        Event(type="trash.changed", data={"kind": kind}),
    )
    return out


@router.delete("/{item_id}", status_code=status.HTTP_202_ACCEPTED)
async def permanently_delete(
    item_id: uuid.UUID, user: MemberUser, db: DbDep, queue: QueueDep
) -> dict[str, bool]:
    item = await own_item(db, user, item_id)
    try:
        await queue.enqueue_trash_purge(item.id)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Permanent deletion could not be queued. The item is still in Trash.",
        ) from exc
    db.add(
        AuditLog(
            actor_id=user.id,
            action="trash.purge_requested",
            target=str(item.entity_id),
            data={"kind": item.kind},
        )
    )
    await db.commit()
    return {"queued": True}
