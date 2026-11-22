"""Cards: a debit card belongs to its bank account (its alerts land there), a credit card can
say which account pays its bill, and savings can take deposits monthly, quarterly or yearly."""

from datetime import date

from sqlalchemy import select

from nexus_common import money
from nexus_common import money_plan as plan
from nexus_common import money_store as store
from nexus_common.models import MoneyAccount, MoneyTxn, User


async def _user(db) -> User:
    return await db.scalar(select(User).where(User.email == "owner@example.com"))


async def test_debit_card_alerts_land_on_the_bank_account(owner, sessionmaker, settings):
    bank = (await owner.post("/api/money/accounts", json={
        "kind": "bank", "institution": "ICICI Bank", "mask": "6320"})).json()  # fmt: skip
    # Alerts from the debit card arrived before Money knew it: they made an account of their own.
    async with sessionmaker() as db:
        user = await _user(db)
        await store.ingest(db, settings, user.id, [
            money.Parsed(date(2026, 11, 1), 2_000_00, "debit", "Croma", "POS",
                         account_mask="4455", institution="ICICI Bank"),
        ], source="alert")  # fmt: skip
        await db.commit()
        assert len((await db.scalars(select(MoneyAccount))).all()) == 2

    debit = {"kind": "debit", "mask": "4455", "account_id": bank["id"]}
    resp = await owner.post("/api/money/accounts", json=debit)
    assert resp.status_code == 201, resp.text
    assert resp.json()["moved"] == 1
    async with sessionmaker() as db:
        (only,) = (await db.scalars(select(MoneyAccount))).all()
        assert str(only.id) == bank["id"] and only.card_mask == "4455"
        # New alerts from the card land on the bank account too.
        await store.ingest(db, settings, only.user_id, [
            money.Parsed(date(2026, 11, 2), 500_00, "debit", "Zepto", "POS",
                         account_mask="4455", institution="ICICI Bank"),
        ], source="alert")  # fmt: skip
        await db.commit()
        txns = (await db.scalars(select(MoneyTxn))).all()
        assert {str(t.account_id) for t in txns} == {bank["id"]}

    view = (await owner.get("/api/money/balances")).json()
    (item,) = view["items"]
    assert item["card_mask"] == "4455"
    # A debit card needs its bank account.
    bad = await owner.post("/api/money/accounts", json={"kind": "debit", "mask": "1111"})
    assert bad.status_code == 422


async def test_credit_card_with_expiry_and_bill_account(owner):
    bank = (await owner.post("/api/money/accounts", json={
        "kind": "bank", "institution": "HDFC Bank", "mask": "1298"})).json()  # fmt: skip
    card = await owner.post("/api/money/accounts", json={
        "kind": "card", "institution": "ICICI Bank", "mask": "4321", "name": "Amazon Pay card",
        "credit_limit_paise": 2_00_000_00, "expires_on": "2029-08-20",
        "bill_from_id": bank["id"],
    })  # fmt: skip
    assert card.status_code == 201, card.text
    view = (await owner.get("/api/money/balances")).json()
    c = next(a for a in view["items"] if a["kind"] == "card")
    assert c["expires_on"] == "2029-08-01" and c["bill_from_label"] == "HDFC Bank ••1298"
    resp = await owner.patch(f"/api/money/accounts/{c['id']}/details",
                             json={"bill_from_id": None})  # fmt: skip
    assert resp.json()["bill_from_id"] is None


async def test_ppf_can_take_a_monthly_deposit(owner):
    resp = await owner.post("/api/money/savings", json={
        "kind": "ppf", "institution": "SBI", "amount_paise": 10_000_00,
        "contribution_paise": 500_00, "contribution_cadence": "monthly",
        "balance_on": "2026-04-01",
    })  # fmt: skip
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["contribution_cadence"] == "monthly" and out["monthly_paise"] == 500_00
    # Left out, PPF stays yearly.
    yearly = (await owner.post("/api/money/savings", json={
        "kind": "ppf", "institution": "SBI", "amount_paise": 1_00_000_00,
        "contribution_paise": 12_000_00})).json()  # fmt: skip
    assert yearly["contribution_cadence"] == "yearly" and yearly["monthly_paise"] == 1_000_00


