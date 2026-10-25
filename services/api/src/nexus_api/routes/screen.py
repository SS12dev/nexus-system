"""The main screen's saved layout (nexus_common/screen.py): read it, switch the layout by hand,
or close what's open. The person's changes here are kept as overrides, which the assistant's
screen tool leaves alone for a while."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from nexus_api.deps import CurrentUser, DbDep, RedisDep
from nexus_common import screen as sc
from nexus_common.models import utcnow

router = APIRouter(prefix="/api/screen", tags=["screen"])


class ModeIn(BaseModel):
    mode: Literal["orb", "list", "dashboard"]


class CloseIn(BaseModel):
    kind: Literal["file", "widget"]
    id: str


@router.get("")
async def get_screen(user: CurrentUser, db: DbDep) -> dict:
    state = await sc.load(db, user.id)
    await db.commit()
    return sc.payload(state)


@router.put("/mode")
async def set_mode(body: ModeIn, user: CurrentUser, db: DbDep, redis: RedisDep) -> dict:
    state = await sc.load(db, user.id, lock=True)
    state.mode = body.mode
    state.updated_at = utcnow()
    sc.mark_override(state, "mode")
    sc.audit(db, user.id, "mode", body.mode, by="user")
    await db.commit()
    await sc.broadcast(redis, user.id, "mode", state, by="user")
    return sc.payload(state)


@router.post("/close")
async def close_focus(body: CloseIn, user: CurrentUser, db: DbDep, redis: RedisDep) -> dict:
    """The person closed something: it stays closed until they open it again or ask for it."""
    state = await sc.load(db, user.id, lock=True)
    sc.mark_override(state, sc.override_key("focus", body.kind, body.id))
    was_open = (state.focus or {}).get("id") == body.id
    if was_open:
        state.focus = None
    state.updated_at = utcnow()
    await db.commit()
    if was_open:
        await sc.broadcast(redis, user.id, "close", state, by="user")
    return sc.payload(state)
