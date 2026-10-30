import json

import httpx

from nexus_api.routes import graph_admin
from nexus_common.models import Memory, MemoryKind


async def test_graph_tab_counts_what_waits_and_queues_runs(owner, queue, redis, sessionmaker):
    me = (await owner.get("/api/me")).json()
    async with sessionmaker() as db:
        db.add(Memory(user_id=me["id"], kind=MemoryKind.fact, content="Has a cat called Miso"))
        await db.commit()
    await redis.delete(graph_admin.EXTRACT_KEY)
    nightly = {"at": "2026-10-31T00:00:00Z", "merged": 2}
    await redis.set(graph_admin.NIGHTLY_KEY, json.dumps(nightly))

    data = (await owner.get("/api/admin/graph")).json()
    # The tests run with the memory model building the graph, so Ollama isn't asked.
    assert data["extractor"] == "memory" and data["ollama"] is None
    assert data["backlog"] == {"memories": 1, "chats": 0, "files": 0}
    assert data["last_nightly"]["merged"] == 2 and data["last_extract"] is None
    assert data["shells"]["0"] == "you" and "association" in data["families"]
    assert data["entities"]["by_shell"] == {"0": 1}  # the owner's own entity

    assert (await owner.post("/api/admin/graph/run", json={"what": "rearrange"})).status_code == 202
    assert (await owner.post("/api/admin/graph/run", json={"what": "nope"})).status_code == 400
    assert queue.graph_jobs == ["graph_rearrange"]


async def test_ollama_status_says_what_is_missing(settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "bge-m3:latest"}]})
        return httpx.Response(200, json={"models": []})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        graph_admin.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    out = await graph_admin._ollama(settings)
    assert out["reachable"] and not out["installed"] and out["model"] == "qwen3.5:9b"


async def test_graph_tab_is_owner_only(owner, client_for, invite):
    await invite(owner, "admin@example.com", role="admin")
    admin = await client_for("admin@example.com")
    assert (await admin.get("/api/admin/graph")).status_code == 403
    assert (await admin.post("/api/admin/graph/run", json={"what": "extract"})).status_code == 403
