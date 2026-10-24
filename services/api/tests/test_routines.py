from datetime import datetime


async def make(client, **extra):
    body = {
        "name": "Daily digest",
        "prompt": "Tell me the news on my topics.",
        "schedule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        "timezone": "Europe/Berlin",
        **extra,
    }
    return await client.post("/api/routines", json=body)


async def test_create_list_update_and_delete(owner):
    resp = await make(owner, delivery=["whatsapp", "email"])
    assert resp.status_code == 201, resp.text
    r = resp.json()
    assert r["schedule_text"] == "Every day at 09:00"
    assert r["delivery"] == ["email", "whatsapp"]
    assert datetime.fromisoformat(r["next_run_at"]).astimezone().utcoffset() is not None
    assert r["chat_id"]

    listed = (await owner.get("/api/routines")).json()
    assert [x["id"] for x in listed] == [r["id"]]

    resp = await owner.patch(f"/api/routines/{r['id']}", json={"enabled": False})
    assert resp.json()["enabled"] is False and resp.json()["next_run_at"] is None
    resp = await owner.patch(
        f"/api/routines/{r['id']}",
        json={"enabled": True, "schedule": "FREQ=WEEKLY;BYDAY=MO,FR;BYHOUR=7;BYMINUTE=15"},
    )
    assert resp.json()["schedule_text"] == "Every Monday and Friday at 07:15"
    assert resp.json()["next_run_at"]

    assert (await owner.delete(f"/api/routines/{r['id']}")).status_code == 204
    assert (await owner.get("/api/routines")).json() == []


async def test_bad_schedules_are_refused(owner):
    assert (await make(owner, schedule="FREQ=MINUTELY")).status_code == 422
    assert (await make(owner, schedule="nonsense")).status_code == 422
    assert (await make(owner, timezone="Nowhere/Land")).status_code == 422
    resp = await make(owner, schedule="FREQ=HOURLY;BYMINUTE=0,30")
    assert resp.status_code == 422 and "60 minutes" in resp.json()["detail"]


async def test_preview(owner):
    resp = await owner.post(
        "/api/routines/preview",
        json={"schedule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8;BYMINUTE=0"},
    )
    body = resp.json()
    assert body["ok"] and body["text"] == "Weekdays at 08:00" and len(body["next"]) == 3
    resp = await owner.post("/api/routines/preview", json={"schedule": "FREQ=SECONDLY"})
    assert resp.json()["ok"] is False and resp.json()["error"]


async def test_limit_per_user(owner, app):
    app.state.settings.routines_max_per_user = 2
    try:
        assert (await make(owner)).status_code == 201
        assert (await make(owner)).status_code == 201
        resp = await make(owner)
        assert resp.status_code == 422 and "most you may have" in resp.json()["detail"]
    finally:
        app.state.settings.routines_max_per_user = 10


async def test_run_now_and_privacy(owner, client_for, invite, queue):
    r = (await make(owner)).json()
    resp = await owner.post(f"/api/routines/{r['id']}/run")
    assert resp.status_code == 202
    assert str(queue.routines[0][0]) == r["id"]

    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    assert (await friend.get("/api/routines")).json() == []
    assert (await friend.post(f"/api/routines/{r['id']}/run")).status_code == 404
    assert (await friend.get(f"/api/routines/{r['id']}/runs")).status_code == 404
    assert (await owner.get(f"/api/routines/{r['id']}/runs")).json() == []
