"""Order emails: the shop, total and payment mode read from a Swiggy, Domino's or Amazon email;
an order names the bank payment it matches, becomes a transaction of its own when none does
(which the bank's alert later joins), and is asked about when nothing explains it."""

from datetime import date, timedelta

from sqlalchemy import select

from nexus_common import advice, money
from nexus_common import money_store as store
from nexus_common.models import MoneyAccount, MoneyTxn, User
from nexus_common.money_orders import paid_by, parse_order

# A scrubbed copy of a real order email: no name, address or phone.
DOMINOS = """Hi Customer

Thank you for choosing Domino's. This email contains important information about your order.
\tOrder Confirmed
Order No. 232|21-11-2026|23:53:56
Order Total
Rs.495.00\t
Payment Mode
Cash\t
Order Type
Current Order
Items
Qty
Price
Chicken Kebab Rolls
1
119.00
Sub Total\t:\tRs.496.00
Discount\t:\t- Rs.50.00
Taxes & Charges\t:\tRs.48.55
Grand Total\t:\tRs.495.00
"""
DAY = date(2026, 11, 21)


def test_reads_order_emails():
    o = parse_order("Order Successful", DOMINOS, DAY, "Dominos India <do-not-reply@dominos.co.in>")
    assert (o.shop, o.amount_paise, o.paid_by, o.category) == (
        "Domino's Pizza", 495_00, "cash", "Food and dining"
    )  # fmt: skip
    assert o.order_no.startswith("232")

    swiggy = parse_order(
        "Your Swiggy order was delivered",
        "Order No: 1234567890 Item Total ₹420 Delivery fee ₹30 Paid Via UPI Order Total ₹ 452.50",
        DAY,
        "Swiggy <noreply@swiggy.in>",
    )
    assert (swiggy.shop, swiggy.amount_paise, swiggy.paid_by) == ("Swiggy", 452_50, "upi")

    amazon = parse_order(
        "Your Amazon.in order #403-1234567-1234567",
        "Order Total: ₹1,299.00 Paid with HDFC Bank Credit Card ending in 5678",
        DAY,
        '"Amazon.in" <auto-confirm@amazon.in>',
    )
    assert (amazon.paid_by, amazon.card_mask, amazon.amount_paise) == ("card", "5678", 1299_00)

    # A shop Money doesn't know is named from the sender; the sub total is never the total.
    local = parse_order(
        "Your order receipt", "Order ID: AB1234 Sub Total ₹300 Total ₹320 Payment method: GPay",
        DAY, "Corner Bakery <orders@cornerbakery.in>",
    )  # fmt: skip
    assert (local.shop, local.amount_paise, local.paid_by) == ("Corner Bakery", 320_00, "upi")

    # Not purchases.
    zomato = "Zomato <noreply@zomato.com>"
    assert parse_order("Your order has been cancelled", "Order Total ₹200", DAY, zomato) is None
    bank = "HDFC Bank <alerts@hdfcbank.bank.in>"
    assert parse_order("Rs 495 debited", "Rs 495 debited. Total 495", DAY, bank) is None
    news = "Some Shop <news@shop.in>"
    assert parse_order("Weekly newsletter", "Big sale. Total savings ₹500", DAY, news) is None
    assert paid_by("Payment method: Visa card ending 4417 Cashback ₹20") == ("card", "4417")


async def _user(db) -> User:
    return await db.scalar(select(User).where(User.email == "owner@example.com"))


def _order(day, amount, how="unknown", shop="Domino's Pizza", no="A1"):
    from nexus_common.money_orders import Order

    return Order(day, amount, shop, "Food and dining", how, order_no=no)


