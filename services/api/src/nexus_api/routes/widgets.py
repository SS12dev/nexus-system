"""Widgets on the main screen (nexus_common/widgets.py): list them, add one from the dock's
menu, press a widget's buttons, edit a note or move a widget along the dock."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from nexus_api.deps import CurrentUser, DbDep, RedisDep
from nexus_common.models import Widget, utcnow
from nexus_common.widgets import NOTE_CHARS, TITLE_CHARS, WidgetError, act, add, announce, payload

router = APIRouter(prefix="/api/widgets", tags=["widgets"])


class WidgetIn(BaseModel):
    kind: Literal["timer", "alarm", "stopwatch", "reminder", "clock", "note"]
    title: str = Field("", max_length=TITLE_CHARS)
    minutes: float | None = None
    time: str | None = None
    days: str | list[str] | None = None
    when: str | None = None
    text: str | None = Field(None, max_length=NOTE_CHARS)
    zones: list[str] | None = None


class ActionIn(BaseModel):
    action: Literal["pause", "resume", "reset", "add_time", "snooze", "dismiss", "remove"]
    minutes: float | None = None


class WidgetPatch(BaseModel):
    title: str | None = Field(None, max_length=TITLE_CHARS)
    text: str | None = Field(None, max_length=NOTE_CHARS)
    slot: int | None = None


async def _own(db, user, widget_id: uuid.UUID) -> Widget:
    w = await db.scalar(select(Widget).where(Widget.id == widget_id, Widget.user_id == user.id))
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such widget")
    return w


@router.get("")
async def list_widgets(user: CurrentUser, db: DbDep) -> list[dict]:
    rows = await db.scalars(
        select(Widget).where(Widget.user_id == user.id).order_by(Widget.slot, Widget.created_at)
    )
    return [payload(w) for w in rows.all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_widget(body: WidgetIn, user: CurrentUser, db: DbDep, redis: RedisDep) -> dict:
    try:
        w = await add(db, user, **body.model_dump())
    except WidgetError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await db.commit()
    await announce(redis, user.id, w)
    return payload(w)


@router.post("/{widget_id}/action")
async def widget_action(
    widget_id: uuid.UUID, body: ActionIn, user: CurrentUser, db: DbDep, redis: RedisDep
) -> dict | None:
    w = await _own(db, user, widget_id)
    if body.action == "remove":
        await db.execute(delete(Widget).where(Widget.id == w.id))
        await db.commit()
        await announce(redis, user.id, w, deleted=True)
        return None
    try:
        act(w, body.action, minutes=body.minutes)
    except WidgetError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.commit()
    await announce(redis, user.id, w)
    return payload(w)


@router.patch("/{widget_id}")
async def edit_widget(
    widget_id: uuid.UUID, body: WidgetPatch, user: CurrentUser, db: DbDep, redis: RedisDep
) -> dict:
    w = await _own(db, user, widget_id)
    if body.title is not None:
        w.title = body.title.strip()[:TITLE_CHARS] or w.title
    if body.text is not None:
        if w.kind != "note":
            raise HTTPException(status.HTTP_409_CONFLICT, "Only a note has text to edit")
        w.state = {**(w.state or {}), "text": body.text}
    if body.slot is not None:
        w.slot = body.slot
    w.updated_at = utcnow()
    await db.commit()
    await announce(redis, user.id, w)
    return payload(w)
