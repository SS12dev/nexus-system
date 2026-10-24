import uuid

from sqlalchemy import select

from nexus_common.models import Chat, Entity, Episode, Mention, Relation, User


async def add(client, content, **extra):
    resp = await client.post("/api/memories", json={"content": content, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_add_list_search_and_edit(owner):
    tables = await add(owner, "Prefers tables for comparisons", kind="preference")
    await add(owner, "Lives in Pune")
    assert tables["source"] == "user" and tables["confidence"] == 1.0

    listed = (await owner.get("/api/memories")).json()
    assert {m["content"] for m in listed} == {"Prefers tables for comparisons", "Lives in Pune"}
    found = (await owner.get("/api/memories", params={"q": "comparison tables"})).json()
    assert found[0]["id"] == tables["id"]

    resp = await owner.patch(
        f"/api/memories/{tables['id']}", json={"content": "Prefers tables", "pinned": True}
    )
    assert resp.json()["pinned"] is True and resp.json()["content"] == "Prefers tables"
    # Pinned first.
    assert (await owner.get("/api/memories")).json()[0]["id"] == tables["id"]
    events = (await owner.get(f"/api/memories/{tables['id']}/events")).json()
    assert [e["op"] for e in events] == ["add", "update", "pin"]


async def test_delete_is_real_and_can_rebuild_the_profile(owner, queue):
    m = await add(owner, "Has a dog called Bruno")
    profile = (await owner.get("/api/memories/profile")).json()
    assert profile["stale"] is True and profile["memory_count"] == 1
    assert (await owner.delete(f"/api/memories/{m['id']}?rebuild=true")).status_code == 204
    assert (await owner.get(f"/api/memories/{m['id']}/events")).status_code == 404
    assert (await owner.get("/api/memories")).json() == []
    assert len(queue.profiles) == 1


async def test_profile_settings_and_forget_everything(owner, queue):
    await add(owner, "Works on Jarvis")
    resp = await owner.patch("/api/memories/profile", json={"memory_enabled": False})
    assert resp.json()["memory_enabled"] is False
    assert (await owner.post("/api/memories/profile/rebuild")).status_code == 202
    assert len(queue.profiles) == 1
    assert (await owner.delete("/api/memories")).status_code == 204
    profile = (await owner.get("/api/memories/profile")).json()
    assert profile["memory_count"] == 0 and profile["content"] == ""


async def test_memories_are_private(owner, client_for, invite):
    m = await add(owner, "Secret project codename Falcon")
    await invite(owner, "admin@example.com", "admin")
    other = await client_for("admin@example.com")
    assert (await other.get("/api/memories")).json() == []
    assert (await other.get("/api/memories", params={"q": "Falcon"})).json() == []
    assert (await other.patch(f"/api/memories/{m['id']}", json={"pinned": True})).status_code == 404
    assert (await other.delete(f"/api/memories/{m['id']}")).status_code == 404


async def seed_graph(sessionmaker, email: str, memory_id: str):
    """Nexus uses LangGraph, both mentioned by the memory."""
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        nexus = Entity(user_id=user.id, type="project", name="Nexus", aliases=[], summary="")
        lg = Entity(user_id=user.id, type="tool", name="LangGraph", aliases=[], summary="")
        db.add_all([nexus, lg])
        await db.flush()
        db.add(
            Relation(
                user_id=user.id,
                src_id=nexus.id,
                dst_id=lg.id,
                predicate="uses",
                fact="Nexus uses LangGraph.",
            )
        )
        for e in (nexus, lg):
            db.add(Mention(user_id=user.id, entity_id=e.id, memory_id=uuid.UUID(memory_id)))
        await db.commit()
        return nexus.id, lg.id


async def test_connections_follow_their_memories(owner, sessionmaker):
    m = await add(owner, "Builds Nexus on LangGraph")
    nexus_id, lg_id = await seed_graph(sessionmaker, "owner@example.com", m["id"])

    g = (await owner.get("/api/memories/graph")).json()
    by_name = {e["name"]: e for e in g["entities"]}
    assert by_name["Nexus"]["memories"] == 1 and by_name["Nexus"]["memory_ids"] == [m["id"]]
    assert by_name["Nexus"]["chat_ids"] == []
    assert by_name["Nexus"]["created_at"] <= by_name["Nexus"]["seen_at"]
    (rel,) = g["relations"]
    assert rel["src"] == str(nexus_id) and rel["dst"] == str(lg_id) and rel["valid_to"] is None

    # Editing a memory unlinks it until the graph job links the new wording.
    await owner.patch(f"/api/memories/{m['id']}", json={"content": "Builds Nexus"})
    g = (await owner.get("/api/memories/graph")).json()
    assert all(e["memories"] == 0 for e in g["entities"])


async def test_deleting_a_memory_prunes_what_only_it_mentioned(owner, sessionmaker):
    keep = await add(owner, "Uses LangGraph at work")
    gone = await add(owner, "Builds Nexus on LangGraph")
    # LangGraph is also mentioned by another memory, so it survives.
    _nexus, lg_id = await seed_graph(sessionmaker, "owner@example.com", gone["id"])
    async with sessionmaker() as db:
        lg = await db.get(Entity, lg_id)
        db.add(Mention(user_id=lg.user_id, entity_id=lg.id, memory_id=uuid.UUID(keep["id"])))
        await db.commit()

    assert (await owner.delete(f"/api/memories/{gone['id']}")).status_code == 204
    g = (await owner.get("/api/memories/graph")).json()
    assert [e["name"] for e in g["entities"]] == ["LangGraph"] and g["relations"] == []


async def test_delete_entity_and_forget_everything(owner, sessionmaker, client_for, invite):
    m = await add(owner, "Builds Nexus on LangGraph")
    nexus_id, _lg = await seed_graph(sessionmaker, "owner@example.com", m["id"])

    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    assert (await friend.delete(f"/api/memories/entities/{nexus_id}")).status_code == 404
    assert (await friend.get("/api/memories/graph")).json() == {"entities": [], "relations": []}

    assert (await owner.delete(f"/api/memories/entities/{nexus_id}")).status_code == 204
    g = (await owner.get("/api/memories/graph")).json()
    assert [e["name"] for e in g["entities"]] == ["LangGraph"] and g["relations"] == []
    # The memory itself stays.
    assert len((await owner.get("/api/memories")).json()) == 1

    assert (await owner.delete("/api/memories")).status_code == 204
    assert (await owner.get("/api/memories/graph")).json()["entities"] == []


async def test_connections_list_the_chats_they_came_up_in(owner, sessionmaker):
    m = await add(owner, "Builds Nexus on LangGraph")
    nexus_id, _lg = await seed_graph(sessionmaker, "owner@example.com", m["id"])
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        live, past = Chat(user_id=user.id), Chat(user_id=user.id)
        db.add_all([live, past])
        await db.flush()
        ep = Episode(
            user_id=user.id, chat_id=past.id, content="Talked about Nexus", message_count=4
        )
        db.add(ep)
        await db.flush()
        db.add(Mention(user_id=user.id, entity_id=nexus_id, chat_id=live.id))
        db.add(Mention(user_id=user.id, entity_id=nexus_id, episode_id=ep.id))
        await db.commit()
        ids = {str(live.id), str(past.id)}

    g = (await owner.get("/api/memories/graph")).json()
    nexus = next(e for e in g["entities"] if e["name"] == "Nexus")
    assert set(nexus["chat_ids"]) == ids
    assert nexus["chats"] == 1 and nexus["past_chats"] == 1
