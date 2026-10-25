import io
import json
import zipfile

from sqlalchemy import select

from nexus_common.models import TERMS_VERSION, Memory, Routine, User


async def test_new_user_is_not_onboarded_and_existing_answers_are_offered(owner):
    me = (await owner.get("/api/me")).json()
    assert me["onboarded_at"] is None
    assert me["profile"] == {}
    options = (await owner.get("/api/profiles")).json()
    assert {"teaching", "student", "software"} <= {r["id"] for r in options["roles"]}
    assert "cooking" in {i["id"] for i in options["interests"]}
    essentials = next(p for p in options["packs"] if p["id"] == "essentials")
    brief = next(r for r in essentials["routines"] if r["key"] == "essentials/morning-brief")
    assert brief["time"] == "08:00" and brief["schedule_text"] == "Every day at 08:00"


async def test_onboarding_needs_a_name(owner):
    resp = await owner.post("/api/me/onboarding", json={})
    assert resp.status_code == 422


async def test_onboarding_stores_answers_memories_routines_and_a_welcome(
    owner, queue, sessionmaker
):
    resp = await owner.post(
        "/api/me/onboarding",
        json={
            "assistant_name": "Friday",
            "roles": ["teaching"],
            "role_note": "Teaches year 7 science",
            "interests": ["cooking", "travel"],
            "about": "Lives in Pune, follows climate news",
            "timezone": "Asia/Kolkata",
            "day_start": "07:30",
            "style": {"length": "short", "tone": "casual", "language": ""},
            "routines": [
                {"key": "essentials/morning-brief"},
                {"key": "teaching/week-ahead", "time": "19:15"},
                {"key": "nope/missing"},
            ],
            "delivery": ["email"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["routines"] == 2
    me = body["me"]
    assert me["onboarded_at"] and me["assistant_name"] == "Friday"
    assert me["timezone"] == "Asia/Kolkata"
    assert set(me["profile"]["packs"]) >= {"essentials", "teaching", "cooking", "travel"}
    assert queue.profiles, "the profile is rebuilt right away"

    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        memories = (await db.scalars(select(Memory).where(Memory.user_id == user.id))).all()
        routines = (await db.scalars(select(Routine).where(Routine.user_id == user.id))).all()
    texts = {m.content for m in memories}
    assert "Work: Teaches year 7 science" in texts
    assert "Works as a teacher." in texts
    assert "Prefers short, to-the-point answers." in texts
    assert {"Wants help with cooking.", "Wants help with travel."} <= texts
    assert all(m.source == "onboarding" for m in memories)
    # Professions and interests are topics around the user straight away, with the memories
    # about them attached; the free-text answer waits for the graph job.
    graph = (await owner.get("/api/memories/graph")).json()
    names = {e["name"]: e for e in graph["entities"]}
    assert {"Teaching", "Cooking", "Travel"} <= set(names)
    assert names["Cooking"]["type"] == "topic" and names["Cooking"]["memories"] == 1
    me = next(e for e in graph["entities"] if e["is_self"])
    predicates = {(r["src"], r["predicate"], r["dst"]) for r in graph["relations"]}
    assert (me["id"], "works in", names["Teaching"]["id"]) in predicates
    assert (me["id"], "wants help with", names["Travel"]["id"]) in predicates
    about = next(m for m in memories if m.content.startswith("Lives in Pune"))
    assert about.graph_at is None
    by_name = {r.name: r for r in routines}
    assert by_name["Morning brief"].schedule == "FREQ=DAILY;BYHOUR=7;BYMINUTE=30"
    assert by_name["Sunday week ahead"].schedule == "FREQ=WEEKLY;BYDAY=SU;BYHOUR=19;BYMINUTE=15"
    assert by_name["Morning brief"].timezone == "Asia/Kolkata"
    assert by_name["Morning brief"].delivery == ["email"]
    assert by_name["Morning brief"].next_run_at is not None
    assert by_name["Morning brief"].chat_id is not None

    welcome = (await owner.get(f"/api/chats/{body['chat_id']}/messages")).json()
    assert len(welcome) == 1 and "Morning brief" in welcome[0]["content"]
    assert "Plan a 45-minute lesson" in welcome[0]["content"]

    suggestions = (await owner.get("/api/me/suggestions")).json()
    assert suggestions[0] == "Plan a 45-minute lesson on this topic"


async def test_running_setup_again_replaces_its_memories_and_keeps_routines(owner, sessionmaker):
    first = {
        "assistant_name": "Friday",
        "roles": ["student"],
        "routines": [{"key": "essentials/morning-brief"}],
    }
    assert (await owner.post("/api/me/onboarding", json=first)).status_code == 200
    again = {"roles": ["software"], "routines": [{"key": "essentials/morning-brief"}]}
    resp = await owner.post("/api/me/onboarding", json=again)
    assert resp.json()["routines"] == 0
    async with sessionmaker() as db:
        texts = set((await db.scalars(select(Memory.content))).all())
        count = len((await db.scalars(select(Routine))).all())
    assert "Works in software or IT." in texts and "Is a student." not in texts
    assert count == 1


async def test_settings_fields(owner):
    resp = await owner.patch(
        "/api/me", json={"timezone": "Europe/London", "phone": "+44 7700 900123"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "Europe/London"
    assert resp.json()["phone"] == "+447700900123"
    assert (await owner.patch("/api/me", json={"timezone": "Mars/Base"})).status_code == 422
    assert (await owner.patch("/api/me", json={"phone": "12345"})).status_code == 422
    resp = await owner.patch("/api/me", json={"share_usage": False, "phone": ""})
    assert resp.json()["share_usage"] is False and resp.json()["phone"] is None
    resp = await owner.patch("/api/me", json={"tour_done": True})
    assert resp.json()["profile"]["tour_done"] is True


async def test_browser_zone_aliases_are_accepted(owner):
    # Chrome reports India as Asia/Calcutta, which slim images' system tz database lacks.
    resp = await owner.patch("/api/me", json={"timezone": "Asia/Calcutta"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "Asia/Calcutta"


async def test_changing_zone_moves_routines_on_the_old_zone(owner, sessionmaker):
    await owner.patch("/api/me", json={"timezone": "UTC"})
    setup = {
        "assistant_name": "Friday",
        "roles": ["software"],
        "routines": [{"key": "essentials/morning-brief"}],
    }
    resp = await owner.post("/api/me/onboarding", json=setup)
    assert resp.status_code == 200, resp.text
    own = {"name": "Berlin digest", "prompt": "Digest", "schedule": "FREQ=DAILY;BYHOUR=9"}
    resp = await owner.post("/api/routines", json={**own, "timezone": "Europe/Berlin"})
    assert resp.status_code == 201, resp.text
    async with sessionmaker() as db:
        before = (await db.scalars(select(Routine).where(Routine.name == "Morning brief"))).one()
        before_next = before.next_run_at
    await owner.patch("/api/me", json={"timezone": "Asia/Kolkata"})
    async with sessionmaker() as db:
        zones = {r.name: r for r in (await db.scalars(select(Routine))).all()}
    assert zones["Morning brief"].timezone == "Asia/Kolkata"
    assert zones["Morning brief"].next_run_at != before_next
    assert zones["Berlin digest"].timezone == "Europe/Berlin"


async def test_export_has_chats_memories_and_routines(owner):
    await owner.post(
        "/api/me/onboarding",
        json={"assistant_name": "Friday", "routines": [{"key": "essentials/morning-brief"}]},
    )
    resp = await owner.get("/api/me/export")
    assert resp.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    assert set(z.namelist()) == {
        "account.json",
        "chats.json",
        "memories.json",
        "routines.json",
        "files.json",
    }
    assert json.loads(z.read("account.json"))[0]["email"] == "owner@example.com"
    assert json.loads(z.read("routines.json"))[0]["name"] == "Morning brief"
    assert json.loads(z.read("chats.json"))


async def test_delete_account(owner, client_for, invite, sessionmaker):
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    resp = await friend.post(
        "/api/me/onboarding",
        json={
            "assistant_name": "Ava",
            "routines": [{"key": "essentials/morning-brief"}],
            "accept_terms": TERMS_VERSION,
        },
    )
    assert resp.status_code == 200, resp.text
    bad = await friend.post("/api/me/delete", json={"confirm": "someone@else.com"})
    assert bad.status_code == 422
    assert (
        await friend.post("/api/me/delete", json={"confirm": "Friend@example.com"})
    ).status_code == 204
    assert (await friend.get("/api/me")).status_code == 401
    async with sessionmaker() as db:
        assert await db.scalar(select(User).where(User.email == "friend@example.com")) is None
        assert not (await db.scalars(select(Routine))).all()
    resp = await owner.post("/api/me/delete", json={"confirm": "owner@example.com"})
    assert resp.status_code == 403


def test_every_shipped_pack_loads(settings):
    from pathlib import Path

    from nexus_common.profiles import load_packs

    files = list((Path(settings.library_path) / "profiles").glob("*.yaml"))
    packs = load_packs(settings.library_path)
    assert len(packs) == len(files) > 0
    assert sum(p.kind == "base" for p in packs) == 1


async def test_friends_must_agree_to_the_terms(owner, client_for, invite, sessionmaker):
    assert (await owner.get("/api/me")).json()["terms_needed"] is False
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    me = (await friend.get("/api/me")).json()
    assert me["terms_needed"] is True and me["terms_current"] == TERMS_VERSION
    refused = await friend.post("/api/me/onboarding", json={"assistant_name": "Ava"})
    assert refused.status_code == 422 and "Terms" in refused.json()["detail"]
    stale = await friend.post(
        "/api/me/onboarding", json={"assistant_name": "Ava", "accept_terms": "2020-01-01"}
    )
    assert stale.status_code == 422
    ok = await friend.post(
        "/api/me/onboarding", json={"assistant_name": "Ava", "accept_terms": TERMS_VERSION}
    )
    assert ok.json()["me"]["terms_needed"] is False
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "friend@example.com"))
        assert user.terms_version == TERMS_VERSION and user.terms_accepted_at

        # The terms change: they are asked again, and agree.
        user.terms_version = "2026-01-01"
        await db.commit()
    assert (await friend.get("/api/me")).json()["terms_needed"] is True
    again = await friend.post("/api/me/terms", json={"version": TERMS_VERSION})
    assert again.json()["terms_needed"] is False


def test_the_public_pages_show_the_current_version():
    from pathlib import Path

    public = Path(__file__).parents[3] / "apps/web/public"
    for page in ("privacy.html", "terms.html"):
        assert f"Version {TERMS_VERSION}" in (public / page).read_text(), page
