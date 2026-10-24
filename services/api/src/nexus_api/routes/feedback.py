"""Feedback people send from the app's menu: a problem, an idea or praise, with the page they
were on. Owners read it on the Admin page's Feedback tab and get it by email; the self-review
reads it (without who sent it) as evidence for its proposals."""

import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from nexus_api.deps import CurrentUser, DbDep, OwnerUser, QueueDep
from nexus_api.ratelimit import messages_limit
from nexus_api.schemas import FeedbackIn, FeedbackOut
from nexus_common.models import AuditLog, Feedback, FeedbackKind, User, utcnow

log = logging.getLogger(__name__)
router = APIRouter(tags=["feedback"])


@router.post(
    "/api/feedback",
    response_model=FeedbackOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[messages_limit],
)
async def send_feedback(body: FeedbackIn, user: CurrentUser, db: DbDep, queue: QueueDep):
    row = Feedback(
        user_id=user.id,
        kind=FeedbackKind(body.kind),
        message=body.message.strip(),
        page=body.page or None,
        created_at=utcnow(),
    )
    db.add(row)
    db.add(AuditLog(actor_id=user.id, action="feedback.sent", data={"kind": body.kind}))
    await db.commit()
    try:
        await queue.enqueue_feedback_notice(row.id)
    except Exception:
        log.warning("Could not queue the feedback email", exc_info=True)
    return FeedbackOut.model_validate(row)


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
