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


def _snap(**kw) -> plan.Snapshot:
    base = dict(
        income_paise=10000000, spend_paise=6000000, essential_paise=4000000, emis_paise=500000,
        liquid_paise=40000000, cash_paise=30000000, balances_known=True, cards=1,
        card_owed_paise=2000000, card_limit_paise=20000000, overdue_bills=0, due_30_paise=1500000,
        month_income_paise=10000000, month_spent_paise=3000000, month_committed_paise=500000,
        days_left=10, months_of_history=3,
    )  # fmt: skip
    return plan.Snapshot(**{**base, **kw})


def test_health_checks_put_safety_first():
    covered = plan.Assumptions(health_cover_paise=100000000, dependants=False)
    out = plan.health(_snap(), covered)
    by = {c["key"]: c for c in out["checks"]}
    # ₹4L against ₹60k a month is 6.7 months; 40% saved; EMIs 5%; cards 10% of the limit.
    assert by["emergency"]["status"] == "good" and by["emergency"]["value"] == "6.7 months"
    assert by["saving"]["status"] == "good" and by["saving"]["value"] == "40% of income"
    assert by["emis"]["status"] == by["cards"]["status"] == by["bills"]["status"] == "good"
    assert by["health"]["status"] == by["life"]["status"] == "good"
    assert out["verdict"] == "good" and out["next_step"] is None and out["ready_to_invest"]
    # ₹1L expected, keep 20%, ₹30k spent, ₹5k still due: ₹45k left over 10 days.
    safe = out["safe_to_spend"]
    assert (safe["left_paise"], safe["per_day_paise"]) == (4500000, 450000)
    b = out["balance"]
    assert (b["essentials_paise"], b["lifestyle_paise"], b["saved_paise"]) == (
        4000000, 2000000, 4000000,
    )  # fmt: skip

    # Nothing about cover told, a thin cushion and an overdue card bill: the bill comes first.
    out = plan.health(_snap(liquid_paise=9000000, overdue_bills=1), plan.Assumptions())
    by = {c["key"]: c for c in out["checks"]}
    assert by["emergency"]["status"] == "act" and by["cards"]["status"] == "act"
    assert by["health"]["status"] == by["life"]["status"] == "unknown"
    assert out["verdict"] == "act" and out["next_step"]["key"] == "cards"
    assert not out["ready_to_invest"]


def test_health_ladders_and_overspending():
    # EMIs at 35% of income are worth watching; at 45% act. Cards at 40% of limit: watch.
    out = plan.health(_snap(emis_paise=3500000, card_owed_paise=8000000), plan.Assumptions())
    by = {c["key"]: c for c in out["checks"]}
    assert by["emis"]["status"] == "watch" and by["cards"]["status"] == "watch"
    assert {c["key"]: c for c in plan.health(_snap(emis_paise=4500000), plan.Assumptions())[
        "checks"]}["emis"]["status"] == "act"  # fmt: skip
    # Spent past the safe amount; bills due beyond the bank balance.
    out = plan.health(_snap(month_spent_paise=9000000, due_30_paise=50000000), plan.Assumptions())
    assert out["safe_to_spend"]["left_paise"] < 0 and out["safe_to_spend"]["status"] == "act"
    assert out["safe_to_spend"]["per_day_paise"] == 0
    assert {c["key"]: c for c in out["checks"]}["bills"]["status"] == "act"
    # Someone depends on the income and there's no term cover.
    life = plan.health(_snap(), plan.Assumptions(dependants=True, life_cover_paise=0))
    assert {c["key"]: c for c in life["checks"]}["life"]["status"] == "act"
    # No income seen yet: the saving rate and safe-to-spend say they don't know.
    empty = plan.health(_snap(income_paise=0, month_income_paise=0), plan.Assumptions())
    assert {c["key"]: c for c in empty["checks"]}["saving"]["status"] == "unknown"
    assert not empty["safe_to_spend"]["known"]
    reminders = store.health_reminders(
        plan.health(_snap(month_spent_paise=9000000, due_30_paise=50000000), plan.Assumptions()),
        date(2026, 11, 21),
    )
    assert [r["tag"] for r in reminders] == ["safe:2026-11", "cover:2026-11"]


