"""Widgets on the main screen (nexus_common/widgets.py): list them, add one from the dock's
menu, press a widget's buttons, edit a note, move a widget along the dock or drag it anywhere
on the main screen. A custom widget
(nexus_common/custom_widgets.py) also has its page, the bridge its page talks through, the
person's approvals and its versions."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from nexus_api.deps import CurrentUser, DbDep, RedisDep, SettingsDep
from nexus_common import custom_widgets as cw
from nexus_common import screen as sc
from nexus_common.models import AuditLog, NotificationKind, Widget, utcnow
from nexus_common.notices import notify
from nexus_common.widgets import (
    NOTE_CHARS,
    RINGS,
    TITLE_CHARS,
    TUNES,
    WidgetError,
    act,
    add,
    announce,
    payload,
    retime,
)

router = APIRouter(prefix="/api/widgets", tags=["widgets"])

Tune = Literal[TUNES]


class WidgetPos(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class WidgetIn(BaseModel):
    kind: Literal["timer", "alarm", "stopwatch", "reminder", "clock", "note"]
    title: str = Field("", max_length=TITLE_CHARS)
    minutes: float | None = None
    time: str | None = None
    days: str | list[str] | None = None
    when: str | None = None
    text: str | None = Field(None, max_length=NOTE_CHARS)
    zones: list[str] | None = None
    tune: Tune | None = None
    # Dropped onto the main screen from the add menu, rather than added to the dock.
    pos: WidgetPos | None = None


class ActionIn(BaseModel):
    action: Literal["pause", "resume", "reset", "add_time", "snooze", "dismiss", "remove", "undo"]
    minutes: float | None = None
    # undo: show this version of a custom widget instead of the one before.
    version: int | None = None


class BridgeIn(BaseModel):
    call: Literal["state.get", "state.set", "fetch", "data", "notify"]
    args: dict[str, Any] = Field(default_factory=dict)


class ApproveIn(BaseModel):
    hosts: list[str] = Field(default_factory=list)
    data: list[str] = Field(default_factory=list)


class WidgetPatch(BaseModel):
    title: str | None = Field(None, max_length=TITLE_CHARS)
    text: str | None = Field(None, max_length=NOTE_CHARS)
    # Place on the dock, 1 = first.
    slot: int | None = Field(None, ge=1)
    # Dragged somewhere on the main screen, or back into the dock (dock: true).
    pos: WidgetPos | None = None
    dock: bool = False
    # What a timer, alarm or reminder rings with.
    tune: Tune | None = None
    # An alarm's new time, HH:MM, and its repeat (none = once); days only goes with a time.
    time: str | None = None
    days: str | list[str] | None = None


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
        opts = body.model_dump()
        if body.pos is not None:
            opts["pos"] = {"x": round(body.pos.x, 4), "y": round(body.pos.y, 4)}
        w = await add(db, user, **opts)
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
        if body.action == "undo":
            if w.kind != "custom":
                raise WidgetError("Only a custom widget has versions to go back to")
            await cw.show_version(db, w, body.version)
        else:
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
    if body.tune is not None:
        if w.kind not in RINGS:
            raise HTTPException(status.HTTP_409_CONFLICT, "Only a timer, alarm or reminder rings")
        w.state = {**(w.state or {}), "tune": body.tune}
    if body.time is not None:
        try:
            retime(w, body.time, body.days)
        except WidgetError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if body.pos is not None or body.dock:
        # Placed by hand, like a move along the dock: the assistant leaves it be for a while.
        w.pos = None if body.dock else {"x": round(body.pos.x, 4), "y": round(body.pos.y, 4)}
        state = await sc.load(db, user.id, lock=True)
        sc.mark_override(state, sc.override_key("move", str(w.id)))
    if body.slot is not None:
        # Moved by hand: the assistant leaves its place alone for a while.
        state = await sc.load(db, user.id, lock=True)
        sc.mark_override(state, sc.override_key("move", str(w.id)))
        w.updated_at = utcnow()
        await sc.reorder(db, redis, user.id, w, body.slot)
        await db.refresh(w)
        await announce(redis, user.id, w)
        return payload(w)
    w.updated_at = utcnow()
    await db.commit()
    await announce(redis, user.id, w)
    return payload(w)


async def _custom(db, user, widget_id: uuid.UUID) -> Widget:
    w = await _own(db, user, widget_id)
    if w.kind != "custom":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a custom widget")
    return w


@router.get("/{widget_id}/frame")
async def widget_frame(
    widget_id: uuid.UUID, user: CurrentUser, db: DbDep, theme: Literal["dark", "light"] = "dark"
) -> Response:
    """The widget's page, for its <iframe>, in the theme of the card it sits in. Its CSP
    sandboxes it into an opaque origin with no network, so even opened on its own it can't
    reach the app or the internet."""
    page = await cw.current(db, await _custom(db, user, widget_id))
    return Response(
        cw.frame_html(page.html, theme),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": cw.FRAME_CSP,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/{widget_id}/bridge")
async def widget_bridge(
    widget_id: uuid.UUID,
    body: BridgeIn,
    user: CurrentUser,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> dict:
    """A call from the widget's page (window.nexus), forwarded by the app. Anything beyond its
    own state has to be in its manifest and approved by the person."""
    w = await _custom(db, user, widget_id)
    try:
        if body.call == "state.get":
            return {"result": (w.state or {}).get("data")}
        if body.call == "state.set":
            cw.set_state(w, body.args.get("value"))
            w.updated_at = utcnow()
            await db.commit()
            return {"result": True}
        if body.call == "fetch":
            return {"result": await cw.fetch(w, str(body.args.get("url", "")), redis)}
        if body.call == "data":
            source = str(body.args.get("source", ""))
            return {
                "result": await cw.read_data(
                    db, settings, user, w, source, body.args.get("options")
                )
            }
        if not await cw.may_notify(redis, w):
            raise cw.CustomWidgetError("Too many notices from this widget; try again later")
        await notify(
            db,
            redis,
            settings,
            user.id,
            NotificationKind.info,
            str(body.args.get("title", ""))[:200] or w.title,
            str(body.args.get("body", "")),
            send_push=False,
        )
        return {"result": True}
    except WidgetError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post("/{widget_id}/approve")
async def approve_widget(
    widget_id: uuid.UUID, body: ApproveIn, user: CurrentUser, db: DbDep, redis: RedisDep
) -> dict:
    """The person let the widget fetch from these hosts or read this data."""
    w = await _custom(db, user, widget_id)
    cw.approve(w, body.hosts, body.data)
    w.updated_at = utcnow()
    db.add(
        AuditLog(
            actor_id=user.id,
            action="widget.approved",
            target=str(w.id),
            data={"hosts": body.hosts, "data": body.data},
        )
    )
    await db.commit()
    await announce(redis, user.id, w)
    return payload(w)


@router.get("/{widget_id}/versions")
async def widget_versions(widget_id: uuid.UUID, user: CurrentUser, db: DbDep) -> list[dict]:
    w = await _custom(db, user, widget_id)
    return [
        {
            "id": str(v.id),
            "version": v.version,
            "name": v.name,
            "about": (v.manifest or {}).get("about", ""),
            "created_at": v.created_at.isoformat(),
        }
        for v in await cw.versions(db, w)
    ]
