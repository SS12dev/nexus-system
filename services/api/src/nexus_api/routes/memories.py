"""The Memory page: see, search, add, edit, pin and delete what Nexus remembers about you, and
your profile, and the connections (knowledge graph) between the people, projects and topics
your memories are about. Deleting a memory deletes it and its history for real, and with it
anything the graph learned only from it."""

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.deps import CurrentUser, DbDep, EmbedderDep, MemberUser, QueueDep
from nexus_api.schemas import (
    MemoryCreateIn,
    MemoryEventOut,
    MemoryOut,
    MemoryUpdateIn,
    ProfileOut,
    ProfileUpdateIn,
)
from nexus_common import graph
from nexus_common.memory import get_profile, mark_profile_stale, record
from nexus_common.models import Entity, Episode, Memory, MemoryEvent, User, utcnow
from nexus_common.search import hybrid_search

router = APIRouter(prefix="/api/memories", tags=["memories"])


async def own_memory(db: AsyncSession, user: User, memory_id: uuid.UUID) -> Memory:
    m = await db.scalar(select(Memory).where(Memory.id == memory_id, Memory.user_id == user.id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memory not found")
    return m


async def profile_out(db: AsyncSession, user: User) -> ProfileOut:
    profile = await get_profile(db, user.id)
    count = await db.scalar(select(func.count()).where(Memory.user_id == user.id))
    await db.commit()
    return ProfileOut(
        content=profile.content,
        memory_enabled=profile.memory_enabled,
        built_at=profile.built_at,
        stale=profile.stale,
        memory_count=count or 0,
    )


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    user: CurrentUser,
    db: DbDep,
    embedder: EmbedderDep,
    q: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=500, le=2000),
):
    if q:
        vec, _model = await embedder.try_embed_one(db, q)
        hits = await hybrid_search(db, "memories", user.id, q, vec, min(limit, 50))
        rows = await db.scalars(select(Memory).where(Memory.id.in_([h.id for h in hits])))
        by_id = {m.id: m for m in rows.all()}
        return [by_id[h.id] for h in hits if h.id in by_id]
    rows = await db.scalars(
        select(Memory)
        .where(Memory.user_id == user.id)
        .order_by(Memory.pinned.desc(), Memory.updated_at.desc())
        .limit(limit)
    )
    return rows.all()


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def add_memory(body: MemoryCreateIn, user: MemberUser, db: DbDep, embedder: EmbedderDep):
    vec, model = await embedder.try_embed_one(db, body.content)
    m = Memory(
        id=uuid.uuid4(),
        user_id=user.id,
        kind=body.kind,
        content=body.content.strip(),
        confidence=1.0,
        pinned=body.pinned,
        source="user",
        embedding=vec,
        embedding_model=model or None,
    )
    db.add(m)
    await db.flush()
    record(db, m, "add", "user", m.content)
    await mark_profile_stale(db, user.id)
    await db.commit()
    return m


@router.get("/profile", response_model=ProfileOut)
async def get_my_profile(user: CurrentUser, db: DbDep):
    return await profile_out(db, user)


@router.patch("/profile", response_model=ProfileOut)
async def update_profile(body: ProfileUpdateIn, user: MemberUser, db: DbDep):
    profile = await get_profile(db, user.id)
    profile.memory_enabled = body.memory_enabled
    await db.commit()
    return await profile_out(db, user)


@router.post("/profile/rebuild", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_profile(user: MemberUser, queue: QueueDep):
    await queue.enqueue_profile_rebuild(user.id)
    return {"status": "queued"}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def forget_everything(user: MemberUser, db: DbDep):
    """Delete every memory, past-chat summary, connection and the profile text."""
    await db.execute(delete(Entity).where(Entity.user_id == user.id))
    await db.execute(delete(Memory).where(Memory.user_id == user.id))
    await db.execute(delete(Episode).where(Episode.user_id == user.id))
    profile = await get_profile(db, user.id)
    profile.content, profile.built_at, profile.stale = "", None, False
    await db.commit()


@router.get("/graph")
async def get_graph(user: CurrentUser, db: DbDep):
    """Every entity with what mentions it, and every relation, current and ended."""
    return await graph.graph_payload(db, user.id)


@router.delete("/entities/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entity(entity_id: uuid.UUID, user: MemberUser, db: DbDep):
    """Remove a person, project or topic and its connections. Memories about it stay."""
    e = await db.scalar(select(Entity).where(Entity.id == entity_id, Entity.user_id == user.id))
    if e is None or e.is_self:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    await db.delete(e)
    await db.commit()


@router.get("/{memory_id}/events", response_model=list[MemoryEventOut])
async def memory_events(memory_id: uuid.UUID, user: CurrentUser, db: DbDep):
    m = await own_memory(db, user, memory_id)
    rows = await db.scalars(
        select(MemoryEvent).where(MemoryEvent.memory_id == m.id).order_by(MemoryEvent.created_at)
    )
    return rows.all()


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: uuid.UUID, body: MemoryUpdateIn, user: MemberUser, db: DbDep, embedder: EmbedderDep
):
    m = await own_memory(db, user, memory_id)
    if body.content is not None and body.content.strip() != m.content:
        m.content = body.content.strip()
        m.embedding, model = await embedder.try_embed_one(db, m.content)
        m.embedding_model = model or None
        m.source, m.confidence = "user", 1.0
        record(db, m, "update", "user", m.content)
        # Linked again from the new wording by the next graph job.
        await graph.forget_mentions(db, memory_id=m.id)
        m.graph_at = None
    if body.kind is not None:
        m.kind = body.kind
    if body.pinned is not None and body.pinned != m.pinned:
        m.pinned = body.pinned
        record(db, m, "pin" if body.pinned else "unpin", "user")
    m.updated_at = utcnow()
    await mark_profile_stale(db, user.id)
    await db.commit()
    return m


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID, user: MemberUser, db: DbDep, queue: QueueDep, rebuild: bool = False
):
    """Delete a memory. With rebuild=true the profile is rebuilt now instead of overnight, so
    the deleted fact leaves the profile at once."""
    m = await own_memory(db, user, memory_id)
    await db.delete(m)
    await db.flush()
    await graph.prune(db, user.id)
    await mark_profile_stale(db, user.id)
    await db.commit()
    if rebuild:
        await queue.enqueue_profile_rebuild(user.id)
