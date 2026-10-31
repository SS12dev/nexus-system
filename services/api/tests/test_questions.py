from datetime import timedelta

from sqlalchemy import select

from nexus_common import questions
from nexus_common.models import (
    Entity,
    GraphQuestion,
    Memory,
    MemoryKind,
    Mention,
    Relation,
    utcnow,
)


async def test_the_starter_deck_runs_from_who_you_are_outward(owner, sessionmaker):
    cards = (await owner.get("/api/questions")).json()
    assert [c["shell"] for c in cards] == sorted(c["shell"] for c in cards)
    assert cards[0]["prompt"] == "Which of these matter most to you?"
    assert cards[0]["form"] == "many" and cards[0]["most"] == 5
    # Asked once: the deck isn't added twice.
    assert len((await owner.get("/api/questions")).json()) == len(questions.STARTERS)

    values = cards[0]["id"]
    bad = await owner.post(f"/api/questions/{values}/answer", json={"answer": {"choices": []}})
    assert bad.status_code == 400
    ok = await owner.post(
        f"/api/questions/{values}/answer",
        json={"answer": {"choices": ["Honesty", "Curiosity", "Not an option"]}},
    )
    assert ok.status_code == 200 and ok.json()["entities"] == ["Honesty", "Curiosity"]
    again = await owner.post(
        f"/api/questions/{values}/answer", json={"answer": {"choices": ["Calm"]}}
    )
    assert again.status_code == 400

    people = next(c for c in cards if c["prompt"].startswith("Who are the people"))
    said = await owner.post(
        f"/api/questions/{people['id']}/answer",
        json={"answer": {"text": "Ana, my sister;   Raj, my manager"}},
    )
    assert said.status_code == 200
    skipped = next(c for c in cards if c["prompt"] == "What drains your energy?")
    assert (await owner.post(f"/api/questions/{skipped['id']}/skip")).status_code == 204

    left = (await owner.get("/api/questions")).json()
    assert len(left) == len(questions.STARTERS) - 3
    async with sessionmaker() as db:
        mems = {m.content: m for m in (await db.scalars(select(Memory))).all()}
        ents = {e.name: e for e in (await db.scalars(select(Entity))).all()}
        rels = (await db.scalars(select(Relation))).all()
        mentions = (await db.scalars(select(Mention))).all()
    # A pick is the user's own word: certain links, from the centre, on the personality shell.
    assert (ents["Honesty"].type, ents["Honesty"].shell, ents["Honesty"].origin) == (
        "value",
        1,
        "user",
    )
    assert {(r.predicate, r.origin, r.confidence) for r in rels} == {("values", "user", 1.0)}
    values_mem = mems["Values most: Honesty, Curiosity."]
    assert values_mem.source == "cards" and values_mem.graph_at is not None
    assert {m.entity_id for m in mentions if m.memory_id == values_mem.id} == {
        ents["Honesty"].id,
        ents["Curiosity"].id,
    }
    # Words go to the graph builder, which finds Ana and Raj in them.
    told = mems["People in my life: Ana, my sister; Raj, my manager"]
    assert told.graph_at is None and told.pinned


async def test_cards_where_the_graph_is_unsure(owner, sessionmaker):
    me = (await owner.get("/api/me")).json()
    async with sessionmaker() as db:
        self_ent = Entity(
            user_id=me["id"],
            type="person",
            shell=0,
            name="Me",
            aliases=[],
            summary="",
            is_self=True,
        )
        old = Entity(
            user_id=me["id"], type="project", shell=3, name="Solitaire", aliases=[], summary=""
        )
        printing = Entity(
            user_id=me["id"], type="topic", shell=4, name="3D printing", aliases=[], summary=""
        )
        db.add_all([self_ent, old, printing])
        await db.flush()
        stale = Relation(
            user_id=me["id"],
            src_id=self_ent.id,
            dst_id=old.id,
            predicate="works on",
            family="activity",
            label="works on",
            fact="",
            weight=0.7,
            last_seen=utcnow() - timedelta(days=90),
            updated_at=utcnow() - timedelta(days=90),
        )
        db.add(stale)
        for i in range(3):
            m = Memory(user_id=me["id"], kind=MemoryKind.fact, content=f"Printed part {i}")
            db.add(m)
            await db.flush()
            db.add(Mention(user_id=me["id"], entity_id=printing.id, memory_id=m.id))
        await db.commit()
        added = await questions.plan(db, me["id"], per_day=3)
        await db.commit()
        # The day's allowance is spent.
        assert await questions.plan(db, me["id"], per_day=3) == 0
    assert added == 2

    cards = {c["kind"]: c for c in (await owner.get("/api/questions")).json()}
    assert cards["still"]["prompt"] == "Is this still true: you works on Solitaire?"
    assert "3D printing" in cards["domain"]["prompt"] and "Hobbies" in cards["domain"]["options"]

    await owner.post(
        f"/api/questions/{cards['still']['id']}/answer", json={"answer": {"yes": False}}
    )
    await owner.post(
        f"/api/questions/{cards['domain']['id']}/answer", json={"answer": {"choice": "Hobbies"}}
    )
    async with sessionmaker() as db:
        assert (await db.get(Relation, stale.id)).valid_to is not None
        assert (await db.get(Entity, printing.id)).domain == "hobbies"
        # Answered cards are never asked again.
        assert await questions.plan(db, me["id"], per_day=10) == 0
        keys = (await db.scalars(select(GraphQuestion.key))).all()
    assert f"still:{stale.id}" in keys
