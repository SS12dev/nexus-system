"""Money's API (/api/money): owner only, statements in, alerts matched, budgets and totals."""

import logging
from datetime import date

from sqlalchemy import select

from nexus_common import money
from nexus_common import money_store as store
from nexus_common.models import MoneySettings, MoneyTxn, User

STATEMENT = b"""HDFC BANK Ltd.
Account No : 50100123451234

Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
01/11/26,NEFT CR-ACME TECHNOLOGIES PVT LTD-SALARY NOV,N305260000123,01/11/26,,125000.00,210000.00
03/11/26,UPI-SWIGGY-swiggy@icici-ICIC0000001-430712345678-UPI,0000430712345678,03/11/26,450.00,,209550.00
05/11/26,UPI-CRED CLUB-cred.club@axisb-UTIB0000001-430700001111-CC BILL,0000430700001111,05/11/26,"9,000.00",,200550.00
06/11/26,POS 512345XXXXXX6789 AMAZON PAY IN,000000000000,06/11/26,"1,299.00",,199251.00
"""


async def upload(client, data: bytes, name: str = "Acct_Statement.csv", **form) -> dict:
    resp = await client.post(
        "/api/money/imports", files={"file": (name, data, "text/csv")}, data=form
    )
    return resp


async def test_members_cannot_see_money(owner, client_for, invite):
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    for path in ("/api/money/overview", "/api/money/transactions", "/api/money/settings"):
        assert (await friend.get(path)).status_code == 403


async def test_statement_import_is_stored_encrypted_and_never_logged(owner, sessionmaker, caplog):
    caplog.set_level(logging.INFO)
    resp = await upload(owner, STATEMENT)
    assert resp.status_code == 201, resp.text
    imp = resp.json()
    assert imp["status"] == "done"
    assert imp["counts"] == {"added": 4, "matched": 0, "skipped": 0}
    assert imp["filename"] == "Acct_Statement.csv"

    # The same file again adds nothing.
    again = (await upload(owner, STATEMENT)).json()
    assert again["counts"] == {"added": 0, "matched": 0, "skipped": 4}

    page = (await owner.get("/api/money/transactions", params={"month": "2026-11"})).json()
    assert page["total"] == 4
    by = {t["merchant"]: t for t in page["items"]}
    assert by["Swiggy"]["category"] == "Food and dining"
    assert by["Cred Club"]["category"] == "Transfers"
    assert by["Amazon"]["channel"] == "card"
    assert "narration" not in by["Swiggy"] or by["Swiggy"]["narration"] is None

    # The narration is readable through the API and encrypted in the database.
    one = (await owner.get(f"/api/money/transactions/{by['Swiggy']['id']}")).json()
    assert "swiggy@icici" in one["narration"]
    async with sessionmaker() as db:
        row = await db.get(MoneyTxn, __import__("uuid").UUID(by["Swiggy"]["id"]))
        assert "swiggy" not in row.narration_enc.lower()
        assert row.account_id is not None

    accounts = (await owner.get("/api/money/accounts")).json()
    assert [a["label"] for a in accounts] == ["HDFC Bank ••1234"]

    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    for secret in ("Swiggy", "swiggy", "45000", "450.00", "Acct_Statement", "ACME"):
        assert secret not in logged, secret


