"""Assemble what Nexus knows for one turn: the profile, relevant memories and past chats, the
connections between what the message is about, and the documents attached to the chat. All of
it is scoped to the chat's owner."""

import html
import uuid
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import graph
from nexus_common.embeddings import CachedEmbedder
from nexus_common.library import document_text
from nexus_common.memory import get_profile
from nexus_common.models import (
    Attachment,
    Chat,
    Entity,
    Episode,
    File,
    FileChunk,
    FileStatus,
    Memory,
    Mention,
    Message,
    Relation,
)
from nexus_common.search import hybrid_search
from nexus_common.settings import Settings

# Past chats further than this (cosine distance) from the question are left out.
EPISODE_MAX_DISTANCE = 0.5
# Graph recall starts from entities this close to the question (or matching it by keyword),
# and from those the closest memories mention, which count for less.
SEED_DISTANCE = 0.45
SEED_ENTITIES = 5
SEED_FROM_MEMORIES = 3
MEMORY_SEED_SCORE = 0.6
# Connections put in the prompt, and entities reported to the app, per turn.
MAX_FACTS = 12
MAX_ENTITIES = 12
# Pictures the model sees itself (turn.user_images), so their OCR text isn't added as well.
PICTURE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def is_picture(f: File) -> bool:
    return f.content_type in PICTURE_TYPES


@dataclass
class TurnContext:
    memory_enabled: bool = True
    profile: str = ""
    memories: list[Memory] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    # Connections between what the message is about, as sentences.
    facts: list[str] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
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
        if self.facts:
            lines = "\n".join(f"- {f}" for f in self.facts)
            parts.append(f"<connections>\n{lines}\n</connections>")
        if self.episodes:
            lines = "\n".join(f"- ({e.updated_at:%d %b %Y}) {e.content}" for e in self.episodes)
            parts.append(f"<past_chats>\n{lines}\n</past_chats>")
        if self.earlier:
            parts.append(f"<earlier_in_this_chat>\n{self.earlier}\n</earlier_in_this_chat>")
        if self.chat_files:
            docs = []
            for f in self.chat_files:
                attrs = f'id="{f.id}" name="{html.escape(f.name)}"'
                if is_picture(f):
                    # Shown with the message it came with; view_image shows it again.
                    docs.append(f"<picture {attrs}/>")
                elif f.status != FileStatus.ready:
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
    # Through the knowledge graph: what the question is about, best first, and the relations
    # among them as sentences.
    entities: list[Entity] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)


async def recall(
    db: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    question: str,
    vec: list[float] | None,
    *,
    exclude_chat_id: uuid.UUID | None = None,
    through_graph: bool | None = None,
) -> Recall:
    """The memories, past-chat summaries and connections a turn gets for `question`. The recall
    check on the Admin page scores this same function, with and without the graph.
    `through_graph` defaults to the GRAPH_RECALL_ENABLED setting."""
    found = Recall()
    hits = await hybrid_search(db, "memories", user_id, question, vec, settings.memory_top_k)
    if hits:
        rows = await db.scalars(select(Memory).where(Memory.id.in_([h.id for h in hits])))
        by_id = {m.id: m for m in rows.all()}
        found.memories = [
            by_id[h.id]
            for h in hits
            if h.id in by_id
            and (h.keyword or h.distance is None or h.distance < settings.memory_max_distance)
        ]
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
    if settings.graph_recall_enabled if through_graph is None else through_graph:
        await _through_graph(db, settings, user_id, question, vec, found)
    return found


