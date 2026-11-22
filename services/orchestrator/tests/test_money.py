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
    User,
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
    assert counts == {
        "messages": 3, "added": 2, "matched": 0, "skipped": 0, "bills": 0, "emis": 0,
        "orders": 0, "orders_matched": 0}  # fmt: skip
    query = google.sent("GET", f"{GMAIL}/messages").url.params["q"]
    assert "debited" in query and "after:" in query and "from:hdfcbank" in query
    assert "-category:promotions" not in query

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

    # Reading a year back asks Gmail for older mail but never moves the mark backwards.
    google.requests.clear()
    back = await money.sync_user(deps, user, back_days=365)
    assert back["added"] == 0
    after = int(
        google.sent("GET", f"{GMAIL}/messages").url.params["q"].split("after:")[1].split()[0]
    )
    assert after < (datetime(2026, 11, 18, tzinfo=UTC) - timedelta(days=300)).timestamp()
    async with sessionmaker() as db:
        prefs = await db.get(MoneySettings, user)
        assert prefs.gmail_synced_until == datetime(2026, 11, 18, 14, 0, tzinfo=UTC)

    # The search itself names banks and shops in general (money.ALERT_SENDERS,
    # money_orders.SHOPS) in the HTTP client's request lines; what must not show in Nexus's own
    # logs is anything from the person's mail: merchants, amounts, the sender's address.
    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records if r.name != "httpx")
    for secret in ("Swiggy", "swiggy", "Blinkit", "25000", "alerts@hdfcbank.net", "UPI txn"):
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


async def test_shares_are_priced_at_nse_close_and_the_list_cached(
    deps, user, sessionmaker, monkeypatch
):
    from nexus_common import money_stocks
    from nexus_common.models import MoneyStock

    async with sessionmaker() as db:
        db.add(MoneyStock(user_id=user, symbol="TCS", name="TCS LTD", quantity=Decimal("2")))
        await db.commit()
    day = date(2026, 11, 20)
    quotes = {"TCS": money_stocks.Quote("TCS", "TCS LTD", "INE467B01029", "EQ", 3_100_50, day)}

    async def fetched(today, client=None):
        return quotes

    monkeypatch.setattr(money_stocks, "fetch_quotes", fetched)
    assert await money.reprice_stocks(deps, today=day) == 1
    async with sessionmaker() as db:
        s = (await db.scalars(select(MoneyStock))).one()
        assert (s.price_paise, s.price_on) == (3_100_50, day)
    # The add dialog's search reads the same list without asking NSE again.
    assert money_stocks.load(await deps.redis.get(money_stocks.CACHE_KEY)) == quotes


