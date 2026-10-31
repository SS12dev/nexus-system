"""Nightly knowledge-graph upkeep, after the memory upkeep:

1. Merge duplicates: entities that share a name or alias, or whose names mean the same within
   one shell, become one; their mentions, relations and evidence move to the survivor.
2. Fade: relations nothing has confirmed for a month lose a little weight each night; the
   app's own ties (USER built Ledgerly) don't.
3. Strengthen: relations recall walked during the day gain a little weight, more the more
   they were used (Hebbian: links that fire together wire together), so the paths the
   assistant keeps taking grow brighter and the ones nobody uses fade.
4. Arrange: per user, igraph ranks every entity by personalised PageRank from the user
   (salience, which the orb sizes and orders by) and groups them into Leiden communities
   (which the orb clusters).
5. Ask: a few question cards where the graph is unsure (nexus_common.questions.plan).

What it did is kept for Admin > Graph.
"""

import json
import logging
import math
import random
import uuid
from collections import defaultdict
from datetime import timedelta

import igraph as ig
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import questions
from nexus_common.graph import norm, used_key
from nexus_common.models import Entity, Mention, Relation, utcnow
from nexus_orchestrator.turn import TurnDeps

log = logging.getLogger(__name__)

# Entity names this close (cosine distance) in the same shell are the same thing. Tighter than
# the resolver's live match, since nobody checks a nightly merge.
MERGE_DISTANCE = 0.08
FADE_AFTER = timedelta(days=30)
FADE_FACTOR = 0.97
MIN_WEIGHT = 0.1
STATUS_KEY = "graph:nightly:last"


class _Sets:
    """Union-find over entity ids."""

    def __init__(self):
        self.parent: dict[uuid.UUID, uuid.UUID] = {}

    def find(self, x: uuid.UUID) -> uuid.UUID:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: uuid.UUID, b: uuid.UUID) -> None:
        self.parent[self.find(a)] = self.find(b)


async def merge_duplicates(db: AsyncSession, user_id: uuid.UUID) -> int:
    entities = (await db.scalars(select(Entity).where(Entity.user_id == user_id))).all()
    if len(entities) < 2:
        return 0
    by_id = {e.id: e for e in entities}
    sets = _Sets()
    by_key: dict[str, uuid.UUID] = {}
    for e in entities:
        for key in {norm(e.name), *e.aliases}:
            if key in by_key:
                sets.union(e.id, by_key[key])
            else:
                by_key[key] = e.id
    near = await db.execute(
        text(
            """
            select a.id as a, n.id as b
            from entities a
            cross join lateral (
                select b.id, b.embedding <=> a.embedding as d from entities b
                where b.user_id = a.user_id and b.id <> a.id and b.shell = a.shell
                  and b.embedding is not null and not b.is_self
                order by b.embedding <=> a.embedding limit 1
            ) n
            where a.user_id = :user_id and a.embedding is not null and not a.is_self
              and n.d < :max_d
            """
        ),
        {"user_id": user_id, "max_d": MERGE_DISTANCE},
    )
    for a, b in near.all():
        sets.union(a, b)

    groups: dict[uuid.UUID, list[Entity]] = defaultdict(list)
    for e in entities:
        groups[sets.find(e.id)].append(e)
    mentions = dict(
        (
            await db.execute(
                select(Mention.entity_id, func.count())
                .where(Mention.user_id == user_id)
                .group_by(Mention.entity_id)
            )
        ).all()
    )
    merged = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        # The user, then an app record, then the best-known one survives.
        group.sort(
            key=lambda e: (e.is_self, e.source_kind is not None, mentions.get(e.id, 0)),
            reverse=True,
        )
        keep = group[0]
        for drop in group[1:]:
            # An app record is never merged away: two with one name (a folder and a project)
            # stay apart, and so does one named like the user.
            if drop.source_kind is not None:
                continue
            await _merge(db, keep, drop)
            by_id.pop(drop.id, None)
            merged += 1
    await db.flush()
    return merged


