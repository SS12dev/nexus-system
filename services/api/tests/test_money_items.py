"""What was bought: items read from a quick-commerce order email or typed by the person, sorted
into kinds, split across what the order really cost; the "what did you buy?" question; and the
day-by-day view with its habits. Names and amounts are made up."""

import itertools
from datetime import date, timedelta

from sqlalchemy import select

from nexus_common import money_habits, money_items, money_suggest
from nexus_common import money_store as store
from nexus_common.models import MoneyAccount, MoneyItem, MoneyTxn, User, utcnow
from nexus_common.money_orders import parse_order

TODAY = date.today()
_keys = itertools.count()


def test_kinds_from_names():
    cases = {
        "Amul Taaza Toned Milk 500 ml": "dairy",
        "Lay's Magic Masala Chips": "snacks",
        "Coca-Cola 750 ml": "drinks",
        "Maggi 2-Minute Noodles": "instant",
        "Surf Excel Matic Liquid": "household",
        "Parachute Hair Oil": "personal",
        "Fortune Sunflower Oil 1 L": "staples",
        "Onion 1 kg": "fresh",
        "Fresh Cream": "dairy",
        "Milk Chocolate Bar": "snacks",
        "Cold Coffee": "drinks",
        "Toilet Roll (4 pcs)": "household",
        "Handling Fee": "fees",
        "Small Cart Fee": "fees",
        "Dolo 650 Tablet": "health",
        "Something Unheard Of": "other",
    }
    assert {n: money_items.kind_of(n) for n in cases} == cases


def test_reads_items_from_an_order_email():
    one_line = """Your order summary
2 x Amul Taaza Toned Milk 500 ml ₹54
Lay's Classic Salted ₹20
Coca-Cola 750 ml x 2 ₹80
Item Total ₹154
Handling Fee ₹4
Delivery Fee ₹30
Discount -₹20
Grand Total ₹168
"""
    got = [(i.name, i.quantity, i.price_paise, i.kind) for i in money_items.parse_items(one_line)]
    assert got == [
        ("Amul Taaza Toned Milk 500 ml", 2, 54_00, "dairy"),
        ("Lay's Classic Salted", 1, 20_00, "snacks"),
        ("Coca-Cola 750 ml", 2, 80_00, "drinks"),
        ("Handling Fee", 1, 4_00, "fees"),
        ("Delivery Fee", 1, 30_00, "fees"),
    ]

    # A table flattened to lines: name, quantity, price.
    table = """Items
Qty
Price
Banana Robusta
1
45.00
Britannia Bread
2
90.00
Sub Total : Rs.135.00
Grand Total : Rs.135.00
"""
    got = [(i.name, i.quantity, i.price_paise) for i in money_items.parse_items(table)]
    assert got == [("Banana Robusta", 1, 45_00), ("Britannia Bread", 2, 90_00)]
    assert money_items.parse_items("Thanks for shopping. Grand Total ₹500") == []


# A scrubbed copy of a real Zomato order email: no name, order number or address.
ZOMATO = """Zoooooooooooop! That was quick.
Hi Customer,
Your order from Urban Paratha was delivered in just 26 minutes.
ORDER ID: 1234567
Delivered
Urban Paratha
2 X Paneer Paratha
1 X Rasgulla
1 X Lime Soda
1 X Veg Raita
2 X Boondi Raita
1 X Gobi Paratha
Total paid - ₹503
Eternal employees or representatives will NEVER ask you for your personal information i.e. your
bank account details, password, PIN, CVV, OTP etc.
©2026 - Zomato, All rights reserved.
"""


