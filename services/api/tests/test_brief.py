"""The morning brief's page, settings and test send; and the parts that are plain functions."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from nexus_api.routes import brief as brief_routes
from nexus_common import brief
from nexus_common import money as m
from nexus_common import money_store as store
from nexus_common import organiser as org
from nexus_common.models import BriefSettings, User

IST = ZoneInfo("Asia/Kolkata")


# --- Plain parts -------------------------------------------------------------------------------


def test_weather_reads_the_range_rain_and_air():
    now = datetime(2026, 11, 23, 8, 15, tzinfo=IST)
    hours = [f"2026-11-23T{h:02d}:00" for h in range(24)]
    fc = {
        "daily": {"temperature_2m_min": [18.4], "temperature_2m_max": [27.6],
                  "weather_code": [80], "precipitation_probability_max": [80]},
        "hourly": {"time": hours, "temperature_2m": list(range(24)),
                   # Rain at 6 am is past; the next likely rain is 2 pm.
                   "precipitation_probability": [0] * 6 + [90] + [0] * 7 + [60] + [0] * 9},
    }  # fmt: skip
    air = {"hourly": {"time": hours, "us_aqi": [300] * 8 + [40] * 16}}
    w = brief.read_weather(fc, air, now)
    assert (w["low_c"], w["high_c"], w["now_c"], w["sky"]) == (18, 28, 8, "showers")
    assert w["rain_from"] == "2 pm" and w["rain_chance"] == 80
    # Only the hours still ahead count for the air.
    assert (w["aqi"], w["air"]) == (40, "good")
    assert brief.read_weather({}, None, now) is None
    assert brief.air_label(151) == "unhealthy" and brief.air_label(999) == "hazardous"


def test_events_skip_cancelled_and_declined_and_put_all_day_first():
    now = datetime(2026, 11, 23, 11, 30, tzinfo=IST)
    items = [
        {"summary": "Standup", "start": {"dateTime": "2026-11-23T10:00:00+05:30"},
         "end": {"dateTime": "2026-11-23T10:15:00+05:30"}},
        {"summary": "Lunch", "start": {"dateTime": "2026-11-23T07:30:00Z"},
         "end": {"dateTime": "2026-11-23T08:30:00Z"}, "location": "  Toit,  Indiranagar ",
         "conferenceData": {"entryPoints": [{"entryPointType": "video",
                                             "uri": "https://zoom.us/j/1"}]},
         "attendees": [{"self": True, "responseStatus": "accepted"}, {"email": "a@b.c"}]},
        {"summary": "Holiday", "start": {"date": "2026-11-23"}, "end": {"date": "2026-11-24"}},
        {"summary": "Gone", "status": "cancelled", "start": {"date": "2026-11-23"}},
        {"summary": "No", "start": {"dateTime": "2026-11-23T15:00:00+05:30"},
         "end": {"dateTime": "2026-11-23T16:00:00+05:30"},
         "attendees": [{"self": True, "responseStatus": "declined"}]},
        {"start": {"dateTime": "not a time"}},
    ]  # fmt: skip
    got = brief.read_events(items, IST, now)
    assert [e["title"] for e in got] == ["Holiday", "Standup", "Lunch"]
    standup, lunch = got[1], got[2]
    assert standup["past"] and standup["starts"] == "10 am" and standup["ends"] == "10:15 am"
    assert (lunch["starts"], lunch["where"], lunch["others"]) == ("1 pm", "Toit, Indiranagar", 1)
    assert lunch["video"] == "https://zoom.us/j/1" and not lunch["past"]


def test_due_now_respects_days_window_and_once_a_day():
    user = User(timezone="Asia/Kolkata")
    prefs = BriefSettings(enabled=True, send_at="07:30", days="weekdays", last_sent_on=None)
    monday = datetime(2026, 11, 23, 7, 30, tzinfo=IST)
    assert brief.due_now(prefs, user, monday.astimezone(UTC))
    assert not brief.due_now(prefs, user, monday - timedelta(minutes=1))
    assert brief.due_now(prefs, user, monday + timedelta(hours=2, minutes=59))
    assert not brief.due_now(prefs, user, monday + timedelta(hours=3))
    assert not brief.due_now(prefs, user, monday - timedelta(days=1))  # Sunday
    prefs.days = "every"
    assert brief.due_now(prefs, user, monday - timedelta(days=1))
    prefs.last_sent_on = date(2026, 11, 23)
    assert not brief.due_now(prefs, user, monday)
    prefs.last_sent_on, prefs.enabled = None, False
    assert not brief.due_now(prefs, user, monday)
    assert brief.parse_send_at("bad") == time(7, 30)


def test_text_and_notice():
    b = {
        "date": date(2026, 11, 23),
        "greeting": "Good morning, Shivam",
        "place": "Pune, Maharashtra, India",
        "weather": {"low_c": 14, "high_c": 29, "sky": "clear", "rain_from": None,
                    "rain_chance": 0, "aqi": 60, "air": "moderate"},
        "calendar": {"connected": True, "events": []},
        "tasks": {"overdue": [], "due": [], "doing": [], "focus": [], "inbox": 2,
                  "review_done": False, "monday": True},
        "money": {
            "safe": {"known": True, "left_paise": -250000, "per_day_paise": 0, "days_left": 8,
                     "status": "act"},
            "due": [{"kind": "card", "label": "HDFC card ••1234 bill", "amount_paise": 820050,
                     "on": date(2026, 11, 21), "overdue": True}],
            "yesterday": {"spent_paise": 0, "count": 0},
            "verdict": "act",
            "next_step": {"title": "Emergency fund", "value": "1.2 months", "status": "act"},
        },
    }  # fmt: skip
    text = brief.as_text(b, url="https://nexus.example/brief")
    assert "Pune: 14 to 29°C, clear." in text and "AQI" not in text  # moderate air: no line
    assert "Nothing on the calendar." in text and "2 things waiting in the Inbox" in text
    assert "Last week's review isn't done yet" in text
    assert "₹2,500 past this month's safe amount, with 8 days to go" in text
    assert "HDFC card ••1234 bill: ₹8,201, was due Sat 21 Nov" in text
    assert "Next for your safety: Emergency fund (1.2 months)" in text
    assert text.endswith("[Open the brief](https://nexus.example/brief)")
    assert "Money" not in brief.as_text(b, with_money=False)
    title, body = brief.notice(b)
    assert title == "Good morning, Shivam"
    assert (
        body == "A clear calendar; spending is past this month's safe amount; 1 payment due soon."
    )
    assert brief.headline({"tasks": None}) == "Nothing pressing today."


# --- The API -----------------------------------------------------------------------------------


@pytest.fixture
def outside(app):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": [
                {"name": "Bengaluru", "admin1": "Karnataka", "country": "India",
                 "latitude": 12.97194, "longitude": 77.59369},
                {"name": "Broken"},
            ]})  # fmt: skip
        return httpx.Response(500)

    async def fake():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            yield http

    app.dependency_overrides[brief_routes.outside_http] = fake
    yield calls
    app.dependency_overrides.pop(brief_routes.outside_http, None)


async def _user(db, email="owner@example.com") -> User:
    return await db.scalar(select(User).where(User.email == email))


async def test_today_page_for_owner_and_member(
    owner, client_for, invite, outside, sessionmaker, settings
):
    async with sessionmaker() as db:
        user = await _user(db)
        user.timezone = "Asia/Kolkata"
        today = org.local_today(user)
        await org.create(db, user, type="task", title="File taxes", due_on=today)
        await store.ingest(db, settings, user.id, [
            m.Parsed(today, 12500000, "credit", "Acme", "SALARY")], source="alert")  # fmt: skip
        await db.commit()

    got = (await owner.get("/api/brief")).json()
    b = got["brief"]
    assert [t["title"] for t in b["tasks"]["due"]] == ["File taxes"]
    # Not connected: no calendar, not an error. No place: no weather call at all.
    assert b["calendar"] == {"connected": False, "events": []} and b["weather"] is None
    assert b["money"]["safe"]["known"] and got["money_available"]
    assert got["settings"]["enabled"] is False and got["settings"]["sections"] == list(
        brief.SECTIONS
    )
    assert outside == []

    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    mine = (await friend.get("/api/brief")).json()
    assert mine["brief"]["money"] is None and not mine["money_available"]
    assert mine["brief"]["tasks"]["due"] == []


async def test_settings_save_partly_and_check_the_place(owner, outside, sessionmaker):
    found = (await owner.get("/api/brief/places", params={"q": "Bengal"})).json()["places"]
    assert found == [{"name": "Bengaluru", "region": "Karnataka, India", "lat": 12.9719,
                      "lon": 77.5937}]  # fmt: skip
    assert (await owner.get("/api/brief/places", params={"q": "B"})).json()["places"] == []

    half = await owner.put("/api/brief/settings", json={"place": "Bengaluru"})
    assert half.status_code == 422
    resp = await owner.put(
        "/api/brief/settings",
        json={"enabled": True, "send_at": "23:59", "days": "weekdays", "telegram_money": True,
              "place": "Bengaluru, Karnataka, India", "lat": 12.97, "lon": 77.59,
              "sections": ["tasks", "weather", "tasks"]},
    )  # fmt: skip
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["sections"] == ["weather", "tasks"] and out["days"] == "weekdays"
    assert (await owner.put("/api/brief/settings", json={"send_at": "7:30"})).status_code == 422
    # Partial: only what's sent changes.
    out = (await owner.put("/api/brief/settings", json={"push": False})).json()
    assert out["enabled"] and out["place"].startswith("Bengaluru") and not out["push"]
    cleared = await owner.put("/api/brief/settings", json={"place": None, "lat": None, "lon": None})
    assert cleared.json()["place"] is None

    # Switched on after today's time has passed: the first brief is tomorrow's.
    await owner.put("/api/brief/settings", json={"send_at": "00:00"})
    async with sessionmaker() as db:
        user = await _user(db)
        prefs = await db.get(BriefSettings, user.id)
        assert prefs.last_sent_on == org.local_today(user)


async def test_send_now_is_queued_and_limited(owner, queue, client_for, invite):
    for _ in range(brief_routes.SENDS_PER_HOUR):
        assert (await owner.post("/api/brief/send")).status_code == 202
    assert len(queue.briefs) == brief_routes.SENDS_PER_HOUR
    assert (await owner.post("/api/brief/send")).status_code == 429