def test_deposit_cadences():
    def worth(cadence):
        a = plan.Asset(kind="nps", amount_paise=0, rate_bp=0, balance_on=date(2026, 1, 1),
                       contribution_paise=1_000_00, cadence=cadence)  # fmt: skip
        return plan.asset_value(a, date(2027, 1, 1))

    assert worth("monthly") == 12_000_00
    assert worth("quarterly") == 4_000_00
    assert worth("yearly") == 1_000_00
    ppf = plan.Asset(kind="ppf", amount_paise=0, balance_on=date(2026, 1, 1),
                     contribution_paise=1_000_00)  # fmt: skip
    assert plan.asset_value(ppf, date(2027, 1, 1)) == 1_000_00  # once, in April
    assert plan.contribution_cadence("ppf", None) == "yearly"
    assert plan.contribution_cadence("epf", None) == "monthly"


async def test_another_mailbox_listed_masked_and_removed(owner, sessionmaker, settings):
    from nexus_common import money_mail

    async with sessionmaker() as db:
        user = await _user(db)
        box = await money_mail.save(db, settings, user.id, "bank.alerts@gmail.com", {
            "scope": f"openid {money_mail.SCOPE}", "refresh_token": "r", "access_token": "a",
            "expires_in": 3600})  # fmt: skip
        # No read access granted, nothing kept.
        assert (
            await money_mail.save(db, settings, user.id, "x@gmail.com", {"scope": "openid"}) is None
        )
        await db.commit()
    out = (await owner.get("/api/money/settings")).json()
    assert [m["email_masked"] for m in out["mailboxes"]] == ["ba•••@gmail.com"]
    assert "bank.alerts" not in str(out)
    # Alerts can be switched on with only the other mailbox (no Gmail on the sign-in account).
    on = await owner.patch("/api/money/settings", json={"gmail_sync": True})
    assert on.status_code == 200, on.text
    gone = await owner.delete(f"/api/money/mailboxes/{box.id}")
    assert gone.status_code == 204
    assert (await owner.get("/api/money/settings")).json()["mailboxes"] == []
    # The connect flow is the owner's, through Google's account picker.
    resp = await owner.get("/auth/google/mailbox", follow_redirects=False)
    assert resp.status_code in (302, 307, 404)


async def test_debit_card_expiry_is_kept_and_reminded_daily(owner, sessionmaker):
    from datetime import timedelta

    from nexus_common import advice

    bank = (await owner.post("/api/money/accounts", json={
        "kind": "bank", "institution": "ICICI Bank", "mask": "6320"})).json()  # fmt: skip
    resp = await owner.post("/api/money/accounts", json={
        "kind": "debit", "mask": "4455", "account_id": bank["id"],
        "expires_on": "2026-12-01"})  # fmt: skip
    assert resp.status_code == 201, resp.text
    (item,) = (await owner.get("/api/money/balances")).json()["items"]
    assert (item["card_mask"], item["card_expires_on"]) == ("4455", "2026-12-01")
    # A renewed card: the expiry can be typed on the bank account.
    edit = await owner.patch(f"/api/money/accounts/{bank['id']}/details",
                             json={"expires_on": "2026-12-01"})  # fmt: skip
    assert edit.status_code == 200, edit.text

    async with sessionmaker() as db:
        user = await _user(db)
        view = await store.accounts_view(db, user.id, date(2026, 12, 10))
    subs = {"items": []}
    # From two weeks before the end of the expiry month, every day, each day's own tag.
    early = store.reminders(subs, view, date(2026, 12, 16))
    assert not [r for r in early if r["tag"].startswith("expiry:")]
    days = [date(2026, 12, 17), date(2026, 12, 18)]
    tags = [r["tag"] for d in days for r in store.reminders(subs, view, d)
            if r["tag"].startswith("expiry:")]  # fmt: skip
    assert len(set(tags)) == 2
    (one,) = [r for r in store.reminders(subs, view, date(2026, 12, 30)) if "expiry" in r["tag"]]
    assert one["title"] == "Debit card ••4455 (ICICI Bank ••6320) expires tomorrow"
    assert not [r for r in store.reminders(subs, view, date(2027, 1, 9)) if "expiry" in r["tag"]]

    # Advice: worth a look six weeks out, "do now" in the last two.
    facts = advice.Facts(today=date(2026, 11, 20), accounts=view["items"])
    (look,) = advice.card_expiry(facts)
    assert look.level == "look" and look.url == "/money/accounts"
    facts.today = date(2026, 12, 31) - timedelta(days=10)
    assert advice.card_expiry(facts)[0].level == "act"


