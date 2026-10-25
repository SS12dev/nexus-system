"""The knowledge graph: the people, projects, places, topics and tools a user's memories are
about, the relations between them, and which memories, past chats, files and chats mention
each one. It lives in three tables next to the memories (entities, relations, mentions), so it
shares their transactions, backups and per-user scoping.

The memory model proposes entities and relations in the same call that proposes memories
(nexus_orchestrator/memory.py); `ingest` resolves names to existing entities, adds relations
and mentions, and closes relations that ended. Recall walks it from the entities a question is
about (`walk`), and the Memory page shows it (`graph_payload`).
"""

import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from nexus_common.embeddings import CachedEmbedder, EmbeddingError
from nexus_common.models import Entity, Mention, Relation, User, utcnow
from nexus_common.search import vector_literal

log = logging.getLogger(__name__)

TYPES = ("person", "project", "organisation", "place", "topic", "tool", "other")
# What the model writes for the user themself in a relation.
SELF = "user"
# A new name this close (cosine distance) to an entity of the same type is that entity.
MATCH_DISTANCE = 0.15
MAX_ENTITIES = 20
MAX_RELATIONS = 20


def norm(name: str) -> str:
    """Lower case, single spaces, no surrounding punctuation: the form names are matched in."""
    return re.sub(r"\s+", " ", name.casefold()).strip(" .,;:!?\"'()[]")


# The model sometimes writes the type into the name ("topic:tray design").
TYPE_PREFIX = re.compile(rf"^(?:{'|'.join(TYPES)})\s*:\s*", re.IGNORECASE)
# A file name or path ("out/tray.stl", "rocket_preview.png") is a file in the library, not
# something in the user's world. Code-like names such as Node.js or socket.io stay.
FILE_NAME = re.compile(
    r"^\S*\.(?:stl|3mf|step|stp|obj|png|jpe?g|gif|webp|svg|pdf|docx?|xlsx?|pptx?|csv|"
    r"txt|md|json|ya?ml|py|sh|zip|html?)$",
    re.IGNORECASE,
)


def clean_name(name: str) -> str:
    """The name as a person would say it, or "" for one that isn't an entity."""
    name = TYPE_PREFIX.sub("", " ".join(str(name).split()))
    return "" if FILE_NAME.match(name) else name


@dataclass
class Extracted:
    """Entities and relations the model found, validated."""

    entities: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.entities or self.relations)


def parse_graph(data: dict) -> Extracted:
    out = Extracted()
    seen: set[str] = set()
    for e in data.get("entities") or []:
        if not isinstance(e, dict):
            continue
        name = clean_name(str(e.get("name") or ""))[:200]
        key = norm(name)
        if not key or key == SELF or key in seen:
            continue
        seen.add(key)
        kind = str(e.get("type") or "other").lower()
        aliases = [norm(a) for a in map(clean_name, e.get("aliases") or []) if norm(a)][:5]
        out.entities.append(
            {
                "name": name,
                "type": kind if kind in TYPES else "other",
                "summary": str(e.get("summary") or "").strip()[:300],
                "aliases": [a for a in aliases if a != key],
            }
        )
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        src = norm(clean_name(str(r.get("from") or "")))
        dst = norm(clean_name(str(r.get("to") or "")))
        predicate = norm(str(r.get("predicate") or ""))[:80]
        if not src or not dst or not predicate or src == dst:
            continue
        out.relations.append(
            {
                "from": src,
                "to": dst,
                "predicate": predicate,
                "fact": str(r.get("fact") or "").strip()[:300],
                "ended": bool(r.get("ended")),
            }
        )
    out.entities, out.relations = out.entities[:MAX_ENTITIES], out.relations[:MAX_RELATIONS]
    return out


def about_names(raw) -> list[str]:
    """The entity names one memory or item is about, as the model listed them."""
    if not isinstance(raw, list):
        return []
    return [norm(n) for n in map(clean_name, raw) if norm(n)][:8]


async def self_entity(db: AsyncSession, user: User) -> Entity:
    found = await db.scalar(select(Entity).where(Entity.user_id == user.id, Entity.is_self))
    if found is None:
        found = Entity(
            id=uuid.uuid4(),
            user_id=user.id,
            type="person",
            name=user.name or "You",
            aliases=[],
            summary="",
            is_self=True,
        )
        db.add(found)
        await db.flush()
    return found


