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
