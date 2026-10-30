import pytest
from sqlalchemy import select

from nexus_common import graph, graph_sync, ontology
from nexus_common.graph import Extracted
from nexus_common.models import (
    Entity,
    Folder,
    OntologyPredicate,
    OntologyType,
    Project,
    Relation,
    RelationEvidence,
    RoleName,
    Routine,
    User,
    UserStatus,
)
from nexus_common.trash import add as add_to_trash


@pytest.mark.parametrize(
    ("label", "predicate", "family"),
    [
        ("Is Building", "works on", "activity"),
        ("works at", "works at", "belonging"),
        ("used to work at", "left", "activity"),
        ("is the younger sister of", "family of", "social"),
        ("moved to", "lives in", "belonging"),
        ("is blocked by", "blocked by", "causal"),
        ("prefers", "prefers", "identity"),
        ("vibes with", "related to", "association"),
    ],
)
def test_wording_maps_to_a_canonical_predicate_and_family(label, predicate, family):
    canon = ontology.canonical(label)
    assert (canon.predicate, canon.family) == (predicate, family)
    assert canon.label == label.lower()


def test_every_type_has_a_shell_and_an_old_type_for_todays_pages():
    assert ontology.shell_of("value") == 1 and ontology.shell_of("person") == 2
    assert ontology.shell_of("routine") == 3 and ontology.shell_of("document") == 4
    assert ontology.shell_of("something new") == 4
    old = {"person", "project", "organisation", "place", "topic", "tool", "other"}
    assert {ontology.legacy_type(t) for t in ontology.TYPES} <= old
    assert ontology.legacy_type("trait") == "topic" and ontology.legacy_type("app") == "project"


@pytest.fixture
async def owner(sessionmaker):
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        user = await create_user(
            db,
            email="shivam@example.com",
            role=RoleName.owner,
            status=UserStatus.active,
            name="Shivam",
        )
        await db.commit()
        return user.id


async def test_app_records_are_filed_into_the_graph_and_leave_with_them(
    sessionmaker, owner, embedder
):
    async with sessionmaker() as db:
        user = await db.get(User, owner)
        # The chats already talked about Ledgerly: its entity is adopted, not duplicated.
        await graph.ingest(
            db,
            embedder,
            user,
            Extracted(
                entities=[{"name": "Ledgerly", "type": "project", "summary": "", "aliases": []}]
            ),
        )
        app = Project(
            user_id=owner,
            workbench_id="w1",
            name="Ledgerly",
            stack="react",
            live_url="https://ledgerly.servhub.cc",
            app_state="running",
        )
        folder = Folder(user_id=owner, name="Rocket model")
        routine = Routine(
            user_id=owner, name="Weekly GitHub summary", prompt="...", schedule="0 9 * * 1"
        )
        db.add_all([app, folder, routine])
        await db.commit()

    assert await graph_sync.sync_all(sessionmaker) > 0
    async with sessionmaker() as db:
        ents = {e.name: e for e in (await db.scalars(select(Entity))).all()}
        rels = (await db.scalars(select(Relation))).all()
        assert await db.scalar(select(OntologyType).where(OntologyType.name == "value"))
        assert await db.scalar(
            select(OntologyPredicate).where(OntologyPredicate.name == "works at")
        )
    me = next(e for e in ents.values() if e.is_self)
    assert me.shell == 0
    assert (ents["Ledgerly"].type, ents["Ledgerly"].source_kind) == ("app", "project")
    assert (ents["Rocket model"].type, ents["Rocket model"].source_kind) == ("project", "folder")
    assert (ents["Weekly GitHub summary"].type, ents["Weekly GitHub summary"].shell) == (
        "routine",
        3,
    )
    assert len([e for e in ents.values() if e.name == "Ledgerly"]) == 1
    ties = {(ents_by_id(ents)[r.dst_id], r.predicate, r.family, r.origin) for r in rels}
    assert ties == {
        ("Ledgerly", "built", "activity", "app"),
        ("Rocket model", "works on", "activity", "app"),
        ("Weekly GitHub summary", "runs", "activity", "app"),
    }

    # A second pass changes nothing; a folder put in Trash and a deleted routine leave.
    assert await graph_sync.sync_all(sessionmaker) == 0
    async with sessionmaker() as db:
        await add_to_trash(db, owner, "folder", folder.id, folder.name)
        await db.delete(await db.get(Routine, routine.id))
        await db.commit()
    await graph_sync.sync_all(sessionmaker)
    async with sessionmaker() as db:
        names = set((await db.scalars(select(Entity.name))).all())
    assert "Rocket model" not in names and "Weekly GitHub summary" not in names
    assert "Ledgerly" in names


def ents_by_id(ents):
    return {e.id: n for n, e in ents.items()}


async def test_relations_from_before_the_ontology_get_a_family(sessionmaker, owner, embedder):
    async with sessionmaker() as db:
        user = await db.get(User, owner)
        me = await graph.self_entity(db, user)
        ust = Entity(user_id=owner, type="organisation", name="UST", aliases=[], summary="")
        db.add(ust)
        await db.flush()
        db.add(
            Relation(
                user_id=owner,
                src_id=me.id,
                dst_id=ust.id,
                predicate="is employed at",
                label="is employed at",
                family=None,
                fact="",
            )
        )
        await db.commit()
    await graph_sync.sync_all(sessionmaker)
    async with sessionmaker() as db:
        rel = await db.scalar(select(Relation))
    assert (rel.predicate, rel.family, rel.label) == ("works at", "belonging", "is employed at")


async def test_chat_evidence_accumulates_on_one_relation(sessionmaker, owner, embedder):
    from nexus_common.models import Chat

    async with sessionmaker() as db:
        user = await db.get(User, owner)
        chats = [Chat(user_id=owner, title=t) for t in ("one", "two")]
        db.add_all(chats)
        await db.flush()
        found = Extracted(
            entities=[{"name": "UST", "type": "organisation", "summary": "", "aliases": []}],
            relations=[
                {"from": "user", "to": "ust", "predicate": "works at", "fact": "", "ended": False}
            ],
        )
        for c in chats:
            await graph.ingest(db, embedder, user, found, chat_id=c.id)
        await db.commit()
        rel = await db.scalar(select(Relation))
        evidence = (await db.scalars(select(RelationEvidence))).all()
    assert rel.evidence_count == 2 and rel.weight == pytest.approx(0.7)
    assert {e.chat_id for e in evidence} == {c.id for c in chats}
