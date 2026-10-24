"""The recall check on the Admin page: does Nexus find the right memory when it needs it?

Each memory and past-chat summary gets one question, written once by the memory model, that
can only be answered well by recalling it. A check embeds every question, recalls exactly as a
chat turn does, and counts how often the target comes back (hit rate) and how high (mean
reciprocal rank). The questions are kept, so checks are comparable over time and only new
memories cost a model call. Each mode in MODES is a way of recalling, so a new method can be
measured side by side with the old one before it is switched on.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import (
    Episode,
    Memory,
    RecallEval,
    RecallQuestion,
    User,
    utcnow,
)
from nexus_common.settings import Settings
from nexus_orchestrator.context import Recall, recall
from nexus_orchestrator.llm import parse_json
from nexus_orchestrator.memory import ask_memory_model
from nexus_orchestrator.prompts import RECALL_QUESTIONS
from nexus_orchestrator.turn import TurnDeps

log = logging.getLogger(__name__)

# How many questions a user's set grows to, and how many items go to the model at once.
MAX_MEMORY_QUESTIONS = 80
MAX_EPISODE_QUESTIONS = 20
BATCH = 15
# Misses kept on a check for the owner to read.
MAX_MISSES = 20

Mode = Callable[[AsyncSession, Settings, uuid.UUID, str, list[float] | None], Awaitable[Recall]]


async def _standard(db, settings, user_id, question, vec) -> Recall:
    return await recall(db, settings, user_id, question, vec)


MODES: dict[str, Mode] = {"standard": _standard}


async def _add_questions(deps: TurnDeps, db: AsyncSession, user: User) -> int:
    """Write questions for memories and past chats that have none, up to the caps."""
    mine = select(func.count()).where(RecallQuestion.user_id == user.id)
    asked = select(RecallQuestion.memory_id).where(RecallQuestion.memory_id.is_not(None))
    have_m = await db.scalar(mine.where(RecallQuestion.memory_id.is_not(None))) or 0
    mems = (
        await db.scalars(
            select(Memory)
            .where(Memory.user_id == user.id, Memory.id.not_in(asked))
            .order_by(Memory.pinned.desc(), Memory.confidence.desc())
            .limit(max(0, MAX_MEMORY_QUESTIONS - have_m))
        )
    ).all()
    asked_e = select(RecallQuestion.episode_id).where(RecallQuestion.episode_id.is_not(None))
    have_e = await db.scalar(mine.where(RecallQuestion.episode_id.is_not(None))) or 0
    eps = (
        await db.scalars(
            select(Episode)
            .where(Episode.user_id == user.id, Episode.id.not_in(asked_e))
            .order_by(Episode.updated_at.desc())
            .limit(max(0, MAX_EPISODE_QUESTIONS - have_e))
        )
    ).all()
    items: list[Memory | Episode] = [*mems, *eps]
    added = 0
    for start in range(0, len(items), BATCH):
        batch = items[start : start + BATCH]
        listing = "\n".join(f"{i}: {it.content[:800]}" for i, it in enumerate(batch))
        raw = await ask_memory_model(
            deps,
            db,
            user,
            RECALL_QUESTIONS,
            f"Items:\n{listing}",
            name="recall.questions",
            session_id=None,
        )
        for q in parse_json(raw).get("questions") or []:
            if not isinstance(q, dict):
                continue
            i, text = q.get("item"), str(q.get("question") or "").strip()
            if not isinstance(i, int) or not 0 <= i < len(batch) or not text:
                continue
            it = batch[i]
            db.add(
                RecallQuestion(
                    user_id=user.id,
                    question=text[:500],
                    memory_id=it.id if isinstance(it, Memory) else None,
                    episode_id=it.id if isinstance(it, Episode) else None,
                )
            )
            added += 1
        await db.flush()
    return added


def _rank(found: Recall, q: RecallQuestion) -> int | None:
    ids = [m.id for m in found.memories] if q.memory_id else [e.id for e in found.episodes]
    target = q.memory_id or q.episode_id
    return ids.index(target) + 1 if target in ids else None


async def score(
    db: AsyncSession, settings: Settings, embedder, user_id: uuid.UUID, modes: dict[str, Mode]
) -> tuple[dict, list]:
    """Recall every question of the user's set with each mode; scores and the misses."""
    questions = (
        await db.scalars(
            select(RecallQuestion)
            .where(RecallQuestion.user_id == user_id)
            .order_by(RecallQuestion.created_at)
        )
    ).all()
    # At least one memory and one past chat each, so a check means something even when the
    # Configuration tab has turned recall down to nothing.
    settings = settings.model_copy(
        update={
            "memory_top_k": max(settings.memory_top_k, 1),
            "episode_top_k": max(settings.episode_top_k, 1),
        }
    )
    targets: dict[uuid.UUID, str] = {}
    mem_ids = [q.memory_id for q in questions if q.memory_id]
    ep_ids = [q.episode_id for q in questions if q.episode_id]
    if mem_ids:
        for m in (await db.scalars(select(Memory).where(Memory.id.in_(mem_ids)))).all():
            targets[m.id] = m.content
    if ep_ids:
        for e in (await db.scalars(select(Episode).where(Episode.id.in_(ep_ids)))).all():
            targets[e.id] = e.content
    totals = {name: {"n": 0, "hits": 0, "rr": 0.0} for name in modes}
    misses: list[dict] = []
    for q in questions:
        vec, _ = await embedder.try_embed_one(db, q.question)
        found_by: list[str] = []
        for name, mode in modes.items():
            rank = _rank(await mode(db, settings, user_id, q.question, vec), q)
            t = totals[name]
            t["n"] += 1
            if rank is not None:
                t["hits"] += 1
                t["rr"] += 1 / rank
                found_by.append(name)
        if "standard" not in found_by and len(misses) < MAX_MISSES:
            misses.append(
                {
                    "question": q.question,
                    "expected": targets.get(q.memory_id or q.episode_id, "")[:300],
                    "target": "memory" if q.memory_id else "past chat",
                    "found_by": found_by,
                }
            )
    scores = {
        name: {
            "n": t["n"],
            "hits": t["hits"],
            "hit_rate": round(t["hits"] / t["n"], 4) if t["n"] else 0.0,
            "mrr": round(t["rr"] / t["n"], 4) if t["n"] else 0.0,
        }
        for name, t in totals.items()
    }
    return scores, misses


async def run_eval(deps: TurnDeps, eval_id: uuid.UUID) -> None:
    async with deps.sessionmaker() as db:
        ev = await db.get(RecallEval, eval_id)
        if ev is None or ev.status != "running":
            return
        user = await db.get(User, ev.user_id)
        try:
            added = await _add_questions(deps, db, user)
            await db.commit()
            ev.scores, ev.misses = await score(db, deps.settings, deps.embedder, user.id, MODES)
            ev.status = "done"
            log.info("Recall check for %s: %d new questions, %s", user.id, added, ev.scores)
        except Exception as exc:
            log.exception("Recall check %s failed", eval_id)
            await db.rollback()
            ev = await db.get(RecallEval, eval_id)
            ev.status, ev.error = "failed", str(exc)[:500]
        ev.finished_at = utcnow()
        await db.commit()