class Resolver:
    """Turns names into a user's entities: by name or alias first, then by meaning within the
    same type, and otherwise a new entity. One per ingest, so a name resolves once."""

    def __init__(self, db: AsyncSession, embedder: CachedEmbedder, user: User):
        self.db, self.embedder, self.user = db, embedder, user
        self.by_name: dict[str, Entity] = {}

    async def load(self) -> None:
        rows = await self.db.scalars(
            select(Entity)
            .options(
                load_only(
                    Entity.id,
                    Entity.name,
                    Entity.aliases,
                    Entity.type,
                    Entity.summary,
                    Entity.is_self,
                )
            )
            .where(Entity.user_id == self.user.id)
        )
        for e in rows.all():
            for key in [norm(e.name), *e.aliases]:
                self.by_name.setdefault(key, e)
        me = await self_entity(self.db, self.user)
        self.by_name[SELF] = self.by_name[norm(me.name)] = me

    async def resolve(self, items: list[dict]) -> None:
        """Make sure every item ({name, type, summary, aliases}) maps to an entity."""
        new = [it for it in items if norm(it["name"]) not in self.by_name]
        vectors: list[list[float] | None] = [None] * len(new)
        model = ""
        if new:
            try:
                vecs, model = await self.embedder.embed(self.db, [it["name"] for it in new])
                vectors = list(vecs)
            except EmbeddingError as exc:
                log.warning("Embedding entity names failed, matching by name only: %s", exc)
        for it, vec in zip(new, vectors, strict=True):
            key = norm(it["name"])
            if key in self.by_name:  # an earlier item in this batch made it
                continue
            match = await self._nearest(it["type"], vec) if vec is not None else None
            if match is not None:
                if key not in match.aliases:
                    match.aliases = [*match.aliases, key][:20]
            else:
                match = Entity(
                    id=uuid.uuid4(),
                    user_id=self.user.id,
                    type=it["type"],
                    name=it["name"],
                    aliases=it.get("aliases", []),
                    summary=it.get("summary", ""),
                    embedding=vec,
                    embedding_model=model or None,
                )
                self.db.add(match)
            for k in [key, *it.get("aliases", [])]:
                self.by_name.setdefault(k, match)
        for it in items:
            e = self.by_name[norm(it["name"])]
            if it.get("summary") and not e.summary:
                e.summary = it["summary"]
        await self.db.flush()

    async def _nearest(self, kind: str, vec: list[float]) -> Entity | None:
        row = (
            await self.db.execute(
                text(
                    """
                    select id, embedding <=> cast(:vec as vector) as d from entities
                    where user_id = :user_id and type = :type and not is_self
                      and embedding is not null
                    order by d limit 1"""
                ),
                {"vec": vector_literal(vec), "user_id": self.user.id, "type": kind},
            )
        ).first()
        if row is None or row.d >= MATCH_DISTANCE:
            return None
        return await self.db.get(Entity, row.id)

    def get(self, name: str) -> Entity | None:
        return self.by_name.get(norm(name))


async def ingest(
    db: AsyncSession,
    embedder: CachedEmbedder,
    user: User,
    found: Extracted,
    *,
    chat_id: uuid.UUID | None = None,
    links: list[tuple[dict, list[str]]] = (),
) -> int:
    """Add what the model found to the user's graph. `links` pairs a mention target, such as
    {"memory_id": id}, with the names it is about; every entity found is also mentioned by
    `chat_id`. Returns how many entities were touched."""
    if not found and not any(names for _t, names in links):
        return 0
    resolver = Resolver(db, embedder, user)
    await resolver.load()
    await resolver.resolve(found.entities)
    now = utcnow()
    touched: set[uuid.UUID] = set()

    for r in found.relations:
        src, dst = resolver.get(r["from"]), resolver.get(r["to"])
        if src is None or dst is None or src.id == dst.id:
            continue
        touched |= {src.id, dst.id}
        same = await db.scalar(
            select(Relation).where(
                Relation.user_id == user.id,
                Relation.src_id == src.id,
                Relation.dst_id == dst.id,
                Relation.predicate == r["predicate"],
                Relation.valid_to.is_(None),
            )
        )
        if r["ended"]:
            if same is not None:
                same.valid_to = same.updated_at = now
            else:
                # Learned only that it used to be true: keep it as history.
                db.add(_relation(user, src, dst, r, chat_id, now, ended=True))
        elif same is not None:
            same.confidence = min(1.0, same.confidence + 0.1)
            same.updated_at = now
            if r["fact"]:
                same.fact = r["fact"]
        else:
            db.add(_relation(user, src, dst, r, chat_id, now))

    targets: list[tuple[dict, list[Entity]]] = []
    if chat_id is not None:
        targets.append(({"chat_id": chat_id}, [resolver.get(e["name"]) for e in found.entities]))
    for target, names in links:
        targets.append((target, [resolver.get(n) for n in names]))
    for target, entities in targets:
        for e in {e.id: e for e in entities if e is not None and not e.is_self}.values():
            touched.add(e.id)
            await db.execute(
                insert(Mention)
                .values(id=uuid.uuid4(), user_id=user.id, entity_id=e.id, created_at=now, **target)
                .on_conflict_do_nothing(constraint="uq_mentions")
            )
    if touched:
        await db.execute(update(Entity).where(Entity.id.in_(touched)).values(seen_at=now))
    await db.flush()
    return len(touched)