async def _through_graph(
    db: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    question: str,
    vec: list[float] | None,
    found: Recall,
) -> None:
    """Add memories about what the question is about and what that links to, and the
    connections among them. Seeds are entities matching the question and those the closest
    memories mention; the walk scores each entity, and a memory scores as its best entity
    times its own confidence."""
    # A name in the question is the strongest seed; a close meaning counts for less.
    seeds: dict[uuid.UUID, float] = {
        h.id: 1.0
        for h in await hybrid_search(db, "entities", user_id, question, None, SEED_ENTITIES)
    }
    if vec is not None:
        for h in await hybrid_search(db, "entities", user_id, question, vec, SEED_ENTITIES):
            if h.distance is not None and h.distance < SEED_DISTANCE:
                seeds.setdefault(h.id, 1.0 - h.distance)
    top = [m.id for m in found.memories[:SEED_FROM_MEMORIES]]
    if top:
        for eid in (
            await db.scalars(select(Mention.entity_id).where(Mention.memory_id.in_(top)))
        ).all():
            seeds.setdefault(eid, MEMORY_SEED_SCORE)
    if seeds:
        selves = await db.scalars(
            select(Entity.id).where(Entity.id.in_(list(seeds)), Entity.is_self)
        )
        for eid in selves.all():
            seeds.pop(eid, None)
    if not seeds:
        return
    scores = await graph.walk(db, user_id, seeds, settings.graph_hops)

    have = {m.id for m in found.memories}
    best: dict[uuid.UUID, float] = {}
    rows = await db.execute(
        select(Mention.memory_id, Mention.entity_id).where(
            Mention.entity_id.in_(list(scores)), Mention.memory_id.is_not(None)
        )
    )
    for memory_id, entity_id in rows:
        if memory_id not in have:
            best[memory_id] = max(best.get(memory_id, 0.0), scores[entity_id])
    if best and settings.graph_extra_memories:
        mems = (await db.scalars(select(Memory).where(Memory.id.in_(list(best))))).all()
        ranked = sorted(mems, key=lambda m: best[m.id] * m.confidence, reverse=True)
        found.memories += ranked[: settings.graph_extra_memories]

    ents = (await db.scalars(select(Entity).where(Entity.id.in_(list(scores))))).all()
    ents = sorted(ents, key=lambda e: scores[e.id], reverse=True)
    found.entities = ents[:MAX_ENTITIES]
    # Relations that still hold and touch a walked entity, including the user's own ("works
    # at UST"), the strongest first.
    rels = (
        await db.scalars(
            select(Relation).where(
                Relation.user_id == user_id,
                Relation.valid_to.is_(None),
                or_(Relation.src_id.in_(list(scores)), Relation.dst_id.in_(list(scores))),
            )
        )
    ).all()
    names = {e.id: e.name for e in ents}
    missing = {i for r in rels for i in (r.src_id, r.dst_id)} - names.keys()
    if missing:
        for e in (await db.scalars(select(Entity).where(Entity.id.in_(missing)))).all():
            names[e.id] = e.name
    rels = sorted(
        rels,
        key=lambda r: max(scores.get(r.src_id, 0.0), scores.get(r.dst_id, 0.0)) * r.confidence,
        reverse=True,
    )
    found.facts = [
        r.fact or f"{names.get(r.src_id, '?')} {r.predicate} {names.get(r.dst_id, '?')}"
        for r in rels[:MAX_FACTS]
    ]


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
    # A picture sent on its own has no text to search with.
    asked = bool(question.strip())
    vec, _model = await embedder.try_embed_one(db, question[:4000]) if asked else (None, "")

    if profile.memory_enabled:
        ctx.profile = profile.content
    if profile.memory_enabled and asked:
        found = await recall(db, settings, user_id, question, vec, exclude_chat_id=chat.id)
        ctx.memories, ctx.episodes = found.memories, found.episodes
        ctx.facts, ctx.entities = found.facts, found.entities

    if history_truncated:
        ctx.earlier = await db.scalar(select(Episode.content).where(Episode.chat_id == chat.id))

    ctx.chat_files = await chat_files(db, chat)
    ready = [f for f in ctx.chat_files if f.status == FileStatus.ready and not is_picture(f)]
    if ready:
        if sum(f.char_count for f in ready) <= settings.inline_document_chars:
            for f in ready:
                ctx.documents[f.id] = await document_text(db, f.id)
        elif asked:
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
