"""Widgets on the main screen: timers, alarms, stopwatches, reminders, clocks, notes and
search boxes.

The server is the clock. A timer stores when it ends, not a countdown, so it keeps running with
the tab closed and shows the same time on the laptop and the phone. Anything that rings sets
fires_at; the worker (fire_due, every 15 seconds) marks it ringing, puts a notice on the screen
and pushes it to the phone, and moves a repeating alarm or reminder on to its next time.

Both the assistant's tools and the app's buttons go through add() and act(), so a timer Orion
started can be paused from the dock and one started from the dock can be cancelled in chat.

Removing a widget puts it away rather than deleting it: it keeps its row (removed_at set) and
waits in the person's widget library, the "Your library" side of the add menu, to be put back
as it was: a note with its text, a clock with its cities, a custom widget with its versions and
approvals. Put away, nothing on it rings. The library keeps every custom widget and the newest
LIBRARY_KEEP of the rest; only "delete for good" (forget) removes a row.
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.events import Event, publish
from nexus_common.models import NotificationKind, User, Widget, utcnow
from nexus_common.notices import notify
from nexus_common.routines import ScheduleError, check_timezone, describe, next_run, rule_for
from nexus_common.settings import Settings

KINDS = ("timer", "alarm", "stopwatch", "reminder", "clock", "note", "search")
# Where a search box sends what's typed in it: results listed in the card ("nexus", opened in
# the reading view), a web search in a new tab, or the assistant.
ENGINES = ("nexus", "google", "duckduckgo", "bing", "assistant")
DEFAULT_ENGINE = "nexus"
# The sounds a timer, alarm or reminder can ring with; the app plays them (apps/web
# lib/tunes.ts), so a new one goes in both places.
TUNES = ("chime", "bell", "marimba", "sunrise", "birdsong", "harp", "retro", "digital", "pulse")
DEFAULT_TUNE = "chime"
RINGS = ("timer", "alarm", "reminder")
MAX_WIDGETS = 24
MAX_TIMER_MINUTES = 24 * 60
NOTE_CHARS = 4000
TITLE_CHARS = 120
MAX_ZONES = 4
SNOOZE_MINUTES = 5
# Widgets the worker handles per pass; the rest wait 15 seconds.
FIRE_BATCH = 200
# Built-in widgets kept in the library once put away; custom ones are all kept.
LIBRARY_KEEP = 40
# A size set by dragging a card's corner, in CSS pixels.
MIN_W, MAX_W = 160, 720
MIN_H, MAX_H = 64, 800


class WidgetError(ValueError):
    """A widget Nexus won't make or an action that doesn't apply; the message is safe to show."""


def zone_of(user: User) -> str:
    return user.timezone or "UTC"


def on_screen():
    """The widgets on the screen, not put away in the library."""
    return Widget.removed_at.is_(None)


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(UTC).isoformat() if t else None


def _parse(t: str | None) -> datetime | None:
    return datetime.fromisoformat(t) if t else None


def payload(w: Widget) -> dict:
    state = w.state or {}
    if w.kind == "custom":
        # A custom widget's own data goes to its frame through the bridge, not to every tab.
        state = {k: v for k, v in state.items() if k != "data"}
    return {
        "id": str(w.id),
        "kind": w.kind,
        "title": w.title,
        "state": state,
        "slot": w.slot,
        "pos": w.pos,
        "size": w.size,
        "chat_id": str(w.chat_id) if w.chat_id else None,
        "fires_at": _iso(w.fires_at),
        "removed_at": _iso(w.removed_at),
        "created_at": w.created_at.isoformat(),
        "updated_at": w.updated_at.isoformat(),
    }


async def announce(redis: Redis, user_id: uuid.UUID, w: Widget, *, deleted: bool = False):
    if deleted:
        event = Event(type="widget.deleted", data={"id": str(w.id)})
    else:
        event = Event(type="widget.updated", data={"widget": payload(w)})
    await publish(redis, user_id, event)


def check_size(width: float, height: float) -> dict:
    """A size dragged out on a card, kept within what fits on a screen."""
    return {
        "w": round(min(max(width, MIN_W), MAX_W)),
        "h": round(min(max(height, MIN_H), MAX_H)),
    }


def check_engine(engine: str | None) -> str:
    engine = (engine or DEFAULT_ENGINE).strip().lower()
    if engine not in ENGINES:
        raise WidgetError(f"A search box searches with {', '.join(ENGINES)}; not {engine}")
    return engine


def _hhmm(time: str) -> tuple[int, int]:
    try:
        hour, minute = (int(x) for x in time.strip().split(":")[:2])
    except ValueError as exc:
        raise WidgetError(f"{time!r} isn't a time; use 24-hour HH:MM like 07:30") from exc
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise WidgetError(f"{time!r} isn't a time; use 24-hour HH:MM like 07:30")
    return hour, minute


def next_at_time(time: str, tz: str, now: datetime) -> datetime:
    """The next time the clock in `tz` shows HH:MM, strictly after now, in UTC."""
    hour, minute = _hhmm(time)
    local = now.astimezone(ZoneInfo(tz))
    at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= local:
        at += timedelta(days=1)
    return at.astimezone(UTC)


def check_tune(tune: str | None) -> str | None:
    if tune is None:
        return None
    tune = tune.strip().lower()
    if tune not in TUNES:
        raise WidgetError(f"There's no {tune!r} tune; pick one of {', '.join(TUNES)}")
    return tune


def _repeat(days: str | list[str] | None, time: str, tz: str, now: datetime):
    """(rule, first time) for a repeating alarm or reminder, or (None, None) for a one-off."""
    if not days:
        return None, None
    try:
        rule = rule_for(days, time)
        first = next_run(rule, tz, now)
    except ScheduleError as exc:
        raise WidgetError(str(exc)) from exc
    if first is None:
        raise WidgetError("That schedule never comes round again")
    return rule, first


def _at(when: str, tz: str, now: datetime) -> datetime:
    """A local date and time ("2026-10-26T21:00", in the person's zone unless it says
    otherwise) as UTC, in the future."""
    try:
        at = datetime.fromisoformat(when.strip())
    except ValueError as exc:
        raise WidgetError(f"{when!r} isn't a date and time; use 2026-10-26T21:00") from exc
    if at.tzinfo is None:
        at = at.replace(tzinfo=ZoneInfo(tz))
    at = at.astimezone(UTC)
    if at <= now:
        raise WidgetError("That time has already passed")
    return at


def _minutes(value: float, what: str = "A timer") -> float:
    if not 0 < value <= MAX_TIMER_MINUTES:
        raise WidgetError(f"{what} can run from a few seconds up to 24 hours")
    return value


def _label(minutes: float) -> str:
    if minutes < 1:
        return f"{round(minutes * 60)} sec"
    if minutes < 60 or minutes % 60:
        return f"{minutes:g} min"
    return f"{minutes / 60:g} h"


async def add(
    db: AsyncSession,
    user: User,
    kind: str,
    *,
    title: str = "",
    chat_id: uuid.UUID | None = None,
    minutes: float | None = None,
    time: str | None = None,
    days: str | list[str] | None = None,
    when: str | None = None,
    text: str | None = None,
    zones: list[str] | None = None,
    tune: str | None = None,
    engine: str | None = None,
    pos: dict | None = None,
    now: datetime | None = None,
) -> Widget:
    """Make a widget and put it at the end of the dock, or at pos (0..1 across the main screen)
    when it was dropped there. Doesn't commit."""
    if kind not in KINDS:
        raise WidgetError(f"There's no {kind} widget; the kinds are {', '.join(KINDS)}")
    await check_room(db, user.id)
    now = now or utcnow()
    tz = zone_of(user)
    state: dict = {}
    fires: datetime | None = None
    title = (title or "").strip()
    if kind == "timer":
        mins = _minutes(float(minutes or 0))
        fires = now + timedelta(minutes=mins)
        state = {"duration_s": round(mins * 60), "ends_at": _iso(fires), "status": "running"}
        title = title or f"{_label(mins)} timer"
    elif kind == "stopwatch":
        state = {"started_at": _iso(now), "elapsed_s": 0, "status": "running"}
        title = title or "Stopwatch"
    elif kind == "alarm":
        if not time:
            raise WidgetError("An alarm needs a time, HH:MM")
        _hhmm(time)
        rule, fires = _repeat(days, time, tz, now)
        fires = fires or next_at_time(time, tz, now)
        state = {"time": time.strip()[:5], "repeat": rule, "tz": tz, "status": "set"}
        title = title or f"Alarm {time.strip()[:5]}"
    elif kind == "reminder":
        text = (text or title or "").strip()
        if not text:
            raise WidgetError("A reminder needs something to remind you of")
        rule = None
        if days:
            if not time:
                raise WidgetError("A repeating reminder needs a time, HH:MM")
            rule, fires = _repeat(days, time, tz, now)
        elif when:
            fires = _at(when, tz, now)
        elif minutes:
            fires = now + timedelta(minutes=_minutes(float(minutes), "A reminder"))
        elif time:
            fires = next_at_time(time, tz, now)
        else:
            raise WidgetError("Say when: a date and time, a time of day, or minutes from now")
        state = {"text": text[:500], "repeat": rule, "tz": tz, "status": "set"}
        if time:
            state["time"] = time.strip()[:5]
        title = title or text
    elif kind == "clock":
        try:
            picked = [check_timezone(z) for z in (zones or [tz])][:MAX_ZONES]
        except ScheduleError as exc:
            raise WidgetError(str(exc)) from exc
        state = {"zones": picked}
        title = title or "Clock"
    elif kind == "note":
        state = {"text": (text or "")[:NOTE_CHARS]}
        title = title or "Note"
    elif kind == "search":
        state = {"engine": check_engine(engine)}
        title = title or "Search"
    if kind in RINGS and (picked := check_tune(tune)):
        state["tune"] = picked
    w = Widget(
        user_id=user.id,
        kind=kind,
        title=title[:TITLE_CHARS],
        state=state,
        slot=await next_slot(db, user.id),
        chat_id=chat_id,
        pos=pos,
        fires_at=fires,
        created_at=now,
        updated_at=now,
    )
    db.add(w)
    await db.flush()
    return w


