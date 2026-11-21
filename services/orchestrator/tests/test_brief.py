"""The morning brief: built from the calendar, weather, organiser and Money; sent once a day at
the person's time, to the bell and Telegram; and read by the assistant."""

import json
import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from nexus_common import brief as b
from nexus_common import money as m
from nexus_common import money_store as store
from nexus_common import organiser as org
from nexus_common.google import SERVICE_SCOPES, encrypt
from nexus_common.models import (
    BriefSettings,
    GoogleGrant,
    Notification,
    RoleName,
    TelegramLink,
    User,
    UserStatus,
    utcnow,
)
from nexus_orchestrator import brief
from nexus_orchestrator.tools import ToolContext, build_tools

IST = "Asia/Kolkata"
# Monday 23 November 2026, 7:32 am in India.
NOW = datetime(2026, 11, 23, 2, 2, tzinfo=UTC)


class Outside:
    """Google Calendar, Open-Meteo and Telegram in one fake. Records what was sent."""

    def __init__(self):
        self.telegram: list[dict] = []
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.paths.append(request.url.host + request.url.path)
        if "googleapis.com/calendar" in url:
            return httpx.Response(200, json={"items": [
                {"summary": "Design review", "start": {"dateTime": "2026-11-23T10:30:00+05:30"},
                 "end": {"dateTime": "2026-11-23T11:00:00+05:30"},
                 "hangoutLink": "https://meet.google.com/abc"},
                {"summary": "Diwali leave", "start": {"date": "2026-11-23"},
                 "end": {"date": "2026-11-24"}},
                {"summary": "Declined thing", "start": {"dateTime": "2026-11-23T12:00:00+05:30"},
                 "end": {"dateTime": "2026-11-23T13:00:00+05:30"},
                 "attendees": [{"self": True, "responseStatus": "declined"}]},
            ]})  # fmt: skip
        if "air-quality" in url:
            hours = [f"2026-11-23T{h:02d}:00" for h in range(24)]
            return httpx.Response(200, json={"hourly": {"time": hours, "us_aqi": [160] * 24}})
        if "open-meteo.com/v1/forecast" in url:
            return httpx.Response(200, json={
                "daily": {"temperature_2m_min": [18.6], "temperature_2m_max": [27.2],
                          "weather_code": [61], "precipitation_probability_max": [70]},
                "hourly": {"time": [f"2026-11-23T{h:02d}:00" for h in range(24)],
                           "temperature_2m": [20.0] * 24,
                           "precipitation_probability": [10] * 15 + [70] * 9},
            })  # fmt: skip
        if "/bot" in url:
            body = json.loads(request.content or b"{}")
            if url.endswith("/sendMessage"):
                self.telegram.append(body)
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(404, json={})


@pytest.fixture
async def owner(sessionmaker):
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        u = await create_user(
            db, email="shivam@example.com", role=RoleName.owner, status=UserStatus.active
        )
        u.timezone, u.name = IST, "Shivam Singh"
        db.add(
            GoogleGrant(
                user_id=u.id,
                scopes=f"openid {SERVICE_SCOPES['calendar']}",
                refresh_token="x",
                access_token=None,
                access_expires_at=None,
            )
        )
        await db.commit()
        return u.id


@pytest.fixture
async def outside(deps, settings, owner, sessionmaker, redis):
    stale = [k async for k in redis.scan_iter("nexus:brief:*")]
    if stale:
        await redis.delete(*stale)
    fake = Outside()
    deps.settings = settings.model_copy(
        update={"telegram_bot_token": "1:x", "telegram_webhook_secret": "s" * 32}
    )
    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    async with sessionmaker() as db:
        grant = await db.get(GoogleGrant, owner)
        grant.refresh_token = encrypt(deps.settings, "refresh-1")
        grant.access_token = encrypt(deps.settings, "access-1")
        grant.access_expires_at = utcnow() + timedelta(hours=1)
        await db.commit()
    yield fake
    await deps.google_http.aclose()


async def _setup(deps, sessionmaker, user_id, today=date(2026, 11, 23), **prefs):
    async with sessionmaker() as db:
        user = await db.get(User, user_id)
        await org.create(db, user, type="task", title="Renew passport",
                         due_on=today - timedelta(days=2))  # fmt: skip
        await org.create(db, user, type="task", title="Call the bank", due_on=today)
        await store.ingest(db, deps.settings, user_id, [
            m.Parsed(today - timedelta(days=1), 45000, "debit", "Swiggy", "UPI-SWIGGY"),
            m.Parsed(today - timedelta(days=3), 12500000, "credit", "Acme", "SALARY"),
        ], source="alert")  # fmt: skip
        db.add(
            BriefSettings(
                user_id=user_id, enabled=True, send_at="07:30", days="weekdays", push=True,
                telegram=True, telegram_money=False, place="Bengaluru, Karnataka, India",
                lat=12.97, lon=77.59, sections=[], **prefs,
            )
        )  # fmt: skip
        db.add(TelegramLink(user_id=user_id, tg_chat_id=777))
        await db.commit()


