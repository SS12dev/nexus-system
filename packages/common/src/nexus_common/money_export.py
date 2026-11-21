"""Money as an Excel workbook, for the person's records or to hand over to family.

One sheet each for the summary (net worth and what goes out each month), accounts and cards,
savings and deposits (with account numbers, maturity and nominees), mutual funds, loans and
EMIs, subscriptions and insurance. Amounts are real numbers in rupees with a ₹ format, dates
are real dates, and every table has a bold, frozen header row, so the file sorts and filters
like any other.

The workbook holds the full account numbers and nominees, so it can be locked with a password:
`protect` encrypts the whole file the way Excel does (Agile encryption, AES-256), and Excel,
Numbers, LibreOffice and Google Sheets ask for the password to open it.

Nothing here logs; the route records only that an export happened.
"""

import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import money
from nexus_common import money_plan as plan
from nexus_common import money_store as store
from nexus_common.models import MoneyAsset, MoneyHolding, MoneyLoan, User
from nexus_common.settings import Settings

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Indian grouping (₹12,34,56,789.00). Conditional sections can't show a minus the same way in
# every app, so a negative amount gets the plain signed format instead (see `_fmt`).
RUPEES = '[>=10000000]"₹"##\\,##\\,##\\,##0.00;[>=100000]"₹"##\\,##\\,##0.00;"₹"#,##0.00'
RUPEES_WHOLE = '[>=10000000]"₹"##\\,##\\,##\\,##0;[>=100000]"₹"##\\,##\\,##0;"₹"#,##0'
SIGNED = {
    RUPEES: '"₹"#,##0.00;[Red]-"₹"#,##0.00',
    RUPEES_WHOLE: '"₹"#,##0;[Red]-"₹"#,##0',
}
DAY = "d mmm yyyy"
PERCENT = "0.00%"
UNITS = "#,##0.000"

INK = "1F2937"
HEADER = PatternFill("solid", fgColor="1E3A5F")
BAND = PatternFill("solid", fgColor="F3F6FA")
TOTAL = PatternFill("solid", fgColor="E6EEF7")
THIN = Side(style="thin", color="C9D3DF")

CADENCE = {"weekly": "Weekly", "monthly": "Monthly", "quarterly": "Quarterly", "yearly": "Yearly"}
STATUS = {
    "new": "To review",
    "active": "Active",
    "cancelled": "Cancelled",
    "ignored": "Not a subscription",
    "closed": "Closed",
}


@dataclass
class Col:
    title: str
    fmt: str | None = None
    width: int | None = None
    wrap: bool = False


@dataclass
class Table:
    """A sheet: a heading line, a note, a header row and rows, with optional totals."""

    name: str
    heading: str
    note: str
    cols: list[Col]
    rows: list[list[Any]] = field(default_factory=list)
    totals: dict[int, Any] = field(default_factory=dict)
    empty: str = "Nothing here yet."


def _fmt(fmt: str, value: Any) -> str:
    return SIGNED.get(fmt, fmt) if isinstance(value, int | float) and value < 0 else fmt


def _r(p: int | None) -> float | None:
    return None if p is None else p / 100


def _pct(bp: int | None) -> float | None:
    return None if not bp else bp / 10000


# --- Gathering ---------------------------------------------------------------------------------


