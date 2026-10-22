import uuid

from nexus_common.models import Link, Message, MessageLink, MessageRole, MessageStatus


async def add(client, url="https://github.com/SS12dev/nexus-system", **body):
    return await client.post("/api/links", json={"url": url, **body})


async def test_add_list_edit_delete(owner, queue):
    resp = await add(owner, note="Nexus itself.")
    assert resp.status_code == 201, resp.text
    link = resp.json()
    assert (link["kind"], link["title"], link["source"]) == (
        "github_repo", "SS12dev/nexus-system", "user",
    )  # fmt: skip
    # The orchestrator fetches the preview (it holds the GitHub App's key).
    assert [str(x) for x in queue.link_previews] == [link["id"]]
    # The same address again is the same link, not a second one.
    again = (
        await add(owner, url="https://github.com/SS12dev/nexus-system/", title="Jarvis")
    ).json()
    assert again["id"] == link["id"] and again["title"] == "Jarvis"
    assert again["note"] == "Nexus itself."
    web = (await add(owner, url="example.com/guide", title="Guide")).json()
    assert web["kind"] == "web" and web["url"] == "https://example.com/guide"
    assert len(queue.link_previews) == 2  # Web pages have no preview to fetch.
    listed = (await owner.get("/api/links")).json()
    assert [x["id"] for x in listed] == [web["id"], link["id"]]
    assert [x["id"] for x in (await owner.get("/api/links", params={"q": "guide"})).json()] == [
        web["id"]
    ]
    edited = await owner.patch(f"/api/links/{web['id']}", json={"note": "How to set it up."})
    assert edited.json()["note"] == "How to set it up."
    assert (await owner.post(f"/api/links/{web['id']}/refresh")).status_code == 409
    assert (await owner.post(f"/api/links/{link['id']}/refresh")).status_code == 202
    assert (await owner.delete(f"/api/links/{web['id']}")).status_code == 204
    assert (await owner.get(f"/api/links/{web['id']}")).status_code == 404


async def test_unsafe_addresses_are_refused(owner):
    for url in ("javascript:alert(1)", "ftp://x.com/a", "https://me:pw@x.com"):
        resp = await add(owner, url=url)
        assert resp.status_code == 422, url


async def test_links_are_private(owner, client_for, invite):
    lid = (await add(owner)).json()["id"]
    await invite(owner, "admin@example.com", "admin")
    other = await client_for("admin@example.com")
    assert (await other.get("/api/links")).json() == []
    assert (await other.get(f"/api/links/{lid}")).status_code == 404
    assert (await other.patch(f"/api/links/{lid}", json={"title": "x"})).status_code == 404
    assert (await other.delete(f"/api/links/{lid}")).status_code == 404
    # Saving the same address is their own link.
    assert (await add(other)).json()["id"] != lid


async def test_viewers_cannot_add(owner, client_for, invite):
    await invite(owner, "v@example.com", "viewer")
    viewer = await client_for("v@example.com")
    assert (await add(viewer)).status_code == 403


async def test_messages_carry_their_links(owner, app):
    chat = (await owner.post("/api/chats", json={})).json()["id"]
    lid = (await add(owner, chat_id=chat)).json()["id"]
    async with app.state.sessionmaker() as db:
        msg = Message(
            chat_id=uuid.UUID(chat), role=MessageRole.assistant, content="Here's the repo.",
            status=MessageStatus.complete,
        )  # fmt: skip
        db.add(msg)
        await db.flush()
        db.add(MessageLink(message_id=msg.id, link_id=uuid.UUID(lid)))
        await db.commit()
        assert (await db.get(Link, uuid.UUID(lid))).chat_id == uuid.UUID(chat)
    (message,) = (await owner.get(f"/api/chats/{chat}/messages")).json()
    assert [x["id"] for x in message["links"]] == [lid]