async def test_orders_meet_their_payments(owner, sessionmaker, settings):
    today = date.today()
    async with sessionmaker() as db:
        user = await _user(db)
        # The bank's alert first, under the payment gateway's name: the order names it.
        await store.ingest(db, settings, user.id, [
            money.Parsed(today - timedelta(days=3), 452_50, "debit", "PAYU", "UPI-PAYU",
                         channel="upi", account_mask="1234", institution="HDFC Bank"),
        ], source="alert")  # fmt: skip
        got = await store.record_order(
            db, settings, user.id, _order(today - timedelta(days=3), 452_50, "upi", "Swiggy"), "m1"
        )
        assert got == "matched"
        again = _order(today - timedelta(days=3), 452_50, "upi", "Swiggy")
        assert await store.record_order(db, settings, user.id, again, "m1") == "skipped"

        # The order first; the bank's alert a day later joins its row.
        order = _order(today - timedelta(days=2), 1299_00, "card", "Amazon", "B2")
        assert await store.record_order(db, settings, user.id, order, "m2") == "added"
        await store.ingest(db, settings, user.id, [
            money.Parsed(today - timedelta(days=1), 1299_00, "debit", "AMAZON PAY", "POS AMZ",
                         channel="card", account_mask="5678", account_kind="card",
                         institution="HDFC Bank"),
        ], source="alert")  # fmt: skip

        # Cash on delivery: recorded as cash, nothing to ask.
        cod = _order(today - timedelta(days=2), 495_00, "cash", no="C3")
        assert await store.record_order(db, settings, user.id, cod, "m3") == "added"
        # No payment matches this one: asked about from the next day.
        lost = _order(today - timedelta(days=1), 640_00, "upi", "Zomato", "D4")
        assert await store.record_order(db, settings, user.id, lost, "m4") == "added"
        await db.commit()

        rows = (await db.scalars(select(MoneyTxn).order_by(MoneyTxn.amount_paise))).all()
        by = {t.amount_paise: t for t in rows}
        assert len(rows) == 4
        assert by[452_50].merchant == "Swiggy" and by[452_50].source == "alert"
        assert by[452_50].category == "Food and dining"
        amazon = by[1299_00]
        card = await db.get(MoneyAccount, amazon.account_id)
        assert amazon.merchant == "Amazon" and card.kind == "card" and amazon.channel == "card"
        assert by[495_00].channel == "cash" and by[495_00].account_id is None

    asks = (await owner.get("/api/money/orders/to-confirm")).json()["items"]
    assert [(a["merchant"], a["amount_paise"]) for a in asks] == [("Zomato", 640_00)]
    (ask,) = asks

    # The advice item names it and leads to the answer.
    items = advice.orders_unpaid(advice.Facts(today=today, orders=[
        {**ask, "occurred_on": date.fromisoformat(ask["occurred_on"])}]))  # fmt: skip
    assert items[0].level == "act" and "Zomato ₹640" in items[0].body
    assert items[0].url == "/money/transactions?orders=1"
    view = (await owner.get("/api/advice")).json()
    assert any(i["key"].startswith("orders:") for i in view["items"])

    accounts = (await owner.get("/api/money/accounts")).json()
    bank = next(a for a in accounts if a["kind"] == "bank")
    paid = await owner.post(f"/api/money/orders/{ask['id']}/paid",
                            json={"how": "account", "account_id": bank["id"]})  # fmt: skip
    assert paid.status_code == 200, paid.text
    assert paid.json()["items"] == []
    # Only the person's own accounts, and only orders.
    bad = await owner.post(f"/api/money/orders/{ask['id']}/paid", json={"how": "account"})
    assert bad.status_code == 422
    async with sessionmaker() as db:
        row = await db.get(MoneyTxn, by[452_50].id)
    wrong = await owner.post(f"/api/money/orders/{row.id}/paid", json={"how": "cash"})
    assert wrong.status_code == 422
    left = await owner.post(f"/api/money/orders/{ask['id']}/paid", json={"how": "leave_out"})
    async with sessionmaker() as db:
        assert (await db.get(MoneyTxn, ask["id"])).excluded is True
    assert left.status_code == 200
