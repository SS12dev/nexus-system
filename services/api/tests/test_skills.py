import io
import zipfile
from pathlib import Path

from jarvis_common.skills import sync_library

SKILL = """---
name: meeting-notes
description: Turn a meeting transcript into decisions, owners and dates. Use for minutes.
---

# Meeting notes

1. List decisions.
"""


async def sync(sessionmaker, settings):
    async with sessionmaker() as db:
        return await sync_library(db, Path(settings.library_path))


async def test_core_skills_are_listed_and_admin_only(
    owner, client_for, invite, sessionmaker, settings
):
    assert await sync(sessionmaker, settings) >= 6
    assert await sync(sessionmaker, settings) == 0  # unchanged files: nothing to do
    skills = (await owner.get("/api/skills")).json()
    slugs = {s["slug"] for s in skills}
    assert {"core/pdf", "core/docx", "core/pptx", "core/xlsx", "core/charts"} <= slugs
    pdf = next(s for s in skills if s["slug"] == "core/pdf")
    assert pdf["can_edit"] is True and "scripts/md_to_pdf.py" in pdf["files"]

    await invite(owner, "member@example.com")
    member = await client_for("member@example.com")
    listed = (await member.get("/api/skills")).json()
    mine = next(s for s in listed if s["slug"] == "core/pdf")
    assert mine["can_edit"] is False
    resp = await member.post(f"/api/skills/{mine['id']}/status", json={"status": "disabled"})
    assert resp.status_code == 403
    # Disabled shared skills disappear for members.
    await owner.post(f"/api/skills/{pdf['id']}/status", json={"status": "disabled"})
    assert "core/pdf" not in {s["slug"] for s in (await member.get("/api/skills")).json()}
    assert (await owner.delete(f"/api/skills/{pdf['id']}")).status_code == 409


async def test_personal_skill_edit_versions_and_rollback(owner):
    resp = await owner.post("/api/skills", json={"content": SKILL})
    assert resp.status_code == 201, resp.text
    s = resp.json()
    assert s["slug"] == "personal/meeting-notes" and s["status"] == "active"
    edited = SKILL.replace("1. List decisions.", "1. List decisions.\n2. Then owners.")
    s2 = (
        await owner.put(f"/api/skills/{s['id']}", json={"content": edited, "note": "owners"})
    ).json()
    assert s2["version"] == 2 and "Then owners" in s2["content"]
    versions = (await owner.get(f"/api/skills/{s['id']}/versions")).json()
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["note"] == "owners"
    back = (await owner.post(f"/api/skills/{s['id']}/rollback", json={"version": 1})).json()
    assert back["version"] == 3 and "Then owners" not in back["content"]
    # Invalid SKILL.md is refused with a reason.
    bad = await owner.put(f"/api/skills/{s['id']}", json={"content": "no frontmatter"})
    assert bad.status_code == 422 and "frontmatter" in bad.json()["detail"]
    dup = await owner.post("/api/skills", json={"content": SKILL})
    assert dup.status_code == 409
    found = (await owner.get("/api/skills", params={"q": "meeting transcript minutes"})).json()
    assert found and found[0]["slug"] == "personal/meeting-notes"


async def test_import_zip_starts_as_draft(owner):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("meeting-notes/SKILL.md", SKILL)
        z.writestr("meeting-notes/scripts/clean.py", "print('hi')\n")
    resp = await owner.post(
        "/api/skills/import", files={"file": ("meeting.zip", buf.getvalue(), "application/zip")}
    )
    assert resp.status_code == 201, resp.text
    s = resp.json()
    assert s["status"] == "draft" and s["file_contents"] == {"scripts/clean.py": "print('hi')\n"}
    drafts = (await owner.get("/api/skills", params={"status": "draft"})).json()
    assert [d["id"] for d in drafts] == [s["id"]]
    assert (await owner.get("/api/skills", params={"scope": "learned"})).json() == []
    on = (await owner.post(f"/api/skills/{s['id']}/status", json={"status": "active"})).json()
    assert on["status"] == "active"


async def test_instruction_layers(owner, client_for, invite):
    data = (await owner.get("/api/instructions")).json()
    assert "never" in data["safety"].lower()
    assert {"researcher", "writer"} <= {r["role"] for r in data["roles"]}
    assert (
        await owner.put("/api/instructions/user", json={"content": "British English."})
    ).status_code == 204
    assert (
        await owner.put("/api/instructions/roles/writer", json={"content": "Short."})
    ).status_code == 204
    data = (await owner.get("/api/instructions")).json()
    assert data["user"] == "British English."
    assert next(r for r in data["roles"] if r["role"] == "writer")["extra"] == "Short."
    await invite(owner, "m2@example.com")
    member = await client_for("m2@example.com")
    assert (await member.put("/api/instructions/global", json={"content": "x"})).status_code == 403
    assert (await member.get("/api/instructions")).json()["user"] == ""


async def test_library_shelves_and_shared_conventions(owner, sessionmaker, settings):
    await sync(sessionmaker, settings)
    skills = (await owner.get("/api/skills")).json()
    shelf = {s["slug"]: s["category"] for s in skills}
    assert shelf["core/pdf"] == "documents"
    assert shelf["core/literature-review"] == "research"
    assert shelf["core/llm-evaluation"] == "data"
    data = (await owner.get("/api/instructions")).json()
    assert "Sources" in data["shared"]
    assert {"scientist", "fact_checker"} <= {r["role"] for r in data["roles"]}


async def test_share_moves_a_skill_into_the_shared_library(owner, client_for, invite):
    s = (await owner.post("/api/skills", json={"content": SKILL})).json()
    await invite(owner, "member@example.com")
    member = await client_for("member@example.com")
    assert "personal/meeting-notes" not in {
        x["slug"] for x in (await member.get("/api/skills")).json()
    }

    # Members can't publish to everyone, even their own skills.
    theirs = (await member.post("/api/skills", json={"content": SKILL})).json()
    assert (await member.post(f"/api/skills/{theirs['id']}/share")).status_code == 403

    shared = await owner.post(f"/api/skills/{s['id']}/share")
    assert shared.status_code == 200, shared.text
    body = shared.json()
    assert body["slug"] == "domain/meeting-notes" and body["owner_id"] is None
    assert body["status"] == "active" and body["version"] == 2
    listed = {x["slug"] for x in (await member.get("/api/skills")).json()}
    assert "domain/meeting-notes" in listed
    versions = (await owner.get(f"/api/skills/{s['id']}/versions")).json()
    assert "Shared with everyone" in versions[0]["note"]
    # Already shared: not one of the owner's own skills any more.
    assert (await owner.post(f"/api/skills/{s['id']}/share")).status_code == 409
