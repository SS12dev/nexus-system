import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from nexus_common import graph
from nexus_common.models import (
    Chat,
    Entity,
    File,
    FileChunk,
    FileStatus,
    Memory,
    MemoryKind,
    Mention,
    Message,
    MessageRole,
    MessageStatus,
    Relation,
    RoleName,
    User,
    UserStatus,
    utcnow,
)
from nexus_orchestrator import memory


@pytest.fixture
async def chat(sessionmaker):
    """An owner with one finished exchange; returns (user_id, chat_id, reply_id)."""
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        user = await create_user(
            db,
            email="shivam@example.com",
            role=RoleName.owner,
            status=UserStatus.active,
            name="Shivam",
        )
        c = Chat(user_id=user.id, title="Nexus")
        db.add(c)
        await db.flush()
        now = utcnow()
        db.add(
            Message(
                chat_id=c.id,
                role=MessageRole.user,
                status=MessageStatus.complete,
                content="At UST I build Nexus on LangGraph. Keep answers short.",
                created_at=now,
            )
        )
        reply = Message(
            chat_id=c.id,
            role=MessageRole.assistant,
            status=MessageStatus.complete,
            content="Noted.",
            created_at=now + timedelta(seconds=1),
        )
        db.add(reply)
        await db.commit()
        return user.id, c.id, reply.id


EXTRACTION = {
    "memories": [
        {
            "content": "Builds Nexus on LangGraph",
            "kind": "fact",
            "confidence": 0.9,
            "about": ["Nexus", "LangGraph"],
        },
        {"content": "Prefers short answers", "kind": "preference", "about": []},
    ],
    "forget": [],
    "entities": [
        {"name": "Nexus", "type": "project", "summary": "his assistant platform"},
        {"name": "LangGraph", "type": "tool", "aliases": ["langgraph framework"]},
        {"name": "UST", "type": "organisation", "summary": "employer"},
        {"name": "USER", "type": "person"},
    ],
    "relations": [
        {"from": "USER", "predicate": "Is Building", "to": "Nexus", "fact": "Builds Nexus."},
        {"from": "Nexus", "predicate": "uses", "to": "LangGraph", "fact": "Nexus uses LangGraph."},
        {"from": "USER", "predicate": "works at", "to": "UST", "fact": "Works at UST."},
        {"from": "Nexus", "predicate": "uses", "to": "Nexus"},
    ],
}


async def entities(sessionmaker, user_id) -> dict[str, Entity]:
    async with sessionmaker() as db:
        rows = await db.scalars(select(Entity).where(Entity.user_id == user_id))
        return {e.name: e for e in rows.all()}


async def test_chat_extraction_builds_the_graph(deps, model, chat, sessionmaker):
    user_id, chat_id, reply_id = chat
    model.script = [json.dumps(EXTRACTION)]
    await memory.extract_memories(deps, chat_id, reply_id)

    ents = await entities(sessionmaker, user_id)
    # The user is one entity of their own; "USER" never becomes a separate one.
    assert set(ents) == {"Shivam", "Nexus", "LangGraph", "UST"} and ents["Shivam"].is_self
    assert (
        ents["LangGraph"].aliases == ["langgraph framework"] and ents["UST"].type == "organisation"
    )
    async with sessionmaker() as db:
        rels = (await db.scalars(select(Relation))).all()
        mentions = (await db.scalars(select(Mention))).all()
        mems = {m.content: m for m in (await db.scalars(select(Memory))).all()}
    by_name = {e.id: n for n, e in ents.items()}
    assert {(by_name[r.src_id], r.predicate, by_name[r.dst_id]) for r in rels} == {
        ("Shivam", "is building", "Nexus"),
        ("Nexus", "uses", "LangGraph"),
        ("Shivam", "works at", "UST"),
    }
    built = mems["Builds Nexus on LangGraph"]
    assert {by_name[m.entity_id] for m in mentions if m.memory_id == built.id} == {
        "Nexus",
        "LangGraph",
    }
    # Every entity found is mentioned by the chat; the user's own entity never is.
    assert {by_name[m.entity_id] for m in mentions if m.chat_id == chat_id} == {
        "Nexus",
        "LangGraph",
        "UST",
    }
    assert built.graph_at is not None and mems["Prefers short answers"].graph_at is not None