async def check_room(db: AsyncSession, user_id: uuid.UUID) -> None:
    count = await db.scalar(select(func.count()).where(Widget.user_id == user_id, on_screen()))
    if (count or 0) >= MAX_WIDGETS:
        raise WidgetError(
            f"The screen holds {MAX_WIDGETS} widgets; put one away in the library first"
        )


async def next_slot(db: AsyncSession, user_id: uuid.UUID) -> int:
    """The end of the dock."""
    slot = await db.scalar(
        select(func.max(Widget.slot)).where(Widget.user_id == user_id, on_screen())
    )
    return (slot or 0) + 1


def put_away(w: Widget, now: datetime | None = None) -> None:
    """Take a widget off the screen into the library. Nothing on it rings while it's there: a
    running timer or stopwatch is paused where it was, an alarm or reminder remembers it was on
    so putting it back sets it again. Doesn't commit; the caller announces widget.deleted."""
    now = now or utcnow()
    s = dict(w.state or {})
    status = s.get("status")
    if w.kind == "timer" and status == "running" and s.get("ends_at"):
        left = max(0.0, (_parse(s["ends_at"]) - now).total_seconds())
        s.update(status="paused", remaining_s=round(left), ends_at=None)
    elif w.kind == "timer" and status == "ringing":
        s.update(status="done", ends_at=None)
    elif w.kind == "stopwatch" and status == "running" and s.get("started_at"):
        run = (now - _parse(s["started_at"])).total_seconds()
        s.update(status="paused", elapsed_s=round(s.get("elapsed_s", 0) + run, 1), started_at=None)
    elif w.kind in ("alarm", "reminder"):
        if w.fires_at and status in ("set", "ringing"):
            s["away_from"] = {"status": "set", "fires_at": _iso(w.fires_at)}
        elif status == "ringing" and s.get("repeat"):
            s["away_from"] = {"status": "set", "fires_at": None}
        if status == "ringing":
            s["status"] = "set" if s.get("repeat") else ("off" if w.kind == "alarm" else "done")
    s.pop("rang_at", None)
    w.state = s
    w.fires_at = None
    w.pos = None
    w.removed_at = now
    w.updated_at = now


