"""Money suggests: the one-tap questions Money asks instead of a list of chores, each answer
applied as the person would by hand, orders matched to payments read later without asking,
and "accept all" taking Money's guesses. Names and amounts are made up."""

import itertools
from datetime import date, timedelta

from sqlalchemy import select

from nexus_common import money
from nexus_common.models import (
    MoneyAccount,
    MoneyLoan,
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
    uid = (await db.scalar(select(User).where(User.email == "owner@example.com"))).id
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
    # The Zomato order took the statement row's payment without a question.
    assert got["matched"] == 1
    by = {i["kind"]: i for i in got["items"]}
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

    async def ask(kind: str, choice: str) -> dict:
        key = next(i["key"] for i in (await owner.get("/api/money/suggestions")).json()["items"]
                   if i["kind"] == kind)  # fmt: skip
        r = await owner.post("/api/money/suggestions/answer", json={"key": key, "choice": choice})
        assert r.status_code == 200, r.text
        return r.json()

    assert "12 months" in (await ask("emi", "12"))["done"]
    assert "PlayStation Plus" in (await ask("yearly", "yearly"))["done"]
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
        pizza = next(t for t in rows if t.merchant == "Pizza Place")
        assert pizza.account_id == ids["bank"]
        loan = await db.scalar(select(MoneyLoan).where(MoneyLoan.user_id == ids["uid"]))
        assert (loan.months, loan.emi_paise, loan.account_id) == (12, 5_000_00, ids["card"])
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
