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
        await db.commit()

        counts = await money_repair.repair(db, settings, uid)
        await db.commit()
        rows = {
            r.dedupe_key: r
            for r in (await db.scalars(select(MoneyTxn).where(MoneyTxn.user_id == uid))).all()
        }
    assert counts == {"payees": 2, "categories": 3, "merged": 1}
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
