"""Jarvis's own tools for a turn: search and read the user's documents, search and add to
their memory. Each tool is bound to the chat's owner, so it can only see that user's data."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_common.embeddings import CachedEmbedder
from jarvis_common.events import Event, publish
from jarvis_common.library import document_text
from jarvis_common.memory import mark_profile_stale, memory_payload, record
from jarvis_common.models import (
    Episode,
    File,
    FileChunk,
    FileStatus,
    Memory,
    MemoryKind,
)
from jarvis_common.search import hybrid_search

READ_CHARS = 12000

Emit = Callable[..., Awaitable[None]]


def build_tools(
    sessionmaker: async_sessionmaker[AsyncSession],
    embedder: CachedEmbedder,
    redis,
    user_id: uuid.UUID,
    chat_id: uuid.UUID,
    emit: Emit,
    memory_enabled: bool,
) -> list[BaseTool]:
    async def search_documents(query: str, limit: int = 6) -> str:
        """Search the text of every document the user has uploaded. Returns the best
        matching excerpts with their document name and id."""
        await emit("tool.call", name="search_documents", query=query)
        async with sessionmaker() as db:
            vec, _ = await embedder.try_embed_one(db, query)
            hits = await hybrid_search(db, "file_chunks", user_id, query, vec, min(limit, 12))
            if not hits:
                return "No matching documents."
            rows = await db.execute(
                select(FileChunk, File)
                .join(File, File.id == FileChunk.file_id)
                .where(FileChunk.id.in_([h.id for h in hits]), File.user_id == user_id)
            )
            by_id = {c.id: (c, f) for c, f in rows.all()}
        out = []
        for h in hits:
            if h.id in by_id:
                c, f = by_id[h.id]
                where = f" / {c.heading}" if c.heading else ""
                out.append(f"[{f.name}{where}] (id {f.id})\n{c.content}")
        return "\n\n---\n\n".join(out)

    async def read_document(file_id: str, offset: int = 0) -> str:
        """Read a document's text by id, about 12,000 characters at a time. Use offset to read
        further into long documents."""
        await emit("tool.call", name="read_document", file_id=file_id)
        try:
            fid = uuid.UUID(file_id)
        except ValueError:
            return "That isn't a document id."
        async with sessionmaker() as db:
            f = await db.scalar(select(File).where(File.id == fid, File.user_id == user_id))
            if f is None:
                return "No such document."
            if f.status != FileStatus.ready:
                return f"{f.name} can't be read: {f.error or 'it is still being processed'}."
            text = await document_text(db, f.id, max(offset, 0), READ_CHARS)
        end = max(offset, 0) + len(text)
        more = (
            f"\n\n[Characters {offset}-{end} of {f.char_count}. Call again with offset={end} "
            "for more.]"
            if end < f.char_count
            else ""
        )
        return f"# {f.name}\n\n{text}{more}"

    async def list_documents() -> str:
        """List the user's 40 most recent documents with their ids."""
        await emit("tool.call", name="list_documents")
        async with sessionmaker() as db:
            rows = await db.scalars(
                select(File)
                .where(File.user_id == user_id)
                .order_by(File.created_at.desc())
                .limit(40)
            )
            files = rows.all()
        if not files:
            return "The user has no documents."
        return "\n".join(
            f"- {f.name} (id {f.id}, {f.status}, uploaded {f.created_at:%d %b %Y})" for f in files
        )

    async def search_memory(query: str) -> str:
        """Search what you remember about the user and summaries of past chats."""
        await emit("tool.call", name="search_memory", query=query)
        if not memory_enabled:
            return "The user has turned memory off."
        async with sessionmaker() as db:
            vec, _ = await embedder.try_embed_one(db, query)
            mem_hits = await hybrid_search(db, "memories", user_id, query, vec, 10)
            ep_hits = await hybrid_search(db, "episodes", user_id, query, vec, 4)
            mems = (
                await db.scalars(select(Memory).where(Memory.id.in_([h.id for h in mem_hits])))
            ).all()
            eps = (
                await db.scalars(select(Episode).where(Episode.id.in_([h.id for h in ep_hits])))
            ).all()
        lines = [f"- [{m.kind}] {m.content}" for m in mems]
        lines += [f"- (past chat, {e.updated_at:%d %b %Y}) {e.content}" for e in eps]
        return "\n".join(lines) or "Nothing found."

    async def remember(
        content: str, kind: Literal["preference", "fact", "goal", "instruction"] = "fact"
    ) -> str:
        """Save something the user explicitly asked you to remember, as one short sentence
        in the third person."""
        await emit("tool.call", name="remember")
        if not memory_enabled:
            return "Not saved: the user has turned memory off."
        async with sessionmaker() as db:
            vec, model = await embedder.try_embed_one(db, content)
            m = Memory(
                id=uuid.uuid4(),
                user_id=user_id,
                kind=MemoryKind(kind),
                content=content.strip()[:1000],
                confidence=0.95,
                source="jarvis",
                source_chat_id=chat_id,
                embedding=vec,
                embedding_model=model or None,
            )
            db.add(m)
            await db.flush()
            record(db, m, "add", "jarvis", m.content)
            await mark_profile_stale(db, user_id)
            await db.commit()
            await publish(
                redis,
                user_id,
                Event(type="memory.updated", data={"op": "add", "memory": memory_payload(m)}),
            )
        return "Saved."

    fns = [search_documents, read_document, list_documents, search_memory, remember]
    return [StructuredTool.from_function(coroutine=fn) for fn in fns]
