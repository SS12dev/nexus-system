"""Shared helpers for seven-day, user-scoped deletion retention."""

import uuid
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import Folder, TrashItem, utcnow

RETENTION_DAYS = 7


def visible(kind: str, entity_id, user_id: uuid.UUID | None = None):
    """SQL predicate excluding an item that is currently in Trash."""
    conditions = [
        (TrashItem.kind == kind) & (TrashItem.entity_id == entity_id),
    ]
    if kind == "memory" and user_id is not None:
        conditions.append((TrashItem.kind == "memories_all") & (TrashItem.user_id == user_id))
    return ~select(TrashItem.id).where(or_(*conditions)).exists()


def visible_file(file_id, folder_id):
    """Hide direct Trash entries and files grouped under a trashed project."""
    return (
        visible("file", file_id)
        & visible("folder", folder_id)
        & ~select(TrashItem.id)
        .join(Folder, Folder.project_id == TrashItem.entity_id)
        .where(TrashItem.kind == "project", Folder.id == folder_id)
        .exists()
    )


def visible_episode(episode_id, chat_id, user_id: uuid.UUID):
    """Hide chat summaries while either the chat or the whole memory archive is in Trash."""
    return (
        visible("episode", episode_id, user_id)
        & visible("chat", chat_id)
        & ~select(TrashItem.id)
        .where(TrashItem.kind == "memories_all", TrashItem.user_id == user_id)
        .exists()
    )


async def add(
    db: AsyncSession,
    user_id: uuid.UUID,
    kind: str,
    entity_id: uuid.UUID,
    title: str,
    *,
    parent_id: uuid.UUID | None = None,
    data: dict | None = None,
) -> TrashItem:
    existing = await db.scalar(
        select(TrashItem).where(TrashItem.kind == kind, TrashItem.entity_id == entity_id)
    )
    if existing is not None:
        return existing
    now = utcnow()
    item = TrashItem(
        user_id=user_id,
        kind=kind,
        entity_id=entity_id,
        parent_id=parent_id,
        title=title[:255],
        data=data or {},
        created_at=now,
        expires_at=now + timedelta(days=RETENTION_DAYS),
    )
    db.add(item)
    return item