async def gather(db: AsyncSession, settings: Settings, user: User, today: date) -> list[Table]:
    """Read everything once and lay it out as tables. Decrypts account numbers and nominees."""
    accounts = await store.accounts_view(db, user.id, today)
    asset_rows = list(
        (
            await db.scalars(
                select(MoneyAsset)
                .where(MoneyAsset.user_id == user.id)
                .order_by(MoneyAsset.status, MoneyAsset.kind, MoneyAsset.created_at)
            )
        ).all()
    )
    loans = await store.loans_view(db, user.id, today)
    loan_rows = {
        str(r.id): r
        for r in (await db.scalars(select(MoneyLoan).where(MoneyLoan.user_id == user.id))).all()
    }
    holdings = list(
        (
            await db.scalars(
                select(MoneyHolding)
                .where(MoneyHolding.user_id == user.id)
                .order_by(MoneyHolding.amc, MoneyHolding.scheme)
            )
        ).all()
    )
    subs = await store.subscriptions_view(db, user.id, today)
    prefs = await store.settings_for(db, user.id)
    assume = plan.Assumptions.from_dict(prefs.plan)

    assets = [(row, store.asset_out(row, today)) for row in asset_rows]
    active_assets = [a for _, a in assets if a["status"] == "active"]
    live_loans = [x for x in loans["items"] if x["status"] == "active" and not x["done"]]
    live_subs = [s for s in subs["items"] if s["status"] in ("new", "active")]
    funds_value = sum(h.value_paise or 0 for h in holdings)

    # --- Accounts and cards
    acct = Table(
        "Accounts and cards",
        "Bank accounts and cards",
        "Balances and bills as last seen in an alert, a statement or typed in. Only the last "
        "four digits are known for these.",
        [
            Col("Type", width=8),
            Col("Bank or issuer", width=18),
            Col("Name", width=22),
            Col("Last 4", width=8),
            Col("Balance", RUPEES, 14),
            Col("Balance on", DAY, 12),
            Col("Card limit", RUPEES, 14),
            Col("Owed now", RUPEES, 14),
            Col("Last bill", RUPEES, 14),
            Col("Bill due", DAY, 12),
            Col("Bill status", width=12),
            Col("Shown in Nexus", width=10),
        ],
        empty="No accounts yet. They appear once an alert or statement is read.",
    )
    for a in accounts["items"]:
        card = a["kind"] == "card"
        bill = (a.get("bill") or {}).get("status")
        acct.rows.append([
            "Card" if card else "Bank",
            a["institution"] or "",
            a["name"] or a["label"],
            a["mask"] or "",
            None if card else _r(a["balance_paise"]),
            None if card else a["balance_on"],
            _r(a.get("credit_limit_paise")),
            _r(a.get("owed_paise")),
            _r(a.get("due_paise")),
            a.get("due_on"),
            (bill or "").replace("_", " ").capitalize() if card else "",
            "No" if a["hidden"] else "Yes",
        ])  # fmt: skip
    acct.totals = {4: _r(accounts["cash_paise"]), 7: _r(accounts["owed_paise"])}

    # --- Savings and deposits
    sav = Table(
        "Savings and deposits",
        "Savings and deposits",
        "FDs and RDs are valued from their terms (compounded quarterly); PPF, EPF, NPS and the "
        "rest from the last balance typed in, grown at the rate shown. Check with the bank or "
        "scheme before relying on a value.",
        [
            Col("Type", width=16),
            Col("Name", width=22),
            Col("Bank or scheme", width=18),
            Col("Branch", width=18),
            Col("Account, folio or member no.", width=24),
            Col("Nominee", width=20),
            Col("Nominee is", width=12),
            Col("Deposit or balance", RUPEES, 16),
            Col("Balance on", DAY, 12),
            Col("Rate a year", PERCENT, 10),
            Col("Started", DAY, 12),
            Col("Matures", DAY, 12),
            Col("Worth today", RUPEES, 16),
            Col("At maturity", RUPEES, 16),
            Col("Goes in each month", RUPEES, 14),
            Col("Interest paid out", width=10),
            Col("Status", width=10),
            Col("Note", width=30, wrap=True),
        ],
        empty="No savings added yet. Add FDs, RDs, PPF and EPF in Money › Savings.",
    )
    for row, a in assets:
        sav.rows.append([
            a["kind_label"],
            a["name"],
            a["institution"] or "",
            a["branch"] or "",
            money.unseal(settings, row.account_no_enc),
            money.unseal(settings, row.nominee_enc),
            a["nominee_relation"] or "",
            _r(a["amount_paise"]),
            a["balance_on"],
            _pct(a["rate_bp"]),
            a["start_on"],
            a["maturity_on"],
            _r(a["value_paise"]),
            _r(a["maturity_value_paise"]),
            _r(a["monthly_paise"]) or None,
            ("Yes" if a["payout"] else "No") if a["kind"] == "fd" else "",
            STATUS.get(a["status"], a["status"]),
            a["note"] or "",
        ])  # fmt: skip
    sav.totals = {
        12: _r(sum(a["value_paise"] for a in active_assets)),
        14: _r(sum(a["monthly_paise"] for a in active_assets)),
    }

    # --- Mutual funds
    mf = Table(
        "Mutual funds",
        "Mutual funds",
        "From the last CAS statement uploaded; values use the latest NAV Nexus fetched.",
        [
            Col("Fund house", width=22),
            Col("Scheme", width=40, wrap=True),
            Col("Folio", width=16),
            Col("ISIN", width=14),
            Col("Units", UNITS, 12),
            Col("NAV", RUPEES, 12),
            Col("NAV on", DAY, 12),
            Col("Value", RUPEES, 16),
            Col("Invested", RUPEES, 16),
            Col("Gain", RUPEES, 14),
            Col("Units as of", DAY, 12),
        ],
        empty="No mutual funds yet. Upload a CAS PDF in Money › Import and settings.",
    )
    for h in holdings:
        gain = (
            (h.value_paise - h.cost_paise) if h.value_paise is not None and h.cost_paise else None
        )
        mf.rows.append([
            h.amc, h.scheme, h.folio, h.isin or "", float(h.units),
            float(h.nav) if h.nav is not None else None, h.nav_date,
            _r(h.value_paise), _r(h.cost_paise), _r(gain), h.as_of,
        ])  # fmt: skip
    mf.totals = {
        7: _r(funds_value),
        8: _r(sum(h.cost_paise or 0 for h in holdings)),
    }

    # --- Loans and EMIs
    ln = Table(
        "Loans and EMIs",
        "Loans and EMIs",
        "What's left is worked out from the terms (reducing balance). The lender's statement "
        "is the final word, especially after a part payment.",
        [
            Col("Type", width=14),
            Col("Name", width=22),
            Col("Lender", width=16),
            Col("Loan account no.", width=22),
            Col("Paid from", width=18),
            Col("Borrowed", RUPEES, 14),
            Col("Rate a year", PERCENT, 10),
            Col("Months", "0", 8),
            Col("EMI", RUPEES, 12),
            Col("First EMI", DAY, 12),
            Col("EMIs paid", "0", 9),
            Col("EMIs left", "0", 9),
            Col("Next EMI", DAY, 12),
            Col("Last EMI", DAY, 12),
            Col("Still owed", RUPEES, 14),
            Col("Interest still to pay", RUPEES, 14),
            Col("Status", width=10),
            Col("Note", width=30, wrap=True),
        ],
        empty="No loans or EMIs added. Add them in Money › Accounts and cards.",
    )
    for x in loans["items"]:
        row = loan_rows.get(x["id"])
        ln.rows.append([
            x["kind_label"], x["name"], x["lender"] or "",
            money.unseal(settings, row.account_no_enc) if row else "",
            x["account"] or "", _r(x["principal_paise"]), _pct(x["rate_bp"]), x["months"],
            _r(x["emi_paise"]), x["first_due_on"], x["paid"], x["left"], x["next_due_on"],
            x["ends_on"], _r(x["outstanding_paise"]), _r(x["interest_left_paise"]),
            "Paid off" if x["done"] else STATUS.get(x["status"], x["status"]), x["note"] or "",
        ])  # fmt: skip
    ln.totals = {8: _r(loans["monthly_paise"]), 14: _r(loans["outstanding_paise"])}

    # --- Subscriptions
    sb = Table(
        "Subscriptions",
        "Subscriptions and bills",
        "Repeating payments found in your transactions, and the ones you added. Monthly is the "
        "amount spread over a month, so yearly plans compare fairly.",
        [
            Col("Name", width=24),
            Col("Category", width=16),
            Col("Kind", width=12),
            Col("How often", width=11),
            Col("Amount", RUPEES, 12),
            Col("A month", RUPEES, 12),
            Col("Next charge", DAY, 12),
            Col("Last charge", DAY, 12),
            Col("Status", width=14),
            Col("Cancelled on", DAY, 12),
            Col("Manage at", width=36),
            Col("Note", width=30, wrap=True),
        ],
        empty="No repeating payments found yet.",
    )
    for s in subs["items"]:
        sb.rows.append([
            s["merchant"], s["category"], "Bill" if s["group"] == "bill" else "Subscription",
            CADENCE.get(s["cadence"], s["cadence"]), _r(s["amount_paise"]),
            _r(s["monthly_paise"]), s["next_on"], s["last_on"],
            STATUS.get(s["status"], s["status"]), s["cancelled_on"], s["manage_url"] or "",
            s["note"] or "",
        ])  # fmt: skip
    sb.totals = {5: _r(sum(s["monthly_paise"] for s in live_subs))}

    # --- Insurance
    ins = Table(
        "Insurance",
        "Insurance",
        "The cover you told the Plan about. Nexus doesn't keep individual policies yet: add "
        "each policy's insurer, number, renewal date and nominee below so this sheet is "
        "complete for whoever needs it.",
        [
            Col("Cover", width=22),
            Col("Amount", RUPEES_WHOLE, 16),
            Col("Insurer", width=18),
            Col("Policy no.", width=20),
            Col("Renews on", DAY, 12),
            Col("Nominee", width=20),
            Col("Note", width=40, wrap=True),
        ],
    )
    ins.rows = [
        ["Health", _r(assume.health_cover_paise), "", "", None, "",
         "" if assume.health_cover_paise is not None else "Not told yet"],
        ["Life (term)", _r(assume.life_cover_paise), "", "", None, "",
         "" if assume.life_cover_paise is not None else "Not told yet"],
        ["People who depend on you", None, "", "", None, "",
         {True: "Yes", False: "No", None: "Not told yet"}[assume.dependants]],
    ]  # fmt: skip

    # --- Summary
    cash = accounts["cash_paise"]
    saved = sum(a["value_paise"] for a in active_assets)
    owed_cards = accounts["owed_paise"]
    owed_loans = sum(x["outstanding_paise"] for x in live_loans)
    worth = cash + saved + funds_value - owed_cards - owed_loans
    by_kind: dict[str, int] = {}
    for a in active_assets:
        by_kind[a["kind_label"]] = by_kind.get(a["kind_label"], 0) + a["value_paise"]
    summary = Table(
        "Summary",
        f"Finances of {user.name or user.email}",
        f"Prepared by Nexus on {today:%-d %B %Y}. Amounts are in Indian rupees. Card dues "
        "and loans count against net worth. Account numbers and nominees are on the "
        "Savings and Loans sheets: keep this file somewhere safe.",
        [Col("", width=34), Col("Amount", RUPEES_WHOLE, 18), Col("", width=60, wrap=True)],
    )
    note_unknown = (
        f"{accounts['unknown_balances']} bank account(s) have no balance yet, so cash is low."
        if accounts["unknown_balances"]
        else ""
    )
    summary.rows = [
        ["What you have", None, None],
        ["Cash in bank accounts", _r(cash), note_unknown],
        *[[f"   {k}", _r(v), None] for k, v in sorted(by_kind.items())],
        ["Mutual funds", _r(funds_value), None],
        ["Total you have", _r(cash + saved + funds_value), None],
        ["What you owe", None, None],
        ["Credit cards", _r(owed_cards), None],
        ["Loans and EMIs", _r(owed_loans), None],
        ["Total you owe", _r(owed_cards + owed_loans), None],
        ["Net worth", _r(worth), "What you have minus what you owe."],
        ["Each month", None, None],
        ["EMIs", _r(loans["monthly_paise"]), f"{len(live_loans)} active"],
        ["Subscriptions and bills", _r(sum(s["monthly_paise"] for s in live_subs)),
         f"{len(live_subs)} active"],
        ["Going into savings", _r(sum(a["monthly_paise"] for a in active_assets)),
         "RDs, PPF, EPF, NPS and the rest"],
    ]  # fmt: skip
    return [summary, acct, sav, mf, ln, sb, ins]


