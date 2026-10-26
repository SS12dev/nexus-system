"""Feedback people send from the app's menu: a problem, an idea or praise, with the page they
were on and up to MAX_SHOTS screenshots. Owners read it on the Admin page's Feedback tab and
get it by email; the self-review reads it (without who sent it) as evidence for its proposals."""

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from nexus_api.deps import CurrentUser, DbDep, OwnerUser, QueueDep, SettingsDep, StorageDep
from nexus_api.ratelimit import messages_limit
from nexus_api.routes.files import clean_name
from nexus_api.schemas import FeedbackOut
from nexus_common.models import AuditLog, Feedback, FeedbackKind, User, utcnow
from nexus_common.storage import feedback_shot_key

log = logging.getLogger(__name__)
router = APIRouter(tags=["feedback"])

# Screenshots a single piece of feedback can carry.
MAX_SHOTS = 3
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


@router.post(
    "/api/feedback",
    response_model=FeedbackOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[messages_limit],
)
async def send_feedback(
    user: CurrentUser,
    db: DbDep,
    queue: QueueDep,
    storage: StorageDep,
    settings: SettingsDep,
    message: Annotated[str, Form(min_length=3, max_length=4000)],
    kind: Annotated[Literal["problem", "idea", "praise"], Form()] = "idea",
    page: Annotated[str, Form(max_length=300)] = "",
    screenshots: Annotated[list[UploadFile], File()] = [],  # noqa: B006 (FastAPI copies it)
):
    if len(screenshots) > MAX_SHOTS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Up to {MAX_SHOTS} screenshots, please."
        )
    shots = [await _read_shot(f, settings.max_upload_mb) for f in screenshots]
    row = Feedback(
        id=uuid.uuid4(),
        user_id=user.id,
        kind=FeedbackKind(kind),
        message=message.strip(),
        page=page or None,
        created_at=utcnow(),
        screenshots=[],
    )
    for n, (name, content_type, data) in enumerate(shots):
        key = feedback_shot_key(row.id, n)
        await storage.put(key, data, content_type)
        row.screenshots.append(
            {"key": key, "name": name, "content_type": content_type, "size": len(data)}
        )
    db.add(row)
    db.add(
        AuditLog(
            actor_id=user.id,
            action="feedback.sent",
            data={"kind": kind, "screenshots": len(shots)},
        )
    )
    await db.commit()
    try:
        await queue.enqueue_feedback_notice(row.id)
    except Exception:
        log.warning("Could not queue the feedback email", exc_info=True)
    return FeedbackOut.model_validate(row)


async def _read_shot(f: UploadFile, max_mb: int) -> tuple[str, str, bytes]:
    """A screenshot's name, type and bytes, checked by its first bytes rather than trusting the
    browser: only PNG, JPEG, WebP and GIF pictures go through."""
    data = await f.read(max_mb * 1024 * 1024 + 1)
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"Screenshots can be up to {max_mb} MB each."
        )
    content_type = next((t for magic, t in _IMAGE_MAGIC if data.startswith(magic)), None)
    if content_type == "image/webp" and data[8:12] != b"WEBP":
        content_type = None
    if content_type is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Screenshots must be PNG, JPEG, WebP or GIF pictures.",
        )
    return clean_name(f.filename), content_type, data


@router.get("/api/admin/feedback/{feedback_id}/screenshots/{n}")
async def screenshot(
    feedback_id: uuid.UUID, n: int, user: OwnerUser, db: DbDep, storage: StorageDep
):
    f = await db.get(Feedback, feedback_id)
    shots = (f.screenshots or []) if f else []
    if not 0 <= n < len(shots):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screenshot not found")
    shot = shots[n]
    return StreamingResponse(
        storage.stream(shot["key"]),
        media_type=shot["content_type"],
        headers={
            "Content-Length": str(shot["size"]),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/api/admin/feedback", response_model=list[FeedbackOut])
async def list_feedback(user: OwnerUser, db: DbDep, open_only: bool = False):
    q = (
        select(Feedback, User)
        .outerjoin(User, User.id == Feedback.user_id)
        .order_by(Feedback.created_at.desc())
        .limit(200)
    )
    if open_only:
        q = q.where(Feedback.done_at.is_(None))
    out = []
    for f, u in (await db.execute(q)).all():
        item = FeedbackOut.model_validate(f)
        item.email, item.name = (u.email, u.name) if u else (None, None)
        out.append(item)
    return out


@router.post("/api/admin/feedback/{feedback_id}/done", response_model=FeedbackOut)
async def mark_done(feedback_id: uuid.UUID, user: OwnerUser, db: DbDep, done: bool = True):
    f = await db.get(Feedback, feedback_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feedback not found")
    f.done_at = utcnow() if done else None
    await db.commit()
    return FeedbackOut.model_validate(f)