def _relation(user, src, dst, r, chat_id, now, ended: bool = False) -> Relation:
    return Relation(
        id=uuid.uuid4(),
        user_id=user.id,
        src_id=src.id,
        dst_id=dst.id,
        predicate=r["predicate"],
        fact=r["fact"],
        confidence=0.7,
        source_chat_id=chat_id,
        valid_from=now,
        valid_to=now if ended else None,
        updated_at=now,
    )


async def forget_mentions(db: AsyncSession, **target) -> None:
    """Drop the mentions of one memory, summary or file before it is linked again."""
    ((col, value),) = target.items()
    await db.execute(delete(Mention).where(getattr(Mention, col) == value))


async def prune(db: AsyncSession, user_id: uuid.UUID | None = None) -> int:
    """Delete entities nothing mentions any more (their relations go with them), so deleting a
    memory or a chat also removes what the graph learned only from it."""
    sql = """
        delete from entities e
        where not e.is_self {scope}
          and not exists (select 1 from mentions m where m.entity_id = e.id)"""
    params = {}
    if user_id is not None:
        params["user_id"] = user_id
    result = await db.execute(
        text(sql.format(scope="and e.user_id = :user_id" if user_id else "")), params
    )
    return result.rowcount or 0


# --- Reading -----------------------------------------------------------------------------------


async def walk(
    db: AsyncSession, user_id: uuid.UUID, seeds: dict[uuid.UUID, float], hops: int
) -> dict[uuid.UUID, float]:
    """Entities within `hops` of the seeds over relations that still hold, each scored by its
    best path: the seed's score, halved per hop and weighted by each relation's confidence.
    The user's own entity is never walked through: everything connects to it."""
    if not seeds or hops <= 0:
        return dict(seeds)
    rows = await db.execute(
        text(
            """
            with recursive walk(id, depth, score) as (
                select s.id, 0, s.score
                from unnest(cast(:ids as uuid[]), cast(:scores as float[])) as s(id, score)
                union all
                select case when r.src_id = w.id then r.dst_id else r.src_id end,
                       w.depth + 1, w.score * 0.5 * r.confidence
                from walk w
                join relations r on w.id in (r.src_id, r.dst_id)
                join entities e
                  on e.id = case when r.src_id = w.id then r.dst_id else r.src_id end
                where w.depth < :hops and r.user_id = :user_id and r.valid_to is null
                  and not e.is_self
            )
            select id, max(score) as score from walk group by id"""
        ),
        {
            "ids": list(seeds),
            "scores": list(seeds.values()),
            "hops": hops,
            "user_id": user_id,
        },
    )
    return {r.id: float(r.score) for r in rows}


async def graph_payload(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """Every entity with what mentions it, and every relation, for the Knowledge page and orb."""
    counts = await db.execute(
        text(
            """
            select e.id, e.type, e.name, e.aliases, e.summary, e.is_self, e.created_at, e.seen_at,
                   count(m.memory_id) as memories, count(m.episode_id) as past_chats,
                   count(m.file_id) as files, count(m.chat_id) as chats,
                   coalesce(array_agg(m.memory_id) filter (where m.memory_id is not null),
                            '{}') as memory_ids,
                   coalesce(array_agg(m.file_id) filter (where m.file_id is not null),
                            '{}') as file_ids,
                   coalesce(array_agg(distinct coalesce(m.chat_id, ep.chat_id))
                            filter (where coalesce(m.chat_id, ep.chat_id) is not null),
                            '{}') as chat_ids
            from entities e left join mentions m on m.entity_id = e.id
                 left join episodes ep on ep.id = m.episode_id
            where e.user_id = :user_id
            group by e.id
            order by e.is_self desc, count(m.id) desc, e.seen_at desc"""
        ),
        {"user_id": user_id},
    )
    entities = [
        {
            "id": str(r.id),
            "type": r.type,
            "name": r.name,
            "aliases": r.aliases or [],
            "summary": r.summary,
            "is_self": r.is_self,
            "created_at": r.created_at.isoformat(),
            "seen_at": r.seen_at.isoformat(),
            "memories": r.memories,
            "past_chats": r.past_chats,
            "files": r.files,
            "chats": r.chats,
            "memory_ids": [str(i) for i in r.memory_ids],
            "file_ids": [str(i) for i in r.file_ids],
            # The chats it came up in, live or summarised, for the Knowledge page to link.
            "chat_ids": [str(i) for i in r.chat_ids],
        }
        for r in counts
    ]
    rels = (
        await db.scalars(
            select(Relation)
            .where(Relation.user_id == user_id)
            .order_by(Relation.valid_to.is_not(None), Relation.updated_at.desc())
        )
    ).all()
    relations = [
        {
            "id": str(r.id),
            "src": str(r.src_id),
            "dst": str(r.dst_id),
            "predicate": r.predicate,
            "fact": r.fact,
            "confidence": r.confidence,
            "valid_from": r.valid_from.isoformat(),
            "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            "source_chat_id": str(r.source_chat_id) if r.source_chat_id else None,
        }
        for r in rels
    ]
    return {"entities": entities, "relations": relations}
