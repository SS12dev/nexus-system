"""Long-term memory jobs, run on the orchestrator queue:

- extract_memories, after each turn: the cheapest model proposes facts about the user; each is
  compared with its nearest existing memories and becomes ADD, UPDATE or NOOP (the Mem0
  pattern), or deletes memories the user asked to forget.
- summarize_chats, every few minutes: quiet chats get an episode summary for later recall.
- rebuild_profile: the short profile at the top of every prompt, from the user's memories.
- nightly: decay memories nobody confirmed for a while, merge near duplicates, rebuild stale
  profiles.
"""

import logging
import re
import uuid
from datetime import timedelta

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import graph
from nexus_common.embeddings import EmbeddingError
from nexus_common.events import Event, publish
from nexus_common.library import document_text
from nexus_common.litellm import ensure_user_key
from nexus_common.memory import get_profile, mark_profile_stale, memory_payload, record
from nexus_common.models import (
    AgentSystem,
    Chat,
    Entity,
    Episode,
    File,
    FileStatus,
    Memory,
    MemoryKind,
    Message,
    MessageRole,
    MessageStatus,
    Profile,
    Skill,
    TrashItem,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.search import hybrid_search
from nexus_common.trash import visible, visible_file
from nexus_orchestrator.llm import parse_json, private
from nexus_orchestrator.prompts import (
    EPISODE,
    GRAPH_EXTRACT,
    MEMORY_DECIDE,
    MEMORY_EXTRACT,
    PROFILE,
)
from nexus_orchestrator.turn import TurnDeps

log = logging.getLogger(__name__)

NEIGHBOURS = 5
# Memories nobody has confirmed for this long lose confidence each night...
DECAY_AFTER = timedelta(days=60)
DECAY_FACTOR = 0.85
# ...and are dropped below this.
MIN_CONFIDENCE = 0.2
# Two memories closer than this (cosine distance) are treated as duplicates.
DUPLICATE_DISTANCE = 0.06
PROFILE_MEMORIES = 80


async def ask_memory_model(
    deps: TurnDeps,
    db: AsyncSession,
    user: User,
    system: str,
    prompt: str,
    *,
    name: str,
    session_id: str | None,
    json_mode: bool = True,
) -> str:
    """One call to the memory model, billed to the user's own key and traced in Langfuse."""
    api_key = await ensure_user_key(db, deps.settings, deps.gateway, user)
    model = deps.model_factory(
        model=deps.settings.memory_model,
        api_key=api_key,
        metadata={
            "trace_id": uuid.uuid4().hex,
            "trace_name": f"nexus.{name}",
            "generation_name": f"nexus.{name}",
            "session_id": session_id,
            "trace_user_id": str(user.id),
            **private(deps.settings, user),
            "tags": ["nexus", "memory", deps.settings.env],
        },
    )
    runnable = model.bind(response_format={"type": "json_object"}) if json_mode else model
    reply = await runnable.ainvoke([SystemMessage(system), HumanMessage(prompt)])
    return reply.text if isinstance(reply.text, str) else reply.text()


async def _active_user(db: AsyncSession, user_id: uuid.UUID) -> tuple[User, Profile] | None:
    user = await db.get(User, user_id)
    if user is None or user.status != UserStatus.active:
        return None
    profile = await get_profile(db, user_id)
    if not profile.memory_enabled:
        return None
    return user, profile


async def _announce(deps: TurnDeps, m: Memory, op: str) -> None:
    payload = {"op": op, "memory": memory_payload(m) if op != "delete" else {"id": str(m.id)}}
    await publish(deps.redis, m.user_id, Event(type="memory.updated", data=payload))


# --- Write path ------------------------------------------------------------------------------


async def extract_memories(deps: TurnDeps, chat_id: uuid.UUID, message_id: uuid.UUID) -> None:
    async with deps.sessionmaker() as db:
        reply = await db.get(Message, message_id)
        chat = await db.get(Chat, chat_id)
        if reply is None or chat is None or reply.chat_id != chat.id:
            return
        found = await _active_user(db, chat.user_id)
        if found is None:
            return
        user, _profile = found
        recent = (
            await db.scalars(
                select(Message)
                .where(
                    Message.chat_id == chat.id,
                    Message.status == MessageStatus.complete,
                    Message.created_at <= reply.created_at,
                )
                .order_by(Message.created_at.desc())
                .limit(6)
            )
        ).all()
        recent = list(reversed(recent))
        latest_user = next((m for m in reversed(recent) if m.role == MessageRole.user), None)
        if latest_user is None or len(latest_user.content.strip()) < 8:
            return
        transcript = "\n\n".join(
            f"{'USER' if m.role == MessageRole.user else 'ASSISTANT'}: {m.content[:3000]}"
            for m in recent
        )
        raw = await ask_memory_model(
            deps,
            db,
            user,
            MEMORY_EXTRACT,
            f"Conversation (the last USER message is the one to learn from):\n\n{transcript}",
            name="memory.extract",
            session_id=str(chat.id),
        )
        data = parse_json(raw)
        candidates = _candidates(data.get("memories"))
        forget = [str(f)[:300] for f in data.get("forget") or [] if str(f).strip()][:5]
        found = graph.parse_graph(data) if deps.settings.graph_enabled else graph.Extracted()
        if not candidates and not forget and not found:
            return
        await apply_candidates(deps, db, user, chat, latest_user, candidates, forget, found)


# Task requests the extractor still sometimes saves ("Wants help choosing a bike", "Agreed to
# build the 2048 MVP"). They describe one chat, not the user, so they are dropped.
REQUEST = re.compile(
    r"^(the user )?(asked|asks|requested|requests|agreed|approved|is asking|is looking for help|"
    r"needs help|wants (help|research|advice|assistance|a recommendation|recommendations|"
    r"suggestions|to know|a team|an agent|jarvis|the assistant)|would like (help|to know))\b"
    # "Wants the habit-tracker app fixed and deployed", "They want the assistant to deploy it".
    r"|^(the user |they |he |she )?wants? (the|this|that|their|his|her) .{0,120}\b(fixed|built|"
    r"deployed|done|made|created|written|finished|implemented|to (implement|fix|build|deploy|"
    r"make|create|write|finish|do))\b",
    re.I,
)


def is_request(content: str) -> bool:
    return bool(REQUEST.match(content.strip()))


def _candidates(raw) -> list[dict]:
    out = []
    for item in raw or []:
        if not isinstance(item, dict) or not str(item.get("content", "")).strip():
            continue
        if is_request(str(item["content"])):
            continue
        kind = item.get("kind")
        try:
            conf = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        out.append(
            {
                "content": str(item["content"]).strip()[:500],
                "kind": kind if kind in MemoryKind.__members__ else "fact",
                "confidence": min(max(conf, 0.1), 1.0),
                "about": graph.about_names(item.get("about")),
            }
        )
    return out[:10]


async def apply_candidates(
    deps: TurnDeps,
    db: AsyncSession,
    user: User,
    chat: Chat,
    source: Message,
    candidates: list[dict],
    forget: list[str],
    found: graph.Extracted | None = None,
) -> None:
    vectors: list[list[float] | None] = [None] * len(candidates)
    model = ""
    if candidates:
        try:
            vecs, model = await deps.embedder.embed(db, [c["content"] for c in candidates])
            vectors = list(vecs)
        except EmbeddingError as exc:
            log.warning("Embedding memories failed, keyword matching only: %s", exc)

    # Existing memories near each candidate (and near each forget request), numbered for the
    # model so it never has to copy a UUID.
    existing: dict[uuid.UUID, Memory] = {}
    for query, vec in [
        *((c["content"], v) for c, v in zip(candidates, vectors, strict=True)),
        *((f, None) for f in forget),
    ]:
        hits = await hybrid_search(db, "memories", user.id, query, vec, NEIGHBOURS)
        if hits:
            rows = await db.scalars(
                select(Memory).where(
                    Memory.id.in_([h.id for h in hits]),
                    visible("memory", Memory.id, user.id),
                )
            )
            for m in rows.all():
                existing[m.id] = m
    numbered = list(existing.values())

    if numbered:
        listing = "\n".join(
            f"{i}: {m.content}{' (pinned)' if m.pinned else ''}" for i, m in enumerate(numbered)
        )
        cands = "\n".join(f"{i}: {c['content']}" for i, c in enumerate(candidates)) or "(none)"
        forgets = "\n".join(f"- {f}" for f in forget) or "(none)"
        raw = await ask_memory_model(
            deps,
            db,
            user,
            MEMORY_DECIDE,
            f"EXISTING:\n{listing}\n\nNEW:\n{cands}\n\nFORGET requests:\n{forgets}",
            name="memory.decide",
            session_id=str(chat.id),
        )
        decision = parse_json(raw)
        actions = decision.get("actions") or []
        to_delete = decision.get("delete") or []
    else:
        actions = [{"candidate": i, "op": "ADD"} for i in range(len(candidates))]
        to_delete = []

    now = utcnow()
    changed: list[tuple[Memory, str]] = []
    # Which memory each candidate ended up as, to link it to the entities it is about.
    by_candidate: dict[int, Memory] = {}
    handled: set[int] = set()
    for act in actions:
        if not isinstance(act, dict):
            continue
        i, op, target = act.get("candidate"), str(act.get("op", "")).upper(), act.get("target")
        if not isinstance(i, int) or not 0 <= i < len(candidates) or i in handled:
            continue
        handled.add(i)
        cand, vec = candidates[i], vectors[i]
        old = numbered[target] if isinstance(target, int) and 0 <= target < len(numbered) else None
        if op == "NOOP" and old is not None:
            old.confidence = min(1.0, old.confidence + 0.1)
            old.reinforced_at = now
            record(db, old, "reinforce", "assistant", None, source.id)
            changed.append((old, "reinforce"))
            by_candidate[i] = old
        elif op == "UPDATE" and old is not None and not old.pinned:
            content = str(act.get("content") or cand["content"]).strip()[:500]
            old.content = content
            old.kind = MemoryKind(cand["kind"])
            old.confidence = min(1.0, max(old.confidence, cand["confidence"]) + 0.05)
            old.updated_at = old.reinforced_at = now
            old.source, old.source_chat_id, old.source_message_id = "assistant", chat.id, source.id
            if content == cand["content"] and vec is not None:
                old.embedding, old.embedding_model = vec, model
            else:
                old.embedding = old.embedding_model = None  # the backfill re-embeds it
            record(db, old, "update", "assistant", content, source.id)
            changed.append((old, "update"))
            by_candidate[i] = old
        elif op in ("ADD", "UPDATE"):
            # UPDATE of a pinned or unknown memory falls back to adding the new fact.
            m = Memory(
                id=uuid.uuid4(),
                user_id=user.id,
                kind=MemoryKind(cand["kind"]),
                content=cand["content"],
                confidence=cand["confidence"],
                source="assistant",
                source_chat_id=chat.id,
                source_message_id=source.id,
                embedding=vec,
                embedding_model=model if vec is not None else None,
            )
            db.add(m)
            await db.flush()
            record(db, m, "add", "assistant", m.content, source.id)
            changed.append((m, "add"))
            by_candidate[i] = m

    deleted: list[Memory] = []
    for target in to_delete:
        if isinstance(target, int) and 0 <= target < len(numbered):
            m = numbered[target]
            if not m.pinned and m not in deleted:
                await db.delete(m)
                deleted.append(m)

    linked = 0
    if deps.settings.graph_enabled and (found or by_candidate):
        ops = {m.id: op for m, op in changed}
        links = []
        for i, m in by_candidate.items():
            about = candidates[i]["about"]
            if ops[m.id] == "update":
                await graph.forget_mentions(db, memory_id=m.id)
            if about:
                links.append(({"memory_id": m.id}, about))
            # A reinforced memory keeps its links unless the model named new ones.
            if about or ops[m.id] != "reinforce":
                m.graph_at = now
        linked = await graph.ingest(
            db, deps.embedder, user, found or graph.Extracted(), chat_id=chat.id, links=links
        )
    if deleted:
        await db.flush()
        await graph.prune(db, user.id)

    if not changed and not deleted and not linked:
        return
    if any(op != "reinforce" for _, op in changed) or deleted:
        await mark_profile_stale(db, user.id)
    profile = await get_profile(db, user.id)
    first_profile = profile.built_at is None
    await db.commit()
    for m, op in changed:
        await _announce(deps, m, op)
    for m in deleted:
        await _announce(deps, m, "delete")
    log.info(
        "Memory for %s: %s, %d deleted",
        user.id,
        ", ".join(op for _, op in changed) or "no changes",
        len(deleted),
    )
    # A new user gets a profile soon after their first memories instead of overnight.
    if first_profile and deps.enqueue is not None:
        await deps.enqueue(
            "rebuild_profile", str(user.id), _job_id=f"profile-first:{user.id}", _defer_by=60
        )


# --- Episodes --------------------------------------------------------------------------------


async def summarize_chats(deps: TurnDeps, limit: int = 20) -> int:
    """Summarise chats that went quiet, and long chats that grew a lot since their summary."""
    idle_before = utcnow() - timedelta(minutes=deps.settings.episode_idle_minutes)
    async with deps.sessionmaker() as db:
        counts = (
            select(Message.chat_id, func.count().label("n"))
            .where(Message.status == MessageStatus.complete)
            .group_by(Message.chat_id)
            .subquery()
        )
        rows = await db.execute(
            select(Chat.id, counts.c.n)
            .join(counts, counts.c.chat_id == Chat.id)
            .outerjoin(Episode, Episode.chat_id == Chat.id)
            .join(User, User.id == Chat.user_id)
            .outerjoin(Profile, Profile.user_id == Chat.user_id)
            .where(
                User.status == UserStatus.active,
                Profile.memory_enabled.is_not(False),
                # A routine's runs (a news digest) say nothing about the person.
                Chat.kind == "chat",
                counts.c.n >= 2,
                or_(
                    (Chat.updated_at < idle_before)
                    & or_(Episode.id.is_(None), Episode.message_count < counts.c.n),
                    counts.c.n - func.coalesce(Episode.message_count, 0) >= 30,
                ),
            )
            .order_by(Chat.updated_at)
            .limit(limit)
        )
        todo = rows.all()
    done = 0
    for chat_id, n in todo:
        try:
            if await summarize_chat(deps, chat_id, n):
                done += 1
        except Exception:
            log.exception("Summary of chat %s failed", chat_id)
    return done


async def summarize_chat(deps: TurnDeps, chat_id: uuid.UUID, count: int) -> bool:
    async with deps.sessionmaker() as db:
        chat = await db.get(Chat, chat_id)
        found = await _active_user(db, chat.user_id) if chat else None
        if found is None:
            return False
        user, _ = found
        episode = await db.scalar(select(Episode).where(Episode.chat_id == chat_id))
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.chat_id == chat_id, Message.status == MessageStatus.complete)
                .order_by(Message.created_at.desc())
                .limit(60)
            )
        ).all()
        transcript = "\n\n".join(
            f"{'USER' if m.role == MessageRole.user else 'ASSISTANT'}: {m.content[:2000]}"
            for m in reversed(messages)
        )
        earlier = f"Summary of earlier messages:\n{episode.content}\n\n" if episode else ""
        raw = await ask_memory_model(
            deps,
            db,
            user,
            EPISODE,
            f"Chat title: {chat.title or 'untitled'}\n\n{earlier}Messages:\n\n{transcript}",
            name="episode",
            session_id=str(chat_id),
        )
        data = parse_json(raw)
        summary = str(data.get("summary") or "").strip()
        if not summary:
            return False
        vec, model = await deps.embedder.try_embed_one(db, summary)
        now = utcnow()
        if episode is None:
            episode = Episode(user_id=user.id, chat_id=chat_id, created_at=now)
            db.add(episode)
        episode.content = summary[:4000]
        episode.message_count = count
        episode.embedding, episode.embedding_model = vec, (model or None)
        episode.updated_at = now
        if deps.settings.graph_enabled:
            await db.flush()
            found = graph.parse_graph(data)
            await graph.forget_mentions(db, episode_id=episode.id)
            names = [graph.norm(e["name"]) for e in found.entities]
            await graph.ingest(
                db,
                deps.embedder,
                user,
                found,
                chat_id=chat_id,
                links=[({"episode_id": episode.id}, names)],
            )
            episode.graph_at = now
        await db.commit()
        return True