async def test_brief_goes_once_at_its_time_without_details_on_the_lock_screen(
    deps, owner, outside, sessionmaker, caplog
):
    caplog.set_level(logging.INFO)
    await _setup(deps, sessionmaker, owner)
    # 7:20 am: not yet.
    assert await brief.send_due(deps, NOW - timedelta(minutes=12)) == 0
    assert await brief.send_due(deps, NOW) == 1
    assert await brief.send_due(deps, NOW + timedelta(minutes=5)) == 0  # once a day

    async with sessionmaker() as db:
        (n,) = (await db.scalars(select(Notification))).all()
        assert n.url == "/brief" and n.title == "Good morning, Shivam"
        # The lock screen gets counts, never titles or amounts.
        assert "1 event left, next at 10:30 am" in n.body and "1 task due, 1 overdue" in n.body
        assert "Design review" not in n.body and "₹" not in n.body
        assert (await db.get(BriefSettings, owner)).last_sent_on == date(2026, 11, 23)

    (msg,) = outside.telegram
    text = msg["text"]
    assert "Design review" in text and "Renew passport" in text and "Bengaluru: 19 to 27°C" in text
    assert "Rain likely from 3 pm (70%)" in text and "Air quality unhealthy (AQI 160)" in text
    assert "Declined thing" not in text
    # Money stays off Telegram unless asked for.
    assert "Money" not in text and "₹" not in text

    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    for secret in ("Design review", "Renew passport", "Bengaluru", "Swiggy", "₹"):
        assert secret not in logged, secret


async def test_weekends_skip_and_money_on_telegram_when_asked(deps, owner, outside, sessionmaker):
    await _setup(deps, sessionmaker, owner)
    async with sessionmaker() as db:
        prefs = await db.get(BriefSettings, owner)
        prefs.telegram_money = True
        await db.commit()
    saturday = NOW - timedelta(days=2)
    assert await brief.send_due(deps, saturday) == 0
    # After the window (three hours late) it waits for tomorrow.
    assert await brief.send_due(deps, NOW + timedelta(hours=3, minutes=1)) == 0
    assert await brief.send_due(deps, NOW + timedelta(hours=1)) == 1
    text = outside.telegram[-1]["text"]
    assert "Money" in text and "Yesterday: ₹450 across 1 payment" in text


async def test_send_now_works_while_off_and_falls_back_to_the_bell(
    deps, owner, outside, sessionmaker
):
    # No settings saved, no Telegram: the bell still gets it.
    sent = await brief.deliver(deps, owner, NOW)
    assert sent == {"notice": True, "telegram": False}
    async with sessionmaker() as db:
        assert (await db.scalar(select(Notification))).url == "/brief"


async def test_a_failing_part_leaves_the_rest(deps, owner, outside, sessionmaker, monkeypatch):
    await _setup(deps, sessionmaker, owner)

    async def broken(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(b, "weather", broken)
    async with sessionmaker() as db:
        user = await db.get(User, owner)
        prefs = await db.get(BriefSettings, owner)
        got = await b.build(db, deps.settings, user, prefs, http=deps.google_http, now=NOW)
    assert got["weather"] is None and got["errors"] == {"weather": "unavailable"}
    assert [t["title"] for t in got["tasks"]["overdue"]] == ["Renew passport"]
    assert got["money"]["yesterday"] == {"spent_paise": 45000, "count": 1}


async def test_assistant_reads_the_brief(deps, owner, outside, sessionmaker):
    async with sessionmaker() as db:
        today = org.local_today(await db.get(User, owner))
    await _setup(deps, sessionmaker, owner, today)

    async def emit(*args, **kwargs):
        pass

    ctx = ToolContext(deps=deps, user_id=owner, chat_id=None, emit=emit)
    tools = {t.name: t for t in build_tools(ctx, ["brief_today"])}
    text = await tools["brief_today"].ainvoke({})
    assert "Design review" in text and "Call the bank" in text and "**Money**" in text

    async with sessionmaker() as db:
        prefs = await store.settings_for(db, owner)
        prefs.assistant_access = False
        await db.commit()
    text = await tools["brief_today"].ainvoke({})
    assert "**Money**" not in text and "switched off" in text
