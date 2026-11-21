"""The life organiser (nexus_common/organiser.py, /api/organiser)."""

import io
import zipfile
from datetime import date, timedelta

from nexus_common.organiser import parse_capture, plain, week_of, wikilinks

MON = date(2026, 11, 16)


def test_capture_reads_tasks_tags_and_dates():
    c = parse_capture("[ ] call the bank tomorrow #money #Admin", MON)
    assert (c.type, c.title, c.due_on, c.tags) == (
        "task",
        "call the bank",
        MON + timedelta(days=1),
        ["money", "admin"],
    )
    c = parse_capture("todo: renew passport by friday", MON)
    assert (c.type, c.title, c.due_on) == ("task", "renew passport", date(2026, 11, 20))
    # Same weekday means next week, not today.
    assert parse_capture("- [ ] gym monday", MON).due_on == MON + timedelta(days=7)
    assert parse_capture("t: file taxes 2027-01-31", MON).due_on == date(2027, 1, 31)
    # A note keeps its words; dates are only read from a task's title.
    c = parse_capture("Idea for Friday\nmore detail here", MON)
    assert (c.type, c.title, c.body, c.due_on) == (
        "note",
        "Idea for Friday",
        "more detail here",
        None,
    )


def test_list_excerpts_are_plain_words():
    text = "## Plan\n\n- [ ] **Book** [[Flat move|the move]]\n| A | B |\n|---|---|\n| x | y |"
    assert plain(text) == "Plan Book the move A B x y"


def test_wikilinks_and_review_week():
    assert wikilinks("see [[Flat move]] and [[Flat move|the move]] and [[Tax#2026]]") == [
        "Flat move",
        "Tax",
    ]
    assert week_of(date(2026, 11, 22)) == date(2026, 11, 23)  # Sunday plans the coming week
    assert week_of(date(2026, 11, 18)) == MON