def test_reads_a_food_order():
    o = parse_order(
        "Your Zomato order from Urban Paratha", ZOMATO, TODAY, "Zomato <noreply@zomato.com>"
    )
    assert (o.shop, o.amount_paise, o.place) == ("Zomato", 503_00, "Urban Paratha")
    got = [(i.name, i.quantity, i.price_paise, i.kind) for i in o.items]
    assert got == [
        ("Paneer Paratha", 2, None, "meals"),
        ("Rasgulla", 1, None, "sweets"),
        ("Lime Soda", 1, None, "drinks"),
        ("Veg Raita", 1, None, "meals"),
        ("Boondi Raita", 2, None, "meals"),
        ("Gobi Paratha", 1, None, "meals"),
    ]
    # No prices: the total is split by item count.
    split = money_items.shares([(i.kind, i.price_paise, i.quantity) for i in o.items], 503_00)
    assert split == {"meals": 377_26, "sweets": 62_87, "drinks": 62_87}
    kinds = {n: money_items.food_kind(n) for n in (
        "Shawarma & Pepsi Combo", "Masala Fizz.", "McFlurry Oreo ( M )", "Black Pepsi Can",
        "Classic Box of 6 Donuts (Buy 5 Get 1 Free)", "Hyderabadi Dum Chicken Biryani",
    )}  # fmt: skip
    assert list(kinds.values()) == ["meals", "drinks", "sweets", "drinks", "sweets", "meals"]
    # Refunds and memberships aren't orders.
    refund = "Refund Processed\nEnoki\n3 Items • ₹1,464.56 paid via Bank\nAmount\n₹1,464.56"
    zomato = "Zomato <noreply@zomato.com>"
    assert parse_order("Refund processed for order #123", refund, TODAY, zomato) is None


def test_reads_what_was_typed():
    got = money_items.parse_typed("milk 60, chips 40, coke and 2 x maggi")
    assert [(i.name, i.price_paise, i.quantity, i.kind) for i in got] == [
        ("milk", 60_00, 1, "dairy"),
        ("chips", 40_00, 1, "snacks"),
        ("coke", None, 1, "drinks"),
        ("maggi", None, 2, "instant"),
    ]
    assert money_items.parse_typed(" , 50 ,") == []


def test_shares_split_what_the_order_really_cost():
    # A ₹20 discount comes off the items, not the fees.
    assert money_items.shares([("dairy", 60_00), ("snacks", 40_00), ("fees", 15_00)], 95_00) == {
        "fees": 15_00, "dairy": 48_00, "snacks": 32_00,
    }  # fmt: skip
    # Unpriced items share what the priced ones leave.
    assert money_items.shares([("dairy", 60_00), ("snacks", None)], 100_00) == {
        "dairy": 60_00,
        "snacks": 40_00,
    }
    assert money_items.shares([("mixed", None)], 347_00) == {"mixed": 347_00}
    assert (
        sum(money_items.shares([("a", 33_33), ("b", 33_33), ("c", 1)], 100_00).values()) == 100_00
    )


def test_basket_shops():
    assert money_items.is_basket("Zepto")
    assert money_items.is_basket("BLINKIT")
    assert money_items.is_basket("Swiggy Instamart")
    assert not money_items.is_basket("Swiggy")
    assert money_habits.is_delivery("Swiggy") and not money_habits.is_delivery("Instamart")


def _txn(uid, **kw) -> MoneyTxn:
    base = dict(
        user_id=uid, occurred_on=TODAY - timedelta(days=2), amount_paise=100_00,
        direction="debit", merchant="Shop", category="Other", category_by="auto",
        narration_enc="", channel="upi", source="alert", dedupe_key=f"i{next(_keys)}",
        created_at=utcnow(),
    )  # fmt: skip
    return MoneyTxn(**{**base, **kw})


async def _user(db) -> User:
    return await db.scalar(select(User).where(User.email == "owner@example.com"))


async def test_order_items_follow_the_payment(owner, sessionmaker, settings):
    body = (
        "Order No: ZP123\n2 x Amul Milk 500 ml ₹54\nKurkure Masala Munch ₹20\n"
        "Handling Fee ₹6\nGrand Total ₹80"
    )
    order = parse_order(
        "Your Zepto order", body, TODAY - timedelta(days=1), "Zepto <no@zeptonow.com>"
    )
    assert order is not None and [i.kind for i in order.items] == ["dairy", "snacks", "fees"]
    async with sessionmaker() as db:
        user = await _user(db)
        bank = MoneyAccount(user_id=user.id, institution="HDFC Bank", mask="1111", kind="bank",
                            created_at=utcnow())  # fmt: skip
        db.add(bank)
        await db.flush()
        # The order first; the statement row a day later is matched to it without asking.
        assert await store.record_order(db, settings, user.id, order, "zm1") == "added"
        db.add(_txn(user.id, account_id=bank.id, merchant="ZEPTO MARKETPLACE", amount_paise=80_00,
                    source="statement", occurred_on=TODAY))  # fmt: skip
        await db.flush()
        assert await money_suggest.match_orders(db, user.id) == 1
        await db.commit()
        (pay,) = (await db.scalars(select(MoneyTxn))).all()
        items = await money_habits.items_of(db, settings, pay.id)
        assert [(i["name"], i["kind"]) for i in items] == [
            ("Amul Milk 500 ml", "dairy"),
            ("Kurkure Masala Munch", "snacks"),
            ("Handling Fee", "fees"),
        ]
        # Names are stored encrypted.
        raw = (await db.scalars(select(MoneyItem.name_enc))).all()
        assert all("Amul" not in r for r in raw)

    detail = (await owner.get(f"/api/money/transactions/{pay.id}")).json()
    assert detail["basket"] and len(detail["items"]) == 3
    # The person corrects one, then replaces them all by typing.
    r = await owner.patch(f"/api/money/items/{detail['items'][1]['id']}", json={"kind": "instant"})
    assert r.json()["kind"] == "instant"
    r = await owner.put(
        f"/api/money/transactions/{pay.id}/items", json={"text": "milk 54, chips 20"}
    )
    assert [i["kind"] for i in r.json()["items"]] == ["dairy", "snacks"]
    r = await owner.put(f"/api/money/transactions/{pay.id}/items", json={"kind": "wrong"})
    assert r.status_code == 422


