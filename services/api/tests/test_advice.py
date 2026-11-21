"""Advice: the rules over Money and the Organiser, what the person did with each item, card-wise
subscriptions and following a subscription's move to another card."""

import logging
from datetime import date, timedelta

from sqlalchemy import select

from nexus_common import advice, money
from nexus_common import money_store as store
from nexus_common.models import User
from nexus_common.organiser import local_today

TODAY = date(2026, 11, 21)
CARD = {"id": "c1", "label": "HDFC Bank card ••5678", "kind": "card", "hidden": False}
BANK = {"id": "b1", "label": "SBI ••1234", "kind": "bank", "hidden": False}


def sub(merchant, amount, account="c1", kind="card", **kw):
    base = {
        "key": money.merchant_key(merchant), "merchant": merchant, "status": "active",
        "group": "subscription", "category": "Subscriptions", "cadence": "monthly",
        "amount_paise": amount, "monthly_paise": amount, "next_on": TODAY + timedelta(days=10),
        "last_on": TODAY - timedelta(days=20), "price_change_paise": 0, "previous_paise": None,
        "charged_after_cancel": None, "account_id": account, "account_label": "x",
        "account_kind": kind, "moving": None, "moved_on": None,
    }  # fmt: skip
    return base | kw


def T(days_ago, amount, merchant, category="Shopping", account="c1"):
    return money.Txn(TODAY - timedelta(days=days_ago), amount, "debit", merchant, category,
                     account_id=account)  # fmt: skip


def keys(items):
    return {i.key.split(":", 1)[0]: i for i in items}


def test_card_rules():
    card = CARD | {
        "credit_limit_paise": 100_000_00, "owed_paise": 62_000_00,
        "statement_on": date(2026, 11, 5), "due_on": date(2026, 11, 15),
        "bill": {"status": "overdue", "left_paise": 30_000_00},
        "expires_on": date(2026, 12, 1),
    }  # fmt: skip
    subs = [
        sub("Netflix", 649_00), sub("Spotify", 139_00), sub("YouTube Premium", 189_00),
        sub("ChatGPT", 1_999_00), sub("Claude", 1_800_00), sub("Google One", 130_00),
        sub("Hotstar", 299_00, account="b1", kind="bank"),
        sub("Insurance plan", 18_000_00, cadence="yearly", monthly_paise=1_500_00),
    ]  # fmt: skip
    items = advice.build(advice.Facts(today=TODAY, accounts=[card, BANK], subs=subs))
    k = keys(items)

    use = k["card-use"]
    assert use.level == "act" and "62%" in use.title
    # 62,000 owed on a 1,00,000 limit: 32,000 to pay before 5 Dec to show under 30%.
    assert "₹32,000 before the statement on 05 Dec" in use.body
    assert k["card-overdue"].level == "act" and "₹30,000" in k["card-overdue"].body
    expiry = k["card-expiry"]
    assert expiry.level == "act" and "40 days" in expiry.title and "subscriptions" in expiry.body
    assert k["card-many"].title.startswith("7 subscriptions")
    assert "Hotstar" in k["subs-on-bank"].body
    fx = k["forex"]
    assert "ChatGPT" in fx.body and "Claude" in fx.body
    assert "needs your OK" in k["otp"].title
    dupes = [i for i in items if i.key.startswith("dupes:")]
    assert {i.title for i in dupes} >= {
        "2 video streaming subscriptions",
        "2 AI chat subscriptions",
    }
    # Most urgent first.
    assert [i.level for i in items] == sorted((i.level for i in items), key=advice.LEVELS.index)
    assert items[0].area == "cards"


