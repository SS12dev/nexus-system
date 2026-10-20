import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from jarvis_common.memory import get_profile
from jarvis_common.models import (
    Chat,
    Episode,
    File,
    FileChunk,
    FileStatus,
    Memory,
    MemoryEvent,
    MemoryKind,
    Message,
    MessageRole,
    MessageStatus,
    RoleName,
    UserStatus,
    utcnow,
)
from jarvis_orchestrator import memory
from jarvis_orchestrator.turn import run_turn


@pytest.fixture
async def chat(sessionmaker):
    """An owner with one finished exchange; returns (user_id, chat_id, reply_id)."""
    from jarvis_api.users import create_user

    async with sessionmaker() as db:
        user = await create_user(
            db,
            email="shivam@example.com",
            role=RoleName.owner,
            status=UserStatus.active,
            name="Shivam",
        )
        c = Chat(user_id=user.id, title="Planning")
        db.add(c)
        await db.flush()
        now = utcnow()
        db.add(
            Message(
                chat_id=c.id,
                role=MessageRole.user,
                status=MessageStatus.complete,
                content="Compare LangGraph and CrewAI. I always prefer tables for comparisons.",
                created_at=now,
            )
        )
        reply = Message(
            chat_id=c.id,
            role=MessageRole.assistant,
            status=MessageStatus.complete,
            content="| | LangGraph | CrewAI |",
            created_at=now + timedelta(seconds=1),
        )
        db.add(reply)
        await db.commit()
        return user.id, c.id, reply.id


async def add_memory(sessionmaker, embedder, user_id, content, **kw) -> uuid.UUID:
    async with sessionmaker() as db:
        vec, model = await embedder.embed_one(db, content)
        m = Memory(
            user_id=user_id,
            kind=MemoryKind.preference,
            content=content,
            embedding=vec,
            embedding_model=model,
            **kw,
        )
        db.add(m)
        await db.commit()
        return m.id


def js(**data) -> str:
    return json.dumps(data)


async def memories(sessionmaker, user_id) -> list[Memory]:
    async with sessionmaker() as db:
        rows = await db.scalars(select(Memory).where(Memory.user_id == user_id))
        return list(rows.all())


async def test_new_preference_is_added_with_its_source(deps, model, chat, sessionmaker, enqueued):
    user_id, chat_id, reply_id = chat
    model.script = [
        js(
            memories=[
                {
                    "content": "Prefers tables for comparisons",
                    "kind": "preference",
                    "confidence": 0.9,
                }
            ],
            forget=[],
        )
    ]
    await memory.extract_memories(deps, chat_id, reply_id)

    (m,) = await memories(sessionmaker, user_id)
    assert m.content == "Prefers tables for comparisons" and m.kind == "preference"
    assert m.source_chat_id == chat_id and m.embedding is not None
    # Nothing to compare against, so only the extraction call was made, in JSON mode, billed
    # to the user's key with a memory trace.
    assert len(model.seen) == 1
    assert model.calls[0]["response_format"] == {"type": "json_object"}
    assert deps.factory_calls[0]["model"] == "jarvis-nano"
    assert deps.factory_calls[0]["metadata"]["trace_name"] == "jarvis.memory.extract"
    # A first memory schedules the first profile.
    assert enqueued.jobs == [("rebuild_profile", str(user_id))]


async def test_repeated_preference_reinforces_instead_of_duplicating(
    deps, model, chat, sessionmaker, embedder
):
    user_id, chat_id, reply_id = chat
    mid = await add_memory(sessionmaker, embedder, user_id, "Prefers tables", confidence=0.6)
    model.script = [
        js(memories=[{"content": "Prefers tables for comparisons", "kind": "preference"}]),
        js(actions=[{"candidate": 0, "op": "NOOP", "target": 0}], delete=[]),
    ]
    await memory.extract_memories(deps, chat_id, reply_id)
    (m,) = await memories(sessionmaker, user_id)
    assert m.id == mid and m.confidence == pytest.approx(0.7)
    assert "EXISTING:\n0: Prefers tables" in model.seen[1][1].content


