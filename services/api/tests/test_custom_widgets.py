"""Custom widgets from the app's side: the page is served sandboxed with no network, and the
bridge only gives what the widget's manifest asks for and the person allowed."""

import uuid

from sqlalchemy import select

from nexus_common import custom_widgets as cw
from nexus_common.models import User

HTML = '<!doctype html><html><head><meta charset="utf-8"></head><body><div>hi</div></body></html>'


async def make(owner, sessionmaker, **manifest):
    await owner.get("/api/me")
    async with sessionmaker() as db:
        u = await db.scalar(select(User).where(User.email == "owner@example.com"))
        w = await cw.save(db, u, "Weather", HTML, cw.check_manifest(**manifest))
        await db.commit()
        return str(w.id)


def test_the_checks_name_what_the_frame_would_block():
    assert cw.check_html(HTML) == []
    bad = HTML.replace("<div>", '<div style="background:url(https://x.io/a.png)">')
    assert any("CSS url()" in p for p in cw.check_html(bad))
    assert any("200 KB" in p for p in cw.check_html(HTML + " " * 210_000))
    # The shim's own nexus.fetch is fine; a bare fetch( isn't.
    assert cw.check_html(HTML.replace("hi", "<script>nexus.fetch('u')</script>")) == []
    assert "<script>" in cw.frame_html(HTML).split("<body>")[0]


async def test_the_page_is_sandboxed_with_no_network(owner, sessionmaker):
    wid = await make(owner, sessionmaker)
    r = await owner.get(f"/api/widgets/{wid}/frame")
    assert r.status_code == 200 and "window.nexus" in r.text
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts") and "connect-src 'none'" in csp
    assert "allow-same-origin" not in csp
    widgets = (await owner.get("/api/widgets")).json()
    assert widgets[0]["kind"] == "custom" and "data" not in widgets[0]["state"]


async def test_state_round_trips_and_has_a_cap(owner, sessionmaker):
    wid = await make(owner, sessionmaker)
    bridge = f"/api/widgets/{wid}/bridge"
    assert (await owner.post(bridge, json={"call": "state.get"})).json() == {"result": None}
    await owner.post(bridge, json={"call": "state.set", "args": {"value": {"ticks": [1, 2]}}})
    r = await owner.post(bridge, json={"call": "state.get"})
    assert r.json() == {"result": {"ticks": [1, 2]}}
    big = {"call": "state.set", "args": {"value": "x" * 70_000}}
    assert (await owner.post(bridge, json=big)).status_code == 403


async def test_fetch_and_data_wait_for_approval(owner, sessionmaker):
    wid = await make(owner, sessionmaker, hosts=["api.open-meteo.com"], data=["files.recent"])
    bridge = f"/api/widgets/{wid}/bridge"
    r = await owner.post(bridge, json={"call": "data", "args": {"source": "files.recent"}})
    assert r.status_code == 403 and "isn't approved" in r.json()["detail"]
    r = await owner.post(
        bridge, json={"call": "fetch", "args": {"url": "https://api.open-meteo.com/v1"}}
    )
    assert r.status_code == 403 and "isn't approved" in r.json()["detail"]

    # Only what the manifest asks for can be allowed.
    r = await owner.post(
        f"/api/widgets/{wid}/approve",
        json={"hosts": ["evil.com"], "data": ["files.recent", "widgets"]},
    )
    assert r.json()["state"]["approved"] == {"hosts": [], "data": ["files.recent"], "actions": []}
    r = await owner.post(bridge, json={"call": "data", "args": {"source": "files.recent"}})
    assert r.status_code == 200 and r.json() == {"result": []}
    r = await owner.post(bridge, json={"call": "data", "args": {"source": "widgets"}})
    assert r.status_code == 403
    r = await owner.post(
        bridge, json={"call": "fetch", "args": {"url": "http://api.open-meteo.com/"}}
    )
    assert r.status_code == 403


async def test_notices_are_rate_limited(owner, sessionmaker):
    wid = await make(owner, sessionmaker)
    bridge = f"/api/widgets/{wid}/bridge"
    codes = [
        (await owner.post(bridge, json={"call": "notify", "args": {"title": f"n{i}"}})).status_code
        for i in range(cw.NOTIFY_PER_HOUR + 1)
    ]
    assert codes[:-1] == [200] * cw.NOTIFY_PER_HOUR and codes[-1] == 403
    tray = (await owner.get("/api/notifications")).json()
    assert tray["items"][0]["kind"] == "info"


async def test_other_people_cannot_see_a_widget(owner, sessionmaker, client_for, invite):
    wid = await make(owner, sessionmaker)
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    assert (await friend.get(f"/api/widgets/{wid}/frame")).status_code == 404
    r = await friend.post(f"/api/widgets/{wid}/bridge", json={"call": "state.get"})
    assert r.status_code == 404
    r = await owner.get(f"/api/widgets/{uuid.uuid4()}/frame")
    assert r.status_code == 404
    assert (
        await owner.post(f"/api/widgets/{wid}/action", json={"action": "undo"})
    ).status_code == 409