# --- Knowledge graph backfill -----------------------------------------------------------------

# Items per call and characters per item when linking memories, past chats and files that the
# chat-time extraction didn't cover: ones added on the Memory page, files, and everything from
# before the graph existed.
GRAPH_BATCH = 20
GRAPH_ITEM_CHARS = 1200
GRAPH_CALLS_PER_USER = 3


async def link_graph(deps: TurnDeps, max_users: int = 20) -> int:
    """Link pending items into each user's graph, a few calls per user per run."""
    if not deps.settings.graph_enabled:
        return 0
    async with deps.sessionmaker() as db:
        pending = (
            select(Memory.user_id.label("user_id"))
            .where(Memory.graph_at.is_(None))
            .union(
                select(Episode.user_id).where(Episode.graph_at.is_(None)),
                select(File.user_id).where(
                    File.graph_at.is_(None), File.status == FileStatus.ready
                ),
            )
            .subquery()
        )
        user_ids = (
            await db.scalars(
                select(User.id)
                .join(pending, pending.c.user_id == User.id)
                .outerjoin(Profile, Profile.user_id == User.id)
                .where(User.status == UserStatus.active, Profile.memory_enabled.is_not(False))
                .limit(max_users)
            )
        ).all()
    done = 0
    for user_id in user_ids:
        for _ in range(GRAPH_CALLS_PER_USER):
            try:
                n = await _link_batch(deps, user_id)
            except Exception:
                log.exception("Linking the graph for %s failed", user_id)
                break
            done += n
            if n < GRAPH_BATCH:
                break
    return done


