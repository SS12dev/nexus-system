import uuid
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from nexus_api.users import create_user
from nexus_common.models import (
    AccountRecoveryRequest,
    AccountRecoveryStatus,
    AuditLog,
    User,
    UserStatus,
    utcnow,
)


async def deleted_account(owner, client_for, invite, sessionmaker):
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    deleted = await friend.post("/api/me/delete", json={"confirm": "friend@example.com"})
    assert deleted.status_code == 204, deleted.text
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "friend@example.com"))
        assert user is not None
        assert user.status == UserStatus.disabled
        assert user.account_deleted_at is not None
        assert user.account_delete_expires_at > utcnow()
    # A later verified sign-in is refused but leaves the signed identity cookie for recovery.
    return await client_for("friend@example.com", expect=403)


async def test_deleted_user_can_request_admin_approved_recovery(
    owner, client_for, invite, sessionmaker, queue, app, settings
):
    app.state.settings = settings.model_copy(
        update={"smtp_host": "smtp.example.com", "smtp_from": "nexus@example.com"}
    )
    former_user = await deleted_account(owner, client_for, invite, sessionmaker)
    state = (await former_user.get("/auth/access-request")).json()
    assert state["status"] == "account_deleted"

    state = (await former_user.post("/auth/account-recovery", json={"action": "recover"})).json()
    assert state["status"] == "recovery_pending"
    assert state["recovery_requested_at"]
    [request] = (await owner.get("/api/admin/account-recovery")).json()["requests"]
    assert request["email"] == "friend@example.com"
    request_id = uuid.UUID(request["id"])
    assert queue.account_recovery_notices == [("requested", request_id)]

    approved = await owner.post(f"/api/admin/account-recovery/{request['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert queue.account_recovery_notices[-1] == ("approved", request_id)
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "friend@example.com"))
        assert user.status == UserStatus.active
        assert user.account_deleted_at is None
        assert await db.scalar(
            select(AuditLog.id).where(AuditLog.action == "account.recovery_approved")
        )
    assert (await former_user.get("/api/me")).status_code == 401


async def test_deleted_user_can_choose_clean_account(
    owner, client_for, invite, sessionmaker, queue
):
    former_user = await deleted_account(owner, client_for, invite, sessionmaker)
    state = (await former_user.post("/auth/account-recovery", json={"action": "new"})).json()
    assert state["status"] == "account_resetting"
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "friend@example.com"))
        assert user.account_reset_requested_at is not None
        assert user.account_purge_started_at is not None
    assert queue.account_purges == [user.id]
    assert (await owner.get("/api/admin/account-recovery")).json()["pending"] == 0


async def test_recovery_requires_admin_and_rejects_expired_request(
    owner, client_for, invite, sessionmaker
):
    former_user = await deleted_account(owner, client_for, invite, sessionmaker)
    await former_user.post("/auth/account-recovery", json={"action": "recover"})
    [request] = (await owner.get("/api/admin/account-recovery")).json()["requests"]
    await invite(owner, "admin@example.com", "admin")
    admin = await client_for("admin@example.com")

    # The admin can only recover roles below their own; this account is a member.
    async with sessionmaker() as db:
        recovery = await db.get(AccountRecoveryRequest, request["id"])
        recovery.expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    denied = await admin.post(f"/api/admin/account-recovery/{request['id']}/approve")
    assert denied.status_code == 410
    denied = await admin.post(f"/api/admin/account-recovery/{request['id']}/deny")
    assert denied.status_code == 200
    assert (
        await admin.post(f"/api/admin/account-recovery/{request['id']}/approve")
    ).status_code == 409


async def test_clean_account_purge_recreates_only_the_identity(
    owner, client_for, invite, sessionmaker, settings, storage
):
    from nexus_orchestrator.accounts import purge_deleted_account

    former_user = await deleted_account(owner, client_for, invite, sessionmaker)
    await former_user.post("/auth/account-recovery", json={"action": "recover"})
    state = (await former_user.post("/auth/account-recovery", json={"action": "new"})).json()
    assert state["status"] == "account_resetting"
    async with sessionmaker() as db:
        deleted = await db.scalar(select(User).where(User.email == "friend@example.com"))
        old_id = deleted.id
        await create_user(
            db,
            email="other@example.com",
            role=deleted.role,
            status=UserStatus.invited,
            invited_by=deleted,
        )
        await db.commit()

    deps = SimpleNamespace(
        sessionmaker=sessionmaker,
        storage=storage,
        gateway=None,
        settings=settings,
        enqueue=None,
        deployer=None,
        workbench=None,
        github=None,
    )
    assert await purge_deleted_account(deps, old_id) is True
    async with sessionmaker() as db:
        fresh = await db.scalar(select(User).where(User.email == "friend@example.com"))
        assert fresh is not None and fresh.id != old_id
        assert fresh.status == UserStatus.active
        assert fresh.account_deleted_at is None
        assert fresh.assistant_name is None
        requests = list((await db.scalars(select(AccountRecoveryRequest))).all())
        assert requests and requests[0].user_id is None
        assert requests[0].status == AccountRecoveryStatus.cancelled
        other = await db.scalar(select(User).where(User.email == "other@example.com"))
        assert other.invited_by is None


