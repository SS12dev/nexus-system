"""The bell on the main screen: notices from finished, failed and waiting teams
(nexus_common/notices.py), newest first, and marking them read."""

import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlalchemy import func, select, update

from nexus_api.deps import CurrentUser, DbDep, RedisDep
from nexus_common.events import Event, publish
from nexus_common.models import File, Link, Notification, utcnow
from nexus_common.notices import TRAY_SIZE, notification_payload

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class ReadIn(BaseModel):
    # None marks every notice read.
    ids: list[uuid.UUID] | None = None


@router.get("")
async def tray(user: CurrentUser, db: DbDep) -> dict:
    rows = (
        await db.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(TRAY_SIZE)
        )
    ).all()
    file_ids = {n.file_id for n in rows if n.file_id}
    link_ids = {n.link_id for n in rows if n.link_id}
    files = {f.id: f for f in (await db.scalars(select(File).where(File.id.in_(file_ids)))).all()}
    links = {x.id: x for x in (await db.scalars(select(Link).where(Link.id.in_(link_ids)))).all()}
    unread = await db.scalar(
        select(func.count()).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return {
        "items": [
            notification_payload(n, files.get(n.file_id), links.get(n.link_id)) for n in rows
        ],
        "unread": unread or 0,
    }


@router.post("/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(body: ReadIn, user: CurrentUser, db: DbDep, redis: RedisDep) -> None:
    """Mark notices read here and on the person's other open tabs."""
    q = update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if body.ids is not None:
        q = q.where(Notification.id.in_(body.ids))
    await db.execute(q.values(read_at=utcnow()))
    await db.commit()
    ids = [str(i) for i in body.ids] if body.ids is not None else None
    await publish(redis, user.id, Event(type="notification.read", data={"ids": ids}))
