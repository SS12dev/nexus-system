"""The morning brief: one look at the day, built fresh each time and never stored.

- Weather and air quality from Open-Meteo (free, no key) for a place the person picks.
- Today's events from their Google Calendar, when it's connected.
- Tasks from the organiser: overdue, due today, being done, this week's focus, the Inbox.
- Money lines for the owner, when Money is set up: safe to spend, payments due in the next
  three days, yesterday's spending and the one next step from the Health tab.

The same brief is the Brief page, the scheduled notice and Telegram message (orchestrator
brief.py) and the assistant's `brief_today` tool. Every outside call has a short timeout and
fails on its own: a brief with no weather is still a brief.

Logs carry ids and counts only, never titles, places or amounts.
"""

import asyncio
import json
import logging
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import advice_store, money
from nexus_common import money_store as store
from nexus_common import organiser as org
from nexus_common.google import GoogleNotConnected, access_token
from nexus_common.models import BriefSettings, MoneySettings, OrgItem, RoleName, User, utcnow
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

SECTIONS = ("weather", "calendar", "tasks", "money")
# Advice items marked "do now" shown under Money.
ADVICE_LINES = 3
DEFAULT_SEND_AT = "07:30"
# A brief that couldn't go at its time (the Mac was asleep) still goes within this window.
SEND_WINDOW = timedelta(hours=3)
DUE_DAYS = 3
MAX_EVENTS = 15
MAX_TASKS = 8
OUTSIDE_TIMEOUT = httpx.Timeout(8, connect=4)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
PLACES_URL = "https://geocoding-api.open-meteo.com/v1/search"
CALENDAR_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
WEATHER_CACHE = 3600

# WMO weather codes as Open-Meteo reports them.
_SKY = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "cloudy",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow",
    80: "showers",
    81: "showers",
    82: "heavy showers",
    85: "snow showers",
    86: "snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}
RAIN_LIKELY = 50

# US AQI bands (Open-Meteo's us_aqi).
_AIR = (
    (50, "good"),
    (100, "moderate"),
    (150, "unhealthy for sensitive groups"),
    (200, "unhealthy"),
    (300, "very unhealthy"),
)


def sky(code: int | None) -> str:
    return _SKY.get(code, "") if code is not None else ""


def air_label(aqi: int) -> str:
    return next((label for top, label in _AIR if aqi <= top), "hazardous")


def whole(p: int) -> str:
    """Rupees without paise: 123456 -> '₹1,235'."""
    rounded = (abs(p) + 50) // 100 * 100  # half up, not to even
    return money.rupees(rounded if p >= 0 else -rounded)[:-3]


def clock(t: datetime | time) -> str:
    """'9:30 am', '12 pm'."""
    h, m = t.hour, t.minute
    suffix = "am" if h < 12 else "pm"
    h = h % 12 or 12
    return f"{h}:{m:02d} {suffix}" if m else f"{h} {suffix}"


def greeting(hour: int) -> str:
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


def parse_send_at(text: str | None) -> time:
    try:
        h, m = (int(x) for x in (text or DEFAULT_SEND_AT).split(":"))
        return time(h, m)
    except (TypeError, ValueError):
        return time(7, 30)


def wanted(prefs: BriefSettings | None) -> set[str]:
    chosen = set(prefs.sections or ()) if prefs else set()
    return (chosen & set(SECTIONS)) or set(SECTIONS)


def due_now(prefs: BriefSettings, user: User, now: datetime) -> bool:
    """Is a scheduled brief due: on, a chosen day, at or after its time (within the window),
    and not sent yet today. All in the person's time zone."""
    if not prefs.enabled:
        return False
    local = now.astimezone(org.zone(user))
    if prefs.days == "weekdays" and local.weekday() >= 5:
        return False
    if prefs.last_sent_on == local.date():
        return False
    at = datetime.combine(local.date(), parse_send_at(prefs.send_at), local.tzinfo)
    return at <= local < at + SEND_WINDOW


async def settings_for(db: AsyncSession, user: User) -> BriefSettings:
    """The person's brief settings; a new row (not yet saved) starts at their day start."""
    row = await db.get(BriefSettings, user.id)
    if row is not None:
        return row
    start = (user.profile or {}).get("day_start")
    return BriefSettings(
        user_id=user.id,
        enabled=False,
        send_at=start if isinstance(start, str) and len(start) == 5 else DEFAULT_SEND_AT,
        days="every",
        push=True,
        telegram=True,
        telegram_money=False,
        sections=[],
    )


