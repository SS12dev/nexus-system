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


def _centred_pdf() -> bytes:
    """ICICI's layout: each row's cells centred, so a three-line remark has a line above the
    date and one below, and the remark wraps mid-word."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 8)
    c.drawString(40, 800, "ICICI Bank Statement of Transactions in Savings Account XXXXXXXX6320")
    y = 770
    for x, text, align in [(40, "S No.", "l"), (70, "Transaction Date", "l"),
                           (150, "Transaction Remarks", "l"), (400, "Withdrawal Amount", "r"),
                           (470, "Deposit Amount", "r"), (550, "Balance", "r")]:  # fmt: skip
        (c.drawString if align == "l" else c.drawRightString)(x, y, text)
    y -= 26
    rows = [
        ("1", "01/03/2026", ["UPI/ZEPTO MARK/zepto@ybl/Pa", "yment fo/YES BA", "NK/512345678901"],
         "412.00", "", "50,000.00"),
        ("2", "02/03/2026", ["NEFT-UTIB0000001-ACME TECHNOL", "OGY INTERNATI-REGULAR SAL",
                             "ARY FEB26"], "", "84,000.00", "1,34,000.00"),
        ("3", "03/03/2026", ["UPI/NETFLIX/netflix@axis/Su", "bscription/AXIS BA",
                             "NK/512345678902"], "499.00", "", "1,33,501.00"),
    ]  # fmt: skip
    for no, day, remark, out, inn, balance in rows:
        c.drawString(150, y + 10, remark[0])
        c.drawString(40, y, no)
        c.drawString(70, y, day)
        c.drawString(150, y, remark[1])
        if out:
            c.drawRightString(400, y, out)
        if inn:
            c.drawRightString(470, y, inn)
        c.drawRightString(550, y, balance)
        c.drawString(150, y - 10, remark[2])
        y -= 40
    c.save()
    return buf.getvalue()


def _icici_year_pdf() -> bytes:
    """ICICI's yearly statement: a summary naming the PPF account first, an opening "B/F" line,
    remarks of one to four lines centred on the date line with every line evenly spaced (so a
    line between two rows is as close to either), the whole payee name glued to a reference
    at the end, then the PPF account's own section."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 7)
    y = 800
    for line in ["Summary of Accounts held under Cust ID: 123456789 as on April 30, 2026",
                 "PPF A/c 000518009999 3,000.00 0.00 3,000.00 Registered",
                 "Savings A/c 000101500777 51,353.18 0.00 51,353.18 Registered",
                 "Statement of Transactions in Savings Account Number: 000101500777 in INR for "
                 "the period April 01, 2026 - April 30, 2026"]:  # fmt: skip
        c.drawString(27, y, line)
        y -= 14
    heads = [(27, "DATE", "l"), (76, "MODE**", "l"), (158, "PARTICULARS", "l"),
             (430, "DEPOSITS", "r"), (510, "WITHDRAWALS", "r"), (590, "BALANCE", "r")]  # fmt: skip

    def header(y: float) -> None:
        for x, text, align in heads:
            (c.drawString if align == "l" else c.drawRightString)(x, y, text)

    def rows(y: float, items: list) -> float:
        for day, mode, remark, inn, out, balance in items:
            # The date line sits in the middle of the remark; every line is 10 points apart.
            mid = y - (len(remark) - 1) * 5
            c.drawString(27, mid, day)
            if mode:
                c.drawString(76, mid, mode)
            for k, part in enumerate(remark):
                c.drawString(158, y - k * 10, part)
            if inn:
                c.drawRightString(430, mid, inn)
            if out:
                c.drawRightString(510, mid, out)
            c.drawRightString(590, mid, balance)
            y -= max(len(remark), 1) * 10
        return y

    header(y)
    y = rows(y - 14, [
        ("01-04-2026", "", ["B/F"], "", "", "0.00"),
        ("29-04-2026", "CMS TRANSACTION",
         ["CMS/ REGULAR SALARY APR26/ACME", "TECHNOLOGY INTERNATI"], "85,000.00", "", "85,000.00"),
        ("29-04-2026", "ICICI DIRECT", ["EBA/PUR_DEMO000/20260429120000"], "", "1,178.82",
         "83,821.18"),
        ("30-04-2026", "", ["UPI/RIYA MENON/riyamenon@oksbi/UPI",
                            "Paymen//500000000001/ICI0a0b0c0d0e0f0a0b0c0d0e",
                            "0f0e0d0c0b0a/RIYA MENON K"], "", "500.00", "83,321.18"),
        ("30-04-2026", "", ["UPI/100000000002/Upi", "Transaction/makemytrip.demo/HDFC BANK",
                            "LTD/HDFD00000000000000000000000000000000/", "MAKEMYTRIP COM"],
         "", "14,468.00", "68,853.18"),
        ("30-04-2026", "", ["UPI/ANAND KUMA/anandk@okaxis/rent/FEDERAL",
                            "BA/500000000003/SBI0a1b2c3d4e5f6a7b8c9d0e1f2",
                            "3a4b5c6dANAND KUMAR"], "", "17,000.00", "51,853.18"),
        ("30-04-2026", "", ["Trf to PPF 000518009999"], "", "500.00", "51,353.18"),
    ])  # fmt: skip
    c.drawString(27, y - 4, "TOTAL 85,000.00 33,646.82 51,353.18")
    c.drawString(27, y - 24, "Statement of Transactions in Account Number: 000518009999 in INR")
    header(y - 38)
    ppf = [("01-04-2026", "", ["B/F"], "", "", "2,500.00"),
           ("30-04-2026", "", ["Trf frm SB 000101500777"], "500.00", "", "3,000.00")]  # fmt: skip
    rows(y - 52, ppf)
    c.save()
    return buf.getvalue()


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