async def _link_batch(deps: TurnDeps, user_id: uuid.UUID) -> int:
    async with deps.sessionmaker() as db:
        user = await db.get(User, user_id)
        items: list[tuple[dict, str, Memory | Episode | File]] = []
        for m in (
            await db.scalars(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.graph_at.is_(None),
                    visible("memory", Memory.id, user_id),
                )
                .limit(GRAPH_BATCH)
            )
        ).all():
            items.append(({"memory_id": m.id}, f"Memory: {m.content}", m))
        for e in (
            await db.scalars(
                select(Episode)
                .where(
                    Episode.user_id == user_id,
                    Episode.graph_at.is_(None),
                    ~select(TrashItem.id)
                    .where(TrashItem.kind == "memories_all", TrashItem.user_id == user_id)
                    .exists(),
                )
                .limit(max(0, GRAPH_BATCH - len(items)))
            )
        ).all():
            items.append(({"episode_id": e.id}, f"Past chat: {e.content[:GRAPH_ITEM_CHARS]}", e))
        for f in (
            await db.scalars(
                select(File)
                .where(
                    File.user_id == user_id,
                    File.graph_at.is_(None),
                    File.status == FileStatus.ready,
                    visible_file(File.id, File.folder_id),
                )
                .limit(max(0, GRAPH_BATCH - len(items)))
            )
        ).all():
            excerpt = await document_text(db, f.id, limit=GRAPH_ITEM_CHARS)
            items.append(({"file_id": f.id}, f"Document {f.name!r}: {excerpt}", f))
        if not items:
            return 0
        listing = "\n\n".join(f"{i}: {text}" for i, (_t, text, _r) in enumerate(items))
        raw = await ask_memory_model(
            deps, db, user, GRAPH_EXTRACT, f"Items:\n\n{listing}", name="graph", session_id=None
        )
        data = parse_json(raw)
        about: dict[int, list[str]] = {}
        for a in data.get("about") or []:
            if isinstance(a, dict) and isinstance(a.get("item"), int):
                about[a["item"]] = graph.about_names(a.get("entities"))
        now = utcnow()
        links = []
        for i, (target, _text, row) in enumerate(items):
            await graph.forget_mentions(db, **target)
            if about.get(i):
                links.append((target, about[i]))
            row.graph_at = now
        await graph.ingest(db, deps.embedder, user, graph.parse_graph(data), links=links)
        await graph.prune(db, user_id)
        await db.commit()
        return len(items)


