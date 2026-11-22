"""Income and clean-up: the payee behind a bank's sign-off, two emails about one payment made
one row, salary history with expected rows for months no credit was read, a statement read
again in place of an earlier import, and the one-off repair of rows stored before all this.

Alert texts follow the banks' wording with made-up numbers and handles."""

from datetime import date, timedelta

from sqlalchemy import select

from nexus_common import money, money_repair
from nexus_common import money_store as store
from nexus_common.models import MoneyTxn, User

SIGN_OFF = (
    " If you did not authorise this transaction, please report it immediately by calling "
    "18002586161. We're here to support you in every step of the way. Warm regards, HDFC Bank"
)
UPI = (
    "Dear Customer, Rs.498.97 is debited from your account ending 1298 towards VPA "
    "****@kotakpay (ZEPTO MARKETPLACE PRIVATE LIMITED) on 25-10-26. UPI transaction reference "
    "no.: 512345678901." + SIGN_OFF
)
CARD = (
    "Dear Customer, Rs.149.00 is debited from your HDFC Bank Debit Card ending 4855 at "
    "YOUTUBEGOOGLE on 29 Aug, 2026 at 18:09:02." + SIGN_OFF
)
MANDATE = (
    "Dear Customer, Your Youtube bill, set up through E-mandate (Auto payment), has been "
    "successfully paid using your HDFC Bank Debit Card ending 4855. Merchant Name: Youtube "
    "Amount: INR 149.00 Date: 29/08/2026." + SIGN_OFF
)
HDFC = "alerts@hdfcbank.net"


def _alert(body: str, day: date, subject: str = "Account update") -> money.Parsed:
    p = money.parse_alert(subject, body, day, sender=HDFC)
    assert p is not None
    return p


def test_the_payee_in_brackets_not_the_sign_off():
    p = _alert(UPI, date(2026, 10, 25), "You have done a UPI txn. Check details!")
    assert (p.merchant, p.channel, p.amount_paise, p.ref) == (
        "Zepto",
        "upi",
        498_97,
        "512345678901",
    )
    assert "support you" not in p.narration.lower() and "warm regards" not in p.narration.lower()
    assert money.categorise(p.merchant, p.narration) == "Groceries"


async def _user(db) -> User:
    return await db.scalar(select(User).where(User.email == "owner@example.com"))


async def test_two_emails_about_one_payment_are_one_row(owner, sessionmaker, settings):
    day = date(2026, 8, 29)
    async with sessionmaker() as db:
        uid = (await _user(db)).id
        first = await store.ingest(
            db, settings, uid, [_alert(CARD, day)], source="alert", source_ref="m1"
        )
        # The autopay's "bill paid" for the same charge.
        second = await store.ingest(
            db, settings, uid, [_alert(MANDATE, day)], source="alert", source_ref="m2"
        )
        # The card alert again, from a second mailbox.
        third = await store.ingest(
            db, settings, uid, [_alert(CARD, day)], source="alert", source_ref="m3"
        )
        await db.commit()
        rows = (await db.scalars(select(MoneyTxn).where(MoneyTxn.user_id == uid))).all()
        # Another YouTube charge the same day at another time is a charge of its own.
        later = _alert(CARD.replace("18:09:02", "21:40:11"), day)
        assert store.same_alert(settings, rows, later, rows[0].account_id, "m4") is None
        assert store.same_alert(settings, rows, _alert(CARD, day), rows[0].account_id, "m5")
    assert [first["added"], second["matched"], third["added"]] == [1, 1, 0]
    assert third["matched"] + third["skipped"] == 1
    assert len(rows) == 1
    assert "autopay" in {r.channel for r in rows}


