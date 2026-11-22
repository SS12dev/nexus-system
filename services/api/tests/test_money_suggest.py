"""Money suggests: the one-tap questions Money asks instead of a list of chores, each answer
applied as the person would by hand, orders matched to payments read later without asking,
and "accept all" taking Money's guesses. Names and amounts are made up."""

import itertools
from datetime import date, timedelta

from sqlalchemy import select

from nexus_common import money, money_salary, money_suggest
from nexus_common import money_plan as plan
from nexus_common import money_store as store
from nexus_common.models import (
    MoneyAccount,
    MoneyLoan,
    MoneySalary,
    MoneySettings,
    MoneySubscription,
    MoneyTxn,
    User,
    utcnow,
)

TODAY = date.today()
_keys = itertools.count()


def _txn(uid, **kw) -> MoneyTxn:
    base = dict(
        user_id=uid, occurred_on=TODAY - timedelta(days=5), amount_paise=100_00,
        direction="debit", merchant="Shop", category="Other", category_by="auto",
        narration_enc="", channel="upi", source="alert", dedupe_key=f"k{next(_keys)}",
        created_at=utcnow(),
    )  # fmt: skip
    return MoneyTxn(**{**base, **kw})


async def _seed(db) -> dict:
    user = await db.scalar(select(User).where(User.email == "owner@example.com"))
    user.name = "Test Person"
    uid = user.id
    bank = MoneyAccount(user_id=uid, institution="ICICI Bank", mask="4321", kind="bank",
                        name="Salary", created_at=utcnow())  # fmt: skip
    card = MoneyAccount(user_id=uid, institution="ICICI Bank", mask="7777", kind="card",
                        name="Card", created_at=utcnow())  # fmt: skip
    db.add_all([bank, card])
    await db.flush()
    rows = []
    # Rent to a person, three months running, filed as a transfer.
    for back in (70, 40, 10):
        day = TODAY - timedelta(days=back)
        rows.append(_txn(uid, account_id=bank.id, merchant="Asha Nair", category="Transfers",
                         amount_paise=15_000_00, occurred_on=day))  # fmt: skip
    # A card bill paid through Cred each month: a transfer by name, not rent.
    for back in (68, 38, 8):
        day = TODAY - timedelta(days=back)
        rows.append(_txn(uid, account_id=bank.id, merchant="Cred Club", category="Transfers",
                         amount_paise=14_000_00, occurred_on=day))  # fmt: skip
    # Money to the owner's own name (an account Money doesn't know), and to someone else.
    rows.append(_txn(uid, account_id=bank.id, merchant="Test Person", amount_paise=2_000_00))
    rows.append(_txn(uid, account_id=bank.id, merchant="Ravi Menon", amount_paise=1_500_00,
                     occurred_on=TODAY - timedelta(days=6)))  # fmt: skip
    # A shop nobody filed.
    rows.append(_txn(uid, account_id=bank.id, merchant="The Shave Co", amount_paise=1_500_00))
    # An order a later statement row explains, and one nothing explains.
    rows.append(_txn(uid, merchant="Zomato", category="Food and dining", source="receipt",
                     amount_paise=390_48, occurred_on=TODAY - timedelta(days=3)))  # fmt: skip
    rows.append(_txn(uid, account_id=bank.id, merchant="Zomato Ltd", source="statement",
                     amount_paise=390_48, occurred_on=TODAY - timedelta(days=2)))  # fmt: skip
    rows.append(_txn(uid, merchant="Pizza Place", category="Food and dining", source="receipt",
                     amount_paise=417_00, occurred_on=TODAY - timedelta(days=4)))  # fmt: skip
    # A card purchase turned into EMIs, term unknown.
    rows.append(_txn(uid, account_id=card.id, merchant="Gadget Store", category="Shopping",
                     amount_paise=60_000_00, channel="card", excluded=True,
                     note="Turned into EMIs: the instalments count as spending."))  # fmt: skip
    # A yearly plan bought once.
    rows.append(_txn(uid, account_id=card.id, merchant="PlayStation", category="Entertainment",
                     amount_paise=4_999_00, channel="card"))  # fmt: skip
    # A subscription seen three months running.
    for back in (65, 35, 5):
        day = TODAY - timedelta(days=back)
        rows.append(_txn(uid, account_id=bank.id, merchant="Netflix", category="Subscriptions",
                         amount_paise=499_00, occurred_on=day))  # fmt: skip
    db.add_all(rows)
    await db.commit()
    return {"uid": uid, "bank": bank.id, "card": card.id}