# --- Writing -----------------------------------------------------------------------------------

SECTION_ROWS = {"What you have", "What you owe", "Each month"}
TOTAL_ROWS = {"Total you have", "Total you owe", "Net worth"}


def _write_summary(ws: Worksheet, t: Table) -> None:
    ws["A1"] = t.heading
    ws["A1"].font = Font(size=16, bold=True, color=INK)
    ws["A2"] = t.note
    ws["A2"].font = Font(italic=True, color="4B5563")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A2:C2")
    ws.row_dimensions[2].height = 48
    r = 4
    for label, amount, note in t.rows:
        if label in SECTION_ROWS:
            r += 1 if r > 4 else 0
            cell = ws.cell(r, 1, label)
            cell.font = Font(bold=True, color="FFFFFF")
            for c in range(1, 4):
                ws.cell(r, c).fill = HEADER
        else:
            ws.cell(r, 1, label)
            if amount is not None:
                ws.cell(r, 2, amount).number_format = _fmt(RUPEES_WHOLE, amount)
            if note:
                ws.cell(r, 3, note).alignment = Alignment(wrap_text=True, indent=1)
            if label in TOTAL_ROWS:
                for c in range(1, 4):
                    ws.cell(r, c).font = Font(bold=True, size=13 if label == "Net worth" else 11)
                    ws.cell(r, c).fill = TOTAL
                    ws.cell(r, c).border = Border(top=THIN)
        r += 1
    for i, col in enumerate(t.cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = col.width or 14


def _write_table(ws: Worksheet, t: Table) -> None:
    ws["A1"] = t.heading
    ws["A1"].font = Font(size=14, bold=True, color=INK)
    ws["A2"] = t.note
    ws["A2"].font = Font(italic=True, color="4B5563")
    ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    last = get_column_letter(len(t.cols))
    ws.merge_cells(f"A2:{last}2")
    ws.row_dimensions[2].height = 32
    head = 4
    for i, col in enumerate(t.cols, 1):
        cell = ws.cell(head, i, col.title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = Border(bottom=THIN)
    ws.row_dimensions[head].height = 30
    ws.freeze_panes = ws.cell(head + 1, 1)
    widths = [len(col.title) + 2 for col in t.cols]
    r = head
    for n, row in enumerate(t.rows):
        r += 1
        for i, (col, value) in enumerate(zip(t.cols, row, strict=True), 1):
            if value == "" or value is None:
                value = None
            cell = ws.cell(r, i, value)
            if col.fmt and value is not None and not isinstance(value, str):
                cell.number_format = _fmt(col.fmt, value)
            if col.wrap:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if n % 2:
                cell.fill = BAND
            if isinstance(value, str):
                widths[i - 1] = max(widths[i - 1], min(len(value) + 2, 48))
    if not t.rows:
        r += 1
        ws.cell(r, 1, t.empty).font = Font(italic=True, color="6B7280")
    elif t.totals:
        r += 1
        ws.cell(r, 1, "Total (active)").font = Font(bold=True)
        for c in range(1, len(t.cols) + 1):
            ws.cell(r, c).fill = TOTAL
            ws.cell(r, c).border = Border(top=THIN)
        for i, value in t.totals.items():
            cell = ws.cell(r, i + 1, value)
            cell.font = Font(bold=True)
            cell.number_format = _fmt(t.cols[i].fmt or RUPEES, value)
        ws.auto_filter.ref = f"A{head}:{last}{r - 1}"
    else:
        ws.auto_filter.ref = f"A{head}:{last}{r}"
    for i, col in enumerate(t.cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = max(col.width or 0, widths[i - 1])


def render(tables: list[Table], *, title: str, when: datetime) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for t in tables:
        ws = wb.create_sheet(t.name[:31])
        ws.sheet_view.showGridLines = t.name != "Summary"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        if t.name == "Summary":
            _write_summary(ws, t)
        else:
            ws.print_title_rows = "4:4"
            _write_table(ws, t)
    wb.properties.title = title
    wb.properties.creator = "Nexus"
    wb.properties.created = when.replace(tzinfo=None)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def protect(data: bytes, password: str) -> bytes:
    """Encrypt the workbook so it needs the password to open (ECMA-376 Agile, AES-256)."""
    from msoffcrypto.format.ooxml import OOXMLFile

    out = io.BytesIO()
    OOXMLFile(io.BytesIO(data)).encrypt(password, out)
    return out.getvalue()


def filename(today: date) -> str:
    return f"finances-{today.isoformat()}.xlsx"
