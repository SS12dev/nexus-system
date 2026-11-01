import json

import httpx
from sqlalchemy import select

from nexus_api.routes import graph_admin
from nexus_common.models import Entity, Memory, MemoryKind, Relation, utcnow


async def test_graph_tab_counts_what_waits_and_queues_runs(owner, queue, redis, sessionmaker):
    me = (await owner.get("/api/me")).json()
    async with sessionmaker() as db:
        db.add(Memory(user_id=me["id"], kind=MemoryKind.fact, content="Has a cat called Miso"))
        db.add(Entity(user_id=me["id"], type="pet", shell=2, name="Miso", aliases=[], summary=""))
        await db.commit()
    await redis.delete(graph_admin.EXTRACT_KEY)
    nightly = {"at": "2026-10-31T00:00:00Z", "merged": 2}
    await redis.set(graph_admin.NIGHTLY_KEY, json.dumps(nightly))

    data = (await owner.get("/api/admin/graph")).json()
    # The tests run with the memory model building the graph, so Ollama isn't asked.
    assert data["extractor"] == "memory" and data["ollama"] is None
    assert data["backlog"] == {"memories": 1, "chats": 0, "files": 0, "imported": 0}
    assert data["last_nightly"]["merged"] == 2 and data["last_extract"] is None
    assert data["shells"]["0"] == "you" and "association" in data["families"]
    assert data["entities"]["by_shell"] == {"2": 1}

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


async def test_rebuild_clears_what_models_made_and_rereads_everything(owner, queue, sessionmaker):
    me = (await owner.get("/api/me")).json()
    async with sessionmaker() as db:
        mem = Memory(user_id=me["id"], kind=MemoryKind.fact, content="Has a cat called Miso")
        db.add(mem)
        cloud = Entity(user_id=me["id"], type="pet", shell=2, name="Miso", aliases=[], summary="")
        said = Entity(
            user_id=me["id"],
            type="place",
            shell=2,
            name="Pune",
            aliases=[],
            summary="",
            origin="user",
        )
        db.add_all([cloud, said])
        await db.flush()
        db.add(
            Relation(
                user_id=me["id"],
                src_id=said.id,
                dst_id=cloud.id,
                predicate="related to",
                family="association",
                label="related to",
                fact="",
            )
        )
        mem.graph_at = utcnow()
        await db.commit()

    assert (
        await owner.post("/api/admin/graph/rebuild", json={"confirm": "yes"})
    ).status_code == 400
    r = await owner.post("/api/admin/graph/rebuild", json={"confirm": "Rebuild"})
    assert r.status_code == 202 and r.json() == {"relations": 1, "entities": 1}
    assert queue.graph_jobs == ["graph_extract"]
    async with sessionmaker() as db:
        names = {e.name for e in (await db.scalars(select(Entity))).all()}
        assert names == {"Pune"}
        assert (await db.scalar(select(Memory))).graph_at is None
