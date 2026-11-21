"""Advice (nexus_common/advice.py): what in the person's own data deserves a decision, and what
they did with each item. Money items only for the owner with Money set up; the Organiser ones
for everyone. Logs carry counts and rule names only."""

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from nexus_api.deps import CurrentUser, DbDep
from nexus_common import advice_store
from nexus_common.organiser import local_today

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/advice", tags=["advice"])


@router.get("")
async def list_advice(user: CurrentUser, db: DbDep) -> dict:
    out = await advice_store.view(db, user, local_today(user))
    # Finishing a card move found while reading is kept.
    await db.commit()
    log.info("advice.listed", extra={"user_id": str(user.id), **out["counts"]})
    return out


class StateIn(BaseModel):
    key: str = Field(min_length=1, max_length=160)
    action: Literal["done", "dismissed", "snoozed", "restore"]
    days: int = Field(default=7, ge=1, le=90)


@router.post("/state")
async def set_state(body: StateIn, user: CurrentUser, db: DbDep) -> dict:
    """Mark an item done, dismiss it, snooze it for some days, or bring it back."""
    today = local_today(user)
    await advice_store.set_state(db, user.id, body.key, body.action, today, body.days)
    await db.commit()
    log.info(
        "advice.state_set",
        extra={"user_id": str(user.id), "rule": body.key.split(":", 1)[0], "action": body.action},
    )
    return await advice_store.view(db, user, today)
