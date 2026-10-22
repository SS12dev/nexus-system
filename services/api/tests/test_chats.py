async def new_chat(client) -> str:
    resp = await client.post("/api/chats", json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_send_message_queues_a_turn(owner, queue):
    chat_id = await new_chat(owner)
    resp = await owner.post(f"/api/chats/{chat_id}/messages", json={"content": "Hello Nexus\nmore"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["chat"]["title"] == "Hello Nexus"
    assert body["user_message"]["status"] == "complete"
    assistant = body["assistant_message"]
    assert assistant["status"] == "streaming" and assistant["model"] == "nexus-mini"
    assert assistant["trace_id"]
    assert [str(m) for m in queue.turns[0]] == [chat_id, assistant["id"]]

    messages = (await owner.get(f"/api/chats/{chat_id}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]


async def test_second_message_waits_for_the_reply(owner):
    chat_id = await new_chat(owner)
    await owner.post(f"/api/chats/{chat_id}/messages", json={"content": "one"})
    resp = await owner.post(f"/api/chats/{chat_id}/messages", json={"content": "two"})
    assert resp.status_code == 409


async def test_queue_outage_marks_reply_failed(owner, queue):
    queue.fail = True
    chat_id = await new_chat(owner)
    resp = await owner.post(f"/api/chats/{chat_id}/messages", json={"content": "hi"})
    assert resp.status_code == 503
    messages = (await owner.get(f"/api/chats/{chat_id}/messages")).json()
    assert messages[-1]["status"] == "failed"


async def test_chats_are_private_to_their_owner(owner, client_for, invite):
    chat_id = await new_chat(owner)
    await owner.post(f"/api/chats/{chat_id}/messages", json={"content": "secret"})

    await invite(owner, "admin@example.com", "admin")
    other = await client_for("admin@example.com")
    assert (await other.get("/api/chats")).json() == []
    assert (await other.get(f"/api/chats/{chat_id}/messages")).status_code == 404
    resp = await other.post(f"/api/chats/{chat_id}/messages", json={"content": "x"})
    assert resp.status_code == 404
    assert (await other.delete(f"/api/chats/{chat_id}")).status_code == 404


async def test_viewers_can_read_but_not_chat(owner, client_for, invite):
    await invite(owner, "v@example.com", "viewer")
    viewer = await client_for("v@example.com")
    assert (await viewer.get("/api/chats")).status_code == 200
    assert (await viewer.post("/api/chats", json={})).status_code == 403


async def test_rename_and_delete_chat(owner):
    chat_id = await new_chat(owner)
    resp = await owner.patch(f"/api/chats/{chat_id}", json={"title": "Plans"})
    assert resp.json()["title"] == "Plans"
    assert (await owner.delete(f"/api/chats/{chat_id}")).status_code == 204
    assert (await owner.get("/api/chats")).json() == []


async def test_requires_sign_in(client_for):
    anon = await client_for()
    assert (await anon.get("/api/chats")).status_code == 401