async def test_money_asks_one_tap_questions_and_applies_answers(owner, sessionmaker):
    async with sessionmaker() as db:
        ids = await _seed(db)
    got = (await owner.get("/api/money/suggestions")).json()
    # The Zomato order took the statement row's payment without a question, and the payment
    # to the owner's own name was filed as a transfer.
    assert got["matched"] == 1 and got["filed"] == 1
    by = {i["kind"]: i for i in got["items"] if not i.get("person")}
    person = next(i for i in got["items"] if i.get("person"))
    assert person["merchant"] == "Ravi Menon" and person["guess"] is None
    assert "Transfers" in {c["id"] for c in person["choices"]}
    # Each question says when, from where and how.
    assert by["rent"]["account"] == "Salary" and by["rent"]["how"] == "UPI"
    assert len(by["rent"]["details"]) == 3 and by["rent"]["first_on"] == str(
        TODAY - timedelta(days=70)
    )
    assert set(by) == {"emi", "yearly", "rent", "subscription", "order", "category"}
    assert by["rent"]["merchant"] == "Asha Nair" and by["rent"]["guess"] == "Rent and housing"
    assert by["order"]["merchant"] == "Pizza Place"
    assert by["order"]["guess"] == f"account:{ids['bank']}"
    assert by["subscription"]["merchant"] == "Netflix" and by["subscription"]["guess"] == "keep"
    assert by["category"]["merchant"] == "The Shave Co" and by["category"]["guess"] is None
    assert by["emi"]["guess"] is None and by["yearly"]["guess"] is None

    # Accept all takes the guesses: rent, the subscription and the order.
    done = (await owner.post("/api/money/suggestions/accept-all")).json()
    assert done["accepted"] == 3
    assert {i["kind"] for i in done["items"]} == {"emi", "yearly", "category"}
    assert len(done["items"]) == 4

    async def ask(kind: str, choice: str, merchant: str | None = None, **extra) -> dict:
        key = next(i["key"] for i in (await owner.get("/api/money/suggestions")).json()["items"]
                   if i["kind"] == kind and merchant in (None, i["merchant"]))  # fmt: skip
        r = await owner.post(
            "/api/money/suggestions/answer", json={"key": key, "choice": choice, **extra}
        )
        assert r.status_code == 200, r.text
        return r.json()

    # An instalment that can't fit the purchase is refused; the bank's figure gives the rate.
    emi = plan.emi_for(60_000_00, 16_00, 9)
    key = next(i["key"] for i in done["items"] if i["kind"] == "emi")
    bad = await owner.post(
        "/api/money/suggestions/answer", json={"key": key, "choice": "9", "amount_paise": 100}
    )
    assert bad.status_code == 422
    assert "16.0% a year" in (await ask("emi", "9", amount_paise=emi))["done"]
    # Not now: asked again after a week.
    later = await ask("yearly", "later")
    assert "again in a week" in later["done"]
    assert "yearly" not in {i["kind"] for i in later["items"]}
    async with sessionmaker() as db:
        again = await money_suggest.suggestions(db, ids["uid"], TODAY + timedelta(days=8))
        assert "yearly" in {i["kind"] for i in again["items"]}
        prefs = await db.get(MoneySettings, ids["uid"])
        prefs.answered = {k: v for k, v in prefs.answered.items() if not k.startswith("yearly")}
        await db.commit()
    assert "PlayStation Plus" in (await ask("yearly", "yearly"))["done"]
    await ask("category", "Transport", "Ravi Menon")
    last = await ask("category", "Health")
    assert last["items"] == []

    bad = await owner.post(
        "/api/money/suggestions/answer", json={"key": "rent:asha nair", "choice": "Other"}
    )
    assert bad.status_code == 422

    async with sessionmaker() as db:
        rows = (await db.scalars(select(MoneyTxn).where(MoneyTxn.user_id == ids["uid"]))).all()
        zomato = [t for t in rows if t.merchant == "Zomato"]
        assert len(zomato) == 1 and zomato[0].source == "statement"
        assert {t.category for t in rows if t.merchant == "Asha Nair"} == {"Rent and housing"}
        assert {t.category for t in rows if t.merchant == "The Shave Co"} == {"Health"}
        assert {t.category for t in rows if t.merchant == "Test Person"} == {"Transfers"}
        assert {t.category for t in rows if t.merchant == "Ravi Menon"} == {"Transport"}
        pizza = next(t for t in rows if t.merchant == "Pizza Place")
        assert pizza.account_id == ids["bank"]
        loan = await db.scalar(select(MoneyLoan).where(MoneyLoan.user_id == ids["uid"]))
        assert (loan.months, loan.emi_paise, loan.account_id) == (9, emi, ids["card"])
        assert abs(loan.rate_bp - 16_00) <= 1
        subs = {
            s.key: s
            for s in (
                await db.scalars(
                    select(MoneySubscription).where(MoneySubscription.user_id == ids["uid"])
                )
            ).all()
        }
        assert subs["netflix"].status == "active"
        assert subs["asha nair"].status == "active"  # rent, tracked as a bill
        ps = subs[money.merchant_key("PlayStation")]
        assert ps.manual and ps.cadence == "yearly" and ps.amount_paise == 4_999_00
        assert ps.next_on == money.add_months(TODAY - timedelta(days=5), 12)


