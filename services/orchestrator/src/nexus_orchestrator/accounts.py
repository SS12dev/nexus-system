"""Expire deleted accounts and remove their managed resources before dropping database rows."""

import logging
import uuid

from sqlalchemy import select, update

from nexus_common.litellm import active_key, get_budget
from nexus_common.models import (
    AccountRecoveryRequest,
    AccountRecoveryStatus,
    File,
    Project,
    Role,
    User,
    UserRole,
    UserStatus,
    utcnow,
)
from nexus_orchestrator.project_cleanup import delete_project
from nexus_orchestrator.turn import TurnDeps

log = logging.getLogger(__name__)


async def expire_deleted_accounts(deps: TurnDeps) -> int:
    """Mark expired accounts for deletion, then enqueue all eligible accounts for retry."""
    now = utcnow()
    async with deps.sessionmaker() as db:
        await db.execute(
            update(User)
            .where(
                User.account_deleted_at.is_not(None),
                User.account_delete_expires_at <= now,
                User.account_purge_started_at.is_(None),
            )
            .values(account_purge_started_at=now)
        )
        await db.commit()
        ids = list(
            (
                await db.scalars(
                    select(User.id).where(
                        User.account_deleted_at.is_not(None),
                        User.account_purge_started_at.is_not(None),
                        (
                            (User.account_delete_expires_at <= now)
                            | User.account_reset_requested_at.is_not(None)
                        ),
                    )
                )
            ).all()
        )

    queued = 0
    for user_id in ids:
        try:
            await deps.enqueue(
                "purge_deleted_account",
                str(user_id),
                _job_id=f"account-purge:{user_id}",
            )
            queued += 1
        except Exception:
            log.exception("account.purge_enqueue_failed", extra={"user_id": str(user_id)})
    return queued


async def purge_deleted_account(deps: TurnDeps, user_id: uuid.UUID) -> bool:
    """Delete a soft-deleted account's external resources and data; optionally create a clean
    account with the same verified identity and role after every old resource is gone."""
    async with deps.sessionmaker() as db:
        user = await db.get(User, user_id, with_for_update=True)
        if user is None:
            return False
        if (
            user.account_deleted_at is None
            or user.account_purge_started_at is None
            or (
                user.account_reset_requested_at is None
                and (
                    user.account_delete_expires_at is None
                    or user.account_delete_expires_at > utcnow()
                )
            )
        ):
            return False
        email = user.email
        name = user.name
        avatar_url = user.avatar_url
        oidc_sub = user.oidc_sub
        role = user.role
        reset = user.account_reset_requested_at is not None
        project_ids = list(
            (await db.scalars(select(Project.id).where(Project.user_id == user_id))).all()
        )

    for project_id in project_ids:
        await delete_project(deps, project_id)

    async with deps.sessionmaker() as db:
        files = list((await db.scalars(select(File).where(File.user_id == user_id))).all())
    if files and deps.storage is None:
        raise RuntimeError("File storage is unavailable; deleted account data was not purged.")
    if deps.storage is not None:
        for file in files:
            await deps.storage.delete(file.storage_key)

    async with deps.sessionmaker() as db:
        user = await db.get(User, user_id, with_for_update=True)
        if user is None:
            return False
        key = await active_key(db, user_id)
    if key is not None:
        if deps.gateway is None:
            raise RuntimeError("Model gateway is unavailable; account access key was not revoked.")
        await deps.gateway.delete_key(key.secret)

    async with deps.sessionmaker() as db:
        user = await db.get(User, user_id, with_for_update=True)
        if user is None:
            return False
        if user.account_deleted_at is None or user.account_purge_started_at is None:
            return False
        await db.execute(
            update(AccountRecoveryRequest)
            .where(
                AccountRecoveryRequest.user_id == user_id,
                AccountRecoveryRequest.status == AccountRecoveryStatus.pending,
            )
            .values(status=AccountRecoveryStatus.cancelled, decided_at=utcnow())
        )
        await db.execute(update(User).where(User.invited_by == user_id).values(invited_by=None))
        await db.delete(user)
        await db.flush()
        if reset:
            fresh = User(
                email=email,
                name=name,
                avatar_url=avatar_url,
                oidc_sub=oidc_sub,
                status=UserStatus.active,
            )
            db.add(fresh)
            await db.flush()
            role_row = await db.scalar(select(Role).where(Role.name == role))
            if role_row is None:
                raise RuntimeError(f"Role {role} is missing; fresh account was not created.")
            db.add(UserRole(user_id=fresh.id, role_id=role_row.id))
            await db.flush()
            await db.refresh(fresh, ["roles"])
            await get_budget(db, deps.settings, fresh)
            log.info(
                "account.reset_completed",
                extra={"old_user_id": str(user_id), "new_user_id": str(fresh.id)},
            )
        await db.commit()

    log.info(
        "account.purged",
        extra={"user_id": str(user_id), "reset": reset, "projects": len(project_ids)},
    )
    return True
