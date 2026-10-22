"""Links in the library: pointers to things that live elsewhere (a project's GitHub repo, a
file or pull request in it, a Google Doc, a running app, a web page). Every link belongs to one
user; other users get a 404. Previews are fetched by the orchestrator, the only service holding
the GitHub App's key, so links to Jarvis's private repos preview too."""

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_api.deps import CurrentUser, DbDep, MemberUser, QueueDep, RedisDep
from jarvis_api.routes.chats import own_chat
from jarvis_api.schemas import LinkIn, LinkOut, LinkUpdateIn
from jarvis_common.events import Event, publish
from jarvis_common.links import GITHUB_KINDS, LinkError, announce, save_link
from jarvis_common.models import Link, User, utcnow

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/links", tags=["links"])


async def own_link(db: AsyncSession, user: User, link_id: uuid.UUID) -> Link:
    link = await db.scalar(select(Link).where(Link.id == link_id, Link.user_id == user.id))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    return link


async def queue_preview(queue, link: Link) -> None:
    if link.kind not in GITHUB_KINDS:
        return
    try:
        await queue.enqueue_link_preview(link.id)
    except Exception:
        log.exception("Could not queue the preview of link %s", link.id)


@router.get("", response_model=list[LinkOut])
async def list_links(
    user: CurrentUser,
    db: DbDep,
    chat_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, le=1000),
):
    query = select(Link).where(Link.user_id == user.id)
    if chat_id is not None:
        query = query.where(Link.chat_id == chat_id)
    if project_id is not None:
        query = query.where(Link.project_id == project_id)
    for word in (q or "").split()[:6]:
        pattern = f"%{word.replace('%', '').replace('_', '')}%"
        query = query.where(
            or_(Link.title.ilike(pattern), Link.note.ilike(pattern), Link.url.ilike(pattern))
        )
    rows = await db.scalars(query.order_by(Link.updated_at.desc()).limit(limit))
    return rows.all()


@router.post("", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
async def add_link(body: LinkIn, user: MemberUser, db: DbDep, queue: QueueDep, redis: RedisDep):
    """Add a link, or update the one already saved for the same address."""
    if body.chat_id is not None:
        await own_chat(db, user, body.chat_id)
    try:
        link, _ = await save_link(
            db, user.id, body.url, title=body.title, note=body.note, chat_id=body.chat_id
        )
    except LinkError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await db.commit()
    await db.refresh(link)
    await queue_preview(queue, link)
    await announce(redis, link)
    return link


@router.get("/{link_id}", response_model=LinkOut)
async def get_link(link_id: uuid.UUID, user: CurrentUser, db: DbDep):
    return await own_link(db, user, link_id)


@router.patch("/{link_id}", response_model=LinkOut)
async def edit_link(
    link_id: uuid.UUID, body: LinkUpdateIn, user: MemberUser, db: DbDep, redis: RedisDep
):
    link = await own_link(db, user, link_id)
    if body.title is not None:
        link.title = body.title.strip() or link.title
    if body.note is not None:
        link.note = body.note.strip() or None
    link.updated_at = utcnow()
    await db.commit()
    await announce(redis, link)
    return link


@router.post("/{link_id}/refresh", response_model=LinkOut, status_code=status.HTTP_202_ACCEPTED)
async def refresh_link(link_id: uuid.UUID, user: MemberUser, db: DbDep, queue: QueueDep):
    """Fetch the preview again; it arrives as a link.updated event."""
    link = await own_link(db, user, link_id)
    if link.kind not in GITHUB_KINDS:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only GitHub links have a preview to fetch")
    await queue_preview(queue, link)
    return link


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(link_id: uuid.UUID, user: MemberUser, db: DbDep, redis: RedisDep):
    """Remove the link from the library. What it points to is left alone."""
    link = await own_link(db, user, link_id)
    await db.delete(link)
    await db.commit()
    await publish(redis, user.id, Event(type="link.deleted", data={"id": str(link_id)}))
