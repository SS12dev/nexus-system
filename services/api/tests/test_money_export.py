"""Account numbers and nominees on savings and loans (encrypted, masked, revealed on request),
and the Excel export of Money."""

import io
import logging
import uuid
from datetime import date

import msoffcrypto
from openpyxl import load_workbook
from sqlalchemy import select, text

from nexus_common import money
from nexus_common.models import AuditLog, MoneyAsset, MoneyLoan

PPF = {
    "kind": "ppf", "name": "PPF", "institution": "SBI", "amount_paise": 50000000,
    "contribution_paise": 15000000, "branch": "  Koramangala   ",
    "account_no": "3245 1234 5678", "nominee": "Asha Singh", "nominee_relation": "Mother",
}  # fmt: skip


async def test_savings_details_are_sealed_and_masked(owner, sessionmaker, caplog):
    caplog.set_level(logging.INFO)
    resp = await owner.post("/api/money/savings", json=PPF)
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["account_no_masked"] == "••••5678" and out["has_nominee"]
    assert out["branch"] == "Koramangala" and out["nominee_relation"] == "Mother"
    assert "account_no" not in out and "nominee" not in out
    assert "Asha" not in resp.text and "3245" not in resp.text
    # The list shows the same masked view.
    listed = (await owner.get("/api/money/savings")).text
    assert "Asha" not in listed and "1234" not in listed

    # Stored encrypted: the plain number and name are nowhere in the row.
    async with sessionmaker() as db:
        row = (await db.execute(text("SELECT * FROM money_assets"))).mappings().one()
        assert "3245" not in str(dict(row)) and "Asha" not in str(dict(row))
        assert row["account_last4"] == "5678"

    # The eye button: the full values, and an audit entry naming no values.
    shown = await owner.get(f"/api/money/savings/{out['id']}/details")
    assert shown.json() == {"account_no": "3245 1234 5678", "nominee": "Asha Singh"}
    async with sessionmaker() as db:
        (entry,) = (
            await db.scalars(select(AuditLog).where(AuditLog.action == "money.details_shown"))
        ).all()
        assert entry.target == f"saving:{out['id']}"

    # Left out: unchanged. Empty: cleared.
    kept = await owner.patch(f"/api/money/savings/{out['id']}", json={"rate_bp": 700})
    assert kept.json()["account_no_masked"] == "••••5678" and kept.json()["has_nominee"]
    cleared = await owner.patch(
        f"/api/money/savings/{out['id']}", json={"account_no": "", "nominee": None, "branch": ""}
    )
    body = cleared.json()
    assert body["account_no_masked"] is None and not body["has_nominee"] and body["branch"] is None
    assert (await owner.get(f"/api/money/savings/{out['id']}/details")).json() == {
        "account_no": None,
        "nominee": None,
    }
    # Only letters, digits, spaces, / and - in a number.
    bad = await owner.post("/api/money/savings", json={**PPF, "account_no": "12<script>"})
    assert bad.status_code == 422

    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    for secret in ("3245", "5678", "Asha", "Koramangala"):
        assert secret not in logged, secret


async def test_loan_account_number(owner, sessionmaker):
    resp = await owner.post(
        "/api/money/loans",
        json={"name": "Car", "kind": "vehicle", "principal_paise": 60000000, "rate_bp": 900,
              "months": 60, "first_due_on": "2026-06-05", "account_no": "lan-778899001"},
    )  # fmt: skip
    assert resp.status_code == 201, resp.text
    loan = resp.json()
    assert loan["account_no_masked"] == "••••9001"
    detail = await owner.get(f"/api/money/loans/{loan['id']}/details")
    assert detail.json() == {"account_no": "LAN-778899001"}
    async with sessionmaker() as db:
        row = await db.get(MoneyLoan, uuid.UUID(loan["id"]))
        assert "778899" not in (row.account_no_enc or "")


async def test_members_cannot_reveal_or_export(owner, client_for, invite):
    saving = (await owner.post("/api/money/savings", json=PPF)).json()
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    assert (await friend.get(f"/api/money/savings/{saving['id']}/details")).status_code == 403
    assert (await friend.post("/api/money/export", json={})).status_code == 403


async def _seed(owner) -> None:
    await owner.post("/api/money/savings", json=PPF)
    await owner.post(
        "/api/money/savings",
        json={"kind": "fd", "name": "HDFC FD", "institution": "HDFC Bank",
              "amount_paise": 10000000, "rate_bp": 700, "start_on": "2026-01-01",
              "maturity_on": "2027-01-01", "account_no": "50300012345678"},
    )  # fmt: skip
    await owner.post(
        "/api/money/loans",
        json={"name": "PS5", "principal_paise": 5499000, "rate_bp": 1500, "months": 12,
              "first_due_on": "2026-10-05", "account_no": "EMI 4455"},
    )  # fmt: skip
    await owner.put(
        "/api/money/plan/assumptions",
        json={"health_cover_paise": 50000000, "life_cover_paise": None, "dependants": True},
    )


