"""Order screenshots and cigarettes: a Zepto or Blinkit order read from a screenshot by the
on-device vision model (faked here), matched to its payment and saved after review; and what
went on cigarettes and tobacco, from paan-shop payments and from the items in orders. Names and
amounts are made up."""

import itertools
from datetime import date, timedelta

from sqlalchemy import select

from nexus_common import money, money_habits, money_items, money_suggest, money_vision
from nexus_common.models import MoneyBudget, MoneyTxn, User, utcnow
from nexus_common.organiser import local_today

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_keys = itertools.count()


def _txn(uid, on: date, **kw) -> MoneyTxn:
    base = dict(
        user_id=uid, occurred_on=on, amount_paise=100_00, direction="debit", merchant="Shop",
        category="Other", category_by="auto", narration_enc="", channel="upi",
        source="alert", dedupe_key=f"s{next(_keys)}", created_at=utcnow(),
    )  # fmt: skip
    return MoneyTxn(**{**base, **kw})


async def _user(db) -> User:
    return await db.scalar(select(User).where(User.email == "owner@example.com"))


def test_cigarettes_are_their_own_kind_and_category():
    for name in ("Gold Flake Kings 10s", "Marlboro Advance", "Classic Milds 20",
                 "Bic Lighter", "Wills Navy Cut"):  # fmt: skip
        assert money_items.kind_of(name) == "tobacco", name
    # Help to quit is health; chips that say "classic" aren't cigarettes.
    assert money_items.kind_of("Nicotex Nicotine Gum 2 mg") == "health"
    assert money_items.kind_of("Lays Classic Salted") == "snacks"
    assert "tobacco" not in money_items.TREATS
    assert "Smoking and tobacco" in money.CATEGORIES
    assert money.categorise("SHARMA PAAN CORNER") == "Smoking and tobacco"
    assert money.categorise("Ravi Pan Shop") == "Smoking and tobacco"
    assert money.categorise("Paani Puri Centre") == "Other"
    assert money.categorise("Japan Cafe") == "Food and dining"
    assert ("Smoking and tobacco", "Cigarettes or a paan shop") in money_suggest.PERSON_CHOICES
    assert "tobacco" in {k for k, _ in money_suggest.BASKET_CHOICES}


def test_reads_an_order_screenshot_answer():
    today = date(2026, 11, 25)
    raw = {
        "shop": "Zepto",
        "date": "2026-11-24",
        "total_paid": 412,
        "items": [
            {"name": "Amul Taaza Milk 500 ml", "quantity": 2, "price": 56, "kind": "dairy"},
            {"name": "Gold Flake Kings", "quantity": 1, "price": 190, "kind": "other"},
            {"name": "Kurkure Masala Munch", "quantity": 1, "price": 20, "kind": "snacks"},
            # The model's kind is used where the words don't know the item.
            {"name": "Haldiram Moong Dal", "quantity": 1, "price": 60, "kind": "snacks"},
            {"name": "Handling fee", "quantity": 1, "price": 11, "kind": "other"},
            {"name": "", "quantity": 1, "price": 5, "kind": "other"},
            {"name": "Mystery", "quantity": 500, "price": None, "kind": "not-a-kind"},
        ],
        "fees": [{"name": "Delivery fee", "amount": 30}, {"name": "Tip", "amount": "x"}],
    }
    order = money_vision.clean_order(raw, today)
    assert (order.shop, order.occurred_on, order.total_paise) == ("Zepto", date(2026, 11, 24),
                                                                  412_00)  # fmt: skip
    got = [(i.name, i.quantity, i.price_paise, i.kind) for i in order.items]
    assert got == [
        ("Amul Taaza Milk 500 ml", 2, 56_00, "dairy"),
        ("Gold Flake Kings", 1, 190_00, "tobacco"),
        ("Kurkure Masala Munch", 1, 20_00, "snacks"),
        ("Haldiram Moong Dal", 1, 60_00, "staples"),
        ("Handling fee", 1, 11_00, "fees"),
        ("Mystery", 99, None, "other"),
        ("Delivery fee", 1, 30_00, "fees"),
    ]
    assert order.dropped == 2
    # A date that isn't one is left out; "30 Dec" read with this year means last year's.
    assert money_vision.clean_order({"date": "Tuesday"}, today).occurred_on is None
    assert money_vision.clean_order({"date": "2026-12-30"}, today).occurred_on == date(2025, 12, 30)