# --- Profile ---------------------------------------------------------------------------------


async def rebuild_profile(deps: TurnDeps, user_id: uuid.UUID) -> None:
    async with deps.sessionmaker() as db:
        user = await db.get(User, user_id)
        if user is None or user.status != UserStatus.active:
            return
        profile = await get_profile(db, user_id)
        memories = (
            await db.scalars(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    visible("memory", Memory.id, user_id),
                )
                .order_by(Memory.pinned.desc(), Memory.confidence.desc(), Memory.updated_at.desc())
                .limit(PROFILE_MEMORIES)
            )
        ).all()
        if memories:
            listing = "\n".join(
                f"- [{m.kind}{', pinned' if m.pinned else ''}, confidence {m.confidence:.1f}, "
                f"{m.updated_at:%Y-%m-%d}] {m.content}"
                for m in memories
            )
            content = await ask_memory_model(
                deps,
                db,
                user,
                PROFILE,
                f"Memories about {user.name or 'the user'}:\n{listing}",
                name="profile",
                session_id=None,
                json_mode=False,
            )
        else:
            content = ""
        profile.content = content.strip()[:6000]
        profile.built_at = utcnow()
        profile.stale = False
        await db.commit()
        await publish(
            deps.redis,
            user_id,
            Event(type="profile.updated", data={"content": profile.content}),
        )