async def test_repair_fixes_payees_categories_and_merges_pairs(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        uid = (await _user(db)).id
        old = f"You have done a UPI txn. Check details!. {UPI}"

        def row(**kw) -> MoneyTxn:
            base = dict(
                user_id=uid, occurred_on=date(2026, 10, 25), amount_paise=498_97, direction="debit",
                merchant="Support You In Every Step", category="Other", category_by="auto",
                narration_enc=money.seal(settings, old), channel="upi", source="alert",
                source_ref="m1", ref="512345678901", dedupe_key="k1",
            )  # fmt: skip
            return MoneyTxn(**{**base, **kw})

        db.add(row())
        # Two emails for one card charge, stored before duplicates were caught.
        card = money.seal(settings, CARD)
        db.add(row(occurred_on=date(2026, 8, 29), amount_paise=149_00, merchant="YouTube",
                   narration_enc=card, channel="card", source_ref="m2", ref=None,
                   dedupe_key="k2"))  # fmt: skip
        db.add(row(occurred_on=date(2026, 8, 29), amount_paise=149_00, merchant="YouTube",
                   narration_enc=money.seal(settings, MANDATE), channel="autopay", source_ref="m3",
                   ref=None, dedupe_key="k3"))  # fmt: skip
        # One the person categorised keeps its category.
        db.add(row(merchant="Support You In Every Step", category="Shopping", category_by="user",
                   occurred_on=date(2026, 9, 1), ref="512345678902", source_ref="m4",
                   dedupe_key="k4"))  # fmt: skip
        # An electricity bill paid through Google Pay, read from a statement as "Google".
        gpay = "UPI/Google Ind/gpayrecharge@o/UPI/AXIS BANK/500000000009/ICI0a0b/Google India"
        db.add(row(merchant="Google", source="statement", source_ref=None, ref=None,
                   narration_enc=money.seal(settings, gpay), amount_paise=2500_00,
                   occurred_on=date(2026, 10, 31), dedupe_key="k5"))  # fmt: skip
        await db.commit()

        counts = await money_repair.repair(db, settings, uid)
        await db.commit()
        rows = {
            r.dedupe_key: r
            for r in (await db.scalars(select(MoneyTxn).where(MoneyTxn.user_id == uid))).all()
        }
    assert counts == {"payees": 3, "categories": 4, "merged": 1}
    assert (rows["k5"].merchant, rows["k5"].category) == ("Google Pay bills", "Bills and utilities")
    assert (rows["k1"].merchant, rows["k1"].category) == ("Zepto", "Groceries")
    assert (rows["k4"].merchant, rows["k4"].category) == ("Zepto", "Shopping")
    youtube = [r for r in rows.values() if r.merchant == "YouTube"]
    assert len(youtube) == 1 and youtube[0].channel == "autopay"
    assert youtube[0].category == "Subscriptions"
    # Running it again changes nothing.
    async with sessionmaker() as db:
        assert await money_repair.repair(db, settings, uid) == {
            "payees": 0,
            "categories": 0,
            "merged": 0,
        }


def _month_start(day: date, back: int) -> date:
    y, m = divmod(day.year * 12 + day.month - 1 - back, 12)
    return date(y, m + 1, 1)


async def test_salary_history_fills_months_and_feeds_the_plan(owner):
    today = date.today()
    start, raised = _month_start(today, 6), _month_start(today, 2)
    resp = await owner.post("/api/money/salary", json={
        "monthly_paise": 74_000_00, "from_on": start.isoformat(), "employer": "Acme Tech",
        "pay_day": 1})  # fmt: skip
    assert resp.status_code == 201, resp.text
    resp = await owner.post("/api/money/salary", json={
        "monthly_paise": 84_000_00, "from_on": raised.isoformat(), "employer": "Acme Tech",
        "pay_day": 1})  # fmt: skip
    out = resp.json()
    assert [s["monthly_paise"] for s in out["items"]] == [84_000_00, 74_000_00]
    assert out["current_id"] == out["items"][0]["id"]
    assert out["expected"] >= 6
    same_day = await owner.post(
        "/api/money/salary", json={"monthly_paise": 1, "from_on": start.isoformat()}
    )
    assert same_day.status_code == 409

    items = (await owner.get("/api/money/transactions", params={"limit": 200})).json()["items"]
    expected = {t["occurred_on"]: t for t in items if t["source"] == "salary"}
    assert expected[start.isoformat()]["amount_paise"] == 74_000_00
    assert expected[raised.isoformat()]["amount_paise"] == 84_000_00
    assert expected[raised.isoformat()]["merchant"] == "Acme Tech"
    assert expected[raised.isoformat()]["category"] == "Income"

    # The real credit for a month, typed in (or read), takes the expected row's place.
    got = await owner.post("/api/money/transactions", json={
        "occurred_on": (raised + timedelta(days=2)).isoformat(), "amount": "83,500",
        "direction": "credit", "merchant": "Acme Tech"})  # fmt: skip
    assert got.status_code == 201
    after = (await owner.get("/api/money/salary")).json()
    assert after["expected"] == out["expected"] - 1
    items = (await owner.get("/api/money/transactions", params={"limit": 200})).json()["items"]
    assert raised.isoformat() not in {t["occurred_on"] for t in items if t["source"] == "salary"}

    plan = (await owner.get("/api/money/plan")).json()
    assert plan["salary_paise"] == 84_000_00
    assert plan["inputs"]["income_paise"] >= 84_000_00
    assert plan["saved_assumptions"].get("income_paise") is None

    # A raise moved: its months follow.
    await owner.patch(
        f"/api/money/salary/{out['items'][0]['id']}", json={"monthly_paise": 90_000_00}
    )
    items = (await owner.get("/api/money/transactions", params={"limit": 200})).json()["items"]
    later = [t for t in items if t["source"] == "salary" and t["occurred_on"] > raised.isoformat()]
    assert later and all(t["amount_paise"] == 90_000_00 for t in later)
    # Deleting both takes the expected rows away.
    for s in out["items"]:
        assert (await owner.delete(f"/api/money/salary/{s['id']}")).status_code == 200
    items = (await owner.get("/api/money/transactions", params={"limit": 200})).json()["items"]
    assert not [t for t in items if t["source"] == "salary"]


STATEMENT = """Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
03/11/26,{a},0000430712345678,03/11/26,450.00,,209550.00
05/11/26,{b},4307001111,05/11/26,"9,000.00",,200550.00
"""


async def test_a_statement_read_again_replaces_the_earlier_import(owner):
    head = "HDFC BANK Ltd.\nAccount No : 50100123451234\n\n"
    bad = head + STATEMENT.format(a="UPI-Bank-x", b="UPI-L-y")
    good = head + STATEMENT.format(
        a="UPI-SWIGGY-swiggy@icici-ICIC0000001-430712345678-UPI", b="UPI-CRED CLUB-cred.club@axisb"
    )
    files = lambda text: {"file": ("Acct_Statement.csv", text.encode(), "text/csv")}  # noqa: E731
    first = (await owner.post("/api/money/imports", files=files(bad))).json()
    assert first["counts"]["added"] == 2
    again = await owner.post("/api/money/imports", files=files(good), data={"replace": "true"})
    assert again.status_code == 201, again.text
    assert again.json()["counts"] == {"added": 2, "matched": 0, "skipped": 0, "replaced": 2}
    items = (await owner.get("/api/money/transactions", params={"limit": 200})).json()["items"]
    assert sorted(t["merchant"] for t in items) == ["Cred Club", "Swiggy"]


# --- ICICI card alerts, three-digit accounts, statements in the mail, tracking ---------------

ICICI_FOOTER = (
    " In case you have not done this transaction, to report it please call on 18001080 or SMS "
    "BLOCK 9006 to 9215676766 from your registered mobile number. NEVER SHARE your Card number, "
    "CVV, PIN, OTP, Internet Banking User ID, Password or URN with anyone."
)


def test_icici_card_alerts_are_read_despite_the_otp_warning():
    cc = money.parse_alert(
        "Transaction alert for your ICICI Bank Credit Card",
        "Dear Customer, Your ICICI Bank Credit Card XX9006 has been used for a transaction of "
        "INR 6,618.00 on Nov 19, 2026 at 03:15:47. Info: PLAYSTATION. The Available Credit "
        "Limit on your card is INR 1,30,520.22." + ICICI_FOOTER,
        date(2026, 11, 19),
        sender="credit_cards@icici.bank.in",
    )
    assert cc is not None
    assert (cc.amount_paise, cc.account_kind, cc.account_mask) == (6618_00, "card", "9006")
    dc = money.parse_alert(
        "Transaction alert for your ICICI Bank debit card",
        "Dear Customer, A purchase of Rs. 7,808.70 has been made using your Debit Card linked to "
        "ICICI Bank Account XX320 on 18-Nov-26. Info: VIN*PLAYSTATION. The Available Balance in "
        "your account is Rs. 4,712.74." + ICICI_FOOTER,
        date(2026, 11, 18),
        sender="customercare@icici.bank.in",
    )
    assert dc is not None
    assert (dc.account_kind, dc.account_mask, dc.balance_paise) == ("bank", "320", 4712_74)
    # A real OTP email is still not a transaction.
    assert (
        money.parse_alert(
            "OTP for online transaction",
            "Your OTP for the transaction of Rs. 500.00 on your card XX9006 is 123456.",
            date(2026, 11, 19),
            sender="credit_cards@icici.bank.in",
        )
        is None
    )


async def test_three_digits_find_the_account_ending_with_them(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        uid = (await _user(db)).id
        full = await store.account_for(db, uid, "ICICI Bank", "6320", "bank")
        assert await store.account_for(db, uid, "ICICI Bank", "320", "bank") is full
        # Another bank's account ending the same way isn't it.
        other = await store.account_for(db, uid, "HDFC Bank", "320", "bank")
        assert other is not full


def test_statement_emails_are_recognised():
    day = date(2026, 11, 22)
    icici = money.parse_statement_mail(
        "Bank Statement from 01-03-2026 to 22-11-2026 for XXXXXXXXXX20", day,
        "estatement@icici.bank.in",
    )  # fmt: skip
    assert icici == money.StatementMail("ICICI Bank", "20", date(2026, 3, 1), date(2026, 11, 22))
    hdfc = money.parse_statement_mail(
        "Email Account Statement of your HDFC Bank Account ***1298 for the period 01-Apr-2026 "
        "TO 21-Nov-2026", day, "emailstatements@hdfcbank.bank.in",
    )  # fmt: skip
    assert (hdfc.mask, hdfc.period_to) == ("1298", date(2026, 11, 21))
    assert (
        money.parse_statement_mail("Consolidated Account Statement", day, "x@camsonline.com")
        is None
    )
    assert (
        money.parse_statement_mail("Your statement is ready to view", day, "a@hdfcbank.bank.in")
        is None
    )
    assert (
        money.parse_statement_mail("Bank Statement", day, "a@icici.bank.in", has_pdf=False) is None
    )


def test_last_working_day():
    from nexus_common import money_salary

    assert money_salary.pay_date(2026, 1, 0) == date(2026, 1, 30)  # 31st is a Saturday
    assert money_salary.pay_date(2026, 3, 0) == date(2026, 3, 31)
    assert money_salary.pay_date(2026, 5, 0) == date(2026, 5, 29)  # 31st is a Sunday
    assert money_salary.pay_date(2026, 2, 30) == date(2026, 2, 28)


async def test_import_a_statement_from_the_mail(app, owner, sessionmaker, settings):
    import base64
    from datetime import timedelta as td

    import httpx

    from nexus_common.google import SERVICE_SCOPES, encrypt
    from nexus_common.models import GoogleGrant, MoneyMailStatement, utcnow

    from .test_money_pdf import _bank_pdf

    pdf = _bank_pdf(password="open sesame")
    gmail = "https://gmail.googleapis.com/gmail/v1/users/me"
    seen: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/messages/m-stmt"):
            return httpx.Response(200, json={"payload": {"parts": [
                {"mimeType": "text/plain", "body": {"data": ""}},
                {"filename": "Statement.pdf", "body": {"attachmentId": "a1"}}]}})  # fmt: skip
        if request.url.path.endswith("/attachments/a1"):
            data = base64.urlsafe_b64encode(pdf).decode().rstrip("=")
            return httpx.Response(200, json={"data": data})
        return httpx.Response(404, json={})

    app.state.google_http = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    async with sessionmaker() as db:
        uid = (await _user(db)).id
        db.add(GoogleGrant(
            user_id=uid, scopes=f"openid {SERVICE_SCOPES['gmail']}",
            refresh_token=encrypt(settings, "r"), access_token=encrypt(settings, "a"),
            access_expires_at=utcnow() + td(hours=1)))  # fmt: skip
        row = MoneyMailStatement(
            user_id=uid, message_id="m-stmt", received_at=utcnow(), institution="HDFC Bank",
            mask="1298", period_from=date(2026, 11, 1), period_to=date(2026, 11, 30),
            created_at=utcnow())  # fmt: skip
        db.add(row)
        await db.commit()
        sid = row.id
    listed = (await owner.get("/api/money/mail-statements")).json()
    assert [(s["status"], s["mask"], s["mailbox"]) for s in listed] == [
        ("new", "1298", "Your sign-in Gmail")
    ]
    url = f"/api/money/mail-statements/{sid}/import"
    wrong = await owner.post(url, json={"password": "nope"})
    assert wrong.status_code == 422 and "password" in wrong.json()["detail"]
    got = await owner.post(url, json={"password": "open sesame"})
    assert got.status_code == 200, got.text
    assert got.json()["counts"]["added"] > 0 and got.json()["origin"] == "gmail"
    assert f"{gmail.split('googleapis.com')[1]}/messages/m-stmt/attachments/a1" in seen
    assert (await owner.get("/api/money/mail-statements")).json()[0]["status"] == "imported"
    async with sessionmaker() as db:
        from nexus_common.models import MoneySettings

        prefs = await db.get(MoneySettings, uid)
        assert prefs is None or prefs.cas_password_enc is None  # never kept

    tracking = (await owner.get("/api/money/tracking")).json()
    hdfc = next(a for a in tracking["accounts"] if a["label"].endswith("1298"))
    assert hdfc["last_statement_on"] and hdfc["status"] in ("ok", "stale")
    assert len(tracking["months"]) == 12 and tracking["statements_waiting"] == 0
    assert tracking["mail"][0]["label"] == "Your sign-in Gmail"
    nov = next(m for m in hdfc["months"] if m["month"] == "2026-11")
    assert nov.get("statements", 0) > 0

    # The bank emails the same week again: what was read in already covers it.
    async with sessionmaker() as db:
        db.add(MoneyMailStatement(
            user_id=uid, message_id="m-again", received_at=utcnow(), institution="HDFC Bank",
            mask="1298", period_from=date(2026, 11, 1), period_to=date(2026, 11, 8),
            created_at=utcnow()))  # fmt: skip
        await db.commit()
    listed = (await owner.get("/api/money/mail-statements")).json()
    assert [s["status"] for s in listed] == ["covered", "imported"]
    assert (await owner.get("/api/money/tracking")).json()["statements_waiting"] == 0


def test_a_new_card_has_no_gaps_before_its_first_spend():
    from nexus_common import money_tracking as mt

    today = date(2026, 11, 22)
    months = mt.months_back(today)
    cells = {"2026-11": {"alerts": 3, "read": 3}}
    first = date(2026, 11, 14)
    # Without knowing when it began, Sep and Oct look missing; a card first used in November
    # has nothing to miss.
    assert mt.assess("card", months, cells, first, None, 0, today)[0] == "gaps"
    assert mt.assess("card", months, cells, first, None, 0, today, first=first) == (
        "ok",
        "Alert emails are arriving.",
    )
    # An older account still shows a real gap.
    cells["2026-09"] = {"alerts": 2, "read": 2}
    status, todo = mt.assess("card", months, cells, first, None, 0, today, first=date(2026, 9, 3))
    assert status == "gaps" and "Oct 2026" in todo
