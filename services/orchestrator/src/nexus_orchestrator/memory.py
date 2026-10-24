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

from nexus_common.embeddings import EmbeddingError
from nexus_common.events import Event, publish
from nexus_common.litellm import ensure_user_key
from nexus_common.memory import get_profile, mark_profile_stale, memory_payload, record
from nexus_common.models import (
    AgentSystem,
    Chat,
    Episode,
    Memory,
    MemoryKind,
    Message,
    MessageRole,
    MessageStatus,
    Profile,
    Skill,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.search import hybrid_search
from nexus_orchestrator.llm import parse_json
from nexus_orchestrator.prompts import EPISODE, MEMORY_DECIDE, MEMORY_EXTRACT, PROFILE
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


async def _ask(
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
        raw = await _ask(
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
        if not candidates and not forget:
            return
        await apply_candidates(deps, db, user, chat, latest_user, candidates, forget)


# Task requests the extractor still sometimes saves ("Wants help choosing a bike", "Agreed to
# build the 2048 MVP"). They describe one chat, not the user, so they are dropped.
REQUEST = re.compile(
    r"^(the user )?(asked|asks|requested|requests|agreed|approved|is asking|is looking for help|"
    r"needs help|wants (help|research|advice|assistance|a recommendation|recommendations|"
    r"suggestions|to know|a team|an agent|jarvis|the assistant)|would like (help|to know))\b",
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
            rows = await db.scalars(select(Memory).where(Memory.id.in_([h.id for h in hits])))
            for m in rows.all():
                existing[m.id] = m
    numbered = list(existing.values())

    if numbered:
        listing = "\n".join(
            f"{i}: {m.content}{' (pinned)' if m.pinned else ''}" for i, m in enumerate(numbered)
        )
        cands = "\n".join(f"{i}: {c['content']}" for i, c in enumerate(candidates)) or "(none)"
        forgets = "\n".join(f"- {f}" for f in forget) or "(none)"
        raw = await _ask(
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

    deleted: list[Memory] = []
    for target in to_delete:
        if isinstance(target, int) and 0 <= target < len(numbered):
            m = numbered[target]
            if not m.pinned and m not in deleted:
                await db.delete(m)
                deleted.append(m)

    if not changed and not deleted:
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
        raw = await _ask(
            deps,
            db,
            user,
            EPISODE,
            f"Chat title: {chat.title or 'untitled'}\n\n{earlier}Messages:\n\n{transcript}",
            name="episode",
            session_id=str(chat_id),
        )
        summary = str(parse_json(raw).get("summary") or "").strip()
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
        await db.commit()
        return True


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
                .where(Memory.user_id == user_id)
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
            content = await _ask(
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
            .where(Memory.pinned.is_(False), Memory.reinforced_at < now - DECAY_AFTER)
            .values(confidence=Memory.confidence * DECAY_FACTOR)
        )
        faded = (
            await db.scalars(
                select(Memory).where(Memory.pinned.is_(False), Memory.confidence < MIN_CONFIDENCE)
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
                    order by b.embedding <=> a.embedding
                    limit 1
                ) n
                where a.embedding is not null and n.d < :max_d
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
        stale = (await db.scalars(select(Profile.user_id).where(Profile.stale.is_(True)))).all()
        await db.commit()
        log.info("Nightly memory: %d faded, %d merged", len(faded), len(gone))
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