async def test_asks_what_was_bought_and_shows_the_day(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await _user(db)
        uid = user.id
        db.add_all([
            _txn(uid, merchant="Blinkit", category="Groceries", amount_paise=347_00,
                 occurred_on=TODAY - timedelta(days=1)),
            _txn(uid, merchant="Zepto", category="Groceries", amount_paise=212_00,
                 occurred_on=TODAY - timedelta(days=1)),
            _txn(uid, merchant="Swiggy", category="Food and dining", amount_paise=450_00,
                 occurred_on=TODAY - timedelta(days=1)),
            _txn(uid, merchant="Landlord", category="Rent and housing", amount_paise=15_000_00,
                 occurred_on=TODAY - timedelta(days=1)),
            # Long ago: not asked.
            _txn(uid, merchant="Zepto", category="Groceries", amount_paise=99_00,
                 occurred_on=TODAY - timedelta(days=80)),
        ])  # fmt: skip
        await db.commit()

    got = (await owner.get("/api/money/suggestions")).json()["items"]
    baskets = [s for s in got if s["kind"] == "basket"]
    assert sorted(s["merchant"] for s in baskets) == ["Blinkit", "Zepto"]
    blinkit = next(s for s in baskets if s["merchant"] == "Blinkit")
    assert blinkit["guess"] is None and "snacks" in {c["id"] for c in blinkit["choices"]}
    r = await owner.post(
        "/api/money/suggestions/answer", json={"key": blinkit["key"], "choice": "snacks"}
    )
    assert r.status_code == 200 and "snacks" in r.json()["done"].lower()
    zepto = next(s for s in baskets if s["merchant"] == "Zepto")
    r = await owner.post(
        "/api/money/suggestions/answer",
        json={"key": zepto["key"], "choice": "items", "text": "bread 50, coke 40"},
    )
    assert r.status_code == 200, r.text
    assert not [s for s in r.json()["items"] if s["kind"] == "basket"]
    bad = await owner.post(
        "/api/money/suggestions/answer", json={"key": zepto["key"], "choice": "items", "text": ""}
    )
    assert bad.status_code == 422

    month = (TODAY - timedelta(days=1)).strftime("%Y-%m")
    day = (await owner.get(f"/api/money/daily?month={month}")).json()
    yesterday = next(d for d in day["days"] if d["on"] == (TODAY - timedelta(days=1)).isoformat())
    assert yesterday["spent_paise"] == 347_00 + 212_00 + 450_00 + 15_000_00
    # Rent is fixed, not an everyday habit.
    assert yesterday["everyday_paise"] == 347_00 + 212_00 + 450_00
    assert yesterday["items"][0]["merchant"] == "Landlord" and yesterday["items"][0]["fixed"]
    b = day["baskets"]
    assert (b["orders"], b["spent_paise"], b["told_paise"], b["untold"]) == (2, 559_00, 559_00, 0)
    by = {k["kind"]: k["spent_paise"] for k in b["by_kind"]}
    # Zepto ₹212 split between bread (dairy) and coke (drinks) by their prices.
    assert by == {"snacks": 347_00, "dairy": 117_77 + 1, "drinks": 94_22}
    assert b["treats_paise"] == 347_00 + 94_22
    assert (day["delivery"]["orders"], day["delivery"]["spent_paise"]) == (1, 450_00)
    assert len(day["weekdays"]) == 7
