"""Money's background work: reading alert emails, valuing funds, and the assistant's tool."""

import base64
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from nexus_common import money as m
from nexus_common import money_store as store
from nexus_common.google import SERVICE_SCOPES, encrypt
from nexus_common.models import (
    GoogleGrant,
    MoneyHolding,
    MoneySettings,
    MoneyTxn,
    RoleName,
    UserStatus,
    utcnow,
)
from nexus_orchestrator import money
from nexus_orchestrator.tools import ToolContext, build_tools

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


async def grant(sessionmaker, settings, user_id, services):
    async with sessionmaker() as db:
        db.add(
            GoogleGrant(
                user_id=user_id,
                scopes=" ".join(["openid", *(SERVICE_SCOPES[s] for s in services)]),
                refresh_token=encrypt(settings, "refresh-1"),
                access_token=encrypt(settings, "access-1"),
                access_expires_at=utcnow() + timedelta(hours=1),
            )
        )
        await db.commit()


class Google:
    """A fake Google: routes by (method, url), records each request."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.routes.get((request.method, str(request.url).split("?")[0]))
        if body is None:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        return httpx.Response(200, json=body)

    def sent(self, method: str, url: str) -> httpx.Request:
        return next(r for r in self.requests if r.method == method and str(r.url).startswith(url))


@pytest.fixture
async def user(sessionmaker):
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        u = await create_user(
            db, email="shivam@example.com", role=RoleName.owner, status=UserStatus.active
        )
        await db.commit()
        return u.id


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _mail(mid: str, subject: str, body: str, sender: str, when: datetime) -> dict:
    return {
        "id": mid,
        "internalDate": str(int(when.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}],
            "body": {"data": _b64(body)},
        },
    }


MAILS = {
    "a1": _mail(
        "a1",
        "You have done a UPI txn",
        "Rs.250.00 has been debited from account **1234 to VPA swiggy@icici SWIGGY on 18-11-26. "
        "Your UPI transaction reference number is 432112345678.",
        "HDFC Bank InstaAlerts <alerts@hdfcbank.net>",
        datetime(2026, 11, 18, 13, 0, tzinfo=UTC),
    ),
    "a2": _mail(
        "a2",
        "OTP",
        "Your OTP for transaction of Rs 2,000.00 at AMAZON on card XX1234 is 123456.",
        "alerts@hdfcbank.net",
        datetime(2026, 11, 18, 14, 0, tzinfo=UTC),
    ),
    "a3": _mail(
        "a3",
        "Transaction alert",
        "INR 560.00 debited from A/c no. XX3456 on 16-11-2026 at BLINKIT. Avl Bal- INR 12,345.67",
        "alerts@axisbank.com",
        datetime(2026, 11, 16, 9, 0, tzinfo=UTC),
    ),
}


def fake_gmail() -> Google:
    routes = {("GET", f"{GMAIL}/messages"): {"messages": [{"id": k} for k in MAILS]}}
    for k, v in MAILS.items():
        routes[("GET", f"{GMAIL}/messages/{k}")] = v
    return Google(routes)


async def switch_on(sessionmaker, user_id, **extra):
    async with sessionmaker() as db:
        prefs = await store.settings_for(db, user_id)
        prefs.gmail_sync = True
        for k, v in extra.items():
            setattr(prefs, k, v)
        await db.commit()


async def test_sync_reads_alerts_once_and_logs_no_details(
    deps, user, sessionmaker, settings, caplog
):
    caplog.set_level(logging.INFO)
    assert await money.sync_user(deps, user) is None  # switched off
    await grant(sessionmaker, settings, user, ["gmail"])
    await switch_on(sessionmaker, user)
    google = fake_gmail()
    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(google))

    counts = await money.sync_user(deps, user)
    assert counts == {"messages": 3, "added": 2, "matched": 0, "skipped": 0}
    query = google.sent("GET", f"{GMAIL}/messages").url.params["q"]
    assert "debited" in query and "after:" in query

    async with sessionmaker() as db:
        rows = (await db.scalars(select(MoneyTxn).order_by(MoneyTxn.occurred_on))).all()
        assert [(r.merchant, r.amount_paise, r.source_ref) for r in rows] == [
            ("Blinkit", 56000, "a3"),
            ("Swiggy", 25000, "a1"),
        ]
        prefs = await db.get(MoneySettings, user)
        assert prefs.gmail_synced_until == datetime(2026, 11, 18, 14, 0, tzinfo=UTC)
        assert prefs.last_error is None

    # The next run reads from a day before the last message, and adds nothing twice.
    again = await money.sync_user(deps, user)
    assert again["added"] == 0 and again["skipped"] == 2

    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    for secret in ("Swiggy", "swiggy", "Blinkit", "25000", "hdfcbank", "UPI txn"):
        assert secret not in logged, secret


async def test_sync_without_gmail_records_why(deps, user, sessionmaker):
    await switch_on(sessionmaker, user)
    assert await money.sync_user(deps, user) is None
    async with sessionmaker() as db:
        prefs = await db.get(MoneySettings, user)
        assert "Gmail" in prefs.last_error


async def test_holdings_are_valued_at_the_latest_nav(deps, user, sessionmaker):
    async with sessionmaker() as db:
        holding = m.Holding(
            folio="123",
            amc="PPFAS",
            scheme="Parag Parikh Flexi Cap - Direct Growth",
            isin="INF879O01027",
            amfi_code="122639",
            units=Decimal("100.5"),
            nav=Decimal("90"),
            nav_date=date(2026, 11, 1),
            value_paise=904500,
            cost_paise=700000,
        )
        assert await store.save_holdings(db, user, [holding], date(2026, 11, 1)) == 1
        # An older statement doesn't replace a newer one.
        assert await store.save_holdings(db, user, [], date(2026, 10, 1)) == 0
        await db.commit()
    navs = m.parse_amfi_navs(
        "122639;INF879O01027;-;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;93.5;20-Nov-2026"
    )
    assert await money.revalue_holdings(deps, navs) == 1
    async with sessionmaker() as db:
        h = (await db.scalars(select(MoneyHolding))).one()
        assert h.nav == Decimal("93.5") and h.nav_date == date(2026, 11, 20)
        assert h.value_paise == 939675  # 100.5 x 93.5 = 9396.75


async def test_assistant_reads_totals_only_when_allowed(deps, user, sessionmaker, settings):
    async def emit(*args, **kwargs):
        pass

    ctx = ToolContext(deps=deps, user_id=user, chat_id=None, emit=emit)
    tool = {t.name: t for t in build_tools(ctx, ["money_summary"])}["money_summary"]
    assert "hasn't let the assistant" in await tool.ainvoke({})

    async with sessionmaker() as db:
        rows = [
            m.Parsed(date(2026, 11, 3), 45000, "debit", "Swiggy", "UPI-SWIGGY"),
            m.Parsed(date(2026, 11, 4), 12500000, "credit", "Acme", "SALARY"),
        ]
        await store.ingest(db, settings, user, rows, source="statement")
        prefs = await store.settings_for(db, user)
        prefs.assistant_access = True
        await db.commit()
    out = await tool.ainvoke({"month": "2026-11"})
    assert "Spent ₹450.00" in out and "income ₹1,25,000.00" in out
    assert "Food and dining ₹450.00" in out
    assert "UPI-SWIGGY" not in out  # never the bank's narration