async def test_names_resolve_to_existing_entities_and_ended_facts_close(
    deps, model, chat, sessionmaker
):
    user_id, chat_id, reply_id = chat
    model.script = [json.dumps(EXTRACTION)]
    await memory.extract_memories(deps, chat_id, reply_id)
    async with sessionmaker() as db:
        user = await db.get(User, user_id)
        found = graph.parse_graph(
            {
                "entities": [
                    {"name": "LangGraph framework", "type": "tool"},
                    {"name": "ust", "type": "organisation"},
                ],
                "relations": [
                    {"from": "user", "predicate": "works at", "to": "UST", "ended": True},
                    {"from": "Nexus", "predicate": "uses", "to": "langgraph framework"},
                ],
            }
        )
        await graph.ingest(db, deps.embedder, user, found, chat_id=chat_id)
        await db.commit()
        rels = {r.predicate: r for r in (await db.scalars(select(Relation))).all()}
    ents = await entities(sessionmaker, user_id)
    assert set(ents) == {"Shivam", "Nexus", "LangGraph", "UST"}
    assert rels["works at"].valid_to is not None
    assert rels["uses"].valid_to is None and rels["uses"].confidence == pytest.approx(0.8)


async def test_backfill_links_memories_and_files_then_prunes(
    deps, model, chat, sessionmaker, embedder
):
    user_id, _chat_id, _reply = chat
    async with sessionmaker() as db:
        m = Memory(user_id=user_id, kind=MemoryKind.fact, content="Planning a Kyoto trip in March")
        f = File(
            user_id=user_id,
            name="itinerary.md",
            content_type="text/markdown",
            size=10,
            sha256="0" * 64,
            storage_key="k",
            status=FileStatus.ready,
        )
        db.add_all([m, f])
        await db.flush()
        db.add(FileChunk(file_id=f.id, user_id=user_id, idx=0, content="Day 1: Fushimi Inari"))
        await db.commit()
    model.script = [
        json.dumps(
            {
                "entities": [{"name": "Kyoto", "type": "place", "summary": "trip in March"}],
                "relations": [
                    {"from": "USER", "predicate": "is planning a trip to", "to": "Kyoto"}
                ],
                "about": [{"item": 0, "entities": ["Kyoto"]}, {"item": 1, "entities": ["kyoto"]}],
            }
        )
    ]
    assert await memory.link_graph(deps) == 2
    prompt = model.seen[0][1].content
    assert "0: Memory: Planning a Kyoto trip" in prompt and "Document 'itinerary.md'" in prompt
    async with sessionmaker() as db:
        mentions = (await db.scalars(select(Mention))).all()
        assert {(x.memory_id, x.file_id) for x in mentions} == {(m.id, None), (None, f.id)}
        assert (await db.get(Memory, m.id)).graph_at is not None
    # Nothing left to do: no second call.
    assert await memory.link_graph(deps) == 0 and len(model.seen) == 1

    # Once nothing mentions Kyoto it goes, with its relations; the user's entity stays.
    async with sessionmaker() as db:
        await db.delete(await db.get(Memory, m.id))
        await db.delete(await db.get(File, f.id))
        await db.flush()
        assert await graph.prune(db, user_id) == 1
        await db.commit()
        assert (await db.scalars(select(Relation))).all() == []
    assert set(await entities(sessionmaker, user_id)) == {"Shivam"}


async def test_graph_off_learns_memories_only(deps, model, chat, sessionmaker):
    user_id, chat_id, reply_id = chat
    deps.settings = deps.settings.model_copy(update={"graph_enabled": False})
    model.script = [json.dumps(EXTRACTION)]
    await memory.extract_memories(deps, chat_id, reply_id)
    assert await entities(sessionmaker, user_id) == {}
    assert await memory.link_graph(deps) == 0


async def seed_nexus(sessionmaker, embedder, user_id):
    """Nexus uses LangGraph; the reason for LangGraph is in a memory that never says Nexus,
    and a distractor shares a word with the question."""
    async with sessionmaker() as db:
        user = await db.get(User, user_id)
        why = Memory(
            user_id=user_id,
            kind=MemoryKind.fact,
            content="Chose it for its state model",
            confidence=0.9,
        )
        others = [
            Memory(user_id=user_id, kind=MemoryKind.fact, content=c)
            for c in ("Likes to pick strawberries", "Bakes a layer cake on birthdays")
        ]
        for m in (why, *others):
            m.embedding, m.embedding_model = await embedder.embed_one(db, m.content)
        db.add_all([why, *others])
        await db.flush()
        found = graph.parse_graph(
            {
                "entities": [
                    {"name": "Nexus", "type": "project"},
                    {"name": "LangGraph", "type": "tool"},
                ],
                "relations": [
                    {
                        "from": "Nexus",
                        "predicate": "uses",
                        "to": "LangGraph",
                        "fact": "Nexus uses LangGraph.",
                    },
                    {"from": "USER", "predicate": "is building", "to": "Nexus"},
                ],
            }
        )
        await graph.ingest(
            db, embedder, user, found, links=[({"memory_id": why.id}, ["langgraph"])]
        )
        await db.commit()
        return why.id, [m.id for m in others]