async def test_two_cards_share_one_limit(owner, sessionmaker, settings):
    ruby = await owner.post("/api/money/accounts", json={
        "kind": "card", "institution": "ICICI Bank", "mask": "1111", "name": "Rubyx Visa",
        "credit_limit_paise": 2_70_000_00})  # fmt: skip
    ruby = ruby.json()
    rupay = await owner.post("/api/money/accounts", json={
        "kind": "card", "institution": "ICICI Bank", "mask": "2222", "name": "Rubyx RuPay",
        "credit_limit_paise": 2_70_000_00, "limit_with_id": ruby["id"]})  # fmt: skip
    assert rupay.status_code == 201, rupay.text
    rupay = rupay.json()

    # An alert on either card says what's left of the shared limit.
    async with sessionmaker() as db:
        user = await _user(db)
        await store.ingest(db, settings, user.id, [
            money.Parsed(date(2026, 11, 20), 30_000_00, "debit", "Croma", "POS", channel="card",
                         account_mask="2222", account_kind="card", institution="ICICI Bank",
                         extra={"available_limit_paise": 2_40_000_00}),
        ], source="alert")  # fmt: skip
        await db.commit()

    view = (await owner.get("/api/money/balances")).json()
    by = {a["mask"]: a for a in view["items"]}
    assert by["2222"]["limit_with_label"] == "Rubyx Visa"
    assert by["2222"]["credit_limit_paise"] is None
    assert by["1111"]["shared_with"] == ["Rubyx RuPay"]
    assert by["1111"]["credit_limit_paise"] == 2_70_000_00
    assert by["1111"]["owed_paise"] == 30_000_00
    # Counted once.
    assert view["owed_paise"] == 30_000_00

    # A card can't share with itself, someone else's card, or a bank account; and can stop.
    bad = await owner.patch(f"/api/money/accounts/{ruby['id']}/details",
                            json={"limit_with_id": rupay["id"]})  # fmt: skip
    assert bad.status_code == 422
    stop = await owner.patch(f"/api/money/accounts/{rupay['id']}/details",
                             json={"limit_with_id": None})  # fmt: skip
    assert stop.status_code == 200 and stop.json()["limit_with_id"] is None


async def test_an_account_made_under_other_digits_merges_in(owner, sessionmaker, settings):
    bank = (await owner.post("/api/money/accounts", json={
        "kind": "bank", "institution": "ICICI Bank", "mask": "6320"})).json()  # fmt: skip
    async with sessionmaker() as db:
        user = await _user(db)
        # An alert on the real account, then a statement that quoted other digits.
        await store.ingest(db, settings, user.id, [
            money.Parsed(date(2026, 3, 2), 500_00, "debit", "Zepto", "UPI-ZEPTO",
                         account_mask="6320", institution="ICICI Bank"),
        ], source="alert")  # fmt: skip
        await store.ingest(db, settings, user.id, [
            money.Parsed(date(2026, 3, 2), 500_00, "debit", "Zepto", "UPI/ZEPTO/1",
                         account_mask="3443", institution="ICICI Bank"),
            money.Parsed(date(2026, 3, 5), 85_000_00, "credit", "Acme", "NEFT ACME SALARY",
                         account_mask="3443", institution="ICICI Bank", balance_paise=90_000_00),
        ], source="statement")  # fmt: skip
        await db.commit()
        other = await db.scalar(select(MoneyAccount).where(MoneyAccount.mask == "3443"))

    wrong = await owner.post(
        f"/api/money/accounts/{other.id}/merge", json={"into_id": str(other.id)}
    )
    assert wrong.status_code == 422
    resp = await owner.post(f"/api/money/accounts/{other.id}/merge", json={"into_id": bank["id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"moved": 1, "matched": 1, "into": "ICICI Bank ••6320"}

    async with sessionmaker() as db:
        (only,) = (await db.scalars(select(MoneyAccount))).all()
        assert only.aliases == ["3443"] and only.balance_paise == 90_000_00
        assert len((await db.scalars(select(MoneyTxn))).all()) == 2
        # The next statement with those digits lands on the real account.
        await store.ingest(db, settings, user.id, [
            money.Parsed(date(2026, 4, 2), 200_00, "debit", "Uber", "UPI/UBER/2",
                         account_mask="3443", institution="ICICI Bank"),
        ], source="statement")  # fmt: skip
        await db.commit()
        assert len((await db.scalars(select(MoneyAccount))).all()) == 1
        # A statement quoting a card first and the account second goes to the known account.
        await store.ingest(db, settings, user.id, [
            money.Parsed(date(2026, 4, 3), 300_00, "debit", "Ola", "UPI/OLA/3",
                         account_mask="7788", mask_options=("7788", "6320"),
                         institution="ICICI Bank"),
        ], source="statement")  # fmt: skip
        await db.commit()
        assert len((await db.scalars(select(MoneyAccount))).all()) == 1
