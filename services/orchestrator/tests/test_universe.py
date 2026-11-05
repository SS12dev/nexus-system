"""Life areas for the universe view: found from the graph's communities, kept stable night to
night, born, split and merged as the data changes; and the payload the view draws."""

import math
import uuid

from sqlalchemy import select

from nexus_common import graph, universe
from nexus_common.models import Area, Entity, Mention, Relation, User


async def _graph(db, user_id, groups: dict[str, list[str]], core: list[str] = (), chat_id=None):
    """Entities in communities (one per group key, numbered in order) plus core traits; each
    group's members link to the first one."""
    u = await db.get(User, user_id)
    me = await graph.self_entity(db, u)
    made: dict[str, Entity] = {}
    for c, (domain, names) in enumerate(groups.items()):
        for n in names:
            e = Entity(
                user_id=user_id,
                type="person",
                shell=2,
                name=n,
                summary="",
                aliases=[],
                domain=domain if domain in universe.DOMAIN_HUE else None,
                community=c,
                salience=0.9 if n == names[0] else 0.3,
            )
            db.add(e)
            made[n] = e
    for n in core:
        e = Entity(user_id=user_id, type="value", shell=1, name=n, summary="", aliases=[])
        db.add(e)
        made[n] = e
    await db.flush()
    if chat_id:
        db.add_all(Mention(user_id=user_id, entity_id=e.id, chat_id=chat_id) for e in made.values())
    for names in groups.values():
        for n in names[1:]:
            db.add(
                Relation(
                    user_id=user_id,
                    src_id=made[names[0]].id,
                    dst_id=made[n].id,
                    predicate="related to",
                    family="association",
                    label="related to",
                    fact="",
                    weight=0.6,
                )
            )
    if core:
        db.add(
            Relation(
                user_id=user_id,
                src_id=me.id,
                dst_id=made[next(iter(groups.values()))[0]].id,
                predicate="works at",
                family="belonging",
                label="works at",
                fact="",
                weight=0.8,
            )
        )
    await db.commit()
    return made


async def _areas(db, user_id) -> dict[str, Area]:
    rows = (await db.scalars(select(Area).where(Area.user_id == user_id))).all()
    return {a.name: a for a in rows}


async def test_communities_become_named_areas_that_keep_their_place(sessionmaker, owner):
    user, _chat = owner
    async with sessionmaker() as db:
        made = await _graph(
            db,
            user.id,
            {
                "work": ["UST", "Rahul", "Priya", "Nexus"],
                "hobbies": ["Bambu printer", "Rocket model", "PLA", "Thingiverse"],
                "other": ["Lone"],
            },
        )
        stats = await universe.track(db, user.id)
        await db.commit()
    assert stats["born"] == 2 and stats["areas"] == 2
    async with sessionmaker() as db:
        areas = await _areas(db, user.id)
        assert set(areas) == {"Work", "Hobbies"}
        work = areas["Work"]
        ents = {e.name: e for e in (await db.scalars(select(Entity))).all()}
        assert ents["Rahul"].area_id == work.id
        # Unlinked and without a domain: loose, no galaxy.
        assert ents["Lone"].area_id is None and ents["Lone"].pos is None
        # The more central, the nearer the galaxy's centre.
        assert ents["UST"].pos["r"] < ents["Rahul"].pos["r"]
        # Spread round the ring, not on top of each other.
        gap = abs(areas["Work"].angle - areas["Hobbies"].angle) % (2 * math.pi)
        assert min(gap, 2 * math.pi - gap) > 1.0
        old = (work.id, work.angle, ents["Rahul"].pos)

    # Another night, nothing changed: same areas, same spots.
    async with sessionmaker() as db:
        stats = await universe.track(db, user.id)
        await db.commit()
        areas = await _areas(db, user.id)
        rahul = await db.get(Entity, made["Rahul"].id)
    assert stats["born"] == 0 and stats["ended"] == 0
    assert (areas["Work"].id, areas["Work"].angle, rahul.pos) == old