async def _merge(db: AsyncSession, keep: Entity, drop: Entity) -> None:
    key = norm(keep.name)
    keep.aliases = [
        a for a in dict.fromkeys([*keep.aliases, norm(drop.name), *drop.aliases]) if a != key
    ][:20]
    keep.summary = keep.summary or drop.summary
    keep.domain = keep.domain or drop.domain
    keep.seen_at = max(filter(None, [keep.seen_at, drop.seen_at]), default=None)
    params = {"keep": keep.id, "drop": drop.id}
    await db.execute(
        text(
            """
            insert into mentions (id, user_id, entity_id, memory_id, episode_id, file_id,
                                  chat_id, created_at)
            select gen_random_uuid(), user_id, :keep, memory_id, episode_id, file_id, chat_id,
                   created_at
            from mentions where entity_id = :drop
            on conflict on constraint uq_mentions do nothing
            """
        ),
        params,
    )
    await db.execute(update(Relation).where(Relation.src_id == drop.id).values(src_id=keep.id))
    await db.execute(update(Relation).where(Relation.dst_id == drop.id).values(dst_id=keep.id))
    await db.execute(delete(Relation).where(Relation.src_id == keep.id, Relation.dst_id == keep.id))
    await _fold_relations(db, keep.id)
    await db.execute(delete(Entity).where(Entity.id == drop.id))


async def _fold_relations(db: AsyncSession, entity_id: uuid.UUID) -> None:
    """Open relations a merge made twice (USER uses X from both names) become one, with the
    evidence of both."""
    rows = (
        await db.scalars(
            select(Relation)
            .where(
                (Relation.src_id == entity_id) | (Relation.dst_id == entity_id),
                Relation.valid_to.is_(None),
            )
            .order_by(Relation.weight.desc(), Relation.valid_from)
        )
    ).all()
    seen: dict[tuple, Relation] = {}
    for r in rows:
        k = (r.src_id, r.dst_id, r.predicate)
        first = seen.setdefault(k, r)
        if first is r:
            continue
        first.evidence_count += r.evidence_count
        first.weight = min(1.0, max(first.weight, r.weight) + 0.05)
        first.last_seen = max(filter(None, [first.last_seen, r.last_seen]), default=None)
        await db.execute(
            text(
                """
                insert into relation_evidence (id, user_id, relation_id, memory_id, episode_id,
                                               file_id, chat_id, created_at)
                select gen_random_uuid(), user_id, :keep, memory_id, episode_id, file_id,
                       chat_id, created_at
                from relation_evidence where relation_id = :drop
                on conflict on constraint uq_relation_evidence do nothing
                """
            ),
            {"keep": first.id, "drop": r.id},
        )
        await db.delete(r)


async def fade(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(Relation)
        .where(
            Relation.user_id == user_id,
            Relation.origin != "app",
            Relation.valid_to.is_(None),
            func.coalesce(Relation.last_seen, Relation.updated_at) < utcnow() - FADE_AFTER,
            Relation.weight > MIN_WEIGHT,
        )
        .values(weight=func.greatest(MIN_WEIGHT, Relation.weight * FADE_FACTOR))
    )
    return result.rowcount or 0