async def test_share_history_is_filled_from_nse_and_recorded_nightly(
    deps, user, sessionmaker, monkeypatch
):
    import io
    import zipfile

    from nexus_common import money_history
    from nexus_common.models import MoneySnapshot, MoneyStock

    monkeypatch.setattr(money, "FILL_PAUSE", 0)
    today = date(2026, 11, 23)  # a Monday
    head = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,FinInstrmNm,ClsPric"
    asked: list[str] = []
    down = {"on": False}

    def serve(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if down["on"]:
            return httpx.Response(503)
        name = request.url.path.rsplit("/", 1)[-1]
        if "BhavCopy" not in name:
            return httpx.Response(404)
        stamp = name.split("_")[6]
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        if day.day == 1:  # a holiday on the 1st: the next trading day's file is used
            return httpx.Response(404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("b.csv", f"{head}\n{day},{day},CM,NSE,STK,1,INE844O01030,GUJGASLTD,EQ,"
                                f"GUJARAT GAS LTD,{400 + day.month}")  # fmt: skip
        return httpx.Response(200, content=buf.getvalue())

    client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    async with sessionmaker() as db:
        db.add(MoneyStock(
            user_id=user, symbol="GUJENERGY", name="GUJARAT ENERGY LTD", isin="INE844O01030",
            quantity=Decimal("10"), avg_price_paise=307_10, bought_on=date(2026, 6, 1),
            price_paise=226_40, price_on=date(2026, 11, 20)))  # fmt: skip
        await db.commit()

    stored = await money.fill_share_history(deps, user, today=today, client=client)
    days = money_history.fill_days(date(2026, 6, 1), today)
    # The weekly days fall on Sundays and move to Monday; the last Monday is today, which is
    # the live price, so it's left out.
    assert stored == len(days) - 1
    async with sessionmaker() as db:
        rows = (await db.scalars(select(MoneySnapshot).order_by(MoneySnapshot.day))).all()
        s = (await db.scalars(select(MoneyStock))).one()
        assert s.history_from == date(2026, 6, 1)
    assert rows[0].day == date(2026, 6, 2)  # 1 June had no file
    assert rows[0].value_paise == 10 * 406_00 and rows[0].cost_paise == 10 * 307_10
    assert all(r.filled for r in rows)

    # Done: nothing is fetched again.
    asked.clear()
    assert await money.fill_share_history(deps, user, today=today, client=client) == 0
    assert asked == []

    # NSE failing part way keeps what came in, and leaves the share to try again tonight.
    async with sessionmaker() as db:
        s = (await db.scalars(select(MoneyStock))).one()
        s.history_from, s.bought_on = None, date(2025, 1, 1)
        await db.commit()
    down["on"] = True
    await money.fill_share_history(deps, user, today=today, client=client)
    async with sessionmaker() as db:
        assert (await db.scalars(select(MoneyStock))).one().history_from is None

    # The nightly run stores today's value next to the filled-in ones.
    assert await money.record_history(deps) == 1
    async with sessionmaker() as db:
        latest = (await db.scalars(select(MoneySnapshot).where(
            MoneySnapshot.day == date(2026, 11, 20)))).one()  # fmt: skip
        assert (latest.value_paise, latest.filled) == (10 * 226_40, False)
    await client.aclose()


async def test_assistant_uses_money_unless_switched_off(deps, user, sessionmaker, settings):
    async def emit(*args, **kwargs):
        pass

    ctx = ToolContext(deps=deps, user_id=user, chat_id=None, emit=emit)
    names = ["money_summary", "money_transactions", "money_accounts", "money_subscriptions",
             "money_subscription_update", "money_plan", "money_health"]  # fmt: skip
    tools = {t.name: t for t in build_tools(ctx, names)}
    assert set(names) <= set(tools)

    async with sessionmaker() as db:
        rows = [
            m.Parsed(date(2026, 11, 3), 45000, "debit", "Swiggy", "UPI-SWIGGY"),
            m.Parsed(date(2026, 11, 4), 12500000, "credit", "Acme", "SALARY"),
        ] + [
            m.Parsed(date(2026, mo, 5), 64900, "debit", "Netflix", f"NETFLIX {mo}")
            for mo in (8, 9, 10, 11)
        ]
        await store.ingest(db, settings, user, rows, source="statement")
        await db.commit()
    # On by default.
    out = await tools["money_summary"].ainvoke({"month": "2026-11"})
    assert "Spent ₹1,099.00" in out and "income ₹1,25,000.00" in out
    assert "Food and dining ₹450.00" in out
    assert "UPI-SWIGGY" not in out  # never the bank's narration

    out = await tools["money_transactions"].ainvoke({"search": "swig"})
    assert "Swiggy [Food and dining]" in out and "UPI-SWIGGY" not in out
    assert "Netflix" not in out

    out = await tools["money_subscriptions"].ainvoke({})
    assert "Netflix (subscription, new)" in out
    out = await tools["money_subscription_update"].ainvoke(
        {"merchant": "netflix", "status": "cancelled"}
    )
    assert out.startswith("Saved: Netflix")
    assert "Netflix (subscription, cancelled)" in await tools["money_subscriptions"].ainvoke({})

    assert "Totals:" in await tools["money_accounts"].ainvoke({})
    plan = await tools["money_plan"].ainvoke({"months": 12, "try_monthly_sip": 5000})
    assert "Net worth now" in plan and "new SIP ₹5,000.00/month" in plan
    health = await tools["money_health"].ainvoke({})
    assert "Overall:" in health and "Emergency fund [unknown]" in health
    assert "Health insurance [unknown]" in health and "UPI-SWIGGY" not in health

    async with sessionmaker() as db:
        prefs = await store.settings_for(db, user)
        prefs.assistant_access = False
        await db.commit()
    for name in names:
        args = {"merchant": "Netflix", "status": "active"} if name.endswith("update") else {}
        assert "switched off" in await tools[name].ainvoke(args)


async def test_card_bills_emi_emails_and_reminders(deps, user, sessionmaker, monkeypatch):
    when = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
    mails = {
        "b1": _mail(
            "b1",
            "Your HDFC Bank Credit Card Statement for November 2026",
            "Your HDFC Bank Credit Card ending 5678 statement is ready. Total Amount Due: Rs. "
            "24,512.00 Minimum Amount Due: Rs. 1,230.00 Payment Due Date: 21/11/2026",
            "emailstatements.cards@hdfcbank.net",
            when,
        ),
        "e1": _mail(
            "e1",
            "Your SmartEMI request is processed",
            "Your transaction of Rs 54,990.00 at SONY INDIA on HDFC Bank Credit Card ending 5678 "
            "has been successfully converted to EMI for a tenure of 12 months at 15% p.a.",
            "alerts@hdfcbank.net",
            when,
        ),
    }
    routes = {("GET", f"{GMAIL}/messages"): {"messages": [{"id": k} for k in mails]}}
    for k, v in mails.items():
        routes[("GET", f"{GMAIL}/messages/{k}")] = v
    fake = Google(routes)
    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    await grant(sessionmaker, deps.settings, user, ["gmail"])
    await switch_on(sessionmaker, user)
    counts = await money.sync_user(deps, user)
    assert (counts["bills"], counts["emis"], counts["added"]) == (1, 1, 0)

    async with sessionmaker() as db:
        accounts = await store.accounts_view(db, user, date(2026, 11, 19))
        (card,) = accounts["items"]
        assert (card["due_paise"], card["bill"]["status"]) == (2451200, "due")
        loans = await store.loans_view(db, user, date(2026, 11, 19))
        assert loans["items"][0]["months"] == 12

    async with sessionmaker() as db:
        (await db.get(User, user)).timezone = "Asia/Kolkata"
        await db.commit()
    # 9 am in Kolkata is 03:30 UTC; the bill is due in two days.
    nine = datetime(2026, 11, 19, 3, 30, tzinfo=UTC)
    assert await money.send_reminders(deps, nine) == 1
    assert await money.send_reminders(deps, nine) == 0  # once only
    assert await money.send_reminders(deps, nine + timedelta(hours=1)) == 0


def test_history_read_says_what_it_found():
    from nexus_orchestrator.money import history_note

    base = {"messages": 0, "added": 0, "matched": 0, "skipped": 0, "bills": 0, "emis": 0}
    title, body = history_note(12, base, None)
    assert title == "No bank alert emails found" and "12 months" in body
    title, body = history_note(12, {**base, "messages": 40}, None)
    assert "none could be understood" in title and "40 possible" in body
    title, body = history_note(
        6, {**base, "messages": 50, "added": 30, "skipped": 5, "bills": 2}, None
    )
    assert title == "Read 6 months of alert emails"
    assert body.startswith("30 new transactions, 5 already in Money, 2 card bills.")
    assert "13 other emails" in body
    title, body = history_note(6, None, "Gmail isn't connected.")
    assert title.startswith("Couldn't") and "Gmail isn't connected." in body


async def test_alerts_from_another_mailbox(deps, user, sessionmaker, settings, caplog):
    """Bank alerts that go to a different Gmail: read with that mailbox's own token, with no
    Gmail on the sign-in account at all. Its address never reaches the logs."""
    from nexus_common import money_mail
    from nexus_common.models import MoneyMailbox

    caplog.set_level(logging.INFO)
    await switch_on(sessionmaker, user)
    async with sessionmaker() as db:
        box = await money_mail.save(db, settings, user, "Alerts.Person@gmail.com", {
            "scope": f"openid email {money_mail.SCOPE}", "refresh_token": "box-refresh",
            "access_token": "box-access", "expires_in": 3600,
        })  # fmt: skip
        await db.commit()
        assert box.email_masked == "al•••@gmail.com"
        assert "Alerts" not in box.email_enc
    google = fake_gmail()
    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(google))

    counts = await money.sync_user(deps, user)
    assert counts["added"] == 2
    assert google.sent("GET", f"{GMAIL}/messages").headers["authorization"] == "Bearer box-access"
    async with sessionmaker() as db:
        box = await db.scalar(select(MoneyMailbox))
        assert box.synced_until == datetime(2026, 11, 18, 14, 0, tzinfo=UTC)
        prefs = await db.get(MoneySettings, user)
        assert prefs.last_error is None and prefs.gmail_synced_at is not None

    # Google refusing the mailbox's sign-in is noted on it; the sync says it got nothing.
    async with sessionmaker() as db:
        box = await db.scalar(select(MoneyMailbox))
        box.access_expires_at = utcnow() - timedelta(minutes=5)
        await db.commit()

    def refusing(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return google(request)

    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(refusing))
    assert await money.sync_user(deps, user) is None
    async with sessionmaker() as db:
        box = await db.scalar(select(MoneyMailbox))
        assert box.last_error == money_mail.REFUSED

    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert "alerts.person" not in logged.lower()


async def test_assistant_reads_advice_and_one_counted_notice(deps, user, sessionmaker, settings,
                                                             monkeypatch):  # fmt: skip
    """`advice_list` reads the Advice page as text and leaves Money out once access is off;
    a new "do now" item sends one notice, with a count only, and only once."""
    from nexus_common import advice_store
    from nexus_common.models import Notification

    async def emit(*args, **kwargs):
        pass

    ctx = ToolContext(deps=deps, user_id=user, chat_id=None, emit=emit)
    tool = {t.name: t for t in build_tools(ctx, ["advice_list"])}["advice_list"]
    async with sessionmaker() as db:
        rows = [
            m.Parsed(date(2026, 11, 2), 64900, "debit", "Netflix", "NETFLIX A"),
            m.Parsed(date(2026, 11, 2), 64900, "debit", "Netflix", "NETFLIX B"),
        ]
        await store.ingest(db, settings, user, rows, source="statement")
        await db.commit()
    out = await tool.ainvoke({})
    assert out.startswith("<advice>") or out == "Nothing needs a decision right now."
    assert "NETFLIX A" not in out  # never the bank's narration

    async with sessionmaker() as db:
        prefs = await store.settings_for(db, user)
        prefs.assistant_access = False
        await db.commit()
    off = await tool.ainvoke({})
    assert "Netflix" not in off

    items = [
        {"key": "card_overdue:c1:2026-11", "level": "act", "title": "Pay the HDFC card bill"},
        {"key": "forex:s1:2026-11", "level": "look", "title": "Foreign charge on a card"},
    ]

    async def fake_view(db, u, today, **kw):
        return {"items": items, "counts": {}, "hidden": 0, "set_aside": [], "money": True}

    monkeypatch.setattr(advice_store, "view", fake_view)
    async with sessionmaker() as db:
        me = await db.get(User, user)
        assert await money._tell_advice(deps, db, me, date(2026, 11, 21)) == 1
        assert await money._tell_advice(deps, db, me, date(2026, 11, 21)) == 0  # told once
        await db.commit()
        (notice,) = (await db.scalars(select(Notification))).all()
    assert notice.title == "1 new thing to sort out" and notice.url == "/advice"
    assert "HDFC" not in f"{notice.title} {notice.body}"


async def test_order_emails_name_payments_and_record_cash(deps, user, sessionmaker, caplog):
    """The hourly read also reads shop order emails: an order names the bank payment it
    matches; a cash-on-delivery order becomes a cash transaction. Logs carry counts only."""
    caplog.set_level(logging.INFO)
    when = datetime(2026, 11, 18, 13, 0, tzinfo=UTC)
    alert = _mail(
        "a1", "You have done a UPI txn",
        "Rs.452.50 has been debited from account **1234 to VPA payu@hdfcbank PAYU on 18-11-26. "
        "Your UPI transaction reference number is 432112345678.",
        "HDFC Bank InstaAlerts <alerts@hdfcbank.net>", when,
    )  # fmt: skip
    swiggy = _mail(
        "o1", "Your Swiggy order was delivered",
        "Order No: 1234567890 Item Total ₹420 Paid Via UPI Order Total ₹ 452.50",
        "Swiggy <noreply@swiggy.in>", when,
    )  # fmt: skip
    dominos = _mail(
        "o2", "Order Successful",
        "Order No. 232 Order Total Rs.495.00 Payment Mode Cash Grand Total : Rs.495.00",
        "Dominos India <do-not-reply@dominos.co.in>", when,
    )  # fmt: skip
    mails = {"a1": alert, "o1": swiggy, "o2": dominos}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == f"{GMAIL}/messages":
            q = request.url.params.get("q", "")
            ids = ["o1", "o2"] if "from:dominos" in q else ["a1"] if "debited" in q else []
            return httpx.Response(200, json={"messages": [{"id": i} for i in ids]})
        mid = url.rsplit("/", 1)[-1]
        return httpx.Response(200, json=mails[mid]) if mid in mails else httpx.Response(404)

    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await grant(sessionmaker, deps.settings, user, ["gmail"])
    await switch_on(sessionmaker, user)
    counts = await money.sync_user(deps, user)
    assert (counts["messages"], counts["added"], counts["orders"], counts["orders_matched"]) == (
        1, 1, 1, 1)  # fmt: skip

    async with sessionmaker() as db:
        rows = (await db.scalars(select(MoneyTxn).order_by(MoneyTxn.amount_paise))).all()
    assert [(r.merchant, r.amount_paise, r.channel, r.source) for r in rows] == [
        ("Swiggy", 45250, "upi", "alert"),
        ("Domino's Pizza", 49500, "cash", "receipt"),
    ]
    # Reading again adds nothing.
    again = await money.sync_user(deps, user)
    assert again["orders"] == 0 and again["orders_matched"] == 0

    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    for secret in ("Swiggy", "Domino", "45250", "49500", "1234567890"):
        assert secret not in logged, secret


async def test_a_read_back_cut_short_is_not_counted(deps, user, sessionmaker, settings):
    """A mailbox Google refuses during a read back: the read doesn't use up one of the day's
    read backs, and the notice says what was read and what wasn't."""
    import time

    from nexus_common import money_mail
    from nexus_orchestrator.money import history_note

    async with sessionmaker() as db:
        await money_mail.save(db, settings, user, "alerts.box@gmail.com", {
            "scope": f"openid {money_mail.SCOPE}", "refresh_token": "r", "access_token": "a",
            "expires_in": 3600})  # fmt: skip
        await db.commit()
    await switch_on(sessionmaker, user)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    key = f"nexus:rl:money-history:{user}:{int(time.time() // 86400)}"
    await deps.redis.set(key, 2)
    assert await money.sync_user(deps, user, back_days=62) is None
    assert int(await deps.redis.get(key)) == 1

    base = {"messages": 10, "added": 4, "matched": 0, "skipped": 0, "bills": 0, "emis": 0}
    title, body = history_note(2, base, "Gmail said: 403.")
    assert title == "Read some of your old emails, but not all"
    assert body.startswith("4 new transactions.") and "didn't count" in body


async def test_reading_keeps_what_it_read_when_google_slows_it(
    deps, user, sessionmaker, settings, monkeypatch
):
    """Google's per-minute quota part way through a long read: the batches before it are saved
    and the mark moves past them, and the next run fetches only what's left."""
    from nexus_orchestrator.google_tools import Google as RealGoogle

    monkeypatch.setattr(money, "READS_PER_SECOND", 10_000.0)
    monkeypatch.setattr(RealGoogle, "retry_waits", ())
    start = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    mails = {}
    for i in range(250):
        when = start + timedelta(hours=8 * i)
        mails[f"m{i:03d}"] = _mail(
            f"m{i:03d}",
            "You have done a UPI txn",
            f"Rs.{100 + i}.00 has been debited from account **1234 to VPA shop{i}@icici SHOP "
            f"on {when:%d-%m-%y}. Your UPI transaction reference number is {400000000000 + i}.",
            "HDFC Bank InstaAlerts <alerts@hdfcbank.net>",
            when,
        )
    newest_first = [{"id": k} for k in sorted(mails, reverse=True)]
    fetched: list[str] = []
    slow = {"on": True}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages"):
            q = request.url.params.get("q", "")
            return httpx.Response(200, json={"messages": newest_first if "debited" in q else []})
        mid = path.rsplit("/", 1)[-1]
        if slow["on"] and int(mid[1:]) >= 150:
            return httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}})
        fetched.append(mid)
        return httpx.Response(200, json=mails[mid])

    await grant(sessionmaker, settings, user, ["gmail"])
    await switch_on(sessionmaker, user)
    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    first = await money.sync_user(deps, user)
    assert first["added"] == 100
    async with sessionmaker() as db:
        assert len((await db.scalars(select(MoneyTxn))).all()) == 100
        prefs = await db.get(MoneySettings, user)
        assert prefs.gmail_synced_until == start + timedelta(hours=8 * 99)
        assert "limiting" in prefs.last_error

    slow["on"] = False
    fetched.clear()
    second = await money.sync_user(deps, user)
    assert second["added"] == 150
    assert len(fetched) == 150 and "m000" not in fetched
    async with sessionmaker() as db:
        prefs = await db.get(MoneySettings, user)
        assert prefs.last_error is None
        assert prefs.gmail_synced_until == start + timedelta(hours=8 * 249)

    # One read at a time: a second one while the first runs is skipped.
    await deps.redis.set(money.READING_KEY.format(user), "1")
    assert await money.sync_user(deps, user) is None
