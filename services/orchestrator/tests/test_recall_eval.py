import json

import pytest
from sqlalchemy import select

from nexus_common.models import (
    Memory,
    MemoryKind,
    RecallEval,
    RecallQuestion,
    RoleName,
    UserStatus,
)
from nexus_orchestrator import recall_eval


@pytest.fixture
async def user_id(sessionmaker):
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        user = await create_user(
            db, email="shivam@example.com", role=RoleName.owner, status=UserStatus.active
        )
        await db.commit()
        return user.id


async def setup(sessionmaker, embedder, user_id, contents):
    async with sessionmaker() as db:
        ids = []
        for c in contents:
            vec, model = await embedder.embed_one(db, c)
            m = Memory(
                user_id=user_id,
                kind=MemoryKind.fact,
                content=c,
                embedding=vec,
                embedding_model=model,
            )
            db.add(m)
            await db.flush()
            ids.append(m.id)
        ev = RecallEval(user_id=user_id, status="running", scores={}, misses=[])
        db.add(ev)
        await db.commit()
        return ids, ev.id


async def test_check_writes_questions_once_and_scores_recall(
    deps, model, user_id, sessionmaker, embedder
):
    ids, ev_id = await setup(
        sessionmaker,
        embedder,
        user_id,
        ["Works at UST as a data scientist", "Keeps bees in the garden", "Does not have a car"],
    )
    # One memory per question, so a near miss counts.
    deps.settings = deps.settings.model_copy(update={"memory_top_k": 1})
    model.script = [
        json.dumps(
            {
                "questions": [
                    {"item": 0, "question": "Where does the data scientist work, UST?"},
                    # Shares no words with its memory but one ("have") with another.
                    {"item": 1, "question": "What hobby do I have outdoors?"},
                    {"item": 7, "question": "out of range, ignored"},
                ]
            }
        )
    ]
    await recall_eval.run_eval(deps, ev_id)

    async with sessionmaker() as db:
        ev = await db.get(RecallEval, ev_id)
        qs = (await db.scalars(select(RecallQuestion))).all()
    assert ev.status == "done" and ev.finished_at is not None
    assert len(qs) == 2 and {q.memory_id for q in qs} == set(ids[:2])
    assert set(ev.scores) == {"standard", "standard_more", "graph"}
    s = ev.scores["standard"]
    assert s["n"] == 2 and s["hits"] == 1 and s["hit_rate"] == 0.5 and s["mrr"] == 0.5
    (miss,) = ev.misses
    assert miss["expected"] == "Keeps bees in the garden"
    # Sending more memories finds it here only because there are just three.
    assert miss["found_by"] == ["standard_more"]
    assert deps.factory_calls[0]["metadata"]["trace_name"] == "nexus.recall.questions"

    # A second check keeps the questions and only asks about the memory still without one.
    async with sessionmaker() as db:
        again = RecallEval(user_id=user_id, status="running", scores={}, misses=[])
        db.add(again)
        await db.commit()
    await recall_eval.run_eval(deps, again.id)
    assert len(model.seen) == 2
    assert model.seen[1][1].content == "Items:\n0: Does not have a car"


async def test_a_failed_check_records_the_error(deps, model, user_id, sessionmaker, embedder):
    _ids, ev_id = await setup(sessionmaker, embedder, user_id, ["Lives in Pune"])
    model.error = RuntimeError("gateway down")
    await recall_eval.run_eval(deps, ev_id)
    async with sessionmaker() as db:
        ev = await db.get(RecallEval, ev_id)
    assert ev.status == "failed" and "gateway down" in ev.error
