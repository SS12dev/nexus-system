"""Money's planning side: the maths (money_plan) and the API (/api/money balances,
subscriptions, savings, loans, goals and plan)."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from nexus_common import money
from nexus_common import money_plan as plan
from nexus_common import money_store as store
from nexus_common.models import MoneyLoan, MoneyTxn, User
from nexus_common.organiser import local_today

# --- The maths ---------------------------------------------------------------------------------


def test_fixed_deposit_compounds_quarterly():
    fd = plan.Asset("fd", 10000000, 700, date(2025, 11, 20), date(2026, 11, 20))
    # ₹1,00,000 at 7% compounded quarterly for a year: ₹1,07,186.
    assert plan.maturity_value(fd) == pytest.approx(10718590, abs=100)
    # Half way, about half the interest; after maturity it stops growing.
    assert 10340000 < plan.asset_value(fd, date(2026, 5, 20)) < 10360000
    assert plan.asset_value(fd, date(2027, 6, 1)) == plan.maturity_value(fd)
    payout = plan.Asset("fd", 10000000, 700, date(2025, 11, 20), date(2026, 11, 20), payout=True)
    assert plan.asset_value(payout, date(2026, 5, 1)) == 10000000


def test_recurring_deposit_matures_above_what_went_in():
    rd = plan.Asset("rd", 500000, 680, date(2025, 12, 1), date(2026, 12, 1))
    value = plan.maturity_value(rd)
    # Twelve ₹5,000 instalments: ₹60,000 in, about ₹62,250 out at 6.8%.
    assert 6200000 < value < 6250000
    assert plan.monthly_contribution(rd) == 500000


def test_ppf_takes_a_yearly_deposit_each_april():
    ppf = plan.Asset(
        "ppf", 50000000, 710, balance_on=date(2026, 3, 31), contribution_paise=15000000
    )
    a_year_on = plan.asset_value(ppf, date(2027, 3, 31))
    # ₹5,00,000 at 7.1% plus ₹1,50,000 in April: a little over ₹6,90,000.
    assert 69000000 < a_year_on < 70500000
    assert plan.monthly_contribution(ppf) == 15000000 // 12


def test_emi_and_loan_state():
    # The PS5: ₹54,990 over 12 months at 15% is ₹4,963 a month.
    assert plan.emi_for(5499000, 1500, 12) == pytest.approx(496330, abs=50)
    assert plan.emi_for(4999000, 0, 6) == 833167
    loan = plan.Loan(5499000, 1500, 12, 496330, date(2026, 10, 5))
    before = plan.loan_state(loan, date(2026, 9, 30))
    assert (before["paid"], before["left"], before["next_due_on"]) == (0, 12, date(2026, 10, 5))
    now = plan.loan_state(loan, date(2026, 11, 21))
    assert (now["paid"], now["left"], now["next_due_on"]) == (2, 10, date(2026, 12, 5))
    assert now["ends_on"] == date(2027, 9, 5)
    assert 4500000 < now["outstanding_paise"] < 4700000
    done = plan.loan_state(loan, date(2027, 9, 6))
    assert done["done"] and done["outstanding_paise"] == 0


def test_goal_and_sip_maths():
    assert plan.monthly_needed(12000000, 0, 0, 12) == 1000000
    assert plan.monthly_needed(12000000, 12000000, 650, 12) == 0
    # Growth lowers what's needed.
    assert plan.monthly_needed(12000000, 0, 1000, 12) < 1000000
    assert plan.future_value_monthly(1000000, 0, 12) == 12000000
    assert plan.future_value_monthly(1000000, 1200, 60) > 60 * 1000000
    assert plan.goal_rate_bp(12, 1000) == 650 and plan.goal_rate_bp(60, 1000) == 1000


def test_forecast_frees_money_when_an_emi_ends():
    loan = plan.Loan(1000000, 0, 3, 333334, date(2026, 10, 5))
    history = plan.History(income=[10000000] * 3, spend=[6333334] * 3, invested=[0] * 3)
    out = plan.forecast(
        today=date(2026, 11, 21),
        months=6,
        cash_paise=5000000,
        funds_paise=0,
        assets=[],
        loans=[loan],
        history=history,
        assume=plan.Assumptions(income_growth_bp=0, inflation_bp=0),
    )
    pts = out["points"]
    assert out["inputs"]["emis_paise"] == 333334
    assert out["inputs"]["surplus_paise"] == 10000000 - 6333334
    # December still pays the last EMI; from January the surplus is larger by the EMI.
    assert pts[0]["emis_paise"] == 333334 and pts[1]["emis_paise"] == 0
    step_dec = pts[1]["cash_paise"] - pts[0]["cash_paise"]
    assert step_dec == 10000000 - 6000000
    assert out["start"]["debt_paise"] > 0 and pts[-1]["debt_paise"] == 0
    # Steady history: no spread.
    assert pts[-1]["low_paise"] == pts[-1]["net_worth_paise"] == pts[-1]["high_paise"]


def test_forecast_range_widens_with_uneven_months():
    history = plan.History(
        income=[10000000, 12000000, 9000000, 11000000],
        spend=[7000000, 6000000, 8000000, 6500000],
        invested=[0, 0, 0, 0],
    )
    out = plan.forecast(
        today=date(2026, 11, 21), months=12, cash_paise=0, funds_paise=10000000, assets=[],
        loans=[], history=history, assume=plan.Assumptions(),
    )  # fmt: skip
    first, last = out["points"][0], out["points"][-1]
    assert last["high_paise"] - last["low_paise"] > first["high_paise"] - first["low_paise"] > 0
    assert last["low_paise"] < last["net_worth_paise"] < last["high_paise"]


# --- The API -----------------------------------------------------------------------------------


async def _user(db) -> User:
    return await db.scalar(select(User).where(User.email == "owner@example.com"))


async def test_members_cannot_see_planning(owner, client_for, invite):
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    for path in ("/api/money/balances", "/api/money/savings", "/api/money/loans",
                 "/api/money/plan", "/api/money/subscriptions"):  # fmt: skip
        assert (await friend.get(path)).status_code == 403


async def test_card_bill_balances_and_payments(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await _user(db)
        today = local_today(user)
        due = today + timedelta(days=10)
        bill = money.CardBill("5678", "HDFC Bank", 2451200, 123000, due, today - timedelta(days=5))
        assert await store.record_card_bill(db, user.id, bill)
        # The same bill again changes nothing.
        assert not await store.record_card_bill(db, user.id, bill)
        rows = [
            money.Parsed(today - timedelta(days=2), 1000000, "credit", "Card bill payment",
                         "PAYMENT", account_mask="5678", account_kind="card",
                         institution="HDFC Bank"),
            money.Parsed(today - timedelta(days=1), 50000, "debit", "Swiggy", "SWIGGY",
                         account_mask="5678", account_kind="card", institution="HDFC Bank",
                         extra={"available_limit_paise": 18000000}),
            money.Parsed(today - timedelta(days=1), 25000, "debit", "Blinkit", "BLINKIT",
                         account_mask="3456", institution="Axis Bank", balance_paise=1234567),
        ]  # fmt: skip
        await store.ingest(db, settings, user.id, rows, source="alert")
        await db.commit()

    view = (await owner.get("/api/money/balances")).json()
    by = {a["mask"]: a for a in view["items"]}
    card, bank = by["5678"], by["3456"]
    assert card["bill"] == {"status": "part", "left_paise": 1451200}
    assert card["available_paise"] == 18000000
    # Without a limit, owed is the bill left plus spending since the statement.
    assert card["owed_paise"] == 1451200 + 50000
    assert bank["balance_paise"] == 1234567
    assert view["cash_paise"] == 1234567 and view["owed_paise"] == 1451200 + 50000

    # With the limit typed in, owed comes from the limit left; then mark the bill paid.
    resp = await owner.patch(
        f"/api/money/accounts/{card['id']}/details",
        json={"credit_limit_paise": 20000000, "paid": True},
    )
    assert resp.status_code == 200, resp.text
    card = resp.json()
    assert card["owed_paise"] == 2000000 and card["bill"]["status"] == "paid"
    resp = await owner.patch(
        f"/api/money/accounts/{bank['id']}/details", json={"balance_paise": 5000000}
    )
    assert resp.json()["balance_paise"] == 5000000

    ov = (await owner.get("/api/money/overview")).json()
    assert all(u["kind"] != "card" for u in ov["upcoming"])


async def test_subscription_decisions(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await _user(db)
        today = local_today(user)
        rows = [
            money.Parsed(today - timedelta(days=30 * k + 3), 64900, "debit", "Netflix", f"NF {k}")
            for k in range(4)
        ] + [
            money.Parsed(today - timedelta(days=30 * k + 1), 11900, "debit", "Spotify", f"SP {k}")
            for k in range(3)
        ]
        await store.ingest(db, settings, user.id, rows, source="statement")
        await db.commit()
    subs = (await owner.get("/api/money/subscriptions")).json()
    assert subs["to_review"] == 2
    assert {s["merchant"]: s["status"] for s in subs["items"]} == {
        "Netflix": "new", "Spotify": "new"}  # fmt: skip

    keep = await owner.put(
        "/api/money/subscriptions",
        json={"merchant": "Netflix", "status": "active", "remind_days": 5},  # fmt: skip
    )
    assert keep.status_code == 200, keep.text
    assert keep.json()["status"] == "active" and keep.json()["remind_days"] == 5
    # Cancelled before the last charge: flagged as charged after cancelling.
    cancelled = await owner.put(
        "/api/money/subscriptions",
        json={"merchant": "Spotify", "status": "cancelled",
              "cancelled_on": str(local_today_of_rows(-10))},
    )  # fmt: skip
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["charged_after_cancel"]["amount_paise"] == 11900

    manual = await owner.put(
        "/api/money/subscriptions",
        json={"merchant": "Society maintenance", "manual": True, "amount_paise": 150000,
              "cadence": "monthly", "next_on": "2026-01-03", "category": "Rent and housing"},
    )  # fmt: skip
    assert manual.status_code == 200, manual.text
    gym = manual.json()
    assert gym["manual"] and gym["group"] == "bill"
    assert gym["next_on"] >= str(date.today() - timedelta(days=1))
    assert (
        await owner.put("/api/money/subscriptions", json={"merchant": "X", "manual": True})
    ).status_code == 422

    subs = (await owner.get("/api/money/subscriptions")).json()
    assert subs["to_review"] == 0
    assert subs["subscriptions_monthly_paise"] == 64900
    assert subs["bills_monthly_paise"] == 150000
    assert subs["saved_monthly_paise"] == 11900

    async with sessionmaker() as db:
        user = await _user(db)
        accounts = await store.accounts_view(db, user.id, local_today(user))
    view = (await owner.get("/api/money/subscriptions")).json()
    for item in view["items"]:
        for key in ("next_on", "last_on", "cancelled_on"):
            if item[key]:
                item[key] = date.fromisoformat(item[key])
        if item["charged_after_cancel"]:
            item["charged_after_cancel"]["on"] = date.fromisoformat(
                item["charged_after_cancel"]["on"]
            )
    tags = [r["tag"] for r in store.reminders(view, accounts, date.today())]
    assert any(t.startswith("sub-late:spotify") for t in tags)

    # Undoing a decision shows it as new again; a manual one is removed.
    assert (await owner.delete("/api/money/subscriptions/netflix")).status_code == 204
    assert (await owner.delete("/api/money/subscriptions/society maintenance")).status_code == 204
    subs = (await owner.get("/api/money/subscriptions")).json()
    assert {s["merchant"]: s["status"] for s in subs["items"]} == {
        "Netflix": "new", "Spotify": "cancelled"}  # fmt: skip


def local_today_of_rows(days: int) -> date:
    return date.today() + timedelta(days=days)


async def test_savings_loans_goals_and_plan(owner, sessionmaker, settings, redis):
    fd = await owner.post(
        "/api/money/savings",
        json={"kind": "fd", "name": "SBI FD", "amount_paise": 10000000, "rate_bp": 700,
              "start_on": "2026-01-01", "maturity_on": "2027-01-01"},
    )  # fmt: skip
    assert fd.status_code == 201, fd.text
    assert fd.json()["value_paise"] > 10000000
    ppf = await owner.post(
        "/api/money/savings",
        json={"kind": "ppf", "amount_paise": 50000000, "contribution_paise": 15000000},
    )
    assert ppf.json()["rate_bp"] == 710  # the government rate by default
    epf = (
        await owner.post(
            "/api/money/savings",
            json={"kind": "epf", "amount_paise": 30000000, "contribution_paise": 1200000},
        )  # fmt: skip
    ).json()
    assert epf["rate_bp"] == 825
    assert (
        await owner.post("/api/money/savings", json={"kind": "rd", "amount_paise": 500000})
    ).status_code == 422
    resp = await owner.patch(f"/api/money/savings/{epf['id']}", json={"amount_paise": 31000000})
    assert resp.json()["amount_paise"] == 31000000

    loan = await owner.post(
        "/api/money/loans",
        json={"name": "PS5", "principal_paise": 5499000, "rate_bp": 1500, "months": 12,
              "first_due_on": "2026-10-05"},
    )  # fmt: skip
    assert loan.status_code == 201, loan.text
    assert loan.json()["emi_paise"] == pytest.approx(496330, abs=50)
    assert loan.json()["kind_label"] == "Card EMI"
    loan_id = loan.json()["id"]
    resp = await owner.patch(f"/api/money/loans/{loan_id}", json={"months": 6})
    assert resp.json()["emi_paise"] > 900000

    goal = await owner.post(
        "/api/money/goals",
        json={"name": "Trip", "target_paise": 20000000, "by_on": "2027-11-01",
              "asset_ids": [fd.json()["id"]]},
    )  # fmt: skip
    assert goal.status_code == 201, goal.text
    bad = await owner.post(
        "/api/money/goals",
        json={"name": "x", "target_paise": 1, "by_on": "2027-01-01",
              "asset_ids": [str(uuid.uuid4())]},
    )  # fmt: skip
    assert bad.status_code == 404

    out = (await owner.get("/api/money/plan", params={"months": 12})).json()
    assert len(out["points"]) == 12
    assert out["start"]["savings_paise"] > 90000000
    assert out["start"]["debt_paise"] > 0
    (g,) = out["goals"]
    assert g["have_paise"] > 10000000 and g["monthly_needed_paise"] > 0
    assert any("PS5 ends" in i["title"] for i in out["insights"])

    tried = (
        await owner.get("/api/money/plan", params={"months": 12, "new_sip_paise": 1000000})
    ).json()
    assert tried["assumptions"]["new_sip_paise"] == 1000000
    assert tried["saved_assumptions"] == {}
    saved = await owner.put("/api/money/plan/assumptions", json={"inflation_bp": 600})
    assert saved.status_code == 200 and saved.json()["inflation_bp"] == 600

    balances = (await owner.get("/api/money/balances")).json()
    assert balances["loans"]["outstanding_paise"] > 0

    assert (
        await owner.post("/api/money/delete-all", json={"confirm": "delete"})
    ).status_code == 204
    assert (await owner.get("/api/money/savings")).json()["items"] == []
    assert (await owner.get("/api/money/loans")).json()["items"] == []
    assert (await owner.get("/api/money/plan")).json()["goals"] == []


async def test_emi_conversion_leaves_out_the_purchase(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await _user(db)
        today = local_today(user)
        card = {"account_mask": "5678", "account_kind": "card", "institution": "HDFC Bank"}
        rows = [
            money.Parsed(today - timedelta(days=20), 5499000, "debit", "Sony", "SONY INDIA",
                         **card),
            money.Parsed(today - timedelta(days=5), 5499000, "credit", "Sony",
                         "SMARTEMI CONVERSION SONY INDIA", **card),
            money.Parsed(today - timedelta(days=4), 433400, "debit", "EMI Sony",
                         "EMI PRINCIPAL 01/12 SONY", **card),
        ]  # fmt: skip
        await store.ingest(db, settings, user.id, rows, source="statement")
        booking = money.EmiBooking("5678", "HDFC Bank", 5499000, 12, 1500, None,
                                   today - timedelta(days=5), "Sony")  # fmt: skip
        assert await store.record_emi_booking(db, user.id, booking) is not None
        assert await store.record_emi_booking(db, user.id, booking) is None
        await db.commit()
        txns = (await db.scalars(select(MoneyTxn).order_by(MoneyTxn.occurred_on))).all()
        assert [(t.direction, t.excluded, t.category) for t in txns] == [
            ("debit", True, "Shopping"),
            ("credit", True, "Transfers"),
            ("debit", False, "Loans and EMIs"),
        ]
        loan = (await db.scalars(select(MoneyLoan))).one()
        assert loan.name == "EMI Sony" and loan.months == 12

    loans = (await owner.get("/api/money/loans")).json()
    # The EMI seen on the card is tracked, so it isn't suggested again.
    assert loans["untracked"] == []
    assert loans["monthly_paise"] == loans["items"][0]["emi_paise"]
    ov = (await owner.get("/api/money/overview")).json()
    assert ov["spent_paise"] == 433400 or ov["spent_paise"] == 0
