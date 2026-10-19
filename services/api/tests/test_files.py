import uuid

from jarvis_common.models import File, FileChunk, FileStatus


async def upload(client, name="notes.txt", data=b"Quarterly revenue grew 12 percent.", **form):
    return await client.post("/api/files", files={"file": (name, data)}, data=form)


async def make_ready(app, file_id: str, texts: list[str]) -> None:
    """Stand in for the doc-worker: store chunks and mark the file ready."""
    embedder = app.state.embedder
    async with app.state.sessionmaker() as db:
        f = await db.get(File, uuid.UUID(file_id))
        vectors, model = await embedder.embed(db, texts)
        for i, (t, v) in enumerate(zip(texts, vectors, strict=True)):
            db.add(
                FileChunk(
                    file_id=f.id, user_id=f.user_id, idx=i, content=t, embedding=v,
                    embedding_model=model,
                )
            )  # fmt: skip
        f.status, f.chunk_count, f.char_count = FileStatus.ready, len(texts), sum(map(len, texts))
        await db.commit()


async def test_upload_stores_the_file_and_queues_parsing(owner, queue, storage):
    resp = await upload(owner)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "processing" and body["name"] == "notes.txt"
    assert body["content_type"] == "text/plain" and body["size"] == 34
    assert [str(f) for f in queue.parses] == [body["id"]]
    (key,) = storage.objects
    assert body["id"] in key and storage.objects[key][0].startswith(b"Quarterly")


async def test_upload_rules(owner, settings, monkeypatch):
    assert (await upload(owner, name="tool.exe")).status_code == 415
    assert (await upload(owner, data=b"")).status_code == 400
    resp = await upload(owner, name="../../etc/passwd.txt")
    assert resp.json()["name"] == "passwd.txt"
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    assert (await upload(owner)).status_code == 413


async def test_queue_outage_marks_the_file_failed(owner, queue):
    queue.fail = True
    resp = await upload(owner)
    assert resp.status_code == 201 and resp.json()["status"] == "failed"


async def test_download_text_and_search(owner, app):
    fid = (await upload(owner, name="report.md", data=b"# Revenue\n\nNorth made 1200.")).json()[
        "id"
    ]
    await make_ready(app, fid, ["# Revenue", "North region revenue was 1200 dollars."])

    resp = await owner.get(f"/api/files/{fid}/content")
    assert resp.content.startswith(b"# Revenue")
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert resp.headers["x-content-type-options"] == "nosniff"

    text = (await owner.get(f"/api/files/{fid}/text")).json()
    assert "1200 dollars" in text["text"] and text["total_chars"] > 0

    hits = (await owner.get("/api/files/search", params={"q": "north revenue"})).json()
    assert {h["file"]["id"] for h in hits} == {fid}
    assert any("1200" in h["snippet"] for h in hits)


async def test_files_are_private(owner, client_for, invite, app):
    fid = (await upload(owner)).json()["id"]
    await make_ready(app, fid, ["Quarterly revenue grew 12 percent."])
    await invite(owner, "admin@example.com", "admin")
    other = await client_for("admin@example.com")
    assert (await other.get("/api/files")).json() == []
    for path in ("", "/text", "/content"):
        assert (await other.get(f"/api/files/{fid}{path}")).status_code == 404
    assert (await other.delete(f"/api/files/{fid}")).status_code == 404
    assert (await other.get("/api/files/search", params={"q": "revenue"})).json() == []
    chat = (await other.post("/api/chats", json={})).json()["id"]
    resp = await other.post(
        f"/api/chats/{chat}/messages", json={"content": "summarise", "file_ids": [fid]}
    )
    assert resp.status_code == 404


async def test_viewers_cannot_upload(owner, client_for, invite):
    await invite(owner, "v@example.com", "viewer")
    viewer = await client_for("v@example.com")
    assert (await upload(viewer)).status_code == 403


async def test_rename_keeps_the_extension(owner):
    fid = (await upload(owner, name="data.csv", data=b"a,b\n1,2")).json()["id"]
    resp = await owner.patch(f"/api/files/{fid}", json={"name": "Q3 numbers"})
    assert resp.json()["name"] == "Q3 numbers.csv"


async def test_delete_removes_file_text_and_bytes(owner, app, storage):
    fid = (await upload(owner)).json()["id"]
    await make_ready(app, fid, ["Quarterly revenue grew 12 percent."])
    assert (await owner.delete(f"/api/files/{fid}")).status_code == 204
    assert storage.objects == {}
    assert (await owner.get(f"/api/files/{fid}")).status_code == 404
    assert (await owner.get("/api/files/search", params={"q": "revenue"})).json() == []


async def test_retry_failed_file(owner, queue):
    queue.fail = True
    fid = (await upload(owner)).json()["id"]
    queue.fail = False
    resp = await owner.post(f"/api/files/{fid}/retry")
    assert resp.json()["status"] == "processing" and str(queue.parses[-1]) == fid
    assert (await owner.post(f"/api/files/{fid}/retry")).status_code == 409


async def test_messages_carry_attachments(owner):
    fid = (await upload(owner)).json()["id"]
    chat = (await owner.post("/api/chats", json={})).json()["id"]
    resp = await owner.post(
        f"/api/chats/{chat}/messages", json={"content": "What changed?", "file_ids": [fid]}
    )
    assert resp.status_code == 202, resp.text
    assert [a["id"] for a in resp.json()["user_message"]["attachments"]] == [fid]
    messages = (await owner.get(f"/api/chats/{chat}/messages")).json()
    assert messages[0]["attachments"][0]["name"] == "notes.txt"
    assert messages[1]["attachments"] == []
    # The file now belongs to the chat it was sent in.
    assert (await owner.get("/api/files", params={"chat_id": chat})).json()[0]["id"] == fid
