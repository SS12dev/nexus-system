"""The universe view: the user's knowledge graph laid out as a core, galaxies and the Nexus hub.

- The core holds who the user is (shell 1: traits, values, goals, habits...). It is never
  named anywhere in the view, so its payload carries no names at all.
- Everything else lives in a galaxy, one per life area. Areas are not fixed: each night `track`
  groups the graph's communities into areas and matches them to yesterday's, so an area keeps
  its id, place on the ring and colour while its members drift. Areas are born, split from a
  parent, merged into a neighbour or end as the data changes.
- `layout` gives every entity a stable spot (in its galaxy, or inside the core) that moves only
  when its meaning changes.
- `payload` is what the web app draws.
"""

import colorsys
import hashlib
import math
import uuid
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import Area, Entity, Relation, TrashItem, utcnow

# A community this big becomes its own galaxy; smaller ones join their nearest neighbour.
MIN_AREA = 4
MAX_AREAS = 12
# How much a group must share with yesterday's area (Jaccard) to be that area.
SAME_AREA = 0.3
# How much of a group must come from one area for it to count as a split of that area.
SPLIT_SHARE = 0.5
ACTIVE_FOR = timedelta(days=30)
# How far a star moves toward its new spot each night, so the picture never jumps.
SETTLE = 0.5

# Core layers, deepest first: values change slowest, habits most.
CORE_LAYER = {
    "value": 0,
    "belief": 0,
    "goal": 1,
    "trait": 1,
    "skill": 1,
    "interest": 1,
    "preference": 2,
    "habit": 2,
    "state": 2,
    "communication_style": 2,
}
LAYER_RADIUS = ((0.05, 0.38), (0.4, 0.66), (0.68, 0.86))
DOMAIN_HUE = {
    "work": 0.58,
    "learning": 0.75,
    "home": 0.08,
    "health": 0.38,
    "social": 0.92,
    "hobbies": 0.13,
    "finance": 0.48,
}

# Names an area from its main members and domain: (name, one-line summary). The nightly pass
# passes one that asks the local model; without it the name is made from the members.
Namer = Callable[[list[str], str | None], Awaitable[tuple[str, str] | None]]