async def strengthen(db: AsyncSession, redis, user_id: uuid.UUID, step: float) -> int:
    """Relations recall used since last night gain `step` per doubling of their uses, up to 1.
    Returns how many grew. The counts are taken and cleared in one go, so a turn running now
    counts toward tomorrow."""
    key = used_key(user_id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hgetall(key)
        pipe.delete(key)
        counts, _ = await pipe.execute()
    if not counts or step <= 0:
        return 0
    uses: dict[uuid.UUID, int] = {}
    for rid, n in counts.items():
        try:
            uses[uuid.UUID(rid.decode() if isinstance(rid, bytes) else rid)] = int(n)
        except ValueError:
            continue
    rows = (
        await db.scalars(
            select(Relation).where(
                Relation.id.in_(list(uses)),
                Relation.user_id == user_id,
                Relation.valid_to.is_(None),
            )
        )
    ).all()
    for r in rows:
        r.weight = round(min(1.0, r.weight + step * math.log2(1 + uses[r.id])), 4)
    await db.flush()
    return len(rows)


async def arrange(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Salience and communities for one user's graph. Returns the number of communities."""
    entities = (
        await db.execute(select(Entity.id, Entity.is_self).where(Entity.user_id == user_id))
    ).all()
    if not entities:
        return 0
    index = {eid: i for i, (eid, _s) in enumerate(entities)}
    me = next((index[eid] for eid, is_self in entities if is_self), None)
    weights: dict[tuple[int, int], float] = defaultdict(float)
    for src, dst, w in (
        await db.execute(
            select(Relation.src_id, Relation.dst_id, Relation.weight).where(
                Relation.user_id == user_id, Relation.valid_to.is_(None)
            )
        )
    ).all():
        a, b = sorted((index[src], index[dst]))
        if a != b:
            weights[(a, b)] += w
    g = ig.Graph(n=len(entities), edges=list(weights))
    g.es["weight"] = list(weights.values())

    if g.ecount():
        rank = g.personalized_pagerank(
            reset_vertices=[me] if me is not None else None, weights="weight", directed=False
        )
        # Leiden's result depends on the order it visits nodes; a fixed seed keeps the orb's
        # clusters from reshuffling every night when nothing changed.
        random.seed(0)
        membership = g.community_leiden(
            objective_function="modularity", weights="weight", n_iterations=-1
        ).membership
    else:
        rank, membership = [0.0] * len(entities), list(range(len(entities)))
    top = max((r for i, r in enumerate(rank) if i != me), default=0) or 1.0
    sizes = defaultdict(int)
    for c in membership:
        sizes[c] += 1
    # Biggest community is 0; a node alone is in none.
    order = {c: n for n, c in enumerate(sorted(sizes, key=lambda c: -sizes[c]))}
    for (eid, is_self), r, c in zip(entities, rank, membership, strict=True):
        await db.execute(
            update(Entity)
            .where(Entity.id == eid)
            .values(
                salience=1.0 if is_self else round(min(1.0, r / top), 4),
                community=order[c] if sizes[c] > 1 else None,
            )
        )
    return sum(1 for n in sizes.values() if n > 1)


async def consolidate(deps: TurnDeps) -> dict:
    stats = {
        "users": 0,
        "merged": 0,
        "faded": 0,
        "strengthened": 0,
        "communities": 0,
        "questions": 0,
        "failed": 0,
    }
    async with deps.sessionmaker() as db:
        user_ids = (await db.scalars(select(Entity.user_id).distinct())).all()
    for user_id in user_ids:
        try:
            async with deps.sessionmaker() as db:
                stats["merged"] += await merge_duplicates(db, user_id)
                stats["faded"] += await fade(db, user_id)
                stats["strengthened"] += await strengthen(
                    db, deps.redis, user_id, deps.settings.graph_strengthen_step
                )
                stats["communities"] += await arrange(db, user_id)
                stats["questions"] += await questions.plan(
                    db, user_id, deps.settings.graph_question_cards_per_day
                )
                await db.commit()
            stats["users"] += 1
        except Exception:
            stats["failed"] += 1
            log.exception("Nightly graph upkeep failed for %s", user_id)
    stats["at"] = utcnow().isoformat()
    await deps.redis.set(STATUS_KEY, json.dumps(stats))
    log.info(
        "Nightly graph: %d merged, %d relations faded, %d strengthened, %d communities over "
        "%d users",
        stats["merged"],
        stats["faded"],
        stats["strengthened"],
        stats["communities"],
        stats["users"],
    )
    return stats