def settings_out(row: BriefSettings) -> dict:
    return {
        "enabled": bool(row.enabled),
        "send_at": row.send_at,
        "days": row.days,
        "push": bool(row.push),
        "telegram": bool(row.telegram),
        "telegram_money": bool(row.telegram_money),
        "place": row.place,
        "lat": row.lat,
        "lon": row.lon,
        "sections": sorted(wanted(row), key=SECTIONS.index),
        "last_sent_on": row.last_sent_on,
    }


# --- Places and weather ------------------------------------------------------------------------


async def places(query: str, http: httpx.AsyncClient) -> list[dict]:
    """Towns and cities matching what was typed, for the place picker."""
    query = " ".join(query.split())[:80]
    if len(query) < 2:
        return []
    r = await http.get(
        PLACES_URL,
        params={"name": query, "count": 8, "language": "en", "format": "json"},
        timeout=OUTSIDE_TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("results") or []:
        try:
            lat, lon = float(p["latitude"]), float(p["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        region = ", ".join(x for x in (p.get("admin1"), p.get("country")) if x)
        out.append({
            "name": str(p.get("name") or "")[:80],
            "region": region[:120],
            "lat": round(lat, 4),
            "lon": round(lon, 4),
        })  # fmt: skip
    return out


def read_weather(fc: dict, air: dict | None, now: datetime) -> dict | None:
    """Today's forecast in a few facts: the range, the sky, when rain is likely, the air."""
    daily = fc.get("daily") or {}
    try:
        lo = round(daily["temperature_2m_min"][0])
        hi = round(daily["temperature_2m_max"][0])
    except (KeyError, IndexError, TypeError):
        return None
    code = (daily.get("weather_code") or [None])[0]
    rain_max = (daily.get("precipitation_probability_max") or [None])[0]
    hourly = fc.get("hourly") or {}
    rain_from = None
    for stamp, chance in zip(
        hourly.get("time") or [], hourly.get("precipitation_probability") or [], strict=False
    ):
        try:
            at = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            continue
        if at.date() == now.date() and at.hour >= now.hour and (chance or 0) >= RAIN_LIKELY:
            rain_from = at.time()
            break
    now_temp = None
    for stamp, temp in zip(
        hourly.get("time") or [], hourly.get("temperature_2m") or [], strict=False
    ):
        if stamp[:13] == now.strftime("%Y-%m-%dT%H") and temp is not None:
            now_temp = round(temp)
            break
    aqi = None
    if air:
        values = [
            v
            for stamp, v in zip(
                (air.get("hourly") or {}).get("time") or [],
                (air.get("hourly") or {}).get("us_aqi") or [],
                strict=False,
            )
            if v is not None
            and stamp[:10] == now.date().isoformat()
            and stamp[11:13] >= f"{now.hour:02d}"
        ]
        if values:
            aqi = round(max(values))
    return {
        "low_c": lo,
        "high_c": hi,
        "now_c": now_temp,
        "code": code,
        "sky": sky(code),
        "rain_chance": round(rain_max) if rain_max is not None else None,
        "rain_from": clock(rain_from) if rain_from else None,
        "aqi": aqi,
        "air": air_label(aqi) if aqi is not None else None,
    }


async def weather(
    redis: Redis | None, http: httpx.AsyncClient, lat: float, lon: float, tz: ZoneInfo,
    now: datetime,
) -> dict | None:  # fmt: skip
    """The forecast for a place, kept for an hour (the same for everyone there)."""
    key = f"nexus:brief:weather:{lat:.2f}:{lon:.2f}:{tz.key}:{now:%Y-%m-%dT%H}"
    if redis is not None and (hit := await redis.get(key)):
        return json.loads(hit)
    common = {"latitude": lat, "longitude": lon, "timezone": tz.key, "forecast_days": 1}
    fc_req = http.get(
        FORECAST_URL,
        params={
            **common,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max",
            "hourly": "temperature_2m,precipitation_probability",
        },
        timeout=OUTSIDE_TIMEOUT,
    )
    air_req = http.get(AIR_URL, params={**common, "hourly": "us_aqi"}, timeout=OUTSIDE_TIMEOUT)
    fc, air = await asyncio.gather(fc_req, air_req, return_exceptions=True)
    if isinstance(fc, BaseException) or fc.status_code != 200:
        raise RuntimeError("forecast unavailable")
    air_json = air.json() if not isinstance(air, BaseException) and air.status_code == 200 else None
    out = read_weather(fc.json(), air_json, now)
    if out is not None and redis is not None:
        await redis.set(key, json.dumps(out), ex=WEATHER_CACHE)
    return out


# --- Calendar ----------------------------------------------------------------------------------


def read_events(items: list[dict], tz: ZoneInfo, now: datetime) -> list[dict]:
    """Today's events, without cancelled ones or ones the person declined."""
    out = []
    for e in items:
        if e.get("status") == "cancelled":
            continue
        me = next((a for a in e.get("attendees") or [] if a.get("self")), None)
        if me and me.get("responseStatus") == "declined":
            continue
        start, end = e.get("start") or {}, e.get("end") or {}
        all_day = "date" in start and "dateTime" not in start
        try:
            if all_day:
                s = e_ = None
            else:
                s = datetime.fromisoformat(start["dateTime"]).astimezone(tz)
                e_ = datetime.fromisoformat(end.get("dateTime") or start["dateTime"]).astimezone(tz)
        except (KeyError, ValueError):
            continue
        video = e.get("hangoutLink") or next(
            (
                p.get("uri")
                for p in (e.get("conferenceData") or {}).get("entryPoints") or []
                if p.get("entryPointType") == "video"
            ),
            None,
        )
        link = str(e.get("htmlLink") or "")
        out.append({
            "title": " ".join(str(e.get("summary") or "(no title)").split())[:200],
            "all_day": all_day,
            "start": s.isoformat() if s else None,
            "end": e_.isoformat() if e_ else None,
            "starts": clock(s) if s else None,
            "ends": clock(e_) if e_ else None,
            "where": " ".join(str(e.get("location") or "").split())[:200] or None,
            "video": video if isinstance(video, str) and video.startswith("https://") else None,
            "link": link if link.startswith("https://") else None,
            "past": bool(e_ and e_ <= now),
            "others": sum(1 for a in e.get("attendees") or [] if not a.get("self")),
        })  # fmt: skip
    out.sort(key=lambda x: (not x["all_day"], x["start"] or ""))
    return out[:MAX_EVENTS]


async def calendar(
    db: AsyncSession, settings: Settings, user: User, http: httpx.AsyncClient, now: datetime
) -> dict:
    tz = org.zone(user)
    try:
        token = await access_token(db, settings, user.id, "calendar", http)
        await db.commit()  # keep a refreshed token
    except GoogleNotConnected:
        return {"connected": False, "events": []}
    day = now.astimezone(tz).date()
    t0 = datetime.combine(day, time(0), tz)
    r = await http.get(
        CALENDAR_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={
            "timeMin": t0.isoformat(),
            "timeMax": (t0 + timedelta(days=1)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 50,
        },
        timeout=OUTSIDE_TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"calendar {r.status_code}")
    return {"connected": True, "events": read_events(r.json().get("items") or [], tz, now)}


# --- Tasks and money ---------------------------------------------------------------------------


def _task(t: OrgItem) -> dict:
    return {
        "id": str(t.id),
        "title": t.title[:200],
        "type": t.type,
        "status": t.status,
        "due_on": t.due_on,
        "priority": t.priority,
    }


async def tasks(db: AsyncSession, user: User, today: date) -> dict:
    a = await org.agenda(db, user, today)
    return {
        "overdue": [_task(t) for t in a.overdue[:MAX_TASKS]],
        "due": [_task(t) for t in a.due[:MAX_TASKS]],
        "doing": [_task(t) for t in a.doing[:MAX_TASKS]],
        "focus": [_task(t) for t in a.focus[:MAX_TASKS]],
        "more": max(0, len(a.overdue) + len(a.due) + len(a.doing) - 3 * MAX_TASKS),
        "inbox": a.inbox,
        "review_done": a.review_done,
        "monday": today.weekday() == 0,
    }


async def money_lines(db: AsyncSession, user: User, today: date) -> dict | None:
    """The owner's money in four lines, or None when Money isn't set up."""
    if user.role != RoleName.owner:
        return None
    if await db.scalar(select(MoneySettings).where(MoneySettings.user_id == user.id)) is None:
        return None
    health = await store.health_view(db, user.id, today)
    # Card bills, EMIs and renewals in the next few days; an overdue card bill stays listed.
    due = await store.upcoming_payments(db, user.id, today, days=DUE_DAYS)
    yesterday = today - timedelta(days=1)
    txns = await store.load(db, user.id, yesterday, today)
    spent = [t for t in txns if money.spending(t)]
    safe = health["safe_to_spend"]
    step = health["next_step"]
    todo = [
        i for i in (await advice_store.view(db, user, today))["items"]
        if i["level"] == "act" and i["area"] != "life"
    ][:ADVICE_LINES]  # fmt: skip
    return {
        "advice": [{"title": i["title"], "url": i["url"]} for i in todo],
        "safe": {
            "known": safe["known"],
            "left_paise": safe["left_paise"],
            "per_day_paise": safe["per_day_paise"],
            "days_left": safe["days_left"],
            "status": safe["status"],
        },
        "due": [
            {
                "kind": u["kind"],
                "label": u["label"],
                "amount_paise": u["amount_paise"],
                "on": u["on"],
                "overdue": u["on"] < today,
            }
            for u in due
        ],
        "yesterday": {"spent_paise": sum(t.amount_paise for t in spent), "count": len(spent)},
        "verdict": health["verdict"],
        "next_step": (
            {"title": step["title"], "value": step["value"], "status": step["status"]}
            if step
            else None
        ),
    }


# --- The brief ---------------------------------------------------------------------------------


async def _guard(name: str, user_id: uuid.UUID, coro) -> tuple[Any, str | None]:
    try:
        return await coro, None
    except Exception as exc:
        log.warning("brief.part_failed", extra={"part": name, "user_id": str(user_id),
                                                "error": type(exc).__name__})  # fmt: skip
        return None, "unavailable"


async def build(
    db: AsyncSession,
    settings: Settings,
    user: User,
    prefs: BriefSettings,
    *,
    http: httpx.AsyncClient,
    redis: Redis | None = None,
    now: datetime | None = None,
) -> dict:
    """Everything the brief shows, as plain data. Parts that fail say so; the rest still show."""
    now = (now or utcnow()).astimezone(org.zone(user))
    today = now.date()
    parts = wanted(prefs)
    errors: dict[str, str] = {}
    out: dict[str, Any] = {
        "date": today,
        "greeting": f"{greeting(now.hour)}, {(user.name or '').split(' ')[0] or 'there'}",
        "generated_at": now.isoformat(),
        "sections": sorted(parts, key=SECTIONS.index),
        "weather": None,
        "place": prefs.place,
        "calendar": None,
        "tasks": None,
        "money": None,
    }
    jobs = {}
    if "weather" in parts and prefs.lat is not None and prefs.lon is not None:
        jobs["weather"] = weather(redis, http, prefs.lat, prefs.lon, org.zone(user), now)
    if "calendar" in parts:
        jobs["calendar"] = calendar(db, settings, user, http, now)
    # The calendar shares the session only to read and keep its token, before any await on
    # Google; the weather doesn't touch it. The database parts run after, one at a time.
    results = await asyncio.gather(*(_guard(k, user.id, v) for k, v in jobs.items()))
    for k, (value, err) in zip(jobs, results, strict=True):
        out[k] = value
        if err:
            errors[k] = err
    if "tasks" in parts:
        out["tasks"], err = await _guard("tasks", user.id, tasks(db, user, today))
        if err:
            errors["tasks"] = err
    if "money" in parts:
        out["money"], err = await _guard("money", user.id, money_lines(db, user, today))
        if err:
            errors["money"] = err
    out["errors"] = errors
    out["headline"] = headline(out)
    return out


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def headline(b: dict, *, amounts: bool = True, with_money: bool = True) -> str:
    """The day in one sentence. Without amounts for the phone's lock screen, and without money
    at all where the person keeps it out (Telegram, unless they chose otherwise)."""
    bits = []
    cal = b.get("calendar") or {}
    timed = [e for e in cal.get("events") or [] if not e["all_day"] and not e["past"]]
    if cal.get("connected"):
        if timed:
            bits.append(f"{_plural(len(timed), 'event')} left, next at {timed[0]['starts']}")
        elif not cal.get("events"):
            bits.append("a clear calendar")
    t = b.get("tasks")
    if t:
        due, late = len(t["due"]), len(t["overdue"])
        if due or late:
            bits.append(_plural(due, "task") + " due" + (f", {late} overdue" if late else ""))
    m = b.get("money") if with_money else None
    if m:
        safe = m["safe"]
        if amounts and safe["known"] and safe["left_paise"] >= 0:
            bits.append(f"{whole(safe['per_day_paise'])} a day safe to spend")
        elif safe["known"] and safe["left_paise"] < 0:
            bits.append("spending is past this month's safe amount")
        if m["due"]:
            bits.append(f"{_plural(len(m['due']), 'payment')} due soon")
    w = b.get("weather")
    if w and w.get("rain_from"):
        bits.append(f"rain likely from {w['rain_from']}")
    if not bits:
        return "Nothing pressing today."
    text = "; ".join(bits)
    return text[0].upper() + text[1:] + "."


def _due_word(day: date, today: date) -> str:
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    if day < today:
        return f"was due {day:%a %d %b}"
    return f"{day:%a %d %b}"


def as_text(b: dict, *, with_money: bool = True, url: str | None = None) -> str:
    """The brief as short Markdown, for Telegram and the assistant."""
    today: date = b["date"]
    lines = [
        f"**{b['greeting']}.** {today:%A %d %B}.",
        headline(b, with_money=with_money),
    ]
    w = b.get("weather")
    if w:
        place = f"{b['place'].split(',')[0]}: " if b.get("place") else ""
        line = f"{place}{w['low_c']} to {w['high_c']}°C"
        if w["sky"]:
            line += f", {w['sky']}"
        if w["rain_from"]:
            line += f". Rain likely from {w['rain_from']} ({w['rain_chance']}%), take an umbrella"
        line += "."
        if w["aqi"] is not None and w["aqi"] > 100:
            line += f" Air quality {w['air']} (AQI {w['aqi']})."
        lines += ["", line]
    cal = b.get("calendar")
    if cal and cal.get("connected"):
        lines += ["", "**Calendar**"]
        if not cal["events"]:
            lines.append("Nothing on the calendar.")
        for e in cal["events"]:
            when = "All day" if e["all_day"] else f"{e['starts']} to {e['ends']}"
            lines.append(f"- {when}: {e['title']}" + (" (done)" if e["past"] else ""))
    t = b.get("tasks")
    if t:
        rows = [
            *(f"- Overdue: {x['title']} ({_due_word(x['due_on'], today)})" for x in t["overdue"]),
            *(f"- Due today: {x['title']}" for x in t["due"]),
            *(f"- In progress: {x['title']}" for x in t["doing"]),
            *(f"- Focus this week: {x['title']}" for x in t["focus"]),
        ]
        if t["inbox"]:
            rows.append(f"- {_plural(t['inbox'], 'thing')} waiting in the Inbox")
        if t["monday"] and not t["review_done"]:
            rows.append("- Last week's review isn't done yet")
        if rows:
            lines += ["", "**Tasks**", *rows]
    m = b.get("money")
    if m and with_money:
        rows = []
        safe = m["safe"]
        if safe["known"]:
            if safe["left_paise"] >= 0:
                rows.append(
                    f"- Safe to spend: {whole(safe['left_paise'])} for the next "
                    f"{_plural(safe['days_left'], 'day')} ({whole(safe['per_day_paise'])} a day)"
                )
            else:
                rows.append(
                    f"- {whole(-safe['left_paise'])} past this month's safe amount, "
                    f"with {_plural(safe['days_left'], 'day')} to go"
                )
        for u in m["due"]:
            rows.append(f"- {u['label']}: {whole(u['amount_paise'])}, {_due_word(u['on'], today)}")
        y = m["yesterday"]
        if y["count"]:
            rows.append(
                f"- Yesterday: {whole(y['spent_paise'])} across {_plural(y['count'], 'payment')}"
            )
        for a in m.get("advice") or []:
            rows.append(f"- To sort out: {a['title']}")
        if m["next_step"] and m["next_step"]["status"] != "good":
            step = m["next_step"]
            rows.append(
                f"- Next for your safety: tell Money about your {step['title'].lower()}"
                if step["status"] == "unknown"
                else f"- Next for your safety: {step['title']} ({step['value']})"
            )
        if rows:
            lines += ["", "**Money**", *rows]
    if url:
        lines += ["", f"[Open the brief]({url})"]
    return "\n".join(lines)


def notice(b: dict) -> tuple[str, str]:
    """Title and body for the bell and the phone: no titles of events, no amounts."""
    return f"{b['greeting']}", headline(b, amounts=False)