async def make(client, **body) -> dict:
    resp = await client.post("/api/organiser/items", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_projects_hold_notes_and_tasks_with_backlinks(owner):
    area = await make(owner, type="area", title="Home")
    project = await make(owner, type="project", title="Flat move", parent_id=area["id"])
    task = await make(owner, type="task", title="Book packers", parent_id=project["id"], priority=3)
    note = await make(
        owner, type="note", title="Quotes", body="For [[Flat move]]; ask [[Nobody yet]]."
    )
    assert task["status"] == "todo" and note["status"] == "active"

    detail = (await owner.get(f"/api/organiser/items/{project['id']}")).json()
    assert detail["parent"]["title"] == "Home"
    assert [c["id"] for c in detail["children"]] == [task["id"]]
    assert [b["id"] for b in detail["backlinks"]] == [note["id"]]
    note_detail = (await owner.get(f"/api/organiser/items/{note['id']}")).json()
    assert note_detail["unresolved"] == ["Nobody yet"]
    assert [x["id"] for x in note_detail["links"]] == [project["id"]]

    tasks = (await owner.get("/api/organiser/items", params={"type": "task"})).json()
    assert [t["title"] for t in tasks] == ["Book packers"]
    assert "body" not in tasks[0] or tasks[0]["body"] is None

    # A task can't hold things, and an area can't sit in a project.
    bad = await owner.post(
        "/api/organiser/items", json={"type": "note", "title": "x", "parent_id": task["id"]}
    )
    assert bad.status_code == 422
    bad = await owner.patch(f"/api/organiser/items/{area['id']}", json={"parent_id": project["id"]})
    assert bad.status_code == 422
    bad = await owner.patch(f"/api/organiser/items/{task['id']}", json={"status": "active"})
    assert bad.status_code == 422

    # Renaming the project keeps the note's link pointing at it once the note says so.
    await owner.patch(f"/api/organiser/items/{project['id']}", json={"title": "Move to Pune"})
    await owner.patch(f"/api/organiser/items/{note['id']}", json={"body": "For [[move to pune]]."})
    detail = (await owner.get(f"/api/organiser/items/{project['id']}")).json()
    assert [b["id"] for b in detail["backlinks"]] == [note["id"]]


async def test_an_outdated_save_is_refused_and_revisions_restore(owner):
    note = await make(owner, title="Reading list", body="one")
    ok = await owner.patch(
        f"/api/organiser/items/{note['id']}", json={"version": 1, "body": "one two"}
    )
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = await owner.patch(
        f"/api/organiser/items/{note['id']}", json={"version": 1, "body": "phone edit"}
    )
    assert stale.status_code == 409
    assert stale.json()["current"]["body"] == "one two"

    revs = (await owner.get(f"/api/organiser/items/{note['id']}/revisions")).json()
    assert [r["version"] for r in revs] == [1]
    # Saving over the other device's edit keeps that edit in the history, however recent.
    mine = await owner.patch(
        f"/api/organiser/items/{note['id']}", json={"body": "laptop", "keep_revision": True}
    )
    assert mine.json()["version"] == 3
    revs = (await owner.get(f"/api/organiser/items/{note['id']}/revisions")).json()
    assert [(r["version"], r["actor"]) for r in revs] == [(2, "conflict"), (1, "user")]
    revs = revs[1:]
    full = (await owner.get(f"/api/organiser/items/{note['id']}/revisions/{revs[0]['id']}")).json()
    assert full["body"] == "one"
    back = await owner.post(f"/api/organiser/items/{note['id']}/revisions/{revs[0]['id']}/restore")
    assert back.json()["body"] == "one" and back.json()["version"] == 4
    # The text that was replaced is kept too.
    revs = (await owner.get(f"/api/organiser/items/{note['id']}/revisions")).json()
    assert len(revs) == 3


async def test_capture_waits_in_the_inbox_until_filed(owner, queue):
    resp = await owner.post("/api/organiser/capture", json={"text": "[ ] pay rent today #home"})
    assert resp.status_code == 201
    item = resp.json()
    assert (item["type"], item["status"], item["tags"]) == ("task", "inbox", ["home"])
    assert [str(i) for i in queue.organiser_suggests] == [item["id"]]

    inbox = (await owner.get("/api/organiser/items", params={"status": "inbox"})).json()
    assert [i["id"] for i in inbox] == [item["id"]]
    overview = (await owner.get("/api/organiser/overview")).json()
    assert overview["inbox"] == 1

    area = await make(owner, type="area", title="Home")
    filed = await owner.patch(
        f"/api/organiser/items/{item['id']}", json={"status": "todo", "parent_id": area["id"]}
    )
    assert filed.json()["status"] == "todo" and filed.json()["suggestion"] is None
    assert (await owner.get("/api/organiser/overview")).json()["inbox"] == 0

    # Typed into a project's list: filed straight away, a task even without "[ ]".
    filed = await owner.post(
        "/api/organiser/capture",
        json={"text": "water plants friday", "inbox": False, "task": True, "parent_id": area["id"]},
    )
    got = filed.json()
    assert (got["type"], got["status"], got["parent_id"], got["title"]) == (
        "task",
        "todo",
        area["id"],
        "water plants",
    )
    assert got["due_on"] is not None and len(queue.organiser_suggests) == 1


async def test_daily_note_is_made_once_and_checkboxes_become_tasks(owner):
    day = "2026-11-20"
    first = (await owner.get(f"/api/organiser/daily/{day}")).json()
    again = (await owner.get(f"/api/organiser/daily/{day}")).json()
    assert first["id"] == again["id"] and first["type"] == "daily"
    assert "Top three" in first["body"]

    await owner.patch(
        f"/api/organiser/items/{first['id']}",
        json={"body": "- [ ] Call the bank\n- [x] done already\n- [ ] [[Linked]]\n"},
    )
    made = (await owner.post(f"/api/organiser/items/{first['id']}/tasks")).json()
    assert [t["title"] for t in made["made"]] == ["Call the bank"]
    assert made["made"][0]["due_on"] == day
    assert "- [ ] [[Call the bank]]" in made["item"]["body"]
    # Daily notes stay out of ordinary lists and can't be deleted.
    assert all(i["type"] != "daily" for i in (await owner.get("/api/organiser/items")).json())
    assert (await owner.delete(f"/api/organiser/items/{first['id']}")).status_code == 422

    today = (await owner.get("/api/organiser/today")).json()
    assert today["daily"]["type"] == "daily"


async def test_deleting_a_project_takes_its_contents_to_trash_and_back(owner):
    project = await make(owner, type="project", title="Garden")
    task = await make(owner, type="task", title="Buy soil", parent_id=project["id"])
    assert (await owner.delete(f"/api/organiser/items/{project['id']}")).status_code == 204
    assert (await owner.get(f"/api/organiser/items/{task['id']}")).status_code == 404
    assert (await owner.get("/api/organiser/items")).json() == []

    trash = (await owner.get("/api/trash")).json()
    assert [(t["kind"], t["title"]) for t in trash] == [("org_item", "Garden")]
    assert (await owner.post(f"/api/trash/{trash[0]['id']}/restore")).status_code == 200
    assert (await owner.get(f"/api/organiser/items/{task['id']}")).status_code == 200


async def test_weekly_review_and_overview(owner):
    project = await make(owner, type="project", title="Quiet project")
    overdue = await make(owner, type="task", title="Old thing", due_on="2020-01-01")
    await owner.post("/api/organiser/capture", json={"text": "loose idea"})

    review = (await owner.get("/api/organiser/review")).json()
    assert [i["title"] for i in review["inbox"]] == ["loose idea"]
    assert [i["id"] for i in review["overdue"]] == [overdue["id"]]
    # No open task: shows as stalled.
    assert [i["id"] for i in review["stalled"]] == [project["id"]]
    assert review["completed_at"] is None

    done = await owner.post(
        "/api/organiser/review", json={"focus": [overdue["id"]], "notes": "Clear the backlog."}
    )
    assert done.status_code == 200
    assert done.json()["completed_at"] and done.json()["focus"] == [overdue["id"]]
    overview = (await owner.get("/api/organiser/overview")).json()
    assert overview["overdue"] == 1 and overview["projects"] == 1
    assert overview["review"]["completed_at"] and not overview["review"]["due"]
    assert [f["id"] for f in (await owner.get("/api/organiser/today")).json()["focus"]] == [
        overdue["id"]
    ]


async def test_export_is_an_obsidian_vault(owner):
    area = await make(owner, type="area", title="Health")
    await make(owner, type="task", title="Book: dentist?", parent_id=area["id"], tags=["Body"])
    await make(owner, type="resource", title="Recipes", status="archived")
    resp = await owner.get("/api/organiser/export.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "attachment" in resp.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(z.namelist())
    assert {
        "Nexus/README.md",
        "Nexus/2 Areas/Health.md",
        "Nexus/Tasks/Book dentist.md",
        "Nexus/4 Archive/Recipes.md",
    } <= names
    health = z.read("Nexus/2 Areas/Health.md").decode()
    assert health.startswith("---\nid: ") and "- [ ] [[Book dentist]]" in health
    task = z.read("Nexus/Tasks/Book dentist.md").decode()
    assert 'parent: "[[Health]]"' in task and 'tags: ["body"]' in task


async def test_other_people_cannot_see_or_touch_items(owner, client_for, invite):
    note = await make(owner, title="Private")
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    assert (await friend.get(f"/api/organiser/items/{note['id']}")).status_code == 404
    assert (
        await friend.patch(f"/api/organiser/items/{note['id']}", json={"title": "x"})
    ).status_code == 404
    assert (await friend.delete(f"/api/organiser/items/{note['id']}")).status_code == 404
    assert (await friend.get("/api/organiser/items")).json() == []
    bad = await friend.post(
        "/api/organiser/items", json={"type": "note", "title": "x", "parent_id": note["id"]}
    )
    assert bad.status_code == 404


async def test_search_finds_organiser_items(owner):
    project = await make(owner, type="project", title="Passport renewal")
    await make(owner, type="note", title="Documents", body="Bring the old passport and photos.")
    daily = (await owner.get("/api/organiser/daily/2026-11-19")).json()
    await owner.patch(f"/api/organiser/items/{daily['id']}", json={"body": "passport office"})
    body = (await owner.get("/api/find", params={"q": "passport"})).json()
    got = {(r["kind"], r["title"]): r for r in body["results"]}
    assert got[("project", "Passport renewal")]["url"] == f"/organiser/item/{project['id']}"
    assert "\x02passport\x03" in got[("note", "Documents")]["snippet"].lower()
    assert got[("note", daily["title"])]["url"] == "/organiser/daily/2026-11-19"
    only = (await owner.get("/api/find", params={"q": "passport", "kinds": ["project"]})).json()
    assert {r["kind"] for r in only["results"]} == {"project"}