async def test_order_screenshot_is_matched_saved_and_counted(
    owner, sessionmaker, settings, monkeypatch
):
    async with sessionmaker() as db:
        user = await _user(db)
        uid = user.id
        today = local_today(user)
        on = today - timedelta(days=1) if today.day > 1 else today
        db.add_all([
            _txn(uid, on, merchant="ZEPTO MARKETPLACE", category="Groceries",
                 amount_paise=317_00),
            _txn(uid, on, merchant="Blinkit", category="Groceries", amount_paise=180_00),
            _txn(uid, on, merchant="Sharma Paan Corner", category="Smoking and tobacco",
                 amount_paise=190_00),
        ])  # fmt: skip
        db.add(MoneyBudget(user_id=uid, category="Smoking and tobacco",
                           monthly_paise=2_000_00, created_at=utcnow()))  # fmt: skip
        await db.commit()

    answer = {
        "shop": "Zepto",
        "date": on.isoformat(),
        "total_paid": 317,
        "items": [
            {"name": "Amul Milk 500 ml", "quantity": 2, "price": 56, "kind": "dairy"},
            {"name": "Gold Flake Kings", "quantity": 1, "price": 190, "kind": "other"},
            {"name": "Lays Classic Salted", "quantity": 1, "price": 20, "kind": "snacks"},
            {"name": "Coke 750 ml", "quantity": 1, "price": 40, "kind": "drinks"},
        ],
        "fees": [{"name": "Handling fee", "amount": 11}],
    }

    async def fake_read(cfg, image, day, **kw):
        assert image == PNG
        return answer

    monkeypatch.setattr(money_vision, "read_order", fake_read)
    files = {"file": ("o.png", PNG, "image/png")}
    r = await owner.post("/api/money/orders/screenshot", files=files)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["match"]["merchant"] == "ZEPTO MARKETPLACE" and not got["match"]["has_items"]
    assert got["candidates"][0]["id"] == got["match"]["id"]
    assert got["listed_paise"] == 56_00 + 190_00 + 20_00 + 40_00 + 11_00
    assert [i["kind"] for i in got["items"]] == ["dairy", "tobacco", "snacks", "drinks", "fees"]

    bad = {"file": ("o.txt", b"hello", "text/plain")}
    assert (await owner.post("/api/money/orders/screenshot", files=bad)).status_code == 415

    # Saved after review, with one kind corrected.
    items = [{k: i[k] for k in ("name", "quantity", "price_paise", "kind")} for i in got["items"]]
    items[2]["kind"] = "instant"
    txn_id = got["match"]["id"]
    r = await owner.put(f"/api/money/transactions/{txn_id}/items", json={"items": items})
    assert r.status_code == 200, r.text
    assert [i["kind"] for i in r.json()["items"]] == [
        "dairy", "tobacco", "instant", "drinks", "fees"
    ]  # fmt: skip
    items[0]["kind"] = "nonsense"
    r = await owner.put(f"/api/money/transactions/{txn_id}/items", json={"items": items})
    assert r.status_code == 422

    # Uploaded from a payment: that payment, even with another total.
    async with sessionmaker() as db:
        blinkit = await db.scalar(select(MoneyTxn).where(MoneyTxn.merchant == "Blinkit"))
    r = await owner.post(
        "/api/money/orders/screenshot", files=files, data={"txn_id": str(blinkit.id)}
    )
    assert r.json()["match"]["id"] == str(blinkit.id)

    # Cigarettes: the paan shop and the cigarettes in the Zepto order.
    day = (await owner.get(f"/api/money/daily?month={on:%Y-%m}")).json()
    smoke = day["smoking"]
    assert smoke["spent_paise"] == 190_00 + 190_00
    assert smoke["days"] == 1 and smoke["last_on"] == on.isoformat()
    assert smoke["budget_paise"] == 2_000_00
    assert smoke["shops"] == [{"merchant": "Sharma Paan Corner", "count": 1, "spent_paise": 190_00}]
    that = next(d for d in day["days"] if d["on"] == on.isoformat())
    assert that["tobacco_paise"] == 380_00
    # Cigarettes aren't counted with the treats (snacks, drinks and fees) in the baskets.
    assert day["baskets"]["treats_paise"] == 20_00 + 40_00 + 11_00
    assert {k["kind"] for k in day["baskets"]["by_kind"]} >= {"tobacco"}


async def test_order_screenshot_without_a_clear_payment_offers_a_pick(
    owner, sessionmaker, settings
):
    async with sessionmaker() as db:
        user = await _user(db)
        today = local_today(user)
        db.add_all([
            _txn(user.id, today, merchant="Blinkit", amount_paise=300_00),
            _txn(user.id, today, merchant="Blinkit", amount_paise=300_00),
            _txn(user.id, today - timedelta(days=1), merchant="Zepto", amount_paise=250_00),
            _txn(user.id, today - timedelta(days=30), merchant="Zepto", amount_paise=300_00),
        ])  # fmt: skip
        await db.commit()
        match, rows = await money_habits.order_candidates(
            db, user.id, shop="Blinkit", on=today, total_paise=300_00, today=today
        )
        # Two Blinkit payments of the same amount: Money can't tell which, so it asks.
        assert match is None
        assert [t.amount_paise for t in rows] == [300_00, 300_00, 250_00]
        match, rows = await money_habits.order_candidates(
            db, user.id, shop="Zepto", on=None, total_paise=250_00, today=today
        )
        assert match is not None and match.merchant == "Zepto"
