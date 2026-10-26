"""Who may sign in, and what each role may do to other users."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import (
    ROLE_RANK,
    AuditLog,
    Role,
    RoleName,
    User,
    UserRole,
    UserStatus,
    utcnow,
)
from nexus_common.settings import Settings


class SignInRefused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def role_by_name(session: AsyncSession, name: RoleName) -> Role:
    return (await session.scalars(select(Role).where(Role.name == name))).one()


async def set_role(session: AsyncSession, user: User, name: RoleName) -> None:
    """Give the user exactly one role. The schema allows several; phase 1 uses one per user."""
    role = await role_by_name(session, name)
    await session.execute(UserRole.__table__.delete().where(UserRole.user_id == user.id))
    session.add(UserRole(user_id=user.id, role_id=role.id))
    await session.flush()
    await session.refresh(user, ["roles"])


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    role: RoleName,
    status: UserStatus,
    invited_by: User | None = None,
    name: str | None = None,
) -> User:
    user = User(
        email=normalize_email(email),
        name=name,
        status=status,
        invited_by=invited_by.id if invited_by else None,
    )
    session.add(user)
    await session.flush()
    await set_role(session, user, role)
    return user


async def resolve_sign_in(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    sub: str | None = None,
    name: str | None = None,
    avatar_url: str | None = None,
) -> User:
    """Find or create the user signing in, or raise SignInRefused.

    The very first user becomes owner. After that, only invited users may join unless the
    signup policy is open, in which case newcomers join as members.
    """
    email = normalize_email(email)
    user = None
    if sub:
        user = await session.scalar(select(User).where(User.oidc_sub == sub))
    if user is None:
        user = await session.scalar(select(User).where(User.email == email))

    if user is None:
        # Serialise first sign-ins so two people racing for an empty install can't both be owner.
        await session.execute(select(Role).where(Role.name == RoleName.owner).with_for_update())
        count = await session.scalar(select(func.count()).select_from(User))
        if count == 0:
            role = RoleName.owner
        elif settings.signup_policy == "open":
            role = RoleName.member
        else:
            raise SignInRefused("not_invited")
        user = await create_user(session, email=email, role=role, status=UserStatus.active)
        session.add(
            AuditLog(
                actor_id=user.id,
                action="user.joined",
                target=str(user.id),
                data={"email": email, "role": role},
            )
        )
    elif user.account_deleted_at is not None:
        raise SignInRefused("account_deleted")
    elif user.status == UserStatus.disabled:
        raise SignInRefused("disabled")
    elif sub and user.oidc_sub and user.oidc_sub != sub:
        raise SignInRefused("identity_mismatch")

    if user.status == UserStatus.invited:
        user.status = UserStatus.active
        session.add(AuditLog(actor_id=user.id, action="user.accepted_invite", target=str(user.id)))
    if sub and not user.oidc_sub:
        user.oidc_sub = sub
    if name:
        user.name = name
    if avatar_url:
        user.avatar_url = avatar_url
    user.last_login_at = utcnow()
    await session.flush()
    return user


def can_assign(actor: User, role: RoleName) -> bool:
    """Owners may grant any role; anyone else only roles ranked below their own."""
    if actor.role == RoleName.owner:
        return True
    return ROLE_RANK[role] < ROLE_RANK[actor.role]


def can_manage(actor: User, target: User) -> bool:
    if actor.role == RoleName.owner:
        return True
    return ROLE_RANK[target.role] < ROLE_RANK[actor.role]


async def owner_count(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .join(User, User.id == UserRole.user_id)
        .where(Role.name == RoleName.owner, User.status == UserStatus.active)
    )
