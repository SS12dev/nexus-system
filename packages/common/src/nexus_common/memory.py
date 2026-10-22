"""Memory helpers shared by the API (the Memory page) and the orchestrator (learning in chats)."""

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import Memory, MemoryEvent, Profile


async def get_profile(db: AsyncSession, user_id: uuid.UUID) -> Profile:
    await db.execute(
        insert(Profile)
        .values(user_id=user_id, content="", memory_enabled=True, stale=False)
        .on_conflict_do_nothing()
    )
    profile = await db.get(Profile, user_id, populate_existing=True)
    assert profile is not None
    return profile


async def mark_profile_stale(db: AsyncSession, user_id: uuid.UUID) -> None:
    profile = await get_profile(db, user_id)
    profile.stale = True


def record(
    db: AsyncSession,
    memory: Memory,
    op: str,
    actor: str,
    content: str | None = None,
    message_id: uuid.UUID | None = None,
) -> None:
    db.add(
        MemoryEvent(
            memory_id=memory.id,
            user_id=memory.user_id,
            op=op,
            actor=actor,
            content=content,
            message_id=message_id,
        )
    )


def memory_payload(m: Memory) -> dict:
    return {
        "id": str(m.id),
        "kind": m.kind,
        "content": m.content,
        "confidence": m.confidence,
        "pinned": m.pinned,
        "source": m.source,
        "source_chat_id": str(m.source_chat_id) if m.source_chat_id else None,
        "created_at": m.created_at.isoformat(),
        "updated_at": m.updated_at.isoformat(),
        "reinforced_at": m.reinforced_at.isoformat(),
    }