def _unit(seed: str, salt: str = "") -> float:
    """A stable number in [0, 1) from a string, so a layout is the same on every run."""
    h = hashlib.sha256(f"{seed}:{salt}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def _hue_mean(hues: list[tuple[float, float]]) -> float | None:
    """Weighted circular mean of (hue, weight)."""
    x = sum(math.cos(h * 2 * math.pi) * w for h, w in hues)
    y = sum(math.sin(h * 2 * math.pi) * w for h, w in hues)
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return None
    return (math.atan2(y, x) / (2 * math.pi)) % 1


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def _gap_angle(taken: list[float]) -> float:
    """The middle of the widest gap on the ring."""
    if not taken:
        return 0.0
    ring = sorted(a % (2 * math.pi) for a in taken)
    gaps = [
        ((ring[(i + 1) % len(ring)] - a) % (2 * math.pi) or 2 * math.pi, a)
        for i, a in enumerate(ring)
    ]
    width, start = max(gaps)
    return (start + width / 2) % (2 * math.pi)


def fallback_name(names: list[str], domain: str | None, others: set[str]) -> str:
    """The domain when no other area has it, else the domain and the main member."""
    top = names[0] if names else "Something new"
    if domain and domain.title() not in others:
        return domain.title()
    return f"{domain.title()}: {top}" if domain else top


async def _groups(
    db: AsyncSession, user_id: uuid.UUID, members: list[Entity]
) -> list[set[uuid.UUID]]:
    """This night's areas, as sets of entity ids: the big communities, with the small ones and
    loose entities joined to the group they link to most. With no big communities yet (a new
    user), the domains seed the areas."""
    ids = {e.id for e in members}
    by_comm: dict[int, set[uuid.UUID]] = defaultdict(set)
    for e in members:
        if e.community is not None:
            by_comm[e.community].add(e.id)
    groups = [g for g in by_comm.values() if len(g) >= MIN_AREA]
    groups.sort(key=len, reverse=True)
    if len(groups) > MAX_AREAS:
        groups = groups[:MAX_AREAS]
    if not groups:
        by_domain: dict[str, set[uuid.UUID]] = defaultdict(set)
        for e in members:
            by_domain[e.domain or "other"].add(e.id)
        seeded = [g for d, g in by_domain.items() if d != "other" and len(g) >= 2]
        groups = sorted(seeded, key=len, reverse=True)[:MAX_AREAS]
        if not groups and members:
            groups = [set(ids)]
    home = {eid: i for i, g in enumerate(groups) for eid in g}

    # Everyone else joins the group it is linked to most (two rounds, so a chain of loose
    # things follows its anchor in).
    links: dict[uuid.UUID, dict[uuid.UUID, float]] = defaultdict(lambda: defaultdict(float))
    for src, dst, w in (
        await db.execute(
            select(Relation.src_id, Relation.dst_id, Relation.weight).where(
                Relation.user_id == user_id, Relation.valid_to.is_(None)
            )
        )
    ).all():
        if src in ids and dst in ids:
            links[src][dst] += w
            links[dst][src] += w
    for _ in range(2):
        for e in members:
            if e.id in home:
                continue
            pull: dict[int, float] = defaultdict(float)
            for other, w in links[e.id].items():
                if other in home:
                    pull[home[other]] += w
            if pull:
                home[e.id] = max(pull, key=pull.get)
    # Still loose: the group most of its domain went to.
    domain_home: dict[str, Counter] = defaultdict(Counter)
    for e in members:
        if e.id in home and e.domain:
            domain_home[e.domain][home[e.id]] += 1
    for e in members:
        if e.id not in home and e.domain and domain_home[e.domain]:
            home[e.id] = domain_home[e.domain].most_common(1)[0][0]
    out: list[set[uuid.UUID]] = [set() for _ in groups]
    for eid, i in home.items():
        out[i].add(eid)
    return [g for g in out if g]


async def track(db: AsyncSession, user_id: uuid.UUID, namer: Namer | None = None) -> dict:
    """Match this night's groups to the user's areas, then lay everything out."""
    now = utcnow()
    entities = (await db.scalars(select(Entity).where(Entity.user_id == user_id))).all()
    members = [e for e in entities if not e.is_self and (e.shell or 4) >= 2]
    groups = await _groups(db, user_id, members)
    by_id = {e.id: e for e in members}

    areas = (
        await db.scalars(select(Area).where(Area.user_id == user_id, Area.ended_at.is_(None)))
    ).all()
    before: dict[uuid.UUID, set[uuid.UUID]] = {a.id: set() for a in areas}
    for e in members:
        if e.area_id in before:
            before[e.area_id].add(e.id)

    # Greedy matching on overlap, best pairs first; each area and each group once.
    pairs = sorted(
        (
            (_jaccard(g, before[a.id]), i, a)
            for i, g in enumerate(groups)
            for a in areas
            if before[a.id] & g
        ),
        key=lambda p: -p[0],
    )
    match: dict[int, Area] = {}
    used: set[uuid.UUID] = set()
    for score, i, a in pairs:
        if score < SAME_AREA or i in match or a.id in used:
            continue
        match[i] = a
        used.add(a.id)

    stats = {"born": 0, "split": 0, "merged": 0, "ended": 0}
    taken = [a.angle for a in areas if a.id in used]
    names_in_use = {a.name for a in areas if a.id in used}
    fresh: set[uuid.UUID] = set()
    for i, g in enumerate(groups):
        if i in match:
            continue
        # Mostly from one area that another group kept: a split of it.
        origin = Counter(e.area_id for e in (by_id[x] for x in g) if e.area_id in before)
        parent = None
        if origin:
            pid, n = origin.most_common(1)[0]
            if n / len(g) >= SPLIT_SHARE:
                parent = next(a for a in areas if a.id == pid)
        angle = (
            (parent.angle + 0.45 + 0.3 * _unit(str(i), "split")) % (2 * math.pi)
            if parent
            else _gap_angle(taken)
        )
        a = Area(
            id=uuid.uuid4(),
            user_id=user_id,
            angle=angle,
            tilt=(_unit(str(angle)) - 0.5) * 0.5,
            parent_id=parent.id if parent else None,
            born_at=now,
            updated_at=now,
        )
        db.add(a)
        match[i] = a
        fresh.add(a.id)
        taken.append(angle)
        stats["split" if parent else "born"] += 1

    # Areas no group kept merged into where most of their members went, or ended.
    kept = {a.id: i for i, a in match.items()}
    for a in areas:
        if a.id in kept:
            continue
        went = Counter(match[i].id for i, g in enumerate(groups) for x in before[a.id] if x in g)
        a.ended_at = now
        if went:
            a.merged_into_id = went.most_common(1)[0][0]
            stats["merged"] += 1
        else:
            stats["ended"] += 1

    # Members, size, activity, colour and name.
    for e in members:
        e.area_id = None
    for i, g in enumerate(groups):
        a = match[i]
        ents = sorted((by_id[x] for x in g), key=lambda e: (-(e.salience or 0), e.name))
        for e in ents:
            e.area_id = a.id
        changed = a.id in fresh or _jaccard(g, before.get(a.id, set())) < 0.5
        a.size = len(ents)
        recent = sum(1 for e in ents if e.seen_at and e.seen_at > now - ACTIVE_FOR)
        a.activity = round(recent / len(ents), 3)
        domains = Counter(e.domain for e in ents if e.domain)
        top_domain, n = domains.most_common(1)[0] if domains else (None, 0)
        a.domain = top_domain if n >= len(ents) / 3 else None
        hue = _hue_mean(
            [
                (DOMAIN_HUE[e.domain], (e.salience or 0) + 0.1)
                for e in ents
                if e.domain in DOMAIN_HUE
            ]
        )
        if hue is None:
            hue = _unit(str(a.id), "hue")
        # Dynamic, but never jumping: drift a third of the way each night.
        if a.id in fresh:
            a.hue = round(hue, 4)
        else:
            d = ((hue - a.hue + 0.5) % 1) - 0.5
            a.hue = round((a.hue + d / 3) % 1, 4)
        if changed or not a.name:
            top = [e.name for e in ents[:8]]
            named = None
            if namer is not None:
                try:
                    named = await namer(top, a.domain)
                except Exception:
                    named = None
            if named and named[0].strip():
                a.name, a.summary = named[0].strip()[:80], named[1].strip()
            else:
                a.name = fallback_name(top, a.domain, names_in_use - {a.name})[:80]
            names_in_use.add(a.name)
        a.updated_at = now

    layout(entities)
    await db.flush()
    stats["areas"] = len(groups)
    return stats


def layout(entities: list[Entity]) -> None:
    """A stable spot for each entity: inside the core by layer for who the user is, in its
    galaxy's disc for everything else (the more central to the user, the nearer the galaxy's
    centre). Existing spots move only part of the way, and keep their angle in the same
    galaxy."""
    by_area: dict[uuid.UUID, list[Entity]] = defaultdict(list)
    for e in entities:
        if e.is_self:
            e.pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        elif (e.shell or 4) <= 1:
            layer = CORE_LAYER.get(e.type, 1)
            lo, hi = LAYER_RADIUS[layer]
            old = e.pos if isinstance(e.pos, dict) and "x" in e.pos else None
            if old and old.get("layer") == layer:
                continue
            k = str(e.id)
            u, th = _unit(k, "u") * 2 - 1, _unit(k, "t") * 2 * math.pi
            r = lo + (hi - lo) * _unit(k, "r")
            s = math.sqrt(1 - u * u)
            e.pos = {
                "x": round(math.cos(th) * s * r, 4),
                "y": round(u * r * 0.9, 4),
                "z": round(math.sin(th) * s * r, 4),
                "layer": layer,
            }
        elif e.area_id is not None:
            by_area[e.area_id].append(e)
        else:
            e.pos = None
    for ents in by_area.values():
        ents.sort(key=lambda e: (-(e.salience or 0), str(e.id)))
        n = len(ents)
        for rank, e in enumerate(ents):
            k = str(e.id)
            target = 0.1 + 0.85 * (rank / max(1, n - 1)) ** 0.8
            old = e.pos if isinstance(e.pos, dict) and "r" in e.pos else None
            if old and old.get("area") == str(e.area_id):
                r = old["r"] + (target - old["r"]) * SETTLE
                a, y = old["a"], old.get("y", 0.0)
            else:
                arm = int(_unit(k, "arm") * 3)
                r = target
                a = arm * 2 * math.pi / 3 + r * 2.4 + (_unit(k, "j") - 0.5) * 0.6
                y = (_unit(k, "y") - 0.5) * 0.08 * (1 - r)
            e.pos = {
                "r": round(r, 4),
                "a": round(a % (2 * math.pi), 4),
                "y": round(y, 4),
                "area": str(e.area_id),
            }


def _hex(hue: float, s: float = 0.7, light: float = 0.6) -> str:
    r, g, b = colorsys.hls_to_rgb(hue % 1, light, s)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


async def payload(db: AsyncSession, user_id: uuid.UUID, graph: dict) -> dict:
    """What the universe view draws, built on graph.graph_payload (which already leaves out
    what is in the Trash). The core comes without names, kinds or summaries."""
    entities = graph["entities"]
    ents = {e["id"]: e for e in entities}
    extra = {
        str(i): (a, t)
        for i, a, t in (
            await db.execute(
                select(Entity.id, Entity.area_id, Entity.type).where(Entity.user_id == user_id)
            )
        ).all()
    }
    areas = (
        await db.scalars(
            select(Area)
            .where(Area.user_id == user_id)
            .where((Area.ended_at.is_(None)) | (Area.ended_at > utcnow() - timedelta(days=2)))
            .order_by(Area.born_at)
        )
    ).all()
    live = {str(a.id) for a in areas if a.ended_at is None}

    core_ids, star_ids = [], []
    self_id = None
    for e in entities:
        if e["is_self"]:
            self_id = e["id"]
        elif (e.get("shell") or 4) <= 1:
            core_ids.append(e["id"])
        else:
            star_ids.append(e["id"])
    core_index = {cid: i for i, cid in enumerate(core_ids)}
    mention_max = (
        max(
            (ents[i]["memories"] + ents[i]["chats"] + ents[i]["past_chats"] for i in core_ids),
            default=1,
        )
        or 1
    )

    neurons = []
    for cid in core_ids:
        e = ents[cid]
        pos = e.get("pos") if isinstance(e.get("pos"), dict) and "x" in e["pos"] else None
        if pos is None:
            layer = CORE_LAYER.get(extra.get(cid, (None, ""))[1], 1)
            lo, hi = LAYER_RADIUS[layer]
            u, th = _unit(cid, "u") * 2 - 1, _unit(cid, "t") * 2 * math.pi
            r = lo + (hi - lo) * _unit(cid, "r")
            s = math.sqrt(1 - u * u)
            pos = {
                "x": math.cos(th) * s * r,
                "y": u * r * 0.9,
                "z": math.sin(th) * s * r,
                "layer": layer,
            }
        n = e["memories"] + e["chats"] + e["past_chats"]
        neurons.append(
            {
                "id": cid,
                "layer": pos.get("layer", 1),
                "pos": [round(pos["x"], 4), round(pos["y"], 4), round(pos["z"], 4)],
                "size": round(0.3 + 0.7 * (e.get("salience") or 0), 3),
                "glow": round(0.35 + 0.65 * min(1.0, n / mention_max), 3),
            }
        )

    stars = []
    for sid in star_ids:
        e = ents[sid]
        area_id = extra.get(sid, (None, None))[0]
        area_id = str(area_id) if area_id is not None and str(area_id) in live else None
        pos = e.get("pos") if isinstance(e.get("pos"), dict) and "r" in e["pos"] else None
        stars.append(
            {
                "id": sid,
                "name": e["name"],
                "kind": e.get("kind") or e["type"],
                "summary": e["summary"],
                "area": area_id,
                "pos": [pos["r"], pos["a"], pos.get("y", 0.0)] if pos and area_id else None,
                "salience": round(e.get("salience") or 0, 4),
                "mentions": e["memories"] + e["chats"] + e["past_chats"] + e["files"],
                "seen_at": e["seen_at"],
                "created_at": e["created_at"],
                "source": e.get("source"),
            }
        )
    star_area = {s["id"]: s["area"] for s in stars}

    core_links, lanes = [], []
    filaments: dict[tuple[str, str], float] = defaultdict(float)
    roots: dict[str, float] = defaultdict(float)
    for r in graph["relations"]:
        if r["valid_to"]:
            continue
        a, b, w = r["src"], r["dst"], r["weight"]
        if a in core_index and b in core_index:
            core_links.append([core_index[a], core_index[b], round(w, 3)])
        elif a in star_area and b in star_area:
            aa, ba = star_area[a], star_area[b]
            if aa and aa == ba:
                lanes.append({"a": a, "b": b, "w": round(w, 3), "family": r["family"]})
            elif aa and ba:
                filaments[tuple(sorted((aa, ba)))] += w
        else:
            # Between the user (or who they are) and something in a galaxy: a root.
            inner = a == self_id or a in core_index or b == self_id or b in core_index
            outer = star_area.get(b) if a == self_id or a in core_index else star_area.get(a)
            if inner and outer:
                roots[outer] += w
    lanes.sort(key=lambda x: -x["w"])

    core_hue = _hue_mean(
        [(DOMAIN_HUE.get(ents[c].get("domain") or "", 0.58), 1.0) for c in core_ids]
    )
    return {
        "core": {
            "hue": round(core_hue if core_hue is not None else 0.58, 4),
            "neurons": neurons,
            "links": core_links,
        },
        "self_id": self_id,
        "areas": [
            {
                "id": str(a.id),
                "name": a.name,
                "summary": a.summary,
                "domain": a.domain,
                "hue": a.hue,
                "color": _hex(a.hue),
                "angle": a.angle,
                "tilt": a.tilt,
                "size": a.size,
                "activity": a.activity,
                "parent": str(a.parent_id) if a.parent_id else None,
                "merged_into": str(a.merged_into_id) if a.merged_into_id else None,
                "born_at": a.born_at.isoformat(),
                "ended_at": a.ended_at.isoformat() if a.ended_at else None,
            }
            for a in areas
        ],
        "stars": stars,
        "lanes": lanes[:3000],
        "filaments": [{"a": a, "b": b, "w": round(w, 3)} for (a, b), w in filaments.items()],
        "roots": [{"area": a, "w": round(w, 3)} for a, w in roots.items()],
    }


async def hidden(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Whether the user cleared all memories (the Trash holds them), so nothing is shown."""
    return bool(
        await db.scalar(
            select(TrashItem.id).where(
                TrashItem.user_id == user_id, TrashItem.kind == "memories_all"
            )
        )
    )
