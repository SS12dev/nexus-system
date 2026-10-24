"""Routine schedules: RFC 5545 RRULEs evaluated in the routine's own time zone.

A schedule is stored as an RRULE without DTSTART ("FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=9;BYMINUTE=0").
It is expanded in local wall-clock time from a fixed anchor and only then turned into UTC, so
"every day at 08:00" stays at 08:00 across daylight-saving changes. The anchor is a Monday at
midnight, which makes INTERVAL rules ("every second week") and the default minute and second
(0) the same wherever and whenever a routine was made.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from dateutil.rrule import DAILY, HOURLY, MONTHLY, WEEKLY, YEARLY, rrule, rrulestr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import Chat, Routine, User
from nexus_common.settings import Settings

ANCHOR = datetime(2026, 1, 5)  # a Monday
# Text the routine prompt asks for when there is nothing worth telling (only_if_new).
NOTHING_NEW = "NOTHING_NEW"
# Failed runs in a row before a routine pauses itself.
MAX_FAILURES = 3
# A run due longer ago than this when the scheduler gets to it (the worker was down) is skipped
# to the next time instead of running late.
LATE_LIMIT = timedelta(hours=1)
ALLOWED_FREQ = {HOURLY, DAILY, WEEKLY, MONTHLY, YEARLY}
DAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
DAY_NAMES = dict(
    zip(
        DAY_CODES,
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
        strict=True,
    )
)
TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ScheduleError(ValueError):
    """A schedule or time zone Nexus won't run; the message is safe to show."""


def check_timezone(name: str | None) -> str:
    name = (name or "").strip()
    if name == "UTC":
        return name
    if not name or name not in available_timezones():
        raise ScheduleError(f"{name or 'An empty name'} isn't a time zone Nexus knows")
    return name


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"{name} isn't a time zone Nexus knows") from exc


def parse(schedule: str) -> rrule:
    text = (schedule or "").strip().removeprefix("RRULE:")
    if not text or "DTSTART" in text.upper() or "\n" in text:
        raise ScheduleError("Give the schedule as one RRULE line without DTSTART")
    try:
        rule = rrulestr(text, dtstart=ANCHOR)
    except (ValueError, TypeError) as exc:
        raise ScheduleError(f"That schedule doesn't parse: {exc}") from exc
    if not isinstance(rule, rrule) or rule._freq not in ALLOWED_FREQ:
        raise ScheduleError("Routines run hourly at most often (FREQ=HOURLY, DAILY, WEEKLY, ...)")
    return rule


def next_run(schedule: str, timezone: str, after: datetime) -> datetime | None:
    """The first time the schedule fires strictly after `after`, in UTC (None if it has ended)."""
    zone = _zone(timezone)
    local = after.astimezone(zone).replace(tzinfo=None)
    nxt = parse(schedule).after(local)
    if nxt is None:
        return None
    return nxt.replace(tzinfo=zone).astimezone(UTC)


def upcoming(schedule: str, timezone: str, after: datetime, count: int = 3) -> list[datetime]:
    out: list[datetime] = []
    t = after
    while len(out) < count and (t := next_run(schedule, timezone, t)) is not None:
        out.append(t)
    return out


def validate(schedule: str, timezone: str, min_interval_minutes: int, now: datetime) -> str:
    """The schedule, normalised, once it is known to fire, and not more often than allowed."""
    check_timezone(timezone)
    text = schedule.strip().removeprefix("RRULE:")
    times = upcoming(text, timezone, now, 4)
    if not times:
        raise ScheduleError("That schedule never runs again")
    gaps = [(b - a) for a, b in zip(times, times[1:], strict=False)]
    if gaps and min(gaps) < timedelta(minutes=min_interval_minutes):
        raise ScheduleError(
            f"Routines may run at most every {min_interval_minutes} minutes; that one runs more "
            "often"
        )
    return text


def rule_for(days: str | list[str], time: str) -> str:
    """An RRULE from the simple form profile packs and the schedule picker use: days is "daily",
    "weekdays", a list of day codes (["MO", "TH"]) or "monthly:<day>"; time is "HH:MM"."""
    m = TIME.match(time.strip())
    if not m:
        raise ScheduleError(f"{time} isn't a time like 08:30")
    at = f"BYHOUR={int(m.group(1))};BYMINUTE={int(m.group(2))}"
    if days == "daily":
        return f"FREQ=DAILY;{at}"
    if days == "weekdays":
        return f"FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;{at}"
    if isinstance(days, str) and days.startswith("monthly:"):
        day = int(days.split(":", 1)[1])
        if not 1 <= day <= 28:
            raise ScheduleError("A monthly routine runs on a day from 1 to 28")
        return f"FREQ=MONTHLY;BYMONTHDAY={day};{at}"
    codes = [days] if isinstance(days, str) else list(days)
    codes = [c.upper()[:2] for c in codes]
    if not codes or any(c not in DAY_CODES for c in codes):
        raise ScheduleError(f"{days} isn't a list of days like MO, TH")
    ordered = [c for c in DAY_CODES if c in codes]
    return f"FREQ=WEEKLY;BYDAY={','.join(ordered)};{at}"