def _padded(data: bytes, pages: int) -> bytes:
    """The statement followed by blank pages, to stand in for a long one."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(data)).pages:
        writer.add_page(page)
    for _ in range(pages - 1):
        writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_a_year_long_statement_is_read(monkeypatch):
    # A busy account's year runs past 90 pages; that used to be turned away at 80.
    rows = money.read_pdf_statement(_padded(_bank_pdf(), 92), "", "year.pdf")
    assert [p.amount_paise for p in rows] == [452_50, 85_000_00, 1_299_00]
    monkeypatch.setattr(money, "MAX_PDF_PAGES", 10)
    with pytest.raises(money.StatementError, match="12 pages; up to 10.*two halves"):
        money.read_pdf_statement(_padded(_bank_pdf(), 12), "", "long.pdf")


def test_a_centred_layout_keeps_each_remark_with_its_row():
    rows = money.read_pdf_statement(_centred_pdf(), "", "statement.pdf")
    got = [(p.amount_paise, p.direction, p.merchant) for p in rows]
    assert got == [(412_00, "debit", "Zepto"), (84_000_00, "credit", "Acme Technol Ogy Internati"),
                   (499_00, "debit", "Netflix")]  # fmt: skip
    assert rows[0].narration.startswith("UPI/ZEPTO MARK") and "512345678901" in rows[0].narration
    assert "SAL ARY FEB26" in rows[1].narration and "512345678901" not in rows[1].narration
    assert rows[0].account_mask == "6320"


def test_an_icici_year_reads_each_remark_with_its_own_row():
    rows = money.read_pdf_statement(_icici_year_pdf(), "", "statement.pdf")
    got = [(p.amount_paise, p.direction, p.merchant) for p in rows]
    assert got == [
        (85_000_00, "credit", "Acme Technology Internati"),
        (1_178_82, "debit", "Eba"),
        (500_00, "debit", "Riya Menon K"),
        (14_468_00, "debit", "MakeMyTrip"),
        (17_000_00, "debit", "Anand Kumar"),
        (500_00, "debit", "Trf to PPF"),
    ]
    assert rows[1].narration == "EBA/PUR_DEMO000/20260429120000"
    assert rows[3].narration.endswith("MAKEMYTRIP COM")
    assert rows[3].narration.startswith("UPI/100000000002/Upi Transaction")
    # The savings account, not the PPF the summary names first; the PPF's own section is left.
    assert {p.account_mask for p in rows} == {"0777"}


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