async def test_expiry_sweep_marks_and_requeues_accounts(owner, client_for, invite, sessionmaker):
    from nexus_orchestrator.accounts import expire_deleted_accounts

    await deleted_account(owner, client_for, invite, sessionmaker)
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "friend@example.com"))
        user.account_delete_expires_at = utcnow() - timedelta(seconds=1)
        user_id = user.id
        await db.commit()
    jobs = []

    async def enqueue(*args, **kwargs):
        jobs.append((args, kwargs))

    deps = SimpleNamespace(sessionmaker=sessionmaker, enqueue=enqueue)
    assert await expire_deleted_accounts(deps) == 1
    assert jobs == [
        (
            ("purge_deleted_account", str(user_id)),
            {"_job_id": f"account-purge:{user_id}"},
        )
    ]
    async with sessionmaker() as db:
        user = await db.get(User, user_id)
        assert user.account_purge_started_at is not None


async def test_manual_invite_queues_email_and_can_retry(owner, app, settings, queue):
    app.state.settings = settings.model_copy(
        update={"smtp_host": "smtp.example.com", "smtp_from": "nexus@example.com"}
    )
    try:
        invited = await owner.post(
            "/api/admin/users", json={"email": "new@example.com", "role": "member"}
        )
        assert invited.status_code == 201, invited.text
        body = invited.json()
        assert body["invite_email_enabled"] is True
        assert body["invite_email_queued"] is True
        assert queue.invite_notices == [uuid.UUID(body["id"])]

        retried = await owner.post(f"/api/admin/users/{body['id']}/invite-email")
        assert retried.status_code == 204
        assert queue.invite_notices == [
            uuid.UUID(body["id"]),
            uuid.UUID(body["id"]),
        ]
    finally:
        app.state.settings = settings


async def test_admin_can_reset_a_managed_profile_to_fresh_onboarding(
    owner, invite, client_for, sessionmaker, queue
):
    invited = await invite(owner, "friend@example.com")
    user_id = uuid.UUID(invited["id"])
    await client_for("friend@example.com")

    mismatch = await owner.post(
        f"/api/admin/users/{user_id}/reset", json={"confirm": "wrong@example.com"}
    )
    assert mismatch.status_code == 422

    response = await owner.post(
        f"/api/admin/users/{user_id}/reset", json={"confirm": "FRIEND@example.com"}
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"status": "resetting"}
    assert queue.account_purges == [user_id]
    async with sessionmaker() as db:
        user = await db.get(User, user_id)
        assert user.status == UserStatus.disabled
        assert user.account_deleted_at is not None
        assert user.account_reset_requested_at is not None
        assert user.account_purge_started_at is not None
        assert await db.scalar(
            select(AuditLog.id).where(
                AuditLog.action == "user.profile_reset_requested",
                AuditLog.target == str(user_id),
            )
        )


async def test_admin_cannot_reset_a_higher_role_profile(owner, invite, client_for, sessionmaker):
    await invite(owner, "admin@example.com", role="admin")
    admin_client = await client_for("admin@example.com")
    owner_id = uuid.UUID((await owner.get("/api/me")).json()["id"])

    response = await admin_client.post(
        f"/api/admin/users/{owner_id}/reset", json={"confirm": "owner@example.com"}
    )
    assert response.status_code == 403
    async with sessionmaker() as db:
        user = await db.get(User, owner_id)
        assert user.account_deleted_at is None
        assert user.status == UserStatus.active


async def test_last_active_owner_cannot_reset_their_only_profile(owner):
    owner_id = (await owner.get("/api/me")).json()["id"]
    response = await owner.post(
        f"/api/admin/users/{owner_id}/reset", json={"confirm": "owner@example.com"}
    )
    assert response.status_code == 409
