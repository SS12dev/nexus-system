"""Export Money as an Excel workbook (nexus_common/money_export.py), for the person's records
or to hand over to family.

Owner only. The file holds full account numbers and nominees, so it can be locked with a
password (sent in the body, never the URL, and never stored or logged). Every export goes in
the audit log with whether it was locked; logs carry ids and counts only.
"""

import asyncio
import logging

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from nexus_api.deps import DbDep, OwnerUser, RedisDep, SettingsDep
from nexus_api.ratelimit import hit, too_many
from nexus_common import money_export as export
from nexus_common.models import AuditLog, utcnow
from nexus_common.organiser import local_today

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/money", tags=["money"])

EXPORTS_PER_HOUR = 10


class ExportIn(BaseModel):
    # Empty or left out: no password.
    password: str | None = Field(default=None, max_length=128)


@router.post("/export")
async def export_finances(
    body: ExportIn, user: OwnerUser, db: DbDep, redis: RedisDep, cfg: SettingsDep
) -> Response:
    """Everything in Money as one .xlsx: summary, accounts, savings, funds, loans,
    subscriptions and insurance."""
    if wait := await hit(redis, "money-export", str(user.id), EXPORTS_PER_HOUR, 3600):
        raise too_many(wait, "exports")
    today = local_today(user)
    tables = await export.gather(db, cfg, user, today)
    data = await asyncio.to_thread(
        export.render, tables, title=f"Finances, {today:%-d %B %Y}", when=utcnow()
    )
    locked = bool(body.password)
    if locked:
        data = await asyncio.to_thread(export.protect, data, body.password)
    rows = {t.name: len(t.rows) for t in tables if t.name != "Summary"}
    db.add(
        AuditLog(
            actor_id=user.id,
            action="money.exported",
            target=str(user.id),
            data={"password": locked, "rows": rows},
        )
    )
    await db.commit()
    log.info(
        "money.exported",
        extra={"user_id": str(user.id), "password": locked, "bytes": len(data), "rows": rows},
    )
    return Response(
        content=data,
        media_type=export.MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename(today)}"',
            "Cache-Control": "no-store",
        },
    )
