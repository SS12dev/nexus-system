"""Question cards that fill in the knowledge graph (nexus_common.questions): the starter deck,
and the few the nightly pass adds where the graph is unsure. The home screen shows the next
one; the Knowledge page lists them all. Every card can be skipped."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from nexus_api.deps import DbDep, EmbedderDep, MemberUser, RedisDep, SettingsDep
from nexus_api.routes.memories import announce_change
from nexus_common import questions
from nexus_common.models import GraphQuestion

router = APIRouter(prefix="/api/questions", tags=["memories"])


def card_out(q: GraphQuestion) -> dict:
    return {
        "id": str(q.id),
        "kind": q.kind,
        "shell": q.shell,
        "prompt": q.prompt,
        "help": q.help,
        "form": q.form,
        "options": q.options,
        "most": (q.data or {}).get("most") or 0,
    }


@router.get("")
async def list_cards(user: MemberUser, db: DbDep):
    await questions.ensure_starter(db, user.id)
    cards = await questions.open_cards(db, user.id)
    await db.commit()
    return [card_out(q) for q in cards]


async def own_card(db, user, card_id: uuid.UUID) -> GraphQuestion:
    q = await db.scalar(
        select(GraphQuestion).where(GraphQuestion.id == card_id, GraphQuestion.user_id == user.id)
    )
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Card not found")
    return q


class AnswerIn(BaseModel):
    # {"choice": str} | {"choices": [str]} | {"yes": bool} | {"text": str}
    answer: dict


@router.post("/{card_id}/answer")
async def answer_card(
    card_id: uuid.UUID,
    body: AnswerIn,
    user: MemberUser,
    db: DbDep,
    embedder: EmbedderDep,
    redis: RedisDep,
    settings: SettingsDep,
):
    q = await own_card(db, user, card_id)
    try:
        done = await questions.apply_answer(
            db, embedder, user, q, body.answer, graph_enabled=settings.graph_enabled
        )
    except questions.AnswerError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await db.commit()
    # The orb and Memory page reload what Nexus knows.
    await announce_change(redis, user, "add", q.id)
    return {"note": done.note, "entities": done.entities, "memories": done.memories}


@router.post("/{card_id}/skip", status_code=status.HTTP_204_NO_CONTENT)
async def skip_card(card_id: uuid.UUID, user: MemberUser, db: DbDep):
    q = await own_card(db, user, card_id)
    if q.status == "open":
        await questions.skip(q)
        await db.commit()
