import hashlib
import secrets
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_common.models import AuditLog, Session, User, UserStatus, utcnow
from jarvis_common.settings import Settings


def client_ip(headers: dict[str, str], fallback: str | None) -> str:
    """The caller's address. Behind the Cloudflare tunnel, Cloudflare sets CF-Connecting-IP and
    overwrites any value the client sent; X-Forwarded-For can be forged, so it is not used."""
    return headers.get("cf-connecting-ip") or fallback or "unknown"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def start_session(
    session: AsyncSession, settings: Settings, user: User, request: Request
) -> str:
    token = secrets.token_urlsafe(32)
    ip = client_ip(
        {k.lower(): v for k, v in request.headers.items()},
        request.client.host if request.client else None,
    )
    row = Session(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=utcnow() + timedelta(days=settings.session_ttl_days),
        user_agent=(request.headers.get("user-agent") or "")[:500],
        ip=ip[:64],
    )
    session.add(row)
    await session.flush()
    session.add(
        AuditLog(actor_id=user.id, action="user.signed_in", target=str(row.id), data={"ip": ip})
    )
    await session.flush()
    return token


def set_session_cookie(response: Response, settings: Settings, request: Request, token: str):
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure or request.url.scheme == "https",
        path="/",
    )


async def user_for_token(session: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    row = await session.scalar(
        select(Session).where(
            Session.token_hash == hash_token(token),
            Session.revoked_at.is_(None),
            Session.expires_at > utcnow(),
        )
    )
    if row is None or row.user.status != UserStatus.active:
        return None
    return row.user


async def revoke_token(session: AsyncSession, token: str) -> None:
    await session.execute(
        update(Session).where(Session.token_hash == hash_token(token)).values(revoked_at=utcnow())
    )


async def revoke_all(session: AsyncSession, user: User, keep_hash: str | None = None) -> None:
    """Sign the user out everywhere, except the session with `keep_hash` if given."""
    q = update(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None))
    if keep_hash:
        q = q.where(Session.token_hash != keep_hash)
    await session.execute(q.values(revoked_at=utcnow()))