async def test_areas_split_merge_and_get_their_name_from_the_local_model(sessionmaker, owner):
    user, _chat = owner
    async with sessionmaker() as db:
        made = await _graph(
            db,
            user.id,
            {
                "work": [
                    "UST",
                    "Rahul",
                    "Priya",
                    "Meera",
                    "Arun",
                    "Nexus",
                    "Orion",
                    "LangGraph",
                    "Ollama",
                ]
            },
        )
        await universe.track(db, user.id)
        await db.commit()
        work = (await _areas(db, user.id))["Work"]

    # The day job and the side project drift apart into two communities.
    async with sessionmaker() as db:
        for n in ["Nexus", "Orion", "LangGraph", "Ollama"]:
            e = await db.get(Entity, made[n].id)
            e.community = 1
        await db.commit()
        asked = []

        async def namer(top, domain):
            asked.append(top)
            return ("Side projects", "What Shivam builds after work.")

        stats = await universe.track(db, user.id, namer)
        await db.commit()
        areas = await _areas(db, user.id)
    assert stats["split"] == 1
    side = areas["Side projects"]
    assert side.parent_id == work.id and side.summary.startswith("What")
    # The bigger half keeps the old area, and only the new one was named.
    assert areas["Work"].id == work.id and asked == [["LangGraph", "Nexus", "Ollama", "Orion"]]

    # They come back together: the smaller one merges into the other and ends.
    async with sessionmaker() as db:
        for n in ["Nexus", "Orion", "LangGraph", "Ollama"]:
            (await db.get(Entity, made[n].id)).community = 0
        await db.commit()
        stats = await universe.track(db, user.id)
        await db.commit()
        side = await db.get(Area, side.id)
    assert stats["merged"] == 1
    assert side.ended_at is not None and side.merged_into_id == work.id


async def test_a_new_user_gets_areas_seeded_from_domains(sessionmaker, owner):
    user, _chat = owner
    async with sessionmaker() as db:
        for name, domain in [
            ("Mum", "social"),
            ("Arjun", "social"),
            ("Gym", "health"),
            ("Yoga", "health"),
        ]:
            db.add(
                Entity(
                    user_id=user.id,
                    type="person",
                    shell=2,
                    name=name,
                    summary="",
                    aliases=[],
                    domain=domain,
                )
            )
        await db.commit()
        stats = await universe.track(db, user.id)
        await db.commit()
        assert set(await _areas(db, user.id)) == {"Social", "Health"}
    assert stats["born"] == 2


async def test_the_payload_never_names_the_core(sessionmaker, owner):
    user, chat_id = owner
    async with sessionmaker() as db:
        await _graph(
            db,
            user.id,
            {"work": ["UST", "Rahul", "Priya", "Nexus"]},
            core=["Honesty", "Curiosity"],
            chat_id=chat_id,
        )
        await universe.track(db, user.id)
        await db.commit()
        out = await universe.payload(db, user.id, await graph.graph_payload(db, user.id))
    core = out["core"]
    assert len(core["neurons"]) == 2
    text = repr(core)
    assert "Honesty" not in text and "Curiosity" not in text and "value" not in text
    assert all(set(n) == {"id", "layer", "pos", "size", "glow"} for n in core["neurons"])
    # Values sit deepest.
    assert all(n["layer"] == 0 and math.dist(n["pos"], (0, 0, 0)) < 0.4 for n in core["neurons"])
    stars = {s["name"]: s for s in out["stars"]}
    assert set(stars) == {"UST", "Rahul", "Priya", "Nexus"}
    area = out["areas"][0]
    assert stars["Rahul"]["area"] == area["id"] and stars["Rahul"]["pos"] is not None
    assert {(lane["a"], lane["b"]) for lane in out["lanes"]} and area["color"].startswith("#")
    # The user works at UST: a root from the core to that galaxy.
    assert out["roots"] == [{"area": area["id"], "w": 0.8}]
    assert uuid.UUID(out["self_id"])


