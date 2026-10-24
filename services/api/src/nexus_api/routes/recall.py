"""The Admin page's Recall tab: how well Nexus finds the right memory or past chat when it
needs one, measured on the owner's own memories. Owners only.

The check itself runs in the orchestrator (nexus_orchestrator/recall_eval.py): the memory
model writes one question per memory and past chat, once, and every check recalls each question
the way a chat turn does and counts how often the right item comes back.
"""

import uuid
from datetime import timedelta

from fastapi import APIRouter, status
from sqlalchemy import delete, func, select

from nexus_api.deps import DbDep, OwnerUser, QueueDep, SettingsDep
from nexus_common.models import AuditLog, Episode, Memory, RecallEval, RecallQuestion, utcnow

router = APIRouter(prefix="/api/admin/recall", tags=["admin"])

# A check still "running" after this long died with its worker.
STALE_CHECK = timedelta(minutes=20)
HISTORY = 12


def _eval_out(e: RecallEval) -> dict:
    return {
        "id": str(e.id),
        "status": e.status,
        "scores": e.scores,
        "misses": e.misses,
        "error": e.error,
        "created_at": e.created_at.isoformat(),
        "finished_at": e.finished_at.isoformat() if e.finished_at else None,
    }


@router.get("")
async def recall_summary(actor: OwnerUser, db: DbDep, settings: SettingsDep):
    count = select(func.count())
    questions = await db.scalar(count.where(RecallQuestion.user_id == actor.id))
    memories = await db.scalar(count.where(Memory.user_id == actor.id))
    episodes = await db.scalar(count.where(Episode.user_id == actor.id))
    evals = (
        await db.scalars(
            select(RecallEval)
            .where(RecallEval.user_id == actor.id)
            .order_by(RecallEval.created_at.desc())
            .limit(HISTORY)
        )
    ).all()
    return {
        "questions": questions or 0,
        "memories": memories or 0,
        "past_chats": episodes or 0,
        "checks": [_eval_out(e) for e in evals],
        # Whether chats recall through connections now (Configuration tab).
        "graph_recall_enabled": settings.graph_recall_enabled,
    }


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_check(actor: OwnerUser, db: DbDep, queue: QueueDep):
    running = await db.scalar(
        select(RecallEval).where(
            RecallEval.user_id == actor.id,
            RecallEval.status == "running",
            RecallEval.created_at > utcnow() - STALE_CHECK,
        )
    )
    if running is not None:
        return _eval_out(running)
    e = RecallEval(id=uuid.uuid4(), user_id=actor.id, status="running", scores={}, misses=[])
    db.add(e)
    db.add(AuditLog(actor_id=actor.id, action="recall.check", target=str(e.id)))
    await db.commit()
    await queue.enqueue_recall_check(e.id)
    return _eval_out(e)


@router.delete("/questions", status_code=status.HTTP_204_NO_CONTENT)
async def reset_questions(actor: OwnerUser, db: DbDep):
    """Drop the question set; the next check writes a fresh one."""
    await db.execute(delete(RecallQuestion).where(RecallQuestion.user_id == actor.id))
    db.add(AuditLog(actor_id=actor.id, action="recall.reset", target=str(actor.id)))
    await db.commit()
