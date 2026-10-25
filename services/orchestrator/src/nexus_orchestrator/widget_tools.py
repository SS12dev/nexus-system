"""The assistant's widget tools: "set a timer for 5 minutes" or "wake me at 7" puts a widget on
the main screen that rings by itself (nexus_common/widgets.py), with no team and no model call
when it goes off."""

import uuid
from collections.abc import Callable
from typing import Any, Literal

from sqlalchemy import delete, select

from nexus_common.models import User, Widget
from nexus_common.widgets import WidgetError, act, add, announce, describe_widget, zone_of

Days = Literal["daily", "weekdays", "weekends"] | list[str] | None


def _days(days):
    return ["SA", "SU"] if days == "weekends" else days


def build_widget_tools(ctx: Any, note: Callable) -> dict[str, Callable]:
    deps, user_id = ctx.deps, ctx.user_id

    async def make(kind: str, **opts) -> str:
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            try:
                w = await add(db, user, kind, chat_id=ctx.chat_id, **opts)
            except WidgetError as exc:
                return f"Not added: {exc}"
            await db.commit()
            line = describe_widget(w, zone_of(user))
        await announce(deps.redis, user_id, w)
        return f"Added to the main screen: {line[2:]}"

    async def start_timer(minutes: float, label: str = "") -> str:
        """Start a countdown timer on the user's main screen; it rings (in the app and as a
        phone notification) when it ends, even with the app closed. minutes may be fractional
        (0.5 = 30 seconds), up to 24 hours. label names it ("Pasta")."""
        await note("start_timer", minutes=minutes, label=label)
        return await make("timer", minutes=minutes, title=label)

    async def set_alarm(time: str, label: str = "", days: Days = None) -> str:
        """Set an alarm at a clock time in the user's time zone, 24-hour "HH:MM". Without days
        it rings once, at the next time the clock shows it; days repeats it: "daily",
        "weekdays", "weekends" or day codes like ["MO", "WE"]."""
        await note("set_alarm", time=time, days=days)
        return await make("alarm", time=time, days=_days(days), title=label)

    async def remind_me(
        text: str,
        when: str = "",
        minutes: float = 0,
        time: str = "",
        days: Days = None,
    ) -> str:
        """Remind the user of something: a notice on screen and on their phone when it's due,
        with no model run. Give exactly one of: when (a local date and time,
        "2026-10-26T21:00"), minutes from now, time ("21:00", the next time the clock shows
        it), or time plus days to repeat ("daily", "weekdays", "weekends", ["MO", "TH"]).
        Use this, not a routine, for a plain reminder; a routine is for work that runs on a
        schedule (a brief, a digest)."""
        await note("remind_me", text=text, when=when or time, days=days)
        return await make(
            "reminder",
            text=text,
            when=when or None,
            minutes=minutes or None,
            time=time or None,
            days=_days(days),
        )

    async def add_widget(
        kind: Literal["stopwatch", "clock", "note"],
        title: str = "",
        text: str = "",
        zones: list[str] | None = None,
    ) -> str:
        """Put a stopwatch (starts at once), a clock (zones: IANA names like "Asia/Kolkata",
        "Europe/London", up to 4; the user's own zone when left out) or a note (text) on the
        user's main screen."""
        await note("add_widget", kind=kind)
        return await make(kind, title=title, text=text or None, zones=zones)

    async def list_widgets() -> str:
        """List the widgets on the user's main screen with their ids and when they ring."""
        await note("list_widgets")
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            rows = (
                await db.scalars(
                    select(Widget).where(Widget.user_id == user_id).order_by(Widget.slot)
                )
            ).all()
            tz = zone_of(user)
        return "\n".join(describe_widget(w, tz) for w in rows) or "No widgets on the screen."

    async def control_widget(
        widget_id: str,
        action: Literal["pause", "resume", "reset", "add_time", "snooze", "dismiss", "remove"],
        minutes: float = 0,
    ) -> str:
        """Change a widget (id from list_widgets): pause, resume or reset a timer or
        stopwatch; add_time (minutes) to a timer; snooze (minutes, default 5) or dismiss one
        that's ringing; pause or resume an alarm; remove any widget from the screen."""
        await note("control_widget", widget=widget_id, action=action)
        try:
            wid = uuid.UUID(widget_id)
        except ValueError:
            return "That isn't a widget id; call list_widgets."
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            w = await db.scalar(select(Widget).where(Widget.id == wid, Widget.user_id == user_id))
            if w is None:
                return "No such widget."
            if action == "remove":
                await db.execute(delete(Widget).where(Widget.id == wid))
                await db.commit()
                await announce(deps.redis, user_id, w, deleted=True)
                return f"Removed {w.title}."
            try:
                act(w, action, minutes=minutes or None)
            except WidgetError as exc:
                return f"Not changed: {exc}"
            await db.commit()
            line = describe_widget(w, zone_of(user))
        await announce(deps.redis, user_id, w)
        return f"Done: {line[2:]}"

    return {
        "start_timer": start_timer,
        "set_alarm": set_alarm,
        "remind_me": remind_me,
        "add_widget": add_widget,
        "list_widgets": list_widgets,
        "control_widget": control_widget,
    }