async def test_contradiction_updates_and_forget_deletes(deps, model, chat, sessionmaker, embedder):
    user_id, chat_id, reply_id = chat
    city = await add_memory(sessionmaker, embedder, user_id, "Lives in Pune")
    await add_memory(sessionmaker, embedder, user_id, "Has a cat called Tom")
    pinned = await add_memory(sessionmaker, embedder, user_id, "Lives near a cat cafe", pinned=True)
    model.script = [
        js(memories=[{"content": "Lives in Delhi", "kind": "fact"}], forget=["the cat"]),
        js(
            actions=[{"candidate": 0, "op": "UPDATE", "target": 0, "content": "Lives in Delhi"}],
            delete=[1, 2],
        ),
    ]
    # Pin the order the model sees: the city first, then the cat, then the pinned memory.
    original = memory.hybrid_search

    async def ordered(db, table, user_id, query, vec, k, **kw):
        hits = await original(db, table, user_id, query, vec, k, **kw)
        return sorted(hits, key=lambda h: 0 if h.id == city else 1 if h.id != pinned else 2)

    memory.hybrid_search = ordered
    try:
        await memory.extract_memories(deps, chat_id, reply_id)
    finally:
        memory.hybrid_search = original

    left = {m.content: m for m in await memories(sessionmaker, user_id)}
    assert set(left) == {"Lives in Delhi", "Lives near a cat cafe"}  # pinned survives
    assert left["Lives in Delhi"].id == city
    async with sessionmaker() as db:
        ops = (await db.scalars(select(MemoryEvent.op).where(MemoryEvent.memory_id == city))).all()
        assert ops == ["update"]
        assert (await get_profile(db, user_id)).stale is True


async def test_memory_off_means_no_learning(deps, model, chat, sessionmaker):
    user_id, chat_id, reply_id = chat
    async with sessionmaker() as db:
        (await get_profile(db, user_id)).memory_enabled = False
        await db.commit()
    await memory.extract_memories(deps, chat_id, reply_id)
    assert model.seen == []


async def test_turn_uses_memories_and_documents_then_queues_learning(
    deps, model, chat, sessionmaker, embedder, enqueued
):
    user_id, chat_id, _ = chat
    await add_memory(sessionmaker, embedder, user_id, "Prefers tables for comparisons")
    async with sessionmaker() as db:
        f = File(
            user_id=user_id,
            chat_id=chat_id,
            name="sales.xlsx",
            content_type="x",
            size=1,
            sha256="s",
            storage_key="k",
            status=FileStatus.ready,
            chunk_count=1,
            char_count=40,
        )
        db.add(f)
        await db.flush()
        db.add(FileChunk(file_id=f.id, user_id=user_id, idx=0, content="| North | 1200 |"))
        (await get_profile(db, user_id)).content = "**About**\n- Builds Jarvis"
        reply = Message(
            chat_id=chat_id,
            role=MessageRole.assistant,
            status=MessageStatus.streaming,
            content="",
            model="jarvis-mini",
            created_at=utcnow() + timedelta(seconds=5),
        )
        db.add(reply)
        await db.commit()

    await run_turn(deps, chat_id, reply.id)

    system = model.seen[0][0].content
    assert "Builds Jarvis" in system
    assert "Prefers tables for comparisons" in system
    assert 'name="sales.xlsx"' in system and "| North | 1200 |" in system
    assert set(model.tools) >= {"search_documents", "read_document", "remember"}
    assert enqueued.jobs[-1] == ("extract_memories", str(chat_id), str(reply.id))


async def test_tool_calls_read_documents_and_count_every_call(
    deps, model, chat, sessionmaker, redis
):
    user_id, chat_id, _ = chat
    async with sessionmaker() as db:
        f = File(
            user_id=user_id,
            name="board-deck.pptx",
            content_type="x",
            size=1,
            sha256="s",
            storage_key="k",
            status=FileStatus.ready,
            chunk_count=1,
            char_count=30,
        )
        db.add(f)
        await db.flush()
        db.add(FileChunk(file_id=f.id, user_id=user_id, idx=0, content="Q3 target is 5 million"))
        reply = Message(
            chat_id=chat_id,
            role=MessageRole.assistant,
            status=MessageStatus.streaming,
            content="",
            model="jarvis-mini",
            created_at=utcnow() + timedelta(seconds=5),
        )
        db.add(reply)
        await db.commit()
    model.script = [
        {"tool": "search_documents", "args": {"query": "Q3 target"}},
        {"tool": "read_document", "args": {"file_id": str(f.id)}},
        "The Q3 target is 5 million.",
    ]

    await run_turn(deps, chat_id, reply.id)

    tool_results = [m.content for m in model.seen[2] if m.type == "tool"]
    assert "board-deck.pptx" in tool_results[0] and "5 million" in tool_results[0]
    assert tool_results[1].startswith("# board-deck.pptx")
    async with sessionmaker() as db:
        done = await db.get(Message, reply.id)
    assert done.content == "The Q3 target is 5 million."
    assert (done.input_tokens, done.output_tokens) == (5 + 5 + 12, 2 + 2 + 4)