async def test_loose_things_join_their_part_of_life_not_the_biggest_area(sessionmaker, owner):
    user, _chat = owner
    async with sessionmaker() as db:
        await _graph(
            db,
            user.id,
            {
                "work": ["UST", "Rahul", "Priya", "Nexus", "Orion"],
                "hobbies": ["Bambu", "PLA", "Rocket", "Drone"],
            },
        )
        loose = [
            ("Gym", "health"),
            ("Yoga", "health"),
            ("Run club", "health"),
            ("Physio", "health"),
        ]
        loose += [("Payroll", "work"), ("Mum", "social")]
        for name, domain in loose:
            db.add(
                Entity(
                    user_id=user.id,
                    type="topic",
                    shell=3,
                    name=name,
                    summary="",
                    aliases=[],
                    domain=domain,
                )
            )
        await db.commit()

        names = iter(["Work at UST", "Work at UST", "Fitness"])

        async def namer(top, domain):
            return (next(names), "")

        await universe.track(db, user.id, namer)
        await db.commit()
        areas = {a.id: a for a in (await db.scalars(select(Area))).all()}
        where = {
            e.name: areas[e.area_id].name if e.area_id else None
            for e in (await db.scalars(select(Entity))).all()
        }
    # The biggest area doesn't swallow everything: health gets its own, payroll joins work,
    # one lone family member stays loose.
    assert where["Payroll"] == where["UST"] == "Work at UST"
    assert where["Gym"] == where["Physio"] and where["Gym"] not in (where["UST"], where["Bambu"])
    assert where["Mum"] is None
    # Two areas never share a name.
    assert len({a.name for a in areas.values()}) == len(areas) == 3


async def test_communities_are_found_without_the_user_at_the_middle(sessionmaker, owner):
    from nexus_orchestrator import graph_nightly

    user, _chat = owner
    async with sessionmaker() as db:
        made = await _graph(
            db,
            user.id,
            {
                "work": ["UST", "Rahul", "Priya", "Meera"],
                "hobbies": ["Bambu", "PLA", "Rocket", "Drone"],
            },
        )
        u = await db.get(User, user.id)
        me = await graph.self_entity(db, u)
        # The user is linked to everything, which used to pull it all into one community.
        for e in made.values():
            db.add(
                Relation(
                    user_id=user.id,
                    src_id=me.id,
                    dst_id=e.id,
                    predicate="knows",
                    family="social",
                    label="knows",
                    fact="",
                    weight=1.0,
                )
            )
        await db.commit()
        await graph_nightly.arrange(db, user.id)
        await db.commit()
        comm = {e.name: e.community for e in (await db.scalars(select(Entity))).all()}
    assert comm["UST"] == comm["Meera"] and comm["Bambu"] == comm["Drone"]
    assert comm["UST"] != comm["Bambu"]


async def test_galaxies_crowded_on_the_ring_move_apart(sessionmaker, owner):
    user, _chat = owner
    groups = {f"g{i}": [f"{i}-{k}" for k in range(4)] for i in range(5)}
    async with sessionmaker() as db:
        await _graph(db, user.id, groups)
        await universe.track(db, user.id)
        await db.commit()
        # As older layouts left them: every galaxy squeezed into one side of the ring.
        areas = (await db.scalars(select(Area))).all()
        for n, a in enumerate(areas):
            a.angle = 0.1 * n
        await db.commit()
        await universe.track(db, user.id)
        await db.commit()
        angles = sorted(a.angle for a in (await db.scalars(select(Area))).all())
        await universe.track(db, user.id)
        await db.commit()
        again = sorted(a.angle for a in (await db.scalars(select(Area))).all())
    gaps = [(b - a) % (2 * math.pi) for a, b in zip(angles, angles[1:] + angles[:1], strict=True)]
    assert min(gaps) > 0.6
    # And once apart they stay put.
    assert again == angles