QUESTION = "Why did I pick the orchestration layer for Nexus?"


async def test_recall_through_connections_finds_what_search_misses(
    deps, chat, sessionmaker, embedder
):
    from nexus_orchestrator.context import recall

    user_id, _c, _r = chat
    why, (other, _cake) = await seed_nexus(sessionmaker, embedder, user_id)
    settings = deps.settings.model_copy(update={"memory_top_k": 1, "graph_extra_memories": 2})
    async with sessionmaker() as db:
        vec, _ = await embedder.try_embed_one(db, QUESTION)
        plain = await recall(db, settings, user_id, QUESTION, vec, through_graph=False)
        linked = await recall(db, settings, user_id, QUESTION, vec, through_graph=True)
        # Off by default: the setting decides when not asked.
        default = await recall(db, settings, user_id, QUESTION, vec)
    assert [m.id for m in plain.memories] == [other] and plain.facts == []
    assert [m.id for m in linked.memories] == [other, why]
    assert [e.name for e in linked.entities] == ["Nexus", "LangGraph"]
    assert "Nexus uses LangGraph." in linked.facts and "Shivam is building Nexus" in linked.facts
    assert [m.id for m in default.memories] == [other]

    # An ended relation is not followed.
    async with sessionmaker() as db:
        rel = await db.scalar(select(Relation).where(Relation.predicate == "uses"))
        rel.valid_to = utcnow()
        await db.commit()
        cut = await recall(db, settings, user_id, QUESTION, vec, through_graph=True)
    assert why not in [m.id for m in cut.memories]


async def test_recall_check_scores_graph_recall_side_by_side(
    deps, model, chat, sessionmaker, embedder
):
    from nexus_common.models import RecallEval
    from nexus_orchestrator import recall_eval

    user_id, _c, _r = chat
    await seed_nexus(sessionmaker, embedder, user_id)
    deps.settings = deps.settings.model_copy(update={"memory_top_k": 1, "graph_extra_memories": 1})
    model.script = [
        json.dumps({"questions": [{"item": 0, "question": QUESTION}]}),
    ]
    async with sessionmaker() as db:
        ev = RecallEval(user_id=user_id, status="running", scores={}, misses=[])
        db.add(ev)
        await db.commit()
    await recall_eval.run_eval(deps, ev.id)
    async with sessionmaker() as db:
        ev = await db.get(RecallEval, ev.id)
    # The most confident memory comes first, so item 0 is "why".
    assert ev.scores["standard"]["hits"] == 0
    assert ev.scores["graph"]["hits"] == 1 and ev.scores["graph"]["mrr"] == 0.5
    assert ev.misses[0]["found_by"] == ["graph"]


async def test_turn_context_carries_the_connections(deps, chat, sessionmaker, embedder):
    from nexus_orchestrator.context import assemble

    user_id, chat_id, _r = chat
    await seed_nexus(sessionmaker, embedder, user_id)
    deps.settings = deps.settings.model_copy(update={"graph_recall_enabled": True})
    async with sessionmaker() as db:
        c = await db.get(Chat, chat_id)
        ctx = await assemble(db, deps.settings, embedder, c, QUESTION, False)
    rendered = ctx.render()
    assert "<connections>\n- Nexus uses LangGraph." in rendered
    assert {e.name for e in ctx.entities} == {"Nexus", "LangGraph"}


def test_names_drop_type_prefixes_and_files_are_not_entities():
    found = graph.parse_graph(
        {
            "entities": [
                {"name": "topic:tray design", "type": "topic", "aliases": ["Topic: trays"]},
                {"name": "out/tray.stl", "type": "tool"},
                {"name": "rocket_preview.png", "type": "other"},
                {"name": "Node.js", "type": "tool"},
            ],
            "relations": [{"from": "USER", "predicate": "is designing", "to": "topic:tray design"}],
        }
    )
    assert [(e["name"], e["aliases"]) for e in found.entities] == [
        ("tray design", ["trays"]),
        ("Node.js", []),
    ]
    assert found.relations[0]["to"] == "tray design"
    assert graph.about_names(["project: Nexus", "tray.3mf"]) == ["nexus"]
