"""Runtime switches admins flip on the Admin page, stored in app_settings."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import AppSetting, RoleName, User

MAINTENANCE = "maintenance"
DEFAULT_MAINTENANCE_MESSAGE = "Nexus is down for maintenance. Back soon."


async def get_setting(db: AsyncSession, key: str) -> dict:
    row = await db.get(AppSetting, key)
    return dict(row.value) if row else {}


async def put_setting(db: AsyncSession, key: str, value: dict, actor: uuid.UUID) -> None:
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by=actor))
    else:
        row.value, row.updated_by = value, actor


async def maintenance_notice(db: AsyncSession) -> str | None:
    """The message to show while maintenance mode is on, else None."""
    value = await get_setting(db, MAINTENANCE)
    if not value.get("enabled"):
        return None
    return value.get("message") or DEFAULT_MAINTENANCE_MESSAGE


async def refuse_during_maintenance(db: AsyncSession, user: User) -> None:
    """New chat turns and runs wait while maintenance is on; admins can still test."""
    if user.has_role(RoleName.admin):
        return
    notice = await maintenance_notice(db)
    if notice:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, notice)