async def test_tools_cannot_read_other_users_documents(deps, model, chat, sessionmaker):
    from jarvis_api.users import create_user

    user_id, chat_id, _ = chat
    async with sessionmaker() as db:
        other = await create_user(
            db, email="o@example.com", role=RoleName.member, status=UserStatus.active
        )
        f = File(
            user_id=other.id,
            name="theirs.txt",
            content_type="x",
            size=1,
            sha256="s",
            storage_key="k",
            status=FileStatus.ready,
            chunk_count=1,
            char_count=10,
        )
        db.add(f)
        await db.flush()
        db.add(FileChunk(file_id=f.id, user_id=other.id, idx=0, content="their secret plan"))
        reply = Message(
            chat_id=chat_id,
            role=MessageRole.assistant,
            status=MessageStatus.streaming,
            content="",
            model="jarvis-mini",
            created_at=utcnow() + timedelta(seconds=5),
        )
        db.add(reply)
        await db.commit()
    model.script = [
        {"tool": "search_documents", "args": {"query": "secret plan"}},
        {"tool": "read_document", "args": {"file_id": str(f.id)}},
        "Nothing found.",
    ]
    await run_turn(deps, chat_id, reply.id)
    tool_results = [m.content for m in model.seen[2] if m.type == "tool"]
    assert tool_results == ["No matching documents.", "No such document."]


async def test_quiet_chats_get_an_episode_that_later_chats_recall(
    deps, model, chat, sessionmaker, settings
):
    user_id, chat_id, _ = chat
    async with sessionmaker() as db:
        (await db.get(Chat, chat_id)).updated_at = utcnow() - timedelta(hours=1)
        await db.commit()
    model.script = [js(summary="The user compared LangGraph and CrewAI and chose LangGraph.")]
    assert await memory.summarize_chats(deps) == 1
    assert await memory.summarize_chats(deps) == 0  # nothing new since
    async with sessionmaker() as db:
        ep = await db.scalar(select(Episode).where(Episode.chat_id == chat_id))
    assert "chose LangGraph" in ep.content and ep.message_count == 2 and ep.embedding is not None


async def test_profile_rebuild_uses_pinned_memories(deps, model, chat, sessionmaker, embedder):
    user_id, _, _ = chat
    await add_memory(sessionmaker, embedder, user_id, "Prefers tables", pinned=True)
    model.script = ["**Preferences**\n- Prefers tables"]
    await memory.rebuild_profile(deps, user_id)
    prompt = model.seen[0][1].content
    assert "pinned" in prompt and "Prefers tables" in prompt
    async with sessionmaker() as db:
        profile = await get_profile(db, user_id)
    assert profile.content.startswith("**Preferences**") and profile.stale is False
    assert profile.built_at is not None


async def test_nightly_decays_stale_memories_and_merges_duplicates(
    deps, model, chat, sessionmaker, embedder
):
    user_id, _, _ = chat
    old = utcnow() - timedelta(days=90)
    await add_memory(
        sessionmaker, embedder, user_id, "Likes jazz", confidence=0.21, reinforced_at=old
    )
    await add_memory(sessionmaker, embedder, user_id, "Uses a MacBook", confidence=0.9)
    dup = await add_memory(sessionmaker, embedder, user_id, "uses a macbook", confidence=0.5)
    keep_pinned = await add_memory(
        sessionmaker,
        embedder,
        user_id,
        "Vegetarian",
        confidence=0.1,
        pinned=True,
        reinforced_at=old,
    )
    model.script = ["**About**\n- Uses a MacBook"]

    await memory.nightly(deps)

    left = {m.id: m for m in await memories(sessionmaker, user_id)}
    assert {m.content for m in left.values()} == {"Uses a MacBook", "Vegetarian"}
    assert dup not in left and keep_pinned in left
    async with sessionmaker() as db:
        assert (await get_profile(db, user_id)).content == "**About**\n- Uses a MacBook"