def test_subscription_rules():
    subs = [
        sub("Netflix", 799_00, price_change_paise=150_00, previous_paise=649_00),
        sub("Amazon Prime", 1_499_00, cadence="yearly", monthly_paise=124_00,
            next_on=TODAY + timedelta(days=12)),
        sub("Gym", 2_000_00, status="cancelled",
            charged_after_cancel={"on": TODAY - timedelta(days=2), "amount_paise": 2_000_00}),
        sub("Zee5", 99_00, status="new"),
        sub("Old card thing", 500_00, moving={"to_id": "c2", "to_label": "ICICI card ••4321",
            "from_label": "HDFC card", "since": TODAY - timedelta(days=20),
            "stuck_on": TODAY - timedelta(days=3)}),
        sub("Spotify", 139_00, moved_on=TODAY - timedelta(days=2)),
    ]  # fmt: skip
    k = keys(advice.build(advice.Facts(today=TODAY, accounts=[CARD], subs=subs)))
    assert "₹1,800 more a year" in k["price"].body
    assert "renews for a year" in k["renews"].title
    assert k["after-cancel"].level == "act"
    assert k["review-subs"].title == "1 repeating payment to confirm"
    assert k["move-stuck"].level == "act" and "ICICI card ••4321" in k["move-stuck"].body
    assert k["moved"].level == "good"


def test_spending_and_life_rules():
    txns = [T(d, 3_000_00, "Zepto", "Groceries") for d in (100, 70, 40)]
    txns += [T(d, 2_500_00, "Blinkit", "Groceries") for d in (19, 15, 10, 5)]
    txns += [T(3, 25_000_00, "Croma"), T(150, 10_00, "Old")]
    txns += [T(2, 450_00, "Swiggy", "Food and dining")] * 2
    txns += [T(30, 590_00, "Late payment fee", "Fees and charges")]
    accounts = [
        BANK
        | {
            "balance_check": {
                "gap_count": 2,
                "days_checked": 40,
                "gaps": [{"on": TODAY, "gap_paise": -1000}],
            }
        }
    ]
    health = {"checks": [
        {"key": "emergency", "status": "act", "title": "Emergency fund: 0.5 months", "body": "b"},
        {"key": "saving", "status": "good", "title": "Saving 25%", "body": "b"},
        {"key": "cards", "status": "act", "title": "Cards", "body": "b"},
    ]}  # fmt: skip
    f = advice.Facts(today=TODAY, accounts=accounts, txns=txns, health=health,
                     life={"inbox_old": 4, "overdue": 2})  # fmt: skip
    items = advice.build(f)
    k = keys(items)
    assert "Groceries is" in k["spike"].title
    assert "Croma" in k["new-big"].title
    assert "2 times" in k["double"].title
    assert "₹590" in k["fees"].title
    assert "SBI ••1234" in k["gaps"].title
    assert k["health"].level == "act" and k["good"].level == "good"
    assert not any(i.key.startswith("health:cards") for i in items)
    assert "4 things" in k["inbox"].title and "2 tasks" in k["overdue"].title

    # Done, dismissed and snoozed items hide; a snooze ends.
    states = {
        k["spike"].key: ("done", None),
        k["fees"].key: ("dismissed", None),
        k["double"].key: ("snoozed", TODAY + timedelta(days=3)),
        k["inbox"].key: ("snoozed", TODAY),
    }
    shown, hidden = advice.visible(items, states, TODAY)
    left = {i.key.split(":", 1)[0] for i in shown}
    assert hidden == 3 and "inbox" in left and not {"spike", "fees", "double"} & left


def test_quiet_when_all_is_well():
    card = CARD | {"credit_limit_paise": 100_000_00, "owed_paise": 10_000_00,
                   "bill": {"status": "paid", "left_paise": 0}}  # fmt: skip
    assert advice.build(advice.Facts(today=TODAY, accounts=[card], subs=[sub("Netflix", 649_00)],
                                     life={"inbox_old": 1, "overdue": 0})) == []  # fmt: skip


def test_next_statement():
    assert advice.next_statement(date(2026, 10, 31), TODAY) == date(2026, 11, 30)
    assert advice.next_statement(date(2026, 11, 21), TODAY) == date(2026, 12, 21)
    assert advice.next_statement(None, TODAY) is None


def P(on, paise, merchant, **kw):
    return money.Parsed(on, paise, "debit", merchant, merchant.upper(), **kw)


