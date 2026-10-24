"""The Admin Monitoring tab: per-route and per-job figures, recent errors and alerts."""

import json
import logging

from nexus_api import alerts
from nexus_common import logs, metrics


async def test_requests_are_counted_by_route_template(owner):
    chat = (await owner.post("/api/chats", json={})).json()["id"]
    for _ in range(3):
        await owner.get(f"/api/chats/{chat}/messages")
    data = (await owner.get("/api/admin/monitoring?minutes=60")).json()
    routes = {r["name"]: r for r in data["routes"]}
    row = routes["GET /api/chats/{chat_id}/messages"]
    assert row["count"] == 3 and row["failed"] == 0
    assert row["p95_ms"] is not None
    assert not any(chat in name for name in routes)


async def test_jobs_are_timed_and_failures_counted(redis):
    @logs.traced_job
    async def parse_file(ctx, file_id: str):
        if file_id == "bad":
            raise ValueError("nope")

    ctx = {"redis": redis, "job_id": "parse:x"}
    await parse_file(ctx, "good")
    try:
        await parse_file(ctx, "bad")
    except ValueError:
        pass
    jobs = {j["name"]: j for j in (await metrics.summary(redis, 60))["jobs"]}
    assert jobs["parse_file"]["count"] == 2
    assert jobs["parse_file"]["failed"] == 1


def test_quantiles_come_from_the_histogram():
    hist = {"50": 90, "1000": 9, "inf": 1}
    assert metrics._quantile(hist, 100, 0.5) == 50
    assert metrics._quantile(hist, 100, 0.95) == 1000
    assert metrics._quantile(hist, 100, 0.999) is None


async def test_errors_reach_the_admin_page_with_their_ids(owner, redis):
    record = logging.LogRecord("nexus.test", logging.ERROR, __file__, 1, "Broke %s", ("x",), None)
    with logs.scope(request_id="req12345", user_id="u1"):
        entry = metrics.error_entry(record, "api", logs.current())
    await redis.lpush(metrics.ERRORS_KEY, json.dumps(entry))
    errors = (await owner.get("/api/admin/monitoring")).json()["errors"]
    assert errors[0]["msg"] == "Broke x"
    assert errors[0]["request_id"] == "req12345"
    assert errors[0]["service"] == "api"


async def test_a_browser_crash_is_logged(owner, caplog):
    with caplog.at_level(logging.ERROR, logger="nexus.web"):
        resp = await owner.post(
            "/api/me/client-error",
            json={"message": "TypeError: x is undefined", "stack": "at Foo", "page": "/agents"},
        )
    assert resp.status_code == 204
    rec = next(r for r in caplog.records if r.name == "nexus.web")
    assert "TypeError" in rec.getMessage() and rec.page == "/agents"


async def test_alerts_need_two_failing_checks_and_clear_when_fixed(app, owner, redis):
    try:
        await _alert_lifecycle(app, owner, redis)
    finally:
        await redis.delete("arq:queue:health-check", "arq:docs:health-check")


async def _alert_lifecycle(app, owner, redis):
    app.state.settings = app.state.settings.model_copy(
        update={"alert_api_errors_5m": 0, "alert_job_failures_15m": 0}
    )
    await redis.set("arq:queue:health-check", "Oct-20 j_complete=1 j_failed=0 j_ongoing=0")
    raised, _ = await alerts.check_once(app)
    assert raised == []  # first sighting is only pending
    raised, _ = await alerts.check_once(app)
    keys = {p.key for p in raised}
    assert "service:Document worker" in keys and "backup" in keys
    assert "service:Orchestrator worker" not in keys
    # Still failing: nothing new is raised.
    assert (await alerts.check_once(app))[0] == []

    await redis.set("arq:docs:health-check", "Oct-20 j_complete=1 j_failed=0 j_ongoing=0")
    _, resolved = await alerts.check_once(app)
    assert [a["key"] for a in resolved] == ["service:Document worker"]

    data = (await owner.get("/api/admin/monitoring")).json()["alerts"]
    assert {a["key"] for a in data["active"]} == {"backup"}
    states = [(h["key"], h["state"]) for h in data["recent"]]
    assert ("service:Document worker", "resolved") in states
