"""Access requests, as the API and the orchestrator both need them: who hears about a request
and how many are waiting."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import (
    AccessRequest,
    AccessRequestStatus,
    Role,
    RoleName,
    User,
    UserRole,
    UserStatus,
)

# Roles that can approve a request (and so hear about one).
DECIDERS = (RoleName.owner, RoleName.admin)


async def deciders(db: AsyncSession) -> list[User]:
    """Active owners and admins."""
    rows = await db.scalars(
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.name.in_(DECIDERS), User.status == UserStatus.active)
        .distinct()
    )
    return list(rows.all())


async def pending_count(db: AsyncSession) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(AccessRequest)
        .where(AccessRequest.status == AccessRequestStatus.pending)
    )