async def test_alert_and_statement_rows_become_one(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        alert = money.parse_alert(
            "UPI txn",
            "Rs.450.00 has been debited from account **1234 to VPA swiggy@icici SWIGGY on "
            "03-11-26. Your UPI transaction reference number is 430712345678.",
            date(2026, 11, 3),
            sender="alerts@hdfcbank.net",
        )
        counts = await store.ingest(db, settings, user.id, [alert], source="alert")
        await db.commit()
    assert counts["added"] == 1
    imp = (await upload(owner, STATEMENT)).json()
    assert imp["counts"] == {"added": 3, "matched": 1, "skipped": 0}
    page = (await owner.get("/api/money/transactions")).json()
    swiggy = [t for t in page["items"] if t["merchant"] == "Swiggy"]
    assert len(swiggy) == 1 and swiggy[0]["confirmed"] and swiggy[0]["source"] == "alert"
    # And importing the statement again still doesn't add it.
    again = (await upload(owner, STATEMENT)).json()
    assert again["counts"]["added"] == 0


async def test_overview_budgets_and_recategorising(owner):
    await upload(owner, STATEMENT)
    resp = await owner.put("/api/money/budgets/Food and dining", json={"monthly_paise": 500000})
    assert resp.status_code == 200
    assert (
        await owner.put("/api/money/budgets/Nonsense", json={"monthly_paise": 1})
    ).status_code == 422

    ov = (await owner.get("/api/money/overview", params={"month": "2026-11"})).json()
    # The card bill payment is a transfer, so not spending.
    assert ov["spent_paise"] == 45000 + 129900
    assert ov["income_paise"] == 12500000
    assert ov["budgets"] == [
        {"category": "Food and dining", "monthly_paise": 500000, "spent_paise": 45000}
    ]
    assert [m["month"] for m in ov["trend"]][-1] == "2026-11"
    assert len(ov["trend"]) == 6

    page = (await owner.get("/api/money/transactions")).json()
    amazon = next(t for t in page["items"] if t["merchant"] == "Amazon")
    resp = await owner.patch(
        f"/api/money/transactions/{amazon['id']}",
        json={"category": "Groceries", "for_merchant": True, "note": "weekly shop"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["txn"]["category"] == "Groceries"
    settings = (await owner.get("/api/money/settings")).json()
    assert settings["rules"] == 1

    # Leaving a row out of totals.
    resp = await owner.patch(f"/api/money/transactions/{amazon['id']}", json={"excluded": True})
    ov = (await owner.get("/api/money/overview", params={"month": "2026-11"})).json()
    assert ov["spent_paise"] == 45000


async def test_manual_cash_and_delete(owner):
    resp = await owner.post(
        "/api/money/transactions",
        json={"occurred_on": "2026-11-10", "amount": "1,250.50", "merchant": "Chai stall"},
    )
    assert resp.status_code == 201, resp.text
    t = resp.json()
    assert (t["amount_paise"], t["channel"], t["source"]) == (125050, "cash", "manual")
    bad = await owner.post(
        "/api/money/transactions",
        json={"occurred_on": "2026-11-10", "amount": "abc", "merchant": "x"},
    )
    assert bad.status_code == 422
    assert (await owner.delete(f"/api/money/transactions/{t['id']}")).status_code == 204

    await upload(owner, STATEMENT)
    row = (await owner.get("/api/money/transactions")).json()["items"][0]
    assert (await owner.delete(f"/api/money/transactions/{row['id']}")).status_code == 400


async def test_bad_files_explain_themselves(owner):
    resp = await upload(owner, b"hello,world\n1,2\n", "x.csv")
    assert resp.status_code == 422
    assert "header row" in resp.json()["detail"]
    resp = await upload(owner, b"%PDF-1.4 not really", "cas.pdf")
    assert resp.status_code == 422
    assert "password" in resp.json()["detail"]
    imports = (await owner.get("/api/money/imports")).json()
    assert [i["status"] for i in imports] == ["failed", "failed"]


async def test_settings_need_gmail_and_store_the_password_encrypted(owner, sessionmaker, queue):
    resp = await owner.patch("/api/money/settings", json={"gmail_sync": True})
    assert resp.status_code == 400
    assert "Gmail" in resp.json()["detail"]
    resp = await owner.patch(
        "/api/money/settings", json={"cas_password": "ABCDE1234F", "assistant_access": True}
    )
    assert resp.status_code == 200
    assert resp.json()["cas_password_set"] and resp.json()["assistant_access"]
    async with sessionmaker() as db:
        prefs = (await db.scalars(select(MoneySettings))).one()
        assert prefs.cas_password_enc and "ABCDE" not in prefs.cas_password_enc
    assert (await owner.post("/api/money/sync")).status_code == 400
    assert queue.money_syncs == []


async def test_subscriptions_and_delete_everything(owner, sessionmaker, settings):
    async with sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        rows = [
            money.Parsed(date(2026, m, 5), 64900, "debit", "Netflix", f"NETFLIX {m}")
            for m in (8, 9, 10, 11)
        ]
        await store.ingest(db, settings, user.id, rows, source="statement")
        await db.commit()
    subs = (await owner.get("/api/money/subscriptions")).json()
    assert [s["merchant"] for s in subs["items"]] == ["Netflix"]
    assert subs["monthly_paise"] == 64900

    assert (await owner.post("/api/money/delete-all", json={"confirm": "nope"})).status_code == 422
    assert (
        await owner.post("/api/money/delete-all", json={"confirm": "delete"})
    ).status_code == 204
    assert (await owner.get("/api/money/transactions")).json()["total"] == 0
    assert (await owner.get("/api/money/subscriptions")).json()["items"] == []