# --- Upkeep ----------------------------------------------------------------------------------


async def nightly(deps: TurnDeps) -> None:
    async with deps.sessionmaker() as db:
        now = utcnow()
        await db.execute(
            update(Memory)
            .where(
                Memory.pinned.is_(False),
                Memory.reinforced_at < now - DECAY_AFTER,
                visible("memory", Memory.id, Memory.user_id),
            )
            .values(confidence=Memory.confidence * DECAY_FACTOR)
        )
        faded = (
            await db.scalars(
                select(Memory).where(
                    Memory.pinned.is_(False),
                    Memory.confidence < MIN_CONFIDENCE,
                    visible("memory", Memory.id, Memory.user_id),
                )
            )
        ).all()
        for m in faded:
            await mark_profile_stale(db, m.user_id)
            await db.delete(m)
        # Near-duplicate pairs: drop the less confident (then older) one unless it is pinned.
        pairs = await db.execute(
            text(
                """
                select a.id as a, n.id as b
                from memories a
                cross join lateral (
                    select b.id, b.embedding <=> a.embedding as d
                    from memories b
                    where b.user_id = a.user_id and b.id <> a.id and b.embedding is not null
                      and not exists (
                        select 1 from trash_items t where t.kind = 'memory' and t.entity_id = b.id
                      )
                      and not exists (
                        select 1 from trash_items t
                        where t.kind = 'memories_all' and t.user_id = b.user_id
                      )
                    order by b.embedding <=> a.embedding
                    limit 1
                ) n
                where a.embedding is not null and n.d < :max_d
                  and not exists (
                    select 1 from trash_items t where t.kind = 'memory' and t.entity_id = a.id
                  )
                  and not exists (
                    select 1 from trash_items t
                    where t.kind = 'memories_all' and t.user_id = a.user_id
                  )
                """
            ),
            {"max_d": DUPLICATE_DISTANCE},
        )
        gone: set[uuid.UUID] = set()
        for a_id, b_id in pairs.all():
            if a_id in gone or b_id in gone:
                continue
            a, b = await db.get(Memory, a_id), await db.get(Memory, b_id)
            keep, drop = sorted(
                (a, b), key=lambda m: (m.pinned, m.confidence, m.updated_at), reverse=True
            )
            if drop.pinned:
                continue
            keep.confidence = min(1.0, max(keep.confidence, drop.confidence) + 0.05)
            record(db, keep, "merge", "assistant", drop.content)
            await mark_profile_stale(db, drop.user_id)
            await db.delete(drop)
            gone.add(drop.id)
        # Entities only deleted memories and chats mentioned.
        pruned = await graph.prune(db)
        stale = (await db.scalars(select(Profile.user_id).where(Profile.stale.is_(True)))).all()
        await db.commit()
        log.info(
            "Nightly memory: %d faded, %d merged, %d entities pruned", len(faded), len(gone), pruned
        )
    for user_id in stale:
        try:
            await rebuild_profile(deps, user_id)
        except Exception:
            log.exception("Profile rebuild for %s failed", user_id)


async def embed_missing(deps: TurnDeps, limit: int = 256) -> int:
    """Embed rows saved while the embedder was down, made by an old model, or (skills, agent
    systems) whose text changed."""
    done = 0
    async with deps.sessionmaker() as db:
        try:
            model = await deps.embedder.current_model(db)
        except EmbeddingError:
            return 0
        texts = {
            Memory: lambda r: r.content,
            Episode: lambda r: r.content,
            Skill: lambda r: f"{r.name}: {r.description}",
            AgentSystem: lambda r: f"{r.name}: {r.goal}",
            # Entities are matched by name, so their vector is of the name alone.
            Entity: lambda r: r.name,
        }
        for table, text_of in texts.items():
            rows = (
                await db.scalars(
                    select(table)
                    .where(or_(table.embedding.is_(None), table.embedding_model != model))
                    .limit(limit)
                )
            ).all()
            if not rows:
                continue
            try:
                vectors, model = await deps.embedder.embed(db, [text_of(r) for r in rows])
            except EmbeddingError:
                return done
            for r, vec in zip(rows, vectors, strict=True):
                r.embedding, r.embedding_model = vec, model
            done += len(rows)
        await db.commit()
    return done