def test_insights_build_the_cushion_before_investing():
    loans: list = []
    short = plan.insights(
        today=date(2026, 11, 21), liquid_paise=6000000, spend_paise=6000000,
        surplus_paise=2000000, income_paise=10000000, loans=loans, assume=plan.Assumptions(),
    )  # fmt: skip
    titles = [x["title"] for x in short]
    # ₹3L short at ₹20k a month: 15 months, so February 2028.
    assert "Emergency fund full by February 2028" in titles
    assert not any(t.startswith("Investing") for t in titles)
    full = plan.insights(
        today=date(2026, 11, 21), liquid_paise=40000000, spend_paise=6000000,
        surplus_paise=2000000, income_paise=10000000, loans=loans, assume=plan.Assumptions(),
    )  # fmt: skip
    assert any(x["title"].startswith("Investing") for x in full)


# --- The API -----------------------------------------------------------------------------------


async def _user(db) -> User:
    return await db.scalar(select(User).where(User.email == "owner@example.com"))


async def test_members_cannot_see_planning(owner, client_for, invite):
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    for path in ("/api/money/balances", "/api/money/savings", "/api/money/loans",
                 "/api/money/plan", "/api/money/subscriptions", "/api/money/health"):  # fmt: skip
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


async def test_health_tab_and_targets(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await _user(db)
        today = local_today(user)
        start = date(today.year, today.month, 1)
        rows = []
        for back in (1, 2, 3):
            m = money.add_months(start, -back)
            rows += [
                money.Parsed(m + timedelta(days=1), 10000000, "credit", "Acme salary", "SALARY",
                             account_mask="3456", institution="Axis Bank"),
                money.Parsed(m + timedelta(days=2), 2500000, "debit", "Landlord", "RENT",
                             account_mask="3456", institution="Axis Bank"),
                money.Parsed(m + timedelta(days=3), 1500000, "debit", "Swiggy", "SWIGGY",
                             account_mask="3456", institution="Axis Bank"),
            ]  # fmt: skip
        rows.append(
            money.Parsed(today, 100000, "debit", "Blinkit", "BLINKIT", account_mask="3456",
                         institution="Axis Bank", balance_paise=12000000)
        )  # fmt: skip
        await store.ingest(db, settings, user.id, rows, source="statement")
        await db.commit()

    out = (await owner.get("/api/money/health")).json()
    by = {c["key"]: c for c in out["checks"]}
    # ₹1.2L in the bank for ₹40k a month: 3 months, short of 6.
    assert by["emergency"]["status"] == "watch" and by["emergency"]["value"] == "3.0 months"
    assert by["saving"]["value"] == "60% of income"
    assert by["health"]["status"] == "unknown"
    b = out["balance"]
    assert (b["essentials_paise"], b["lifestyle_paise"]) == (2500000, 1500000)
    assert out["safe_to_spend"]["keep_paise"] == 2000000

    # Saving targets and cover save on their own, without touching the forecast's.
    await owner.put("/api/money/plan/assumptions", json={"inflation_bp": 600})
    resp = await owner.put(
        "/api/money/plan/assumptions",
        json={"save_target_bp": 3000, "health_cover_paise": 100000000, "dependants": False},
    )
    assert resp.status_code == 200 and resp.json()["inflation_bp"] == 600
    out = (await owner.get("/api/money/health")).json()
    by = {c["key"]: c for c in out["checks"]}
    assert by["health"]["status"] == "good" and by["life"]["status"] == "good"
    assert out["targets"]["save_target_bp"] == 3000
    assert out["safe_to_spend"]["keep_paise"] == 3000000
