"""Question cards: short questions that fill in the knowledge graph, shown on the home screen
and the Knowledge page, always skippable.

- The starter deck asks, from the centre out, what the graph can't learn from chats alone:
  values, how someone works, how direct the assistant should be, the people and places in
  their life, what they are working on and learning.
- Each night, a few more cards go where the graph is unsure (`plan`): two names that may be
  one thing, a link to the user with no sign for months, a thing mentioned often with no part
  of life, a part of life with nothing in it. At most GRAPH_QUESTION_CARDS_PER_DAY a day.

Answers are the user's own word: links they make are certain (origin "user"), and anything
said in words becomes a memory (source "cards"), which the Memory page lists and the graph
builder reads like any other.
"""

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import graph, ontology
from nexus_common.embeddings import CachedEmbedder
from nexus_common.graph import SELF, Extracted, norm
from nexus_common.memory import mark_profile_stale, record
from nexus_common.models import (
    Entity,
    GraphQuestion,
    Memory,
    MemoryKind,
    Mention,
    Relation,
    User,
    utcnow,
)

SOURCE = "cards"
# Two entities this close in meaning (cosine distance) may be one thing: closer than this and
# further than the nightly merge's own limit (graph_nightly.MERGE_DISTANCE), the user decides.
CONFIRM_FROM, CONFIRM_TO = 0.08, 0.16
# A link to the user with no new sign for this long, that once mattered, is worth checking.
STILL_AFTER = timedelta(days=60)
STILL_MIN_WEIGHT = 0.4
# Mentioned this often with no part of life: worth asking which.
DOMAIN_MIN_MENTIONS = 3
# A part of life is only "thin" once the graph has something to compare it with.
THIN_AFTER_ENTITIES = 15
NONE_OF_THESE = "None of these"

DOMAIN_LABEL = {
    "work": "Work",
    "learning": "Learning",
    "home": "Home and family",
    "health": "Health",
    "social": "Friends and social life",
    "hobbies": "Hobbies",
    "finance": "Money",
}
THIN_PROMPT = {
    "health": "You haven't told me much about your health. Anything you'd like me to keep in mind?",
    "hobbies": "What do you do for fun when you're not working?",
    "social": "Who do you spend time with outside work?",
    "finance": "Any money goals or habits you'd like me to keep in mind?",
    "home": "Who's at home with you, and anything about home I should know?",
    "learning": "What are you learning or curious about at the moment?",
    "work": "What do you do for work, and what's on your plate right now?",
}


@dataclass(frozen=True)
class Starter:
    id: str
    shell: int
    prompt: str
    form: str
    help: str = ""
    options: tuple[str, ...] = ()
    # For "many": how many may be picked.
    most: int = 0


VALUES = (
    "Honesty",
    "Curiosity",
    "Family",
    "Freedom",
    "Craft and quality",
    "Growth",
    "Health",
    "Fairness",
    "Ambition",
    "Calm",
    "Creativity",
    "Loyalty",
)
WORK_STYLE = {
    "Deep focus on one thing at a time": "Works best with deep focus on one thing at a time.",
    "Many threads at once": "Likes juggling many threads at once.",
    "Depends on the day": "Switches between deep focus and many threads, depending on the day.",
}
DIRECTNESS = {
    "Blunt: tell me straight": "Wants blunt, straight answers, with pushback when warranted.",
    "Direct but kind": "Wants direct answers delivered kindly.",
    "Gentle": "Prefers a gentle, encouraging way of putting things.",
}

STARTERS: tuple[Starter, ...] = (
    Starter(
        "values",
        1,
        "Which of these matter most to you?",
        "many",
        "Pick up to five. They sit closest to you in the orb.",
        VALUES,
        most=5,
    ),
    Starter("work_style", 1, "How do you like to work?", "one", options=tuple(WORK_STYLE)),
    Starter(
        "directness",
        1,
        "How direct should I be with you?",
        "one",
        "This changes how I word things, not what I tell you.",
        tuple(DIRECTNESS),
    ),
    Starter("drains", 1, "What drains your energy?", "text", "So I can steer around it."),
    Starter(
        "people",
        2,
        "Who are the people you mention most, and how do you know them?",
        "text",
        "For example: Ana, my sister; Raj, my manager at work.",
    ),
    Starter("places", 2, "Where do you live, and where do you work?", "text"),
    Starter(
        "projects",
        3,
        "What are you working on right now, and what's the goal?",
        "text",
        "Work or personal, a sentence each is plenty.",
    ),
    Starter("learning", 4, "What are you learning or curious about at the moment?", "text"),
)
STARTER_BY_ID = {s.id: s for s in STARTERS}