async def test_export_workbook(owner, sessionmaker, caplog):
    caplog.set_level(logging.INFO)
    await _seed(owner)
    resp = await owner.post("/api/money/export", json={})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert 'filename="finances-' in resp.headers["content-disposition"]
    assert resp.headers["cache-control"] == "no-store"

    wb = load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames == [
        "Summary", "Accounts and cards", "Savings and deposits", "Mutual funds", "Shares",
        "Loans and EMIs", "Subscriptions", "Insurance",
    ]  # fmt: skip

    sav = wb["Savings and deposits"]
    assert sav.freeze_panes == "A5"
    header = [c.value for c in sav[4]]
    assert header[:7] == ["Type", "Name", "Bank or scheme", "Branch",
                          "Account, folio or member no.", "Nominee", "Nominee is"]  # fmt: skip
    assert sav["A4"].font.bold
    rows = {r[1]: r for r in sav.iter_rows(min_row=5, max_row=6, values_only=True)}
    ppf = rows["PPF"]
    assert ppf[3:7] == ("Koramangala", "3245 1234 5678", "Asha Singh", "Mother")
    assert ppf[7] == 500000  # rupees, as a number
    fd = rows["HDFC FD"]
    assert fd[4] == "50300012345678" and fd[5] is None
    assert (
        fd[9] == 0.07
        and sav.cell(6 if sav["B6"].value == "HDFC FD" else 5, 10).number_format == "0.00%"
    )
    matures = fd[11]
    assert matures.date() == date(2027, 1, 1)
    money_cell = sav.cell(5, 8)
    assert '"₹"##\\,##' in money_cell.number_format  # Indian grouping
    assert sav.cell(7, 1).value == "Total (active)"

    loans = wb["Loans and EMIs"]
    assert loans.cell(5, 2).value == "PS5" and loans.cell(5, 4).value == "EMI 4455"

    summary = [r for r in wb["Summary"].iter_rows(values_only=True) if r[0]]
    labels = {r[0]: r[1] for r in summary}
    assert labels["Total you owe"] > 0 and "Net worth" in labels
    assert labels["   PPF"] >= 500000

    ins = {r[0]: r for r in wb["Insurance"].iter_rows(min_row=5, values_only=True) if r[0]}
    assert ins["Health"][1] == 500000 and ins["Life (term)"][6] == "Not told yet"
    assert ins["People who depend on you"][6] == "Yes"

    assert wb["Mutual funds"].cell(5, 1).value.startswith("No mutual funds yet")

    async with sessionmaker() as db:
        (entry,) = (
            await db.scalars(select(AuditLog).where(AuditLog.action == "money.exported"))
        ).all()
        assert entry.data["password"] is False and entry.data["rows"]["Savings and deposits"] == 2
    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    for secret in ("3245", "Asha", "HDFC FD", "PS5"):
        assert secret not in logged, secret


async def test_export_with_a_password(owner, sessionmaker):
    await _seed(owner)
    resp = await owner.post("/api/money/export", json={"password": "family-only-2026"})
    assert resp.status_code == 200
    # Locked: not a zip any more, and it opens only with the password.
    assert not resp.content.startswith(b"PK")
    office = msoffcrypto.OfficeFile(io.BytesIO(resp.content))
    assert office.is_encrypted()
    plain = io.BytesIO()
    office.load_key(password="family-only-2026")
    office.decrypt(plain)
    wb = load_workbook(plain)
    assert wb["Savings and deposits"].cell(5, 5).value in ("3245 1234 5678", "50300012345678")
    async with sessionmaker() as db:
        entry = await db.scalar(select(AuditLog).where(AuditLog.action == "money.exported"))
        assert entry.data["password"] is True
        assert "family-only" not in str(entry.data)


async def test_export_is_rate_limited(owner, redis):
    for key in [k async for k in redis.scan_iter("*money-export*")]:
        await redis.delete(key)
    for _ in range(10):
        assert (await owner.post("/api/money/export", json={})).status_code == 200
    assert (await owner.post("/api/money/export", json={})).status_code == 429


def test_mask_helpers():
    assert money.last4("3245 1234 5678") == "5678"
    assert money.last4("lan-77/88-9a") == "889A"
    assert money.last4("") is None and money.last4(None) is None
    assert money.masked("5678") == "••••5678" and money.masked(None) is None
    assert MoneyAsset.__table__.c.account_no_enc is not None
