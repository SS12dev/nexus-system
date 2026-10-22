import uuid

from sqlalchemy import select

from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Approval,
    ApprovalStatus,
    AuditLog,
    RunStatus,
    SystemLifetime,
    SystemStatus,
)

SPEC = {
    "name": "Email team",
    "goal": "Write and send the summary",
    "topology": "single",
    "agents": [
        {"id": "mailer", "name": "Mailer", "instructions": "Send it.", "tools": ["send_email"]}
    ],
}


async def me(client) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/me")).json()["id"])


async def make_system(sessionmaker, user_id, *, status=RunStatus.awaiting_approval, spec=SPEC):
    async with sessionmaker() as db:
        s = AgentSystem(
            id=uuid.uuid4(),
            user_id=user_id,
            name=spec["name"],
            goal=spec["goal"],
            spec=spec,
            topology=spec["topology"],
            lifetime=SystemLifetime.ephemeral,
            status=SystemStatus.idle,
        )
        db.add(s)
        await db.flush()
        run = AgentRun(
            id=uuid.uuid4(),
            system_id=s.id,
            user_id=user_id,
            input="go",
            status=status,
            budget_usd=0.5,
            cost_usd=0,
            input_tokens=0,
            output_tokens=0,
            steps=0,
            agents={},
            trace_id="t",
        )
        db.add(run)
        await db.flush()
        a = Approval(
            run_id=run.id,
            system_id=s.id,
            user_id=user_id,
            agent="mailer",
            tool="send_email",
            tool_call_id="c1",
            args={"to": ["a@x.com"]},
            reason="Send email",
            status=ApprovalStatus.pending,
        )
        db.add(a)
        await db.commit()
        return s.id, run.id, a.id


async def test_approval_decision_is_audited_and_continues_run(
    owner, sessionmaker, queue, client_for, invite
):
    uid = await me(owner)
    system_id, run_id, approval_id = await make_system(sessionmaker, uid)
    pending = (await owner.get("/api/approvals")).json()
    assert [a["id"] for a in pending] == [str(approval_id)]

    # Someone else can't see or decide it.
    await invite(owner, "other@example.com")
    other = await client_for("other@example.com")
    assert (
        await other.post(f"/api/approvals/{approval_id}", json={"decision": "approve"})
    ).status_code == 404

    resp = await owner.post(
        f"/api/approvals/{approval_id}",
        json={"decision": "approve", "args": {"to": ["b@x.com"]}, "note": "use b"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved" and resp.json()["decided_args"] == {
        "to": ["b@x.com"]
    }
    assert queue.runs == [("continue_run", run_id)]
    again = await owner.post(f"/api/approvals/{approval_id}", json={"decision": "reject"})
    assert again.status_code == 409
    async with sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        assert run.status == RunStatus.running
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "approval.approved"))
        assert audit.actor_id == uid and audit.data["edited_args"] == {"to": ["b@x.com"]}

    detail = (await owner.get(f"/api/runs/{run_id}")).json()
    assert detail["approvals"][0]["status"] == "approved"
    assert detail["system"]["nodes"][0]["id"] == "mailer"


async def test_controls_templates_and_listing(owner, sessionmaker, queue):
    uid = await me(owner)
    system_id, run_id, _ = await make_system(sessionmaker, uid, status=RunStatus.running)
    paused = await owner.post(f"/api/runs/{run_id}/pause")
    assert paused.json()["status"] == "paused" and queue.runs == []
    assert (await owner.post(f"/api/runs/{run_id}/pause")).status_code == 409
    await owner.post(f"/api/runs/{run_id}/resume")
    assert queue.runs == [("continue_run", run_id)]
    assert (await owner.delete(f"/api/systems/{system_id}")).status_code == 409
    await owner.post(f"/api/runs/{run_id}/cancel")
    async with sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        run.status = RunStatus.completed  # what the orchestrator does when it wraps up
        system = await db.get(AgentSystem, system_id)
        system.status = SystemStatus.archived
        await db.commit()

    assert (await owner.get("/api/systems")).json() == []
    kept = (await owner.post(f"/api/systems/{system_id}/template", json={"name": "Mailer"})).json()
    assert kept["lifetime"] == "template" and kept["status"] == "idle" and kept["name"] == "Mailer"
    listed = (await owner.get("/api/systems")).json()
    assert [s["id"] for s in listed] == [str(system_id)]
    assert listed[0]["run"]["status"] == "completed"

    # Running a template again checks the budget and queues a run.
    resp = await owner.post(f"/api/systems/{system_id}/runs", json={"request": "again"})
    assert resp.status_code == 202, resp.text
    assert queue.runs[-1][0] == "run_system"
    assert (
        await owner.post(f"/api/systems/{system_id}/runs", json={"request": "x"})
    ).status_code == 409
    detail = (await owner.get(f"/api/systems/{system_id}")).json()
    assert len(detail["runs"]) == 2 and detail["spec"]["name"] == "Mailer"


async def test_expensive_run_is_refused(owner, sessionmaker):
    uid = await me(owner)
    big = {
        **SPEC,
        "topology": "critic_loop",
        "max_rounds": 10,
        "agents": [
            {"id": "a", "name": "A", "instructions": "x", "tier": "max", "max_steps": 40},
            {"id": "b", "name": "B", "instructions": "x", "tier": "max", "max_steps": 40},
        ],
    }
    system_id, run_id, _ = await make_system(
        sessionmaker, uid, status=RunStatus.completed, spec=big
    )
    resp = await owner.post(f"/api/systems/{system_id}/runs", json={"request": "go"})
    assert resp.status_code == 402 and "estimated" in resp.json()["detail"]


async def test_tools_and_grants(owner, client_for, invite):
    tools = {t["name"]: t for t in (await owner.get("/api/tools")).json()}
    assert tools["run_code"]["allowed"] and tools["send_email"]["asks_first"]
    member_info = await invite(owner, "dev@example.com")
    member = await client_for("dev@example.com")
    tools = {t["name"]: t for t in (await member.get("/api/tools")).json()}
    assert not tools["run_code"]["allowed"] and tools["web_search"]["allowed"]
    resp = await owner.put(
        f"/api/admin/users/{member_info['id']}/grants", json={"capabilities": ["exec"]}
    )
    assert resp.json() == {"granted": ["exec"], "effective": ["exec"]}
    tools = {t["name"]: t for t in (await member.get("/api/tools")).json()}
    assert tools["run_code"]["allowed"] and not tools["send_email"]["allowed"]
    assert (
        await member.put(
            f"/api/admin/users/{member_info['id']}/grants", json={"capabilities": ["outbound"]}
        )
    ).status_code == 403