async def ensure_starter(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Add the starter deck once; cards already asked (answered or skipped) are not re-added."""
    now = utcnow()
    for i, s in enumerate(STARTERS):
        await db.execute(
            insert(GraphQuestion)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                key=f"starter:{s.id}",
                kind="starter",
                shell=s.shell,
                prompt=s.prompt,
                help=s.help,
                form=s.form,
                options=list(s.options),
                data={"starter": s.id, "order": i, "most": s.most},
                status="open",
                # In deck order, ahead of anything the nightly pass adds later.
                created_at=now - timedelta(days=365) + timedelta(seconds=i),
            )
            .on_conflict_do_nothing(constraint="uq_graph_questions_key")
        )


async def open_cards(db: AsyncSession, user_id: uuid.UUID) -> list[GraphQuestion]:
    rows = await db.scalars(
        select(GraphQuestion)
        .where(GraphQuestion.user_id == user_id, GraphQuestion.status == "open")
        .order_by(GraphQuestion.created_at)
    )
    return list(rows.all())


# --- Answers ------------------------------------------------------------------------------------


class AnswerError(ValueError):
    pass


@dataclass
class Applied:
    """What an answer changed, for the card to say so."""

    memories: int = 0
    entities: list[str] = field(default_factory=list)
    note: str = ""


async def _memory(
    db: AsyncSession,
    embedder: CachedEmbedder | None,
    user: User,
    kind: MemoryKind,
    content: str,
    graph_now: bool,
) -> Memory:
    vec, model = await embedder.try_embed_one(db, content) if embedder else (None, "")
    m = Memory(
        id=uuid.uuid4(),
        user_id=user.id,
        kind=kind,
        content=content,
        confidence=1.0,
        pinned=True,
        source=SOURCE,
        embedding=vec,
        embedding_model=model or None,
        # Said in words: the graph builder reads it. Already in the graph: it needn't.
        graph_at=utcnow() if graph_now else None,
    )
    db.add(m)
    await db.flush()
    record(db, m, "add", "user", content)
    return m


def _text(answer: dict) -> str:
    return " ".join(str(answer.get("text") or "").split())[:2000]


async def apply_answer(
    db: AsyncSession,
    embedder: CachedEmbedder | None,
    user: User,
    q: GraphQuestion,
    answer: dict,
    *,
    graph_enabled: bool = True,
) -> Applied:
    """Apply an answer to the user's memories and graph, and close the card."""
    if q.status != "open":
        raise AnswerError("This card was already answered.")
    out = await _apply(db, embedder, user, q, answer, graph_enabled)
    q.status, q.answer, q.answered_at = "answered", answer, utcnow()
    if out.memories:
        await mark_profile_stale(db, user.id)
    await db.flush()
    return out


async def _apply(db, embedder, user, q, answer, graph_enabled) -> Applied:
    out = Applied()
    if q.kind == "starter":
        s = STARTER_BY_ID.get(q.data.get("starter", ""))
        if s is None:
            raise AnswerError("Unknown card.")
        return await _starter(db, embedder, user, s, answer, graph_enabled)
    if q.kind == "thin":
        words = _text(answer)
        if not words:
            raise AnswerError("Write an answer, or skip the card.")
        await _memory(db, embedder, user, MemoryKind.fact, words, graph_now=False)
        out.memories = 1
        return out

    if q.kind == "domain":
        choice = answer.get("choice")
        if choice not in q.options:
            raise AnswerError("Pick one of the options.")
        e = await db.get(Entity, uuid.UUID(q.data["entity"]))
        domain = next((d for d, label in DOMAIN_LABEL.items() if label == choice), None)
        if e is not None and e.user_id == user.id and domain:
            e.domain = domain
            out.note = f"{e.name} is now under {choice.lower()}."
        return out

    yes = answer.get("yes")
    if not isinstance(yes, bool):
        raise AnswerError("Answer yes or no.")
    if q.kind == "confirm":
        a, b = (await db.get(Entity, uuid.UUID(i)) for i in q.data["entities"])
        if yes and a is not None and b is not None and a.user_id == b.user_id == user.id:
            # The same thing under two names: the second becomes an alias of the first, and
            # tonight's upkeep merges them (it merges entities that share a name or alias).
            key = norm(b.name)
            if key not in a.aliases:
                a.aliases = [*a.aliases, key][:20]
            out.note = f"{b.name} and {a.name} will be merged tonight."
        return out
    if q.kind == "still":
        r = await db.get(Relation, uuid.UUID(q.data["relation"]))
        if r is None or r.user_id != user.id or r.valid_to is not None:
            return out
        now = utcnow()
        if yes:
            r.confidence, r.last_seen, r.updated_at = 1.0, now, now
            r.weight = min(1.0, r.weight + 0.1)
        else:
            # Kept as history, so "used to work on X" still answers questions about the past.
            r.valid_to = r.updated_at = now
        return out
    raise AnswerError("Unknown card.")


async def _starter(db, embedder, user, s: Starter, answer: dict, graph_enabled) -> Applied:
    out = Applied()
    found = Extracted()
    links: list[tuple[dict, list[str]]] = []
    if s.form == "many":
        picked = [c for c in dict.fromkeys(answer.get("choices") or []) if c in s.options]
        if not picked or len(picked) > s.most:
            raise AnswerError(f"Pick between one and {s.most}.")
        m = await _memory(
            db, embedder, user, MemoryKind.preference, f"Values most: {', '.join(picked)}.", True
        )
        links.append(({"memory_id": m.id}, picked))
        for c in picked:
            found.entities.append({"name": c, "type": "value", "summary": ""})
            found.relations.append(
                {"from": SELF, "to": c, "predicate": "values", "weight": 0.9, "ended": False}
            )
        out.entities = picked
    elif s.form == "one":
        choice = answer.get("choice")
        if choice not in s.options:
            raise AnswerError("Pick one of the options.")
        if s.id == "work_style":
            sentence = WORK_STYLE[choice]
            m = await _memory(db, embedder, user, MemoryKind.fact, sentence, True)
            name = choice.split(":")[0].lower()
            links.append(({"memory_id": m.id}, [name]))
            found.entities.append({"name": name, "type": "trait", "summary": sentence})
            found.relations.append(
                {"from": SELF, "to": name, "predicate": "has trait", "weight": 0.8, "ended": False}
            )
            out.entities = [name]
        else:
            await _memory(db, embedder, user, MemoryKind.instruction, DIRECTNESS[choice], True)
    else:
        words = _text(answer)
        if not words:
            raise AnswerError("Write an answer, or skip the card.")
        kind, lead = {
            "drains": (MemoryKind.fact, "Drained by"),
            "people": (MemoryKind.fact, "People in my life"),
            "places": (MemoryKind.fact, "Where I live and work"),
            "projects": (MemoryKind.goal, "Working on"),
            "learning": (MemoryKind.goal, "Learning"),
        }[s.id]
        # In words: the graph builder finds the people, places and projects in it.
        await _memory(db, embedder, user, kind, f"{lead}: {words}", False)
    out.memories = 1
    if found and graph_enabled and embedder is not None:
        await graph.ingest(db, embedder, user, found, links=links, origin="user")
    return out


async def skip(q: GraphQuestion) -> None:
    q.status, q.answered_at = "skipped", utcnow()


# --- Nightly cards ------------------------------------------------------------------------------


async def plan(db: AsyncSession, user_id: uuid.UUID, per_day: int) -> int:
    """Add up to `per_day` cards (less any added today) where the graph is unsure, the most
    useful kind first. Returns how many were added."""
    if per_day <= 0:
        return 0
    today = await db.scalar(
        select(func.count())
        .select_from(GraphQuestion)
        .where(
            GraphQuestion.user_id == user_id,
            GraphQuestion.kind != "starter",
            GraphQuestion.created_at > utcnow() - timedelta(hours=20),
        )
    )
    room = per_day - (today or 0)
    if room <= 0:
        return 0
    asked = set(
        (await db.scalars(select(GraphQuestion.key).where(GraphQuestion.user_id == user_id))).all()
    )
    cards: list[dict] = []
    for maker in (_confirm_cards, _still_cards, _domain_cards, _thin_cards):
        if len(cards) >= room:
            break
        cards += [c for c in await maker(db, user_id) if c["key"] not in asked][: room - len(cards)]
    now = utcnow()
    for c in cards:
        await db.execute(
            insert(GraphQuestion)
            .values(id=uuid.uuid4(), user_id=user_id, status="open", created_at=now, **c)
            .on_conflict_do_nothing(constraint="uq_graph_questions_key")
        )
    return len(cards)


def _card(key, kind, shell, prompt, form, data, help="", options=()) -> dict:
    return {
        "key": key,
        "kind": kind,
        "shell": shell,
        "prompt": prompt,
        "help": help,
        "form": form,
        "options": list(options),
        "data": data,
    }


async def _confirm_cards(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    rows = await db.execute(
        text(
            """
            select a.id as a, a.name as a_name, a.summary as a_sum, a.shell as shell,
                   n.id as b, n.name as b_name, n.summary as b_sum
            from entities a
            cross join lateral (
                select b.id, b.name, b.summary, b.embedding <=> a.embedding as d from entities b
                where b.user_id = a.user_id and b.id <> a.id and b.shell = a.shell
                  and b.embedding is not null and not b.is_self and b.source_kind is null
                order by b.embedding <=> a.embedding limit 1
            ) n
            where a.user_id = :user_id and a.embedding is not null and not a.is_self
              and a.source_kind is null and a.id < n.id and n.d >= :lo and n.d < :hi
            order by n.d
            limit 10
            """
        ),
        {"user_id": user_id, "lo": CONFIRM_FROM, "hi": CONFIRM_TO},
    )
    out = []
    for r in rows:
        about = "; ".join(f"{n}: {s}" for n, s in ((r.a_name, r.a_sum), (r.b_name, r.b_sum)) if s)
        out.append(
            _card(
                f"confirm:{r.a}:{r.b}",
                "confirm",
                r.shell,
                f"Are “{r.a_name}” and “{r.b_name}” the same thing?",
                "yesno",
                {"entities": [str(r.a), str(r.b)]},
                help=about,
            )
        )
    return out


async def _still_cards(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    me = await db.scalar(select(Entity.id).where(Entity.user_id == user_id, Entity.is_self))
    if me is None:
        return []
    rows = await db.execute(
        select(Relation, Entity.name, Entity.shell)
        .join(Entity, Entity.id == Relation.dst_id)
        .where(
            Relation.user_id == user_id,
            Relation.src_id == me,
            Relation.valid_to.is_(None),
            Relation.origin == "model",
            Relation.weight >= STILL_MIN_WEIGHT,
            func.coalesce(Relation.last_seen, Relation.updated_at) < utcnow() - STILL_AFTER,
        )
        .order_by(Relation.weight.desc())
        .limit(10)
    )
    return [
        _card(
            f"still:{r.id}",
            "still",
            shell,
            f"Is this still true: you {r.label or r.predicate} {name}?",
            "yesno",
            {"relation": str(r.id)},
            help="I haven't heard about it for a while. No keeps it as history.",
        )
        for r, name, shell in rows
    ]


async def _domain_cards(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    mentions = func.count(Mention.id)
    rows = await db.execute(
        select(Entity.id, Entity.name, Entity.shell)
        .join(Mention, Mention.entity_id == Entity.id)
        .where(
            Entity.user_id == user_id,
            Entity.domain.is_(None),
            ~Entity.is_self,
            Entity.source_kind.is_(None),
            Entity.shell >= 2,
        )
        .group_by(Entity.id)
        .having(mentions >= DOMAIN_MIN_MENTIONS)
        .order_by(mentions.desc())
        .limit(10)
    )
    return [
        _card(
            f"domain:{eid}",
            "domain",
            shell,
            f"You mention {name} a lot. Which part of your life is it in?",
            "one",
            {"entity": str(eid)},
            options=[*DOMAIN_LABEL.values(), NONE_OF_THESE],
        )
        for eid, name, shell in rows
    ]


async def _thin_cards(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    total = await db.scalar(
        select(func.count()).select_from(Entity).where(Entity.user_id == user_id)
    )
    if (total or 0) < THIN_AFTER_ENTITIES:
        return []
    have = set(
        (
            await db.scalars(
                select(Entity.domain)
                .where(Entity.user_id == user_id, Entity.domain.is_not(None))
                .distinct()
            )
        ).all()
    )
    return [
        _card(f"thin:{d}", "thin", None, THIN_PROMPT[d], "text", {"domain": d})
        for d in ontology.DOMAINS
        if d not in have and d in THIN_PROMPT
    ]
