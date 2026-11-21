"""Money moving between the person's own accounts, card bill payments, refunds and savings
payments are tied together (money_links) so none of it counts as spending or income; balances
are checked against what the bank printed; and Money's own encryption key."""

from datetime import date, timedelta

from sqlalchemy import select

from nexus_common import money, money_links
from nexus_common import money_store as store
from nexus_common.models import MoneyAsset, MoneyTxn, User
from nexus_common.organiser import local_today

HDFC = {"account_mask": "1234", "institution": "HDFC Bank"}
SBI = {"account_mask": "9999", "institution": "SBI"}
CARD = {"account_mask": "5678", "account_kind": "card", "institution": "HDFC Bank"}


def P(on, paise, direction, merchant, narration="", **kw):
    return money.Parsed(on, paise, direction, merchant, narration or merchant.upper(), **kw)


async def _user(db) -> User:
    return await db.scalar(select(User).where(User.email == "owner@example.com"))


async def _rows(db, user_id) -> dict[str, MoneyTxn]:
    rows = (await db.scalars(select(MoneyTxn).where(MoneyTxn.user_id == user_id))).all()
    return {f"{t.merchant}/{t.direction}": t for t in rows}


async def test_transfers_card_payments_and_refunds(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await _user(db)
        user.name = "Shivam Singh"
        today = local_today(user)
        d = today - timedelta(days=5)
        await store.ingest(db, settings, user.id, [
            # Salary moved from HDFC to SBI, arriving the next day.
            P(d, 2000000, "debit", "To SBI", "NEFT TO OWN SBI", **HDFC),
            P(d + timedelta(days=1), 2000000, "credit", "From HDFC", "NEFT CR", **SBI),
            # The HDFC card's bill paid from HDFC.
            P(d, 1500000, "debit", "Card bill payment", "CC PAYMENT", **HDFC),
            P(d, 1500000, "credit", "Card bill payment", "PAYMENT RECEIVED", **CARD),
            # A purchase and its refund.
            P(d - timedelta(days=10), 249900, "debit", "Amazon", "AMAZON PAY", **CARD),
            P(d, 249900, "credit", "Amazon", "AMAZON REFUND", **CARD),
            # Money from his own account elsewhere (Kotak, not in Money).
            P(d, 500000, "credit", "SHIVAM SINGH", "UPI/shivam@kotak", **SBI),
            # Ordinary spending stays spending.
            P(d, 45000, "debit", "Swiggy", **HDFC),
        ], source="alert")  # fmt: skip
        await db.commit()
        rows = await _rows(db, user.id)

    out, back = rows["To SBI/debit"], rows["From HDFC/credit"]
    assert out.pair_id == back.id and back.pair_id == out.id and out.pair_kind == "transfer"
    assert out.category == back.category == "Transfers"
    paid, got = rows["Card bill payment/debit"], rows["Card bill payment/credit"]
    assert paid.pair_kind == got.pair_kind == "card" and paid.pair_id == got.id
    refund = rows["Amazon/credit"]
    assert refund.pair_kind == "refund" and refund.category == "Shopping"
    assert refund.pair_id == rows["Amazon/debit"].id
    assert rows["SHIVAM SINGH/credit"].category == "Transfers"
    assert rows["Swiggy/debit"].pair_id is None

    # The month counts only Swiggy as spending (the Amazon order and its refund cancel out) and
    # no income at all.
    month = (await owner.get("/api/money/overview")).json()
    summary = month.get("summary", month)
    if d.month == today.month and (d - timedelta(days=10)).month == today.month:
        assert summary["spent_paise"] == 45000
        assert summary["income_paise"] == 0
    detail = (await owner.get(f"/api/money/transactions/{out.id}")).json()
    assert detail["paired"]["account"] and detail["paired"]["direction"] == "credit"

    # Undo a pair: both go back to the word list's category and stay apart.
    resp = await owner.patch(f"/api/money/transactions/{out.id}", json={"unpair": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["txn"]["pair_id"] is None
    async with sessionmaker() as db:
        back = await db.get(MoneyTxn, back.id)
        assert back.pair_id is None and back.category == "Income"
    # And pair by hand.
    matches = (await owner.get(f"/api/money/transactions/{out.id}/matches")).json()
    assert [m["id"] for m in matches["transfers"]] == [str(back.id)]
    resp = await owner.patch(f"/api/money/transactions/{out.id}", json={"pair_with": str(back.id)})
    assert resp.json()["txn"]["pair_kind"] == "transfer"
    assert resp.json()["txn"]["category"] == "Transfers"
    bad = await owner.patch(
        f"/api/money/transactions/{out.id}", json={"pair_with": str(rows["Swiggy/debit"].id)}
    )
    assert bad.status_code == 422


async def test_savings_payments_are_tied_to_the_deposit(owner, sessionmaker, settings):
    rd = (
        await owner.post(
            "/api/money/savings",
            json={
                "kind": "rd",
                "name": "HDFC RD",
                "institution": "HDFC Bank",
                "amount_paise": 500000,
                "rate_bp": 670,
                "start_on": "2026-04-05",
                "maturity_on": "2027-04-05",
            },
        )  # fmt: skip
    ).json()
    ppf = (
        await owner.post(
            "/api/money/savings",
            json={"kind": "ppf", "institution": "SBI", "amount_paise": 40000000},
        )
    ).json()
    async with sessionmaker() as db:
        user = await _user(db)
        today = local_today(user)
        await store.ingest(db, settings, user.id, [
            P(today, 500000, "debit", "Unknown", "RD INSTALLMENT 50400098761234", **HDFC),
            P(today, 15000000, "debit", "Unknown", "TRF TO PPF A/C", **SBI),
            P(today, 300000, "debit", "Unknown", "TRF TO PPF A/C 2", **HDFC),
        ], source="alert")  # fmt: skip
        await db.commit()
        rows = (await db.scalars(select(MoneyTxn).order_by(MoneyTxn.amount_paise))).all()
    small, instalment, big = rows
    assert str(instalment.asset_id) == rd["id"] and instalment.category == "Investments"
    assert str(big.asset_id) == ppf["id"] and str(small.asset_id) == ppf["id"]
    view = (await owner.get("/api/money/savings")).json()
    by = {a["id"]: a for a in view["items"]}
    assert by[rd["id"]]["paid_this_month"] and by[rd["id"]]["last_paid_on"] == today.isoformat()

    # Tie and untie by hand.
    resp = await owner.patch(f"/api/money/transactions/{small.id}", json={"asset_id": None})
    assert resp.json()["txn"]["asset_id"] is None
    resp = await owner.patch(f"/api/money/transactions/{small.id}", json={"asset_id": rd["id"]})
    assert resp.json()["txn"]["saving"] == "HDFC RD"


async def test_scan_finds_pairs_in_older_rows(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await _user(db)
        today = local_today(user)
        await store.ingest(db, settings, user.id, [P(today, 70000, "debit", "To SBI", **HDFC)],
                           source="alert")  # fmt: skip
        await store.ingest(db, settings, user.id, [P(today, 70000, "credit", "From HDFC", **SBI)],
                           source="statement")  # fmt: skip
        # Pretend these arrived before pairing existed.
        for t in (await db.scalars(select(MoneyTxn))).all():
            t.pair_id = t.pair_kind = None
            t.category, t.category_by = ("Other" if t.direction == "debit" else "Income"), "auto"
        await db.commit()
    resp = await owner.post("/api/money/links/scan")
    assert resp.status_code == 200 and resp.json()["transfers"] == 1


def test_reconcile_finds_missing_rows():
    d = date(2026, 11, 1)
    rows = [
        (d, -1000, 99000),
        (d + timedelta(days=1), -500, 98500),
        # Two rows the same day in either order; the closing one is 97500.
        (d + timedelta(days=2), -500, 97500),
        (d + timedelta(days=2), -500, 98000),
        # A row with no balance (from an alert) still counts.
        (d + timedelta(days=3), -2000, None),
        (d + timedelta(days=4), 10000, 105500),
        # ₹1,000 went out that Money has no row for.
        (d + timedelta(days=5), -500, 104000),
    ]
    checked, gaps = money.reconcile(rows)
    assert checked == 4
    assert gaps == [money.Gap(d + timedelta(days=5), -100000 // 100)]


async def test_balance_check_on_accounts(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await _user(db)
        today = local_today(user)
        await store.ingest(db, settings, user.id, [
            P(today - timedelta(days=3), 1000, "debit", "A", balance_paise=99000, **HDFC),
            P(today - timedelta(days=2), 1000, "debit", "B", balance_paise=98000, **HDFC),
            P(today - timedelta(days=1), 1000, "debit", "C", balance_paise=96000, **HDFC),
        ], source="statement")  # fmt: skip
        await db.commit()
    view = (await owner.get("/api/money/balances")).json()
    (bank,) = [a for a in view["items"] if a["mask"] == "1234"]
    check = bank["balance_check"]
    assert check["days_checked"] == 2 and check["gap_count"] == 1
    assert check["gaps"][0]["gap_paise"] == -1000


def test_rules_for_self_payees_interest_and_maturity():
    assert money.is_self_payee("SHIVAM SINGH", "Shivam Singh")
    assert money.is_self_payee("Mr Shivam Kumar Singh", "Shivam Singh")
    assert not money.is_self_payee("SHIVAM TRADERS", "Shivam Singh")
    assert not money.is_self_payee("Anyone", None)
    assert money.categorise("SBI", "FD MATURITY PROCEEDS", credit=True) == "Investments"
    assert money.categorise("SBI", "INT.PD 01-10-26 TO 31-12-26", credit=True) == "Income"
    assert money.saving_kind("RD INSTALLMENT 123") == "rd"
    assert money.saving_kind("TRF TO PPF A/C") == "ppf"
    assert money.saving_kind("Swiggy") is None
    # A refund lowers its category's spending; it isn't income.
    t = money.Txn
    s = money.summarise([
        t(date(2026, 11, 1), 1000, "debit", "Amazon", "Shopping"),
        t(date(2026, 11, 2), 400, "credit", "Amazon", "Shopping"),
        t(date(2026, 11, 3), 9000, "credit", "SBI", "Investments"),
    ])  # fmt: skip
    assert s["spent_paise"] == 600 and s["income_paise"] == 0
    assert s["categories"] == [{"category": "Shopping", "spent_paise": 600}]


async def test_money_has_its_own_key(sessionmaker, settings, owner):
    old = money.seal(settings, "A/C 3245")
    own = settings.model_copy(update={"money_encryption_keys": "money-key-1"})
    assert not money.sealed_current(own, old)
    assert money.unseal(own, old) == "A/C 3245"
    new = money.seal(own, "A/C 3245")
    assert money.sealed_current(own, new)
    # The token keys alone can't open it.
    assert money.unseal(settings, new) == ""
    # Old values move over in the background.
    async with sessionmaker() as db:
        user = await _user(db)
        db.add(MoneyAsset(user_id=user.id, kind="ppf", amount_paise=1, account_no_enc=old))
        await db.commit()
        assert await store.reseal(db, own) == 1
        assert await store.reseal(db, own) == 0
        row = await db.scalar(select(MoneyAsset))
        assert money.sealed_current(own, row.account_no_enc)
        assert money.unseal(own, row.account_no_enc) == "A/C 3245"
        # Without the setting nothing moves.
        assert await store.reseal(db, settings) == 0
    assert money_links.TRANSFER_DAYS == 3