def test_frequent_shops_and_bank_fees_are_not_subscriptions():
    today = date(2026, 11, 22)

    def t(day: str, amount: int, merchant: str, category: str) -> money.Txn:
        return money.Txn(date.fromisoformat(day), amount, "debit", merchant, category)

    txns = [
        t("2026-08-17", 8900, "Forex Markup", "Fees and charges"),
        t("2026-09-17", 8900, "Forex Markup", "Fees and charges"),
        t("2026-10-17", 8900, "Forex Markup", "Fees and charges"),
        t("2026-11-17", 8900, "Forex Markup", "Fees and charges"),
    ]
    # Groceries most weeks; a few orders a month apart happen to cost the same.
    day = date(2026, 8, 1)
    while day < today:
        txns.append(money.Txn(day, 50000 + day.day * 100, "debit", "Zepto", "Groceries"))
        day += timedelta(days=6)
    for d in ("2026-08-27", "2026-09-27", "2026-10-27"):
        txns.append(t(d, 28200, "Zepto", "Groceries"))
    assert money.find_subscriptions(txns, today) == []


def test_people_dcc_fees_and_emi_rates():
    assert money_suggest.looks_like_person("Ravi Menon")
    assert not money_suggest.looks_like_person("The Shave Co")
    assert not money_suggest.looks_like_person("Zomato")
    assert not money_suggest.looks_like_person("Sharma Medical Store")
    assert money.categorise("Dc Intl Pos Txn Dcc", "DC INTL POS TXN DCC+ST") == "Fees and charges"
    assert money_suggest.solve_rate_bp(60_000_00, 6_000_00, 10) == 0
    emi = plan.emi_for(69_000_00, 14_50, 9)
    assert abs(money_suggest.solve_rate_bp(69_000_00, emi, 9) - 14_50) <= 1


async def test_a_late_month_salary_counts_towards_the_next_month(owner, sessionmaker):
    """Paid on the last working day of October, spent in November: November's income."""
    async with sessionmaker() as db:
        uid = (await db.scalar(select(User).where(User.email == "owner@example.com"))).id
        bank = MoneyAccount(user_id=uid, institution="HDFC Bank", mask="1111", kind="bank",
                            name="Pay", created_at=utcnow())  # fmt: skip
        db.add(bank)
        await db.flush()
        db.add(MoneySalary(user_id=uid, monthly_paise=90_000_00, from_on=date(2026, 1, 1),
                           employer="Acme", account_id=bank.id, pay_day=0))  # fmt: skip
        oct_pay = money_salary.pay_date(2026, 10, 0)
        nov_pay = money_salary.pay_date(2026, 11, 0)
        db.add_all([
            _txn(uid, account_id=bank.id, direction="credit", category="Income", merchant="Acme",
                 amount_paise=90_500_00, occurred_on=oct_pay),
            _txn(uid, account_id=bank.id, direction="credit", category="Income", merchant="Acme",
                 amount_paise=90_500_00, occurred_on=nov_pay),
            # Not salary: a small credit late in the month stays where it is.
            _txn(uid, account_id=bank.id, direction="credit", category="Income",
                 merchant="Interest", amount_paise=500_00, occurred_on=date(2026, 10, 30)),
            _txn(uid, account_id=bank.id, amount_paise=20_000_00, occurred_on=date(2026, 11, 10)),
        ])  # fmt: skip
        await db.commit()
        nov = await store.overview(db, uid, "2026-11", date(2026, 11, 22))
        assert nov["income_paise"] == 90_500_00
        assert nov["salary_from"] == oct_pay
        october = await store.overview(db, uid, "2026-10", date(2026, 11, 22))
        assert october["income_paise"] == 500_00
        # The real dates stay as they are without pay months.
        plain = await store.load(db, uid, date(2026, 11, 1), date(2026, 12, 1))
        assert sum(t.amount_paise for t in plain if money.income(t)) == 90_500_00
        assert all(t.paid_on is None for t in plain)