def shift_time(time: str, minutes: int) -> str:
    m = TIME.match(time.strip())
    if not m:
        raise ScheduleError(f"{time} isn't a time like 08:30")
    total = (int(m.group(1)) * 60 + int(m.group(2)) + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


@dataclass
class _Parts:
    freq: str
    interval: int
    days: list[str]
    monthday: list[int]
    hours: list[int]
    minutes: list[int]


def _parts(schedule: str) -> _Parts:
    fields = {}
    for piece in schedule.strip().removeprefix("RRULE:").split(";"):
        if "=" in piece:
            k, v = piece.split("=", 1)
            fields[k.upper()] = v
    ints = lambda key: [int(x) for x in fields.get(key, "").split(",") if x.lstrip("-").isdigit()]  # noqa: E731
    return _Parts(
        freq=fields.get("FREQ", "").upper(),
        interval=int(fields.get("INTERVAL", "1") or 1),
        days=[d for d in fields.get("BYDAY", "").upper().split(",") if d],
        monthday=ints("BYMONTHDAY"),
        hours=ints("BYHOUR"),
        minutes=ints("BYMINUTE"),
    )


def lower_first(text: str) -> str:
    """For a description mid-sentence: "every Sunday at 18:00", keeping the day's capital."""
    return text[:1].lower() + text[1:]


def describe(schedule: str) -> str:
    """The schedule in words ("Weekdays at 08:00"), falling back to the rule itself."""
    try:
        parse(schedule)
    except ScheduleError:
        return schedule
    p = _parts(schedule)
    minute = p.minutes[0] if p.minutes else 0
    times = ", ".join(f"{h:02d}:{minute:02d}" for h in p.hours) if p.hours else ""
    at = f" at {times}" if times else ""
    every = "" if p.interval == 1 else f"{p.interval} "
    if p.freq == "HOURLY":
        return "Every hour" if p.interval == 1 else f"Every {p.interval} hours"
    if p.freq == "DAILY":
        return ("Every day" if p.interval == 1 else f"Every {p.interval} days") + at
    if p.freq == "WEEKLY":
        days = [d for d in p.days if d in DAY_CODES]
        if days == ["MO", "TU", "WE", "TH", "FR"] and p.interval == 1:
            return f"Weekdays{at}"
        if set(days) == set(DAY_CODES) and p.interval == 1:
            return f"Every day{at}"
        names = [DAY_NAMES[d] for d in days] or ["week"]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        lead = "Every" if p.interval == 1 else f"Every {every}weeks on"
        return f"{lead} {listed}{at}"
    if p.freq == "MONTHLY" and p.monthday:
        day = p.monthday[0]
        which = "the last day" if day == -1 else f"day {day}"
        lead = "Every month" if p.interval == 1 else f"Every {p.interval} months"
        return f"{lead} on {which}{at}"
    return schedule


# --- Storing routines (shared by the API and the assistant's routine tools) ------------------

DELIVERY = ("push", "email", "whatsapp")
NAME_MAX = 80
PROMPT_MAX = 4000


class RoutineLimit(ValueError):
    """The person already has as many routines as they may."""


def routine_payload(r: Routine) -> dict:
    return {
        "id": str(r.id),
        "name": r.name,
        "prompt": r.prompt,
        "schedule": r.schedule,
        "schedule_text": describe(r.schedule),
        "timezone": r.timezone,
        "delivery": list(r.delivery or []),
        "only_if_new": r.only_if_new,
        "enabled": r.enabled,
        "source": r.source,
        "chat_id": str(r.chat_id) if r.chat_id else None,
        "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
        "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
        "failures": r.failures,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def reschedule(r: Routine, now: datetime) -> None:
    """Set when it runs next: never while paused, else the first time after now."""
    r.next_run_at = next_run(r.schedule, r.timezone, now) if r.enabled else None


def clean_delivery(channels: list[str] | None) -> list[str]:
    return [c for c in DELIVERY if c in set(channels or [])]


async def add_routine(
    db: AsyncSession,
    settings: Settings,
    user: User,
    *,
    name: str,
    prompt: str,
    schedule: str,
    timezone: str | None = None,
    delivery: list[str] | None = None,
    only_if_new: bool = False,
    enabled: bool = True,
    source: str = "user",
) -> Routine:
    """Validate and store a new routine with a chat of its own. Raises ScheduleError or
    RoutineLimit with a message that is safe to show."""
    name = " ".join(name.split())[:NAME_MAX]
    prompt = prompt.strip()[:PROMPT_MAX]
    if not name or not prompt:
        raise ScheduleError("A routine needs a name and a prompt")
    count = await db.scalar(select(func.count()).where(Routine.user_id == user.id))
    if count >= settings.routines_max_per_user:
        raise RoutineLimit(
            f"You already have {count} routines, the most you may have. Delete or reuse one."
        )
    now = datetime.now(UTC)
    tz = timezone or user.timezone or "UTC"
    schedule = validate(schedule, tz, settings.routine_min_interval_minutes, now)
    chat = Chat(user_id=user.id, title=name)
    db.add(chat)
    await db.flush()
    r = Routine(
        user_id=user.id,
        name=name,
        prompt=prompt,
        schedule=schedule,
        timezone=tz,
        delivery=clean_delivery(delivery),
        only_if_new=only_if_new,
        enabled=enabled,
        source=source[:40],
        chat_id=chat.id,
        failures=0,
        created_at=now,
        updated_at=now,
    )
    reschedule(r, now)
    db.add(r)
    await db.flush()
    return r
