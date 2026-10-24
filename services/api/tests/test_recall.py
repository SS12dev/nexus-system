from sqlalchemy import select

from nexus_common.models import RecallEval


async def test_owner_starts_one_check_at_a_time(owner, queue, sessionmaker):
    summary = (await owner.get("/api/admin/recall")).json()
    assert summary == {
        "questions": 0,
        "memories": 0,
        "past_chats": 0,
        "checks": [],
        "graph_recall_enabled": False,
    }

    first = await owner.post("/api/admin/recall/run")
    assert first.status_code == 202 and first.json()["status"] == "running"
    again = await owner.post("/api/admin/recall/run")
    assert again.json()["id"] == first.json()["id"]
    assert [str(i) for i in queue.recall_checks] == [first.json()["id"]]

    async with sessionmaker() as db:
        ev = await db.scalar(select(RecallEval))
        ev.status, ev.scores = "done", {"standard": {"n": 4, "hits": 3, "hit_rate": 0.75}}
        await db.commit()
    checks = (await owner.get("/api/admin/recall")).json()["checks"]
    assert checks[0]["scores"]["standard"]["hit_rate"] == 0.75
    assert (await owner.delete("/api/admin/recall/questions")).status_code == 204


async def test_recall_tab_is_owner_only(owner, client_for, invite):
    await invite(owner, "admin@example.com", role="admin")
    admin = await client_for("admin@example.com")
    assert (await admin.get("/api/admin/recall")).status_code == 403
    assert (await admin.post("/api/admin/recall/run")).status_code == 403
