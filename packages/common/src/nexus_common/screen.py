"""The main screen as the assistant and the person share it: which layout it shows (the orb,
plain lists, or a board of widgets), what's open over it (a result file, a widget), and the
order of the widget dock.

The assistant changes it with its screen tool and the person with the app's own controls; both
end up here, saved per user (so a reload or another device shows the same screen) and sent to
every open tab as a screen.op event.

The person's own changes win. Each one is kept for a while as an override, and the assistant's
tool leaves that part of the screen alone until it expires, unless the person asked for the
change in so many words. Every change the assistant makes goes in the audit log with the chat
that caused it, so "why did my screen change" has an answer.
"""

import uuid
from datetime import datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.events import Event, publish
from nexus_common.models import AuditLog, ScreenState, Widget, utcnow
from nexus_common.widgets import announce

MODES = ("orb", "list", "dashboard")
FOCUS_KINDS = ("file", "widget")
# How long the person's own change keeps the assistant's hands off that part of the screen.
OVERRIDE_HOURS = 12
MAX_OVERRIDES = 20


def override_key(op: str, *parts: str) -> str:
    """ "mode", "move:<widget id>", "focus:file:<id>"."""
    return ":".join([op, *parts])


def _live(overrides: list | None, now: datetime) -> list[dict]:
    cutoff = now - timedelta(hours=OVERRIDE_HOURS)
    return [o for o in overrides or [] if datetime.fromisoformat(o["at"]) > cutoff]


def overridden(state: ScreenState, key: str, now: datetime | None = None) -> bool:
    """Whether the person changed this part of the screen themselves recently."""
    return any(o["key"] == key for o in _live(state.overrides, now or utcnow()))


def mark_override(state: ScreenState, key: str, now: datetime | None = None) -> None:
    now = now or utcnow()
    kept = [o for o in _live(state.overrides, now) if o["key"] != key]
    # A new list, so the JSONB column sees the change.
    state.overrides = [*kept, {"key": key, "at": now.isoformat()}][-MAX_OVERRIDES:]


def payload(state: ScreenState) -> dict:
    return {
        "mode": state.mode,
        "focus": state.focus,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }


async def load(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> ScreenState:
    """The user's screen, made (with the defaults) the first time. Doesn't commit."""
    query = select(ScreenState).where(ScreenState.user_id == user_id)
    if lock:
        query = query.with_for_update()
    state = await db.scalar(query)
    if state is None:
        state = ScreenState(
            user_id=user_id, mode="orb", focus=None, overrides=[], updated_at=utcnow()
        )
        db.add(state)
        await db.flush()
    return state


async def broadcast(
    redis: Redis,
    user_id: uuid.UUID,
    op: str,
    state: ScreenState,
    *,
    by: str,
    chat_id: uuid.UUID | None = None,
    **extra,
) -> None:
    await publish(
        redis,
        user_id,
        Event(
            type="screen.op",
            chat_id=chat_id,
            data={"op": op, "by": by, "screen": payload(state), **extra},
        ),
    )


def audit(
    db: AsyncSession,
    user_id: uuid.UUID,
    op: str,
    target: str = "",
    *,
    by: str,
    chat_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    **data,
) -> None:
    db.add(
        AuditLog(
            actor_id=user_id,
            action=f"screen.{op}",
            target=target[:200] or None,
            data={
                "by": by,
                "chat_id": str(chat_id) if chat_id else None,
                "run_id": str(run_id) if run_id else None,
                **data,
            },
        )
    )


async def reorder(
    db: AsyncSession, redis: Redis, user_id: uuid.UUID, widget: Widget, position: int
) -> int:
    """Move a widget to a place on the dock (1 = first) and renumber the rest. Commits and
    announces the widgets that moved; returns where it ended up."""
    rows = (
        await db.scalars(
            select(Widget).where(Widget.user_id == user_id).order_by(Widget.slot, Widget.created_at)
        )
    ).all()
    order = [w for w in rows if w.id != widget.id]
    at = max(0, min(position - 1, len(order)))
    order.insert(at, widget)
    moved = []
    for slot, w in enumerate(order):
        if w.slot != slot:
            w.slot = slot
            w.updated_at = utcnow()
            moved.append(w)
    await db.commit()
    for w in moved:
        await announce(redis, user_id, w)
    return at + 1
