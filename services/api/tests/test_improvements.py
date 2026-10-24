"""The Admin Improvements tab: proposals from self-review, approved, denied or undone."""

import uuid

import pytest
from sqlalchemy import select

from nexus_common import improve
from nexus_common.models import (
    ImprovementReview,
    Instruction,
    InstructionLayer,
    Proposal,
    ProposalKind,
    ProposalStatus,
    Skill,
    SkillScope,
    SkillStatus,
)

SKILL_MD = (
    "---\nname: trip-planning\ndescription: Plan a trip with options and a budget.\n---\n\n"
    "1. Ask for dates.\n2. Compare options.\n"
)


@pytest.fixture
def propose(sessionmaker):
    async def make(kind: ProposalKind, payload: dict, title: str = "Do a thing") -> str:
        async with sessionmaker() as db:
            p = Proposal(
                kind=kind,
                status=ProposalStatus.pending,
                title=title,
                summary="Because the figures say so.",
                evidence=["x: 3"],
                payload=payload,
                fingerprint=uuid.uuid4().hex,
                result={},
            )
            db.add(p)
            await db.commit()
            return str(p.id)

    return make


async def test_an_approved_skill_is_shared_and_undo_turns_it_off(owner, propose, sessionmaker):
    pid = await propose(
        ProposalKind.skill,
        {"name": "trip-planning", "description": "Plan a trip.", "content": SKILL_MD},
    )
    listing = (await owner.get("/api/admin/improvements")).json()
    assert listing["counts"]["pending"] == 1
    assert (await owner.get("/api/admin/summary")).json()["proposals"] == 1

    p = (await owner.post(f"/api/admin/improvements/{pid}/approve", json={})).json()
    assert p["status"] == "applied"
    async with sessionmaker() as db:
        skill = await db.get(Skill, uuid.UUID(p["result"]["skill_id"]))
    assert skill.scope == SkillScope.domain and skill.status == SkillStatus.active
    assert skill.source == f"proposal:{pid}"
    # Twice is refused.
    assert (await owner.post(f"/api/admin/improvements/{pid}/approve", json={})).status_code == 409

    p = (await owner.post(f"/api/admin/improvements/{pid}/revert")).json()
    assert p["status"] == "reverted"
    async with sessionmaker() as db:
        assert (await db.get(Skill, skill.id)).status == SkillStatus.disabled


async def test_an_approved_instruction_is_added_and_can_be_undone(owner, propose, sessionmaker):
    await owner.put("/api/instructions/global", json={"content": "Be brief."})
    pid = await propose(
        ProposalKind.instruction, {"layer": "global", "role": "", "text": "Cite sources."}
    )
    await owner.post(f"/api/admin/improvements/{pid}/approve", json={})
    assert (await owner.get("/api/instructions")).json()["global"] == "Be brief.\n\nCite sources."
    await owner.post(f"/api/admin/improvements/{pid}/revert")
    assert (await owner.get("/api/instructions")).json()["global"] == "Be brief."

    # Edited since: undo refuses rather than losing the edit.
    pid = await propose(
        ProposalKind.instruction, {"layer": "role", "role": "researcher", "text": "Use 3 sources."}
    )
    await owner.post(f"/api/admin/improvements/{pid}/approve", json={})
    await owner.put("/api/instructions/roles/researcher", json={"content": "Changed by hand."})
    resp = await owner.post(f"/api/admin/improvements/{pid}/revert")
    assert resp.status_code == 409 and "edited since" in resp.json()["detail"]
    async with sessionmaker() as db:
        row = await db.scalar(
            select(Instruction).where(
                Instruction.layer == InstructionLayer.role, Instruction.key == "researcher"
            )
        )
    assert row.content == "Changed by hand."


async def test_an_approved_setting_applies_at_once_and_undo_restores_it(owner, propose, app):
    pid = await propose(
        ProposalKind.setting,
        {"name": "route_model", "label": "Router model", "value": "nexus-mini"},
    )
    before = app.state.settings.route_model
    await owner.post(f"/api/admin/improvements/{pid}/approve", json={})
    assert app.state.settings.route_model == "nexus-mini"
    view = (await owner.get("/api/admin/config")).json()
    opt = next(o for o in view["options"] if o["name"] == "route_model")
    assert opt["overridden"] and opt["value"] == "nexus-mini"

    await owner.post(f"/api/admin/improvements/{pid}/revert")
    assert app.state.settings.route_model == before
    view = (await owner.get("/api/admin/config")).json()
    assert not next(o for o in view["options"] if o["name"] == "route_model")["overridden"]


