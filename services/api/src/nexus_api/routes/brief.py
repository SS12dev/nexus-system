"""The morning brief (nexus_common/brief.py): the Brief page, its settings and a test send.

Any member has a brief; the Money lines are the owner's only. The brief is built fresh for each
request and never stored. Logs carry ids and counts only.
"""

import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from nexus_api.deps import DbDep, MemberUser, QueueDep, RedisDep, SettingsDep
from nexus_api.ratelimit import hit, too_many
from nexus_common import brief
from nexus_common.models import RoleName, TelegramLink, utcnow
from nexus_common.organiser import zone

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brief", tags=["brief"])

PLACE_SEARCHES_PER_HOUR = 60
SENDS_PER_HOUR = 5


async def outside_http() -> AsyncIterator[httpx.AsyncClient]:
    """For Google Calendar and Open-Meteo. A dependency, so tests can swap it."""
    async with httpx.AsyncClient(timeout=20) as http:
        yield http


HttpDep = Annotated[httpx.AsyncClient, Depends(outside_http)]


@router.get("")
async def today(
    user: MemberUser, db: DbDep, cfg: SettingsDep, redis: RedisDep, http: HttpDep
) -> dict:
    """Today's brief, built now, with the settings that shape it."""
    prefs = await brief.settings_for(db, user)
    b = await brief.build(db, cfg, user, prefs, http=http, redis=redis)
    linked = await db.get(TelegramLink, user.id) is not None
    await db.commit()
    return {
        "brief": b,
        "settings": brief.settings_out(prefs),
        "telegram_linked": linked and cfg.telegram_enabled,
        "money_available": user.role == RoleName.owner,
    }


@router.get("/places")
async def find_places(
    user: MemberUser, redis: RedisDep, http: HttpDep, q: Annotated[str, Query(max_length=80)] = ""
) -> dict:
    """Places matching what was typed, for the weather."""
    if wait := await hit(redis, "brief-places", str(user.id), PLACE_SEARCHES_PER_HOUR, 3600):
        raise too_many(wait, "place searches")
    try:
        found = await brief.places(q, http)
    except httpx.HTTPError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The place search didn't answer. Try again."
        ) from None
    return {"places": found}


class BriefIn(BaseModel):
    enabled: bool | None = None
    send_at: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: Literal["every", "weekdays"] | None = None
    push: bool | None = None
    telegram: bool | None = None
    telegram_money: bool | None = None
    place: str | None = Field(default=None, max_length=120)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    sections: list[Literal["weather", "calendar", "tasks", "money"]] | None = Field(
        default=None, min_length=1, max_length=4
    )

    @model_validator(mode="after")
    def whole_place(self):
        given = {k for k in ("place", "lat", "lon") if k in self.model_fields_set}
        empty = {self.place is None, self.lat is None, self.lon is None}
        if given and (given != {"place", "lat", "lon"} or len(empty) != 1):
            raise ValueError("a place needs its name, lat and lon, or all three empty")
        return self


@router.put("/settings")
async def save_settings(body: BriefIn, user: MemberUser, db: DbDep) -> dict:
    """Change when and where the brief goes. Only the fields sent change."""
    prefs = await brief.settings_for(db, user)
    if prefs not in db:
        prefs.created_at = utcnow()
        db.add(prefs)
    changes = body.model_dump(exclude_unset=True)
    if "place" in changes and changes["place"] is not None:
        changes["place"] = " ".join(changes["place"].split()) or None
    for key, value in changes.items():
        setattr(prefs, key, value)
    if body.sections is not None:
        prefs.sections = sorted(set(body.sections), key=brief.SECTIONS.index)
    # Switched on, or moved to a time already past today: the first brief is tomorrow's, not
    # one straight away.
    if "enabled" in changes or "send_at" in changes:
        local = datetime.now(zone(user))
        at = brief.parse_send_at(prefs.send_at)
        if (local.hour, local.minute) >= (at.hour, at.minute):
            prefs.last_sent_on = local.date()
    prefs.updated_at = utcnow()
    await db.commit()
    log.info(
        "brief.settings_saved",
        extra={"user_id": str(user.id), "fields": sorted(changes), "enabled": prefs.enabled},
    )
    return brief.settings_out(prefs)


@router.post("/send", status_code=status.HTTP_202_ACCEPTED)
async def send_now(user: MemberUser, redis: RedisDep, queue: QueueDep) -> dict:
    """Send today's brief now on the chosen channels, to see what it looks like."""
    if wait := await hit(redis, "brief-send", str(user.id), SENDS_PER_HOUR, 3600):
        raise too_many(wait, "test briefs")
    await queue.enqueue_brief(user.id)
    log.info("brief.send_queued", extra={"user_id": str(user.id)})
    return {"queued": True}
