"""Assemble what Nexus knows for one turn: the profile, relevant memories and past chats, and
the documents attached to the chat. All of it is scoped to the chat's owner."""

import html
import uuid
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.embeddings import CachedEmbedder
from nexus_common.library import document_text
from nexus_common.memory import get_profile
from nexus_common.models import (
    Attachment,
    Chat,
    Episode,
    File,
    FileChunk,
    FileStatus,
    Memory,
    Message,
)
from nexus_common.search import hybrid_search
from nexus_common.settings import Settings

# Past chats further than this (cosine distance) from the question are left out.
EPISODE_MAX_DISTANCE = 0.5


@dataclass
class TurnContext:
    memory_enabled: bool = True
    profile: str = ""
    memories: list[Memory] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    # Summary of the start of this chat, when the history sent is cut short.
    earlier: str | None = None
    chat_files: list[File] = field(default_factory=list)
    # Whole text of the attached documents, when they are short enough.
    documents: dict[uuid.UUID, str] = field(default_factory=dict)
    excerpts: list[tuple[File, FileChunk]] = field(default_factory=list)

    def render(self) -> str:
        parts: list[str] = []
        if self.profile:
            parts.append(f"<profile>\n{self.profile}\n</profile>")
        if self.memories:
            lines = "\n".join(f"- [{m.kind}] {m.content}" for m in self.memories)
            parts.append(f"<memories>\n{lines}\n</memories>")
        if self.episodes:
            lines = "\n".join(f"- ({e.updated_at:%d %b %Y}) {e.content}" for e in self.episodes)
            parts.append(f"<past_chats>\n{lines}\n</past_chats>")
        if self.earlier:
            parts.append(f"<earlier_in_this_chat>\n{self.earlier}\n</earlier_in_this_chat>")
        if self.chat_files:
            docs = []
            for f in self.chat_files:
                attrs = f'id="{f.id}" name="{html.escape(f.name)}"'
                if f.status != FileStatus.ready:
                    note = "still being read" if f.status == FileStatus.processing else f.error
                    docs.append(f'<document {attrs} unavailable="{html.escape(note or "")}"/>')
                elif f.id in self.documents:
                    docs.append(f"<document {attrs}>\n{self.documents[f.id]}\n</document>")
                else:
                    docs.append(f'<document {attrs} chars="{f.char_count}" excerpts_only="true"/>')
            for f, chunk in self.excerpts:
                where = f' section="{html.escape(chunk.heading)}"' if chunk.heading else ""
                docs.append(
                    f'<excerpt document="{html.escape(f.name)}" id="{f.id}"{where}>\n'
                    f"{chunk.content}\n</excerpt>"
                )
            parts.append("<documents>\n" + "\n".join(docs) + "\n</documents>")
        return "\n\n".join(parts)


async def chat_files(db: AsyncSession, chat: Chat) -> list[File]:
    attached = select(Attachment.file_id).join(Message, Message.id == Attachment.message_id)
    rows = await db.scalars(
        select(File)
        .where(
            File.user_id == chat.user_id,
            or_(
                File.chat_id == chat.id,
                File.id.in_(attached.where(Message.chat_id == chat.id)),
            ),
        )
        .order_by(File.created_at)
    )
    return list(rows.all())


@dataclass
class Recall:
    memories: list[Memory] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)


async def recall(
    db: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    question: str,
    vec: list[float] | None,
    *,
    exclude_chat_id: uuid.UUID | None = None,
) -> Recall:
    """The memories and past-chat summaries a turn gets for `question`. The recall check on
    the Admin page scores this same function."""
    found = Recall()
    hits = await hybrid_search(db, "memories", user_id, question, vec, settings.memory_top_k)
    if hits:
        rows = await db.scalars(select(Memory).where(Memory.id.in_([h.id for h in hits])))
        by_id = {m.id: m for m in rows.all()}
        found.memories = [by_id[h.id] for h in hits if h.id in by_id]
    if vec is not None and settings.episode_top_k:
        hits = await hybrid_search(
            db,
            "episodes",
            user_id,
            question,
            vec,
            settings.episode_top_k * 3,
            exclude_chat_id=exclude_chat_id,
        )
        close = [
            h.id for h in hits if h.distance is not None and h.distance < EPISODE_MAX_DISTANCE
        ][: settings.episode_top_k]
        if close:
            rows = await db.scalars(select(Episode).where(Episode.id.in_(close)))
            by_id = {e.id: e for e in rows.all()}
            found.episodes = [by_id[i] for i in close if i in by_id]
    return found


async def assemble(
    db: AsyncSession,
    settings: Settings,
    embedder: CachedEmbedder,
    chat: Chat,
    question: str,
    history_truncated: bool,
) -> TurnContext:
    ctx = TurnContext()
    user_id = chat.user_id
    profile = await get_profile(db, user_id)
    ctx.memory_enabled = profile.memory_enabled
    vec, _model = await embedder.try_embed_one(db, question[:4000])

    if profile.memory_enabled:
        ctx.profile = profile.content
        found = await recall(db, settings, user_id, question, vec, exclude_chat_id=chat.id)
        ctx.memories, ctx.episodes = found.memories, found.episodes

    if history_truncated:
        ctx.earlier = await db.scalar(select(Episode.content).where(Episode.chat_id == chat.id))

    ctx.chat_files = await chat_files(db, chat)
    ready = [f for f in ctx.chat_files if f.status == FileStatus.ready]
    if ready:
        if sum(f.char_count for f in ready) <= settings.inline_document_chars:
            for f in ready:
                ctx.documents[f.id] = await document_text(db, f.id)
        else:
            hits = await hybrid_search(
                db,
                "file_chunks",
                user_id,
                question,
                vec,
                settings.chunk_top_k,
                file_ids=[f.id for f in ready],
            )
            if hits:
                rows = await db.scalars(
                    select(FileChunk).where(FileChunk.id.in_([h.id for h in hits]))
                )
                by_id = {c.id: c for c in rows.all()}
                files = {f.id: f for f in ready}
                ctx.excerpts = [
                    (files[by_id[h.id].file_id], by_id[h.id]) for h in hits if h.id in by_id
                ]
    return ctx