async def test_code_proposals_go_to_a_pull_request_or_stay_a_spec(owner, propose, app, queue):
    spec = {"spec": "Add a tool that reads invites.", "files": []}
    pid = await propose(ProposalKind.tool, spec, "Add an invite reader")
    p = (await owner.post(f"/api/admin/improvements/{pid}/approve", json={})).json()
    assert p["status"] == "applied" and p["result"] == {"spec_only": True}
    assert queue.proposal_prs == []

    app.state.settings = app.state.settings.model_copy(
        update={
            "improve_repo": "SS12dev/nexus-system",
            "github_apps_org": "servhub-apps",
            "github_app_id": "1",
            "github_app_installation_id": "2",
            "github_app_private_key_b64": "eA==",
        }
    )
    pid = await propose(ProposalKind.code, spec, "Speed up the router")
    p = (await owner.post(f"/api/admin/improvements/{pid}/approve", json={})).json()
    assert p["status"] == "applying"
    assert queue.proposal_prs == [uuid.UUID(pid)]
    assert (await owner.post(f"/api/admin/improvements/{pid}/revert")).status_code == 409


async def test_a_denial_keeps_the_reason_and_is_final(owner, propose):
    pid = await propose(ProposalKind.code, {"spec": "x" * 50, "files": []})
    p = (
        await owner.post(f"/api/admin/improvements/{pid}/deny", json={"note": "Not worth it"})
    ).json()
    assert p["status"] == "denied" and p["note"] == "Not worth it"
    assert (await owner.post(f"/api/admin/improvements/{pid}/approve", json={})).status_code == 409
    denied = (await owner.get("/api/admin/improvements?status=denied")).json()["proposals"]
    assert [d["id"] for d in denied] == [pid]
    audit = (await owner.get("/api/admin/audit?action=improve.")).json()["entries"]
    assert audit[0]["action"] == "improve.denied"


async def test_review_now_queues_one_review_at_a_time(owner, queue, sessionmaker):
    first = (await owner.post("/api/admin/improvements/review")).json()
    again = (await owner.post("/api/admin/improvements/review")).json()
    assert first["id"] == again["id"] and first["status"] == "running"
    assert queue.reviews == [uuid.UUID(first["id"])]
    async with sessionmaker() as db:
        assert await db.get(ImprovementReview, uuid.UUID(first["id"])) is not None
    listing = (await owner.get("/api/admin/improvements")).json()
    assert listing["reviews"][0]["id"] == first["id"]
    assert listing["schedule"]["every_days"] == 7


async def test_only_the_owner_sees_improvements(client_for, owner, invite, propose):
    pid = await propose(ProposalKind.code, {"spec": "x" * 50, "files": []})
    await invite(owner, "admin@example.com", role="admin")
    admin = await client_for("admin@example.com")
    assert (await admin.get("/api/admin/improvements")).status_code == 403
    assert (await admin.post(f"/api/admin/improvements/{pid}/approve", json={})).status_code == 403


def test_drafts_are_checked_before_they_are_kept():
    roles = ["researcher", "writer"]
    skill = improve.check(
        {
            "kind": "skill",
            "title": "Trip planning skill",
            "impact": "huge",
            "skill": {"name": "Trip Planning", "description": "Plan trips.", "body": "Steps."},
        },
        roles,
    )
    assert skill["payload"]["name"] == "trip-planning" and skill["impact"] == "medium"
    for bad in (
        {"kind": "magic", "title": "Do magic"},
        {"kind": "skill", "title": "No name", "skill": {"name": "", "description": "x"}},
        {
            "kind": "instruction",
            "title": "Bad role",
            "instruction": {"layer": "role", "role": "pilot", "text": "Fly."},
        },
        {
            "kind": "setting",
            "title": "Open sign-up",
            "setting": {"name": "signup_policy", "value": "open"},
        },
        {
            "kind": "setting",
            "title": "Bad value",
            "setting": {"name": "chat_model", "value": "gpt-4"},
        },
        {"kind": "code", "title": "Too vague", "change": {"spec": "Make it better."}},
    ):
        with pytest.raises(improve.ProposalError):
            improve.check(bad, roles)
    assert improve.fingerprint("skill", "Add a Trip-planning skill!") == improve.fingerprint(
        "skill", "add a trip planning skill"
    )