async def test_advice_api_moves_and_states(owner, sessionmaker, settings, client_for, invite,
                                           caplog):  # fmt: skip
    caplog.set_level(logging.INFO)
    card = (await owner.post("/api/money/accounts", json={
        "kind": "card", "institution": "HDFC Bank", "mask": "5678",
        "credit_limit_paise": 100_000_00})).json()  # fmt: skip
    bank = (await owner.post("/api/money/accounts", json={
        "kind": "bank", "institution": "SBI", "mask": "1234"})).json()  # fmt: skip
    resp = await owner.patch(f"/api/money/accounts/{card['id']}/details",
                             json={"expires_on": "2027-03-17"})  # fmt: skip
    assert resp.status_code == 200 and resp.json()["expires_on"] == "2027-03-01"

    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        today = local_today(user)
        await store.ingest(db, settings, user.id, [
            *(P(today - timedelta(days=d), 649_00, "Netflix", account_mask="1234",
                institution="State Bank of India") for d in (75, 45, 15)),
            *(P(today - timedelta(days=d), 1_999_00, "OpenAI", account_mask="5678",
                account_kind="card", institution="HDFC Bank") for d in (70, 40, 10)),
        ], source="alert")  # fmt: skip
        await db.commit()

    subs = {s["merchant"]: s for s in (await owner.get("/api/money/subscriptions")).json()["items"]}
    assert subs["Netflix"]["account_id"] == bank["id"] and subs["Netflix"]["account_kind"] == "bank"
    assert subs["OpenAI"]["account_label"] == "HDFC Bank card ••5678"

    out = (await owner.get("/api/advice")).json()
    assert out["money"] is True
    found = {i["key"].split(":", 1)[0]: i for i in out["items"]}
    assert {"subs-on-bank", "forex", "review-subs"} <= set(found)

    # Move Netflix to the card; done once a charge lands there.
    resp = await owner.put("/api/money/subscriptions",
                           json={"merchant": "Netflix", "status": "active",
                                 "move_to_id": card["id"]})  # fmt: skip
    assert resp.status_code == 200, resp.text
    moving = resp.json()["moving"]
    assert moving["to_label"] == "HDFC Bank card ••5678" and moving["stuck_on"] is None
    nobody = "00000000-0000-4000-8000-000000000000"
    stranger = await owner.put(
        "/api/money/subscriptions", json={"merchant": "Netflix", "account_id": nobody}
    )
    assert stranger.status_code == 404
    async with sessionmaker() as db:
        await store.ingest(db, settings, user.id, [
            P(today, 649_00, "Netflix", account_mask="5678", account_kind="card",
              institution="HDFC Bank"),
        ], source="alert")  # fmt: skip
        await db.commit()
    subs = {s["merchant"]: s for s in (await owner.get("/api/money/subscriptions")).json()["items"]}
    netflix = subs["Netflix"]
    assert netflix["moving"] is None and netflix["moved_on"] == today.isoformat()
    assert netflix["account_id"] == card["id"] and netflix["account_set"]
    out = (await owner.get("/api/advice")).json()
    moved = next(i for i in out["items"] if i["key"].startswith("moved:"))
    assert moved["level"] == "good" and "Netflix" in moved["title"]
    assert not any(i["key"].startswith("subs-on-bank") for i in out["items"])

    # Dismiss, then bring back.
    fx = next(i for i in out["items"] if i["key"].startswith("forex:"))
    after = (await owner.post("/api/advice/state",
                              json={"key": fx["key"], "action": "dismissed"})).json()  # fmt: skip
    assert fx["key"] not in {i["key"] for i in after["items"]} and after["hidden"] == 1
    snooze = {"key": fx["key"], "action": "snoozed", "days": 3}
    snoozed = (await owner.post("/api/advice/state", json=snooze)).json()
    assert fx["key"] not in {i["key"] for i in snoozed["items"]}
    (aside,) = snoozed["set_aside"]
    assert aside["key"] == fx["key"] and aside["status"] == "snoozed" and aside["until"]
    back = (await owner.post("/api/advice/state",
                             json={"key": fx["key"], "action": "restore"})).json()  # fmt: skip
    assert fx["key"] in {i["key"] for i in back["items"]} and back["hidden"] == 0
    assert back["set_aside"] == []

    # A member gets Organiser advice only.
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    theirs = (await friend.get("/api/advice")).json()
    assert theirs["money"] is False and all(i["area"] == "life" for i in theirs["items"])

    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    for secret in ("Netflix", "OpenAI"):
        assert secret not in logged, secret
