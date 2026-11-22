"""Statement PDFs: banks email them locked with a password. The password opens the file in
memory and is never stored; the table is read whether or not it has ruling lines, a description
that runs onto a second line stays with its transaction, and a card statement's unsigned amounts
are spending unless marked Cr. All amounts, names and numbers here are made up."""

import io

import pytest
from sqlalchemy import select

from nexus_common import money
from nexus_common.models import MoneySettings, MoneyTxn

pytest.importorskip("reportlab")


def _bank_pdf(password: str = "", two_line_header: bool = False) -> bytes:
    """A bank statement laid out like an emailed one: text in columns, no table lines."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 8)
    y = 800
    c.drawString(40, y, "HDFC BANK Ltd.")
    c.drawString(40, y - 12, "Account No : 50100000001298  Statement From : 01/11/2026")
    y -= 36
    heads = [(40, "Date", "l"), (90, "Narration", "l"), (290, "Chq./Ref.No.", "l"),
             (350, "Value Dt", "l"), (450, "Withdrawal Amt.", "r"), (510, "Deposit Amt.", "r"),
             (580, "Closing Balance", "r")]  # fmt: skip
    for x, text, align in heads:
        first, _, rest = text.partition(" ")
        if two_line_header and rest:
            # Narrow columns wrap their header over two lines.
            for i, part in enumerate((first, rest)):
                draw = c.drawString if align == "l" else c.drawRightString
                draw(x, y - i * 9, part)
        else:
            (c.drawString if align == "l" else c.drawRightString)(x, y, text)
    y -= 24 if two_line_header else 14
    rows = [
        ("02/11/26", ["UPI-SWIGGY-SWIGGY@ICICI-ICIC0DC0099-", "631234567890-PAYMENT"],
         "0000631234", "452.50", "", "10,047.50"),
        ("05/11/26", ["NEFT CR-ACME TECH PVT LTD-SALARY NOV"], "N309261234", "", "85,000.00",
         "95,047.50"),
        ("07/11/26", ["POS 416021XXXXXX1234 AMAZON PAY INDIA"], "0000000000", "1,299.00", "",
         "93,748.50"),
    ]  # fmt: skip
    for day, narration, ref, out, inn, balance in rows:
        c.drawString(40, y, day)
        c.drawString(90, y, narration[0])
        c.drawString(290, y, ref)
        c.drawString(350, y, day)
        if out:
            c.drawRightString(450, y, out)
        if inn:
            c.drawRightString(510, y, inn)
        c.drawRightString(580, y, balance)
        for more in narration[1:]:
            y -= 10
            c.drawString(90, y, more)
        y -= 12
    c.drawString(250, y - 20, "Page 1 of 1")
    c.save()
    return _lock(buf.getvalue(), password) if password else buf.getvalue()


def _card_pdf() -> bytes:
    """A card statement with a ruled table and unsigned amounts."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    buf = io.BytesIO()
    style = getSampleStyleSheet()
    table = Table([
        ["Date", "Transaction Details", "Amount (in Rs.)"],
        ["03/11/2026", "NETFLIX.COM MUMBAI", "649.00"],
        ["10/11/2026", "PAYMENT RECEIVED - THANK YOU", "12,000.00 Cr"],
        ["12/11/2026", "UBER INDIA SYSTEMS", "312.40"],
    ])  # fmt: skip
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "black")]))
    SimpleDocTemplate(buf, pagesize=A4).build([
        Paragraph("ICICI Bank Credit Card Statement", style["Title"]),
        Paragraph("Card Number 4375 XXXX XXXX 2222", style["Normal"]),
        table,
    ])  # fmt: skip
    return buf.getvalue()


def _lock(data: bytes, password: str) -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(data)).pages:
        writer.add_page(page)
    writer.encrypt(password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.mark.parametrize("two_line_header", [False, True])
def test_reads_a_bank_statement_pdf(two_line_header):
    data = _bank_pdf("open sesame", two_line_header)
    with pytest.raises(money.PdfLocked, match="locked"):
        money.open_pdf(data)
    with pytest.raises(money.PdfLocked, match="didn't open"):
        money.open_pdf(data, ["wrong"])
    assert money.open_pdf(data, ["wrong", "open sesame"]) == ("statement", "open sesame")

    rows = money.read_pdf_statement(data, "open sesame", "statement.pdf")
    got = [(p.amount_paise, p.direction, p.merchant, p.balance_paise) for p in rows]
    assert got == [
        (452_50, "debit", "Swiggy", 10_047_50),
        (85_000_00, "credit", "Acme Tech", 95_047_50),
        (1_299_00, "debit", "Amazon", 93_748_50),
    ]
    # The second line of a description joins it; the page footer doesn't.
    assert rows[0].narration.endswith("631234567890-PAYMENT")
    assert "Page" not in rows[-1].narration
    assert (rows[0].account_mask, rows[0].institution) == ("1298", "HDFC Bank")


def test_reads_a_card_statement_pdf():
    data = _card_pdf()
    assert money.open_pdf(data) == ("statement", "")
    rows = money.read_pdf_statement(data, "", "card.pdf")
    got = [(p.amount_paise, p.direction, p.merchant) for p in rows]
    assert got == [(649_00, "debit", "Netflix"), (12_000_00, "credit", "Received - Thank You"),
                   (312_40, "debit", "Uber")]  # fmt: skip
    assert (rows[0].account_kind, rows[0].account_mask) == ("card", "2222")


async def test_upload_a_locked_pdf_and_the_password_is_not_kept(owner, sessionmaker):
    # An account the statement doesn't name: its rows go on a new one, and the import says so.
    await owner.post("/api/money/accounts", json={
        "kind": "bank", "institution": "HDFC Bank", "mask": "9999"})  # fmt: skip
    data = _bank_pdf("open sesame")
    send = {"file": ("Acct_Statement.pdf", data, "application/pdf")}
    locked = await owner.post("/api/money/imports", files=send)
    assert locked.status_code == 422 and "locked" in locked.json()["detail"]

    resp = await owner.post(
        "/api/money/imports",
        files=send,
        data={"password": "open sesame", "remember_password": "true"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["counts"]["added"] == 3
    assert resp.json()["counts"]["new_accounts"] == 1
    async with sessionmaker() as db:
        assert len((await db.scalars(select(MoneyTxn))).all()) == 3
        prefs = (await db.scalars(select(MoneySettings))).one()
        # Only a CAS password is ever remembered.
        assert prefs.cas_password_enc is None
