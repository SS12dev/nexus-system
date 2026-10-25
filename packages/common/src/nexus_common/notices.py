"""Notices on the main screen: a team finished (with the one result worth opening), failed, or
is waiting for approval. Each is saved (the bell's tray survives a reload and shows on every
device), sent live as a notification.created event (the toast, and the viewer opening by itself
when that chat is open), and pushed to the person's phone for when no tab is open.

Which of a run's outputs is "the result" is decided here too (featured): the agent can name it
with present_result; otherwise the most viewable thing it made wins, so a 3D model beats the
script that generated it and a live app beats both."""

import logging
import uuid
from dataclasses import dataclass
from pathlib import PurePath

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import push
from nexus_common.events import Event, publish
from nexus_common.library import file_payload
from nexus_common.links import link_payload
from nexus_common.models import File, FileStatus, Link, Notification, NotificationKind
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

TITLE_CHARS = 200
BODY_CHARS = 400
# The bell keeps this many; older ones are still in the table but not listed.
TRAY_SIZE = 50

# Most viewable first. A file whose type isn't listed can still win when it's all there is,
# except source and CAD exchange files, which are never "the result" to look at.
_RANK = [
    {".glb", ".gltf"},
    {".stl", ".3mf", ".obj", ".ply"},
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"},
    {".pdf"},
    {".pptx", ".docx", ".xlsx"},
    {".html"},
    {".md", ".markdown"},
    {".csv", ".ipynb"},
    {".txt", ".json"},
]
_NEVER = {".py", ".js", ".ts", ".step", ".stp", ".scad", ".sh", ".toml", ".yaml", ".yml", ".log"}
_LINK_KINDS = ("app",)


@dataclass
class Featured:
    file: File | None = None
    link: Link | None = None


def _rank(f: File) -> int | None:
    ext = PurePath(f.name).suffix.lower()
    if ext in _NEVER:
        return None
    for i, group in enumerate(_RANK):
        if ext in group:
            return i
    return len(_RANK)


def featured(
    files: list[File],
    links: list[Link],
    presented: dict | None = None,
) -> Featured:
    """The one output to open for the person. presented is what the agent named with
    present_result ({"file_id"} or {"link_id"}), honoured when it's one of the run's own."""
    if presented:
        for f in files:
            if str(f.id) == presented.get("file_id") and f.status != FileStatus.failed:
                return Featured(file=f)
        for link in links:
            if str(link.id) == presented.get("link_id"):
                return Featured(link=link)
    for link in links:
        if link.kind in _LINK_KINDS:
            return Featured(link=link)
    best: tuple[int, int] | None = None
    pick = None
    for i, f in enumerate(files):
        if f.status == FileStatus.failed:
            continue
        rank = _rank(f)
        if rank is None:
            continue
        # Equal rank: the later file wins (the final render, not the first draft).
        key = (rank, -i)
        if best is None or key < best:
            best, pick = key, f
    return Featured(file=pick)


def notification_payload(
    n: Notification, file: File | None = None, link: Link | None = None
) -> dict:
    return {
        "id": str(n.id),
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "chat_id": str(n.chat_id) if n.chat_id else None,
        "run_id": str(n.run_id) if n.run_id else None,
        "url": n.url,
        "file": file_payload(file) if file is not None else None,
        "link": link_payload(link) if link is not None else None,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "created_at": n.created_at.isoformat(),
    }


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


async def notify(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    user_id: uuid.UUID,
    kind: NotificationKind,
    title: str,
    body: str = "",
    *,
    url: str = "/",
    chat_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    file: File | None = None,
    link: Link | None = None,
    send_push: bool = True,
    tag: str | None = None,
) -> Notification:
    """Save a notice, show it on every open tab and push it to the person's devices. Commits."""
    n = Notification(
        user_id=user_id,
        kind=kind,
        title=_clip(title, TITLE_CHARS),
        body=_clip(body, BODY_CHARS),
        url=url[:300],
        chat_id=chat_id,
        run_id=run_id,
        file_id=file.id if file is not None else None,
        link_id=link.id if link is not None else None,
    )
    db.add(n)
    await db.commit()
    await publish(
        redis,
        user_id,
        Event(
            type="notification.created",
            chat_id=chat_id,
            data={"notification": notification_payload(n, file, link)},
        ),
    )
    if send_push:
        try:
            await push.send(
                db, settings, user_id, push.Notice(title=n.title, body=n.body, url=url, tag=tag)
            )
        except Exception:
            log.warning("Could not push a notice", exc_info=True)
    return n