async def restore(db: AsyncSession, user: User, w: Widget, now: datetime | None = None) -> None:
    """Put a widget from the library back at the end of the dock, as it was. An alarm or
    reminder that was on is set again for its next time; a one-off whose time went by while it
    was away comes back done (a reminder) or off (an alarm). Doesn't commit."""
    if w.removed_at is None:
        raise WidgetError(f"{w.title} is already on the screen")
    await check_room(db, user.id)
    now = now or utcnow()
    s = dict(w.state or {})
    was = s.pop("away_from", None)
    if w.kind in ("alarm", "reminder") and was:
        tz = s.get("tz") or zone_of(user)
        fires = _parse(was.get("fires_at"))
        if s.get("repeat"):
            fires = next_run(s["repeat"], tz, now)
        elif w.kind == "alarm" and s.get("time") and (fires is None or fires <= now):
            # A one-off alarm rings at its time; the next one is as good as the one it missed.
            fires = next_at_time(s["time"], tz, now)
        if fires and fires > now:
            s["status"] = "set"
            w.fires_at = fires
        else:
            s["status"] = "off" if w.kind == "alarm" else "done"
    w.state = s
    w.removed_at = None
    w.slot = await next_slot(db, user.id)
    w.updated_at = now


async def prune_library(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Delete for good the oldest built-in widgets in the library beyond LIBRARY_KEEP. Custom
    widgets stay: they were made for the person, and only they decide to delete one."""
    old = (
        await db.scalars(
            select(Widget.id)
            .where(
                Widget.user_id == user_id,
                Widget.removed_at.is_not(None),
                Widget.kind != "custom",
            )
            .order_by(Widget.removed_at.desc())
            .offset(LIBRARY_KEEP)
        )
    ).all()
    if old:
        await db.execute(delete(Widget).where(Widget.id.in_(old)))
    return len(old)


def retime(
    w: Widget,
    time: str,
    days: str | list[str] | None,
    *,
    now: datetime | None = None,
):
    """Move an alarm to a new time and repeat (no days = once), switching it on."""
    if w.kind != "alarm":
        raise WidgetError("Only an alarm has a time to change")
    now = now or utcnow()
    _hhmm(time)
    s = dict(w.state or {})
    tz = s.get("tz") or "UTC"
    rule, fires = _repeat(days, time, tz, now)
    w.fires_at = fires or next_at_time(time, tz, now)
    old = s.get("time")
    s.update(time=time.strip()[:5], repeat=rule, status="set")
    s.pop("rang_at", None)
    w.state = s
    if w.title == f"Alarm {old}":
        w.title = f"Alarm {s['time']}"
    w.updated_at = now


ACTIONS = ("pause", "resume", "reset", "add_time", "snooze", "dismiss", "remove")
# What can be done to a widget in the library (beside looking at it).
LIBRARY_ACTIONS = ("restore", "forget")


def act(w: Widget, action: str, *, minutes: float | None = None, now: datetime | None = None):
    """Change a widget in place: pause, resume or reset a timer or stopwatch, add time, snooze
    or dismiss what's ringing. "remove" is the caller's to do (put_away)."""
    now = now or utcnow()
    s = dict(w.state or {})
    status = s.get("status")
    if action not in ACTIONS:
        raise WidgetError(f"Widgets can {', '.join(ACTIONS)}; not {action}")
    if action == "remove":
        return
    if w.removed_at is not None:
        raise WidgetError(f"{w.title} is in the library; put it back on the screen first")
    if w.kind == "timer":
        if action == "pause" and status == "running":
            left = max(0.0, (_parse(s["ends_at"]) - now).total_seconds())
            s.update(status="paused", remaining_s=round(left), ends_at=None)
            w.fires_at = None
        elif action == "resume" and status == "paused":
            ends = now + timedelta(seconds=s.get("remaining_s") or s["duration_s"])
            s.update(status="running", ends_at=_iso(ends))
            s.pop("remaining_s", None)
            w.fires_at = ends
        elif action == "reset":
            s.update(status="paused", remaining_s=s["duration_s"], ends_at=None)
            w.fires_at = None
        elif action == "add_time":
            extra = timedelta(minutes=_minutes(float(minutes or 1)))
            if status == "running":
                ends = _parse(s["ends_at"]) + extra
            elif status == "paused":
                s["remaining_s"] = round((s.get("remaining_s") or 0) + extra.total_seconds())
                ends = None
            else:  # ringing or done: start again with the extra time
                ends = now + extra
            if ends is not None:
                s.update(status="running", ends_at=_iso(ends))
                w.fires_at = ends
        elif action == "snooze" and (
            status == "ringing" or (status == "running" and _parse(s["ends_at"]) <= now)
        ):
            # A running one past its end is ringing on screen before the worker's next pass.
            ends = now + timedelta(minutes=minutes or SNOOZE_MINUTES)
            s.update(status="running", ends_at=_iso(ends))
            w.fires_at = ends
        elif action == "dismiss":
            s.update(status="done", ends_at=None)
            w.fires_at = None
        else:
            raise WidgetError(f"A {status} timer can't {action.replace('_', ' ')}")
    elif w.kind == "stopwatch":
        if action == "pause" and status == "running":
            run = (now - _parse(s["started_at"])).total_seconds()
            s.update(status="paused", elapsed_s=round(s.get("elapsed_s", 0) + run, 1))
            s["started_at"] = None
        elif action == "resume" and status == "paused":
            s.update(status="running", started_at=_iso(now))
        elif action == "reset":
            s.update(status="paused", elapsed_s=0, started_at=None)
        else:
            raise WidgetError(f"A {status} stopwatch can't {action.replace('_', ' ')}")
    elif w.kind in ("alarm", "reminder"):
        if action == "snooze":
            w.fires_at = now + timedelta(minutes=minutes or SNOOZE_MINUTES)
            s["status"] = "set"
        elif action == "dismiss":
            # A repeating one already knows its next time (fire_due set it); a one-off is over.
            s["status"] = "set" if s.get("repeat") else ("off" if w.kind == "alarm" else "done")
            if not s.get("repeat"):
                w.fires_at = None
        elif action == "pause" and w.kind == "alarm":
            s["status"] = "off"
            w.fires_at = None
        elif action == "resume" and w.kind == "alarm":
            tz = s.get("tz") or "UTC"
            if s.get("repeat"):
                w.fires_at = next_run(s["repeat"], tz, now)
            else:
                w.fires_at = next_at_time(s["time"], tz, now)
            s["status"] = "set"
        else:
            raise WidgetError(f"A {w.kind} can't {action.replace('_', ' ')}")
    else:
        raise WidgetError(f"A {w.kind} widget has nothing to {action.replace('_', ' ')}")
    if s.get("status") != "ringing":
        s.pop("rang_at", None)
    w.state = s
    w.updated_at = now


def describe_widget(w: Widget, tz: str) -> str:
    """One line for the assistant: what it is and when it next rings."""
    s = w.state or {}
    when = ""
    if w.fires_at:
        local = w.fires_at.astimezone(ZoneInfo(s.get("tz") or tz))
        when = f", rings {local:%a %d %b %H:%M}"
    repeat = f", {describe(s['repeat']).lower()}" if s.get("repeat") else ""
    status = f" ({s['status']})" if s.get("status") else ""
    if w.kind == "custom":
        status = f" (version {s.get('version')} of {s.get('versions')})"
    if w.kind == "search":
        status = f" (searches with {s.get('engine', DEFAULT_ENGINE)})"
    away = ", in the library" if w.removed_at else ""
    return f"- {w.kind} {w.title!r} (id {w.id}){status}{when}{repeat}{away}"


async def fire_due(db: AsyncSession, redis: Redis, settings: Settings, now=None) -> int:
    """Ring every timer, alarm and reminder whose time has come. Returns how many rang."""
    now = now or utcnow()
    due = (
        await db.scalars(
            select(Widget)
            .where(Widget.fires_at.is_not(None), Widget.fires_at <= now)
            .order_by(Widget.fires_at)
            .limit(FIRE_BATCH)
            .with_for_update(skip_locked=True)
        )
    ).all()
    rang = []
    for w in due:
        s = dict(w.state or {})
        s.update(status="ringing", rang_at=_iso(now))
        w.fires_at = None
        if s.get("repeat"):
            w.fires_at = next_run(s["repeat"], s.get("tz") or "UTC", now)
        w.state = s
        w.updated_at = now
        rang.append(w)
    await db.commit()
    for w in rang:
        await announce(redis, w.user_id, w)
        kind = NotificationKind.reminder if w.kind == "reminder" else NotificationKind.timer
        title = {"timer": "Time's up", "alarm": "Alarm"}.get(w.kind, "Reminder")
        body = (w.state or {}).get("text") or w.title
        await notify(db, redis, settings, w.user_id, kind, title, body, tag=f"widget:{w.id}")
    return len(rang)
