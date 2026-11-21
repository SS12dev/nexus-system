"""Money's parsing and arithmetic: alert emails, statement files, CAS output, NAVs, analysis.

The alert samples follow the wording Indian banks actually send (account numbers and handles
made up)."""

from datetime import date
from decimal import Decimal

import pytest

from nexus_common import money
from nexus_common.money import Txn

TODAY = date(2026, 11, 20)


# --- Amounts -------------------------------------------------------------------------------


def test_paise_and_rupees_round_trip():
    assert money.paise("1,23,456.78") == 12345678
    assert money.paise("Rs. 250") == 25000
    assert money.paise("INR 99.5") == 9950
    assert money.paise("₹1,000.00") == 100000
    assert money.paise(12) == 1200
    with pytest.raises(ValueError):
        money.paise("abc")
    assert money.rupees(12345678) == "₹1,23,456.78"
    assert money.rupees(99) == "₹0.99"
    assert money.rupees(-100000) == "-₹1,000.00"
    assert money.rupees(1000000000) == "₹1,00,00,000.00"


# --- Merchants and categories ----------------------------------------------------------------


def test_clean_merchant():
    assert money.clean_merchant("VPA swiggy@icici SWIGGY") == "Swiggy"
    assert money.clean_merchant("AMAZON PAY INDIA PVT LTD") == "Amazon"
    assert money.clean_merchant("rahul.k@okaxis") == "rahul.k@okaxis"
    assert money.clean_merchant("BLUE TOKAI COFFEE ROASTERS") == "Blue Tokai Coffee Roasters"
    assert money.clean_merchant("") == "Unknown"


def test_categorise():
    assert money.categorise("Swiggy") == "Food and dining"
    assert money.categorise("Netflix") == "Subscriptions"
    assert money.categorise("Uber") == "Transport"
    assert money.categorise("rahul.k@okaxis") == "Transfers"
    assert money.categorise("Some Shop") == "Other"
    # Money back from a shop is income, not shopping.
    assert money.categorise("Amazon", credit=True) == "Income"
    assert money.categorise("ACME CORP", "NEFT SALARY NOV", credit=True) == "Income"
    # The person's own rule wins.
    rules = {money.merchant_key("Swiggy"): "Groceries"}
    assert money.categorise("Swiggy", rules=rules) == "Groceries"
    assert set(c for c, _ in money._RULES) <= set(money.CATEGORIES)


# --- Alert emails ----------------------------------------------------------------------------


def test_hdfc_upi_debit():
    p = money.parse_alert(
        "You have done a UPI txn. Check details!",
        "Dear Customer, Rs.250.00 has been debited from account **1234 to VPA swiggy@icici "
        "SWIGGY on 18-11-26. Your UPI transaction reference number is 432112345678. If you did "
        "not authorize this transaction, please report it immediately.",
        TODAY,
    )
    assert p is not None
    assert (p.amount_paise, p.direction, p.channel) == (25000, "debit", "upi")
    assert p.merchant == "Swiggy"
    assert p.account_mask == "1234"
    assert p.occurred_on == date(2026, 11, 18)
    assert p.ref == "432112345678"
    assert p.institution is None or p.institution == "HDFC Bank"


def test_hdfc_credit_card_spend():
    p = money.parse_alert(
        "Alert : Update on your HDFC Bank Credit Card",
        "Thank you for using your HDFC Bank Credit Card ending 5678 for Rs 1,499.00 at "
        "NETFLIX COM on 15-11-2026 10:22:31. Authorization code:- 012345",
        TODAY,
    )
    assert p is not None
    assert (p.amount_paise, p.direction, p.channel, p.account_kind) == (
        149900,
        "debit",
        "card",
        "card",
    )
    assert p.account_mask == "5678"
    assert p.merchant.lower().startswith("netflix")
    assert p.institution == "HDFC Bank"
    assert p.occurred_on == date(2026, 11, 15)


def test_icici_card_spend():
    p = money.parse_alert(
        "Transaction alert for your ICICI Bank Credit Card",
        "Dear Customer, your ICICI Bank Credit Card XX9012 has been used for a transaction of "
        "INR 3,250.00 on Nov 19, 2026 at 08:14:01. Info: AMAZON PAY IN E COMMERC. The "
        "Available Credit Limit on your card is INR 1,46,750.00.",
        TODAY,
    )
    assert p is not None
    assert p.amount_paise == 325000  # not the available limit
    assert p.direction == "debit"
    assert p.account_mask == "9012"
    assert p.merchant == "Amazon"
    assert p.institution == "ICICI Bank"


def test_sbi_bare_amount():
    p = money.parse_alert(
        "",
        "Dear UPI user A/C X4321 debited by 120.0 on date 17Nov26 trf to RAPIDO Refno "
        "432198765432. If not u? call 1800111109. -SBI",
        TODAY,
    )
    assert p is not None
    assert (p.amount_paise, p.direction) == (12000, "debit")
    assert p.account_mask == "4321"
    assert p.merchant == "Rapido"
    assert p.occurred_on == date(2026, 11, 17)
    assert p.ref == "432198765432"
    assert money.categorise(p.merchant) == "Transport"


def test_axis_debit_with_balance():
    p = money.parse_alert(
        "Debit transaction alert",
        "INR 560.00 debited from A/c no. XX3456 on 16-11-2026 12:01:13 IST at BLINKIT. "
        "Avl Bal- INR 12,345.67. Not you? SMS BLOCK 3456 to 919951860002 - Axis Bank",
        TODAY,
    )
    assert p is not None
    assert p.amount_paise == 56000
    assert p.merchant == "Blinkit"
    assert p.institution == "Axis Bank"


def test_kotak_salary_credit():
    p = money.parse_alert(
        "Credit alert",
        "Rs. 1,25,000.00 is credited to your Kotak Bank a/c XX7788 on 01/11/2026 by NEFT from "
        "ACME TECHNOLOGIES PVT LTD. Avl bal Rs 2,10,000.00",
        TODAY,
    )
    assert p is not None
    assert (p.amount_paise, p.direction, p.channel) == (12500000, "credit", "neft")
    assert p.account_mask == "7788"
    assert p.occurred_on == date(2026, 11, 1)
    assert "Acme" in p.merchant


@pytest.mark.parametrize(
    "body",
    [
        "Your OTP for transaction of Rs 2,000.00 at AMAZON on card XX1234 is 123456.",
        "Your transaction of Rs 899.00 on card ending 1234 at MYNTRA was declined.",
        "Rs 15,000.00 is due on your credit card XX1234. Minimum amount due Rs 750.00.",
        "Rs 499.00 will be debited from a/c XX1234 on 25-11-26 towards NETFLIX e-mandate.",
        "Get 10% cashback up to Rs 500 on your next order!",
    ],
)
def test_not_transactions(body):
    assert money.parse_alert("Alert", body, TODAY) is None


def test_date_out_of_range_falls_back_to_received():
    p = money.parse_alert(
        "",
        "Rs 100.00 debited from a/c XX1111 on 01-01-2020 to VPA shop@ybl.",
        TODAY,
    )
    assert p is not None and p.occurred_on == TODAY


# --- Statement exports -------------------------------------------------------------------------


HDFC_CSV = b"""HDFC BANK Ltd.
Account Statement from 01/11/26 to 20/11/26

Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
01/11/26,NEFT CR-ACME TECHNOLOGIES PVT LTD-SALARY NOV,N305260000123,01/11/26,,125000.00,210000.00
03/11/26,UPI-SWIGGY-swiggy@icici-ICIC0000001-430712345678-UPI,0000430712345678,03/11/26,450.00,,209550.00
05/11/26,POS 512345XXXXXX6789 AMAZON PAY IN,000000000000,05/11/26,"1,299.00",,208251.00
07/11/26,ATM-NWD-SBIN-BANGALORE,000000000001,07/11/26,2000.00,,206251.00
"""


def test_hdfc_statement_csv():
    rows = money.parse_statement(HDFC_CSV, "Acct_Statement_XX1234.csv")
    assert [r.amount_paise for r in rows] == [12500000, 45000, 129900, 200000]
    assert [r.direction for r in rows] == ["credit", "debit", "debit", "debit"]
    assert [r.channel for r in rows] == ["neft", "upi", "card", "atm"]
    assert rows[1].merchant == "Swiggy"
    assert rows[2].merchant == "Amazon"
    assert rows[0].institution == "HDFC Bank"
    assert rows[3].balance_paise == 20625100
    assert rows[0].occurred_on == date(2026, 11, 1)


def test_sbi_statement_with_dr_cr_column():
    data = (
        b"Txn Date\tValue Date\tDescription\tRef No./Cheque No.\tAmount\tDr/Cr\tBalance\n"
        b"2 Nov 2026\t2 Nov 2026\tTO TRANSFER-UPI/DR/430811112222/ZOMATO/YESB\t"
        b"TRANSFER\t320.00\tDR\t9680.00\n"
        b"4 Nov 2026\t4 Nov 2026\tBY TRANSFER-INB IMPS/REFUND\tTRANSFER\t150.00\tCR\t9830.00\n"
    )
    rows = money.parse_statement(data, "sbi.tsv")
    assert [(r.amount_paise, r.direction) for r in rows] == [(32000, "debit"), (15000, "credit")]
    assert rows[0].occurred_on == date(2026, 11, 2)
    assert rows[0].merchant == "Zomato"


def test_signed_amount_csv():
    data = b"Date,Description,Amount\n2026-11-02,Spotify,-119.00\n2026-11-03,Interest,42.10\n"
    rows = money.parse_statement(data, "export.csv")
    assert [(r.amount_paise, r.direction) for r in rows] == [(11900, "debit"), (4210, "credit")]


def test_xlsx_statement(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["ICICI Bank savings account"])
    ws.append([])
    ws.append(
        ["S No.", "Value Date", "Transaction Date", "Cheque Number", "Transaction Remarks",
         "Withdrawal Amount (INR )", "Deposit Amount (INR )", "Balance (INR )"]
    )  # fmt: skip
    ws.append(
        [1, "10/11/2026", "10/11/2026", "-", "UPI/430900001111/Uber/uber@axis", 312.5, 0, 5000]
    )
    path = tmp_path / "s.xlsx"
    wb.save(path)
    rows = money.parse_statement(path.read_bytes(), "s.xlsx")
    assert len(rows) == 1
    assert rows[0].amount_paise == 31250 and rows[0].direction == "debit"
    assert rows[0].merchant == "Uber"
    assert rows[0].institution == "ICICI Bank"


def test_unreadable_statements():
    with pytest.raises(money.StatementError):
        money.parse_statement(b"hello,world\n1,2\n", "x.csv")
    with pytest.raises(money.StatementError):
        money.parse_statement(b"\xd0\xcf\x11\xe0", "old.xls")


def test_dedupe_key_is_stable_and_distinct():
    rows = money.parse_statement(HDFC_CSV, "a.csv")
    again = money.parse_statement(HDFC_CSV, "a.csv")
    assert [money.dedupe_key(r, "acc") for r in rows] == [money.dedupe_key(r, "acc") for r in again]
    assert len({money.dedupe_key(r, "acc") for r in rows}) == len(rows)
    assert money.dedupe_key(rows[0], "acc") != money.dedupe_key(rows[0], "other")


# --- Mutual funds -----------------------------------------------------------------------------


def test_holdings_from_cas():
    cas = {
        "statement_period": {"from": "01-Jan-2020", "to": "19-Nov-2026"},
        "folios": [
            {
                "folio": "1234567 / 89",
                "amc": "Parag Parikh Mutual Fund",
                "schemes": [
                    {
                        "scheme": "Parag Parikh Flexi Cap Fund - Direct Growth",
                        "isin": "INF879O01027",
                        "amfi": "122639",
                        "close": 512.345,
                        "valuation": {
                            "date": "19-Nov-2026",
                            "nav": 92.1234,
                            "value": 47198.11,
                            "cost": 35000,
                        },
                    },  # fmt: skip
                    {"scheme": "Old fund", "isin": "INF000000000", "close": 0, "valuation": {}},
                ],
            }
        ],
    }
    holdings, as_of = money.holdings_from_cas(cas)
    assert as_of == date(2026, 11, 19)
    assert len(holdings) == 1
    h = holdings[0]
    assert h.units == Decimal("512.345")
    assert h.value_paise == 4719811 and h.cost_paise == 3500000
    assert h.amfi_code == "122639"
    assert h.nav_date == date(2026, 11, 19)


def test_parse_amfi_navs():
    text = (
        "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;"
        "Net Asset Value;Date\n\nOpen Ended Schemes(Equity Scheme - Flexi Cap Fund)\n\n"
        "122639;INF879O01027;-;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;93.5012;"
        "20-Nov-2026\n"
        "999999;-;-;Broken;N.A.;20-Nov-2026\n"
    )
    navs = money.parse_amfi_navs(text)
    assert navs["INF879O01027"] == (Decimal("93.5012"), date(2026, 11, 20))
    assert navs["122639"][0] == Decimal("93.5012")
    assert "999999" not in navs and "-" not in navs


# --- Analysis --------------------------------------------------------------------------------


def _t(day, amount, merchant="Shop", category="Shopping", direction="debit", **kw):
    return Txn(date.fromisoformat(day), amount, direction, merchant, category, **kw)


def test_month_bounds():
    assert money.month_bounds("2026-11") == (date(2026, 11, 1), date(2026, 12, 1))
    assert money.month_bounds("2026-12") == (date(2026, 12, 1), date(2027, 1, 1))


def test_summarise_skips_transfers_and_hidden_rows():
    s = money.summarise(
        [
            _t("2026-11-01", 12500000, "Acme", "Income", "credit"),
            _t("2026-11-02", 45000, "Swiggy", "Food and dining"),
            _t("2026-11-03", 30000, "Swiggy", "Food and dining"),
            _t("2026-11-04", 129900, "Amazon", "Shopping"),
            _t("2026-11-05", 5000000, "me@okicici", "Transfers"),
            _t("2026-11-06", 99900, "Croma", "Shopping", excluded=True),
            _t("2026-11-07", 1000000, "Groww", "Investments"),
        ]
    )
    assert s["spent_paise"] == 204900
    assert s["income_paise"] == 12500000
    assert s["invested_paise"] == 1000000
    assert s["net_paise"] == 12500000 - 204900 - 1000000
    assert [c["category"] for c in s["categories"]] == ["Shopping", "Food and dining"]
    assert s["merchants"][1] == {"merchant": "Swiggy", "spent_paise": 75000, "count": 2}


def test_find_subscriptions():
    txns = [
        _t("2026-08-05", 64900, "Netflix", "Subscriptions"),
        _t("2026-09-05", 64900, "Netflix", "Subscriptions"),
        _t("2026-10-05", 64900, "Netflix", "Subscriptions"),
        _t("2026-11-05", 64900, "Netflix", "Subscriptions"),
        # Yearly
        _t("2025-03-01", 149900, "Amazon Prime", "Subscriptions"),
        _t("2026-03-02", 149900, "Amazon Prime", "Subscriptions"),
        # Frequent but not a subscription
        _t("2026-11-01", 25000, "Swiggy", "Food and dining"),
        _t("2026-11-03", 41000, "Swiggy", "Food and dining"),
        _t("2026-11-10", 18000, "Swiggy", "Food and dining"),
        # Lapsed: last paid in June
        _t("2026-04-10", 11900, "Spotify", "Subscriptions"),
        _t("2026-05-10", 11900, "Spotify", "Subscriptions"),
        _t("2026-06-10", 11900, "Spotify", "Subscriptions"),
        # Paying off a card isn't a subscription, however regular.
        _t("2026-09-20", 1400000, "Cred Club", "Transfers"),
        _t("2026-10-20", 1400000, "Cred Club", "Transfers"),
        _t("2026-11-19", 1400000, "Cred Club", "Transfers"),
        # Wildly different amounts
        _t("2026-09-01", 10000, "Airtel", "Bills and utilities"),
        _t("2026-10-01", 90000, "Airtel", "Bills and utilities"),
        _t("2026-11-01", 30000, "Airtel", "Bills and utilities"),
    ]
    subs = money.find_subscriptions(txns, TODAY)
    by = {s.merchant: s for s in subs}
    assert set(by) == {"Netflix", "Amazon Prime"}
    assert by["Netflix"].cadence == "monthly" and by["Netflix"].next_on == date(2026, 12, 5)
    assert by["Netflix"].count == 4
    assert by["Amazon Prime"].cadence == "yearly"
    assert by["Amazon Prime"].monthly_paise == 149900 // 12


def test_statement_account_mask():
    assert money.statement_mask("Account No : 50100123456789 Branch : MG Road") == "6789"
    assert money.statement_mask("Acct_Statement_XX1234.csv") == "1234"
    assert money.statement_mask("Card No. 4375 XXXX XXXX 1234") == "1234"
    assert money.statement_mask("Savings statement") is None
    rows = money.parse_statement(HDFC_CSV, "Acct_Statement_XX1234.csv")
    assert {r.account_mask for r in rows} == {"1234"}


def test_card_bill_payment_is_a_transfer():
    assert money.categorise("Cred Club", "UPI-CRED CLUB-cred.club@axisb") == "Transfers"
    assert money.categorise("Payment", "PAYMENT RECEIVED - THANK YOU", credit=True) == "Transfers"


def test_read_cas_rejects_non_pdf():
    with pytest.raises(money.StatementError):
        money.read_cas(b"not a pdf", "ABCDE1234F")


def test_brand_names():
    assert money.clean_merchant("JIOMART GROCERY") == "JioMart"
    assert money.clean_merchant("OPENAI *CHATGPT SUBSCR") == "OpenAI"
    assert money.clean_merchant("nobroker.rent@hdfcbank") == "NoBroker"


# --- Balances, limits, card bills, EMIs and price changes --------------------------------------


def test_alerts_carry_the_balance_and_available_limit():
    bank = money.parse_alert(
        "Debit transaction alert",
        "INR 560.00 debited from A/c no. XX3456 on 16-11-2026 12:01:13 IST at BLINKIT. "
        "Avl Bal- INR 12,345.67. Not you? SMS BLOCK 3456 to 919951860002 - Axis Bank",
        TODAY,
    )
    assert bank.balance_paise == 1234567
    card = money.parse_alert(
        "Transaction alert for your ICICI Bank Credit Card",
        "Dear Customer, your ICICI Bank Credit Card XX9012 has been used for a transaction of "
        "INR 3,250.00 on Nov 19, 2026 at 08:14:01. Info: AMAZON PAY IN E COMMERC. The "
        "Available Credit Limit on your card is INR 1,46,750.00.",
        TODAY,
    )
    assert card.balance_paise is None
    assert card.extra["available_limit_paise"] == 14675000


def test_card_payment_received_is_a_bill_payment():
    p = money.parse_alert(
        "Payment received on your credit card",
        "Dear Cardmember, Payment of Rs 15,230.00 has been received towards your HDFC Bank "
        "Credit Card ending 5678 on 18-11-2026. Thank you.",
        TODAY,
        sender="alerts@hdfcbank.net",
    )
    assert p is not None and p.direction == "credit"
    assert (p.merchant, p.account_kind, p.account_mask) == ("Card bill payment", "card", "5678")
    assert money.categorise(p.merchant, p.narration, credit=True) == "Transfers"


@pytest.mark.parametrize(
    "sender,subject,body,expected",
    [
        (
            "emailstatements.cards@hdfcbank.net",
            "Your HDFC Bank Credit Card Statement for November 2026",
            "Dear Customer, your HDFC Bank Credit Card ending 5678 statement is ready. "
            "Total Amount Due: Rs. 24,512.00 Minimum Amount Due: Rs. 1,230.00 "
            "Payment Due Date: 07/12/2026",
            ("5678", "HDFC Bank", 2451200, 123000, date(2026, 12, 7)),
        ),
        (
            "credit_cards@icicibank.com",
            "ICICI Bank Credit Card Statement",
            "Your ICICI Bank Credit Card XX9012 statement for the period 16 Oct 2026 to "
            "15 Nov 2026. Total Amount due INR 8,431.50, Minimum Amount due INR 430.00. "
            "Due Date: December 3, 2026",
            ("9012", "ICICI Bank", 843150, 43000, date(2026, 12, 3)),
        ),
        (
            "statements@sbicard.com",
            "Your SBI Card Monthly Statement - Nov 2026",
            "SBI Card ending 4321. Total Amount Due ₹12,000.00. Min. Amount Due ₹600.00. "
            "Payment due by 28 Nov 2026.",
            ("4321", "SBI Card", 1200000, 60000, date(2026, 11, 28)),
        ),
        (
            "cc.statements@axisbank.com",
            "Axis Bank Credit Card Statement",
            "Credit Card No. XX7788. Total Payment Due: INR 3,100 Minimum Payment Due: INR 200 "
            "Payment Due Date: 05-12-2026",
            ("7788", "Axis Bank", 310000, 20000, date(2026, 12, 5)),
        ),
    ],
)
def test_card_bills(sender, subject, body, expected):
    bill = money.parse_card_bill(subject, body, TODAY, sender=sender)
    assert bill is not None
    got = (bill.account_mask, bill.institution, bill.total_due_paise, bill.min_due_paise,
           bill.due_on)  # fmt: skip
    assert got == expected


def test_not_card_bills():
    # A due-date reminder without the amounts, a promotion, an ordinary alert.
    assert money.parse_card_bill("Payment reminder", "Your card XX5678 is due soon.", TODAY) is None
    assert (
        money.parse_card_bill(
            "Get a new credit card",
            "Lifetime free credit card statement offer. Total due Rs 0. Due date 01/01/2030",
            TODAY,
        )
        is None
    )


def test_bill_status():
    due, today = date(2026, 12, 7), date(2026, 11, 25)
    stmt = date(2026, 11, 18)
    assert money.bill_status(None, None, None, None, 0, today)["status"] == "none"
    assert money.bill_status(2451200, due, stmt, None, 0, today) == {
        "status": "due", "left_paise": 2451200}  # fmt: skip
    assert money.bill_status(2451200, due, stmt, None, 1000000, today)["status"] == "part"
    assert money.bill_status(2451200, due, stmt, None, 2451200, today)["status"] == "paid"
    assert money.bill_status(2451200, due, stmt, None, 0, date(2026, 12, 8))["status"] == "overdue"
    assert money.bill_status(2451200, due, stmt, date(2026, 11, 20), 0, today)["status"] == "paid"


def test_emi_lines_are_loans_not_shopping():
    for narration in ("EMI PRINCIPAL 03/12 SONY INDIA PVT LTD", "EMI INTEREST 03/12 SONY",
                      "ACH D- BAJAJ FINANCE LTD-12345678", "GST ON EMI INTEREST"):  # fmt: skip
        merchant = money.clean_merchant(narration)
        assert money.categorise(merchant, narration) == "Loans and EMIs", narration
    assert money.clean_merchant("EMI PRINCIPAL 03/12 SONY INDIA PVT LTD") == "EMI Sony"
    assert money.clean_merchant("EMI INTEREST 03/12 SONY") == "EMI Sony"
    # "Premium" is not an EMI.
    assert money.categorise("YouTube Premium", "YOUTUBE PREMIUM") == "Subscriptions"
    # The purchase coming back as EMIs is a transfer, not income.
    assert money.is_emi_conversion("SMARTEMI CONVERSION SONY PLAYSTATION 5")
    assert money.categorise("Sony", "EMI CONVERSION SONY", credit=True) == "Transfers"


def test_emi_booking_email():
    b = money.parse_emi_booking(
        "Your SmartEMI request is processed",
        "Dear Customer, your transaction of Rs 54,990.00 at SONY INDIA on HDFC Bank Credit "
        "Card ending 5678 has been successfully converted to EMI for a tenure of 12 months at "
        "an interest rate of 15% p.a. Your EMI amount is Rs 4,963.00.",
        date(2026, 9, 2),
        sender="alerts@hdfcbank.net",
    )
    assert b is not None
    assert (b.account_mask, b.principal_paise, b.months, b.rate_bp, b.emi_paise) == (
        "5678", 5499000, 12, 1500, 496300)  # fmt: skip
    assert b.institution == "HDFC Bank"
    no_cost = money.parse_emi_booking(
        "EMI booked",
        "Your purchase of INR 49,990 on ICICI Bank Credit Card XX9012 has been converted to a "
        "No Cost EMI of 6 months.",
        date(2026, 9, 2),
    )
    assert no_cost is not None and (no_cost.months, no_cost.rate_bp) == (6, 0)
    # An offer is not a booking.
    assert (
        money.parse_emi_booking(
            "Convert to EMI",
            "Your purchase of Rs 54,990 on card XX5678 is eligible to be converted to EMI for "
            "12 months. Convert now!",
            date(2026, 9, 2),
        )
        is None
    )


def test_find_emis():
    txns = [
        _t("2026-10-05", 433400, "EMI Sony", "Loans and EMIs", account_id="c1"),
        _t("2026-10-05", 54900, "EMI Sony", "Loans and EMIs", account_id="c1"),
        _t("2026-11-05", 440000, "EMI Sony", "Loans and EMIs", account_id="c1"),
        _t("2026-11-05", 48300, "EMI Sony", "Loans and EMIs", account_id="c1"),
        _t("2026-06-05", 100000, "EMI Old", "Loans and EMIs", account_id="c1"),
    ]
    found = money.find_emis(txns, TODAY)
    assert [(e.merchant, e.monthly_paise, e.months_seen) for e in found] == [
        ("EMI Sony", 488300, 2)
    ]
    # EMIs are loans, not repeating payments to review.
    assert money.find_subscriptions(txns, TODAY) == []


def test_subscription_price_change():
    txns = [
        _t("2026-08-05", 49900, "Netflix", "Subscriptions"),
        _t("2026-09-05", 49900, "Netflix", "Subscriptions"),
        _t("2026-10-05", 49900, "Netflix", "Subscriptions"),
        _t("2026-11-05", 64900, "Netflix", "Subscriptions"),
    ]
    (sub,) = money.find_subscriptions(txns, TODAY)
    assert (sub.typical_paise, sub.previous_paise, sub.price_change_paise) == (64900, 49900, 15000)
    assert money.manage_url("Netflix") == "https://www.netflix.com/account"
    assert money.roll_forward(date(2026, 1, 31), "monthly", TODAY) == date(2026, 11, 30)
    assert money.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_savings_instalments_are_investing_not_emis():
    for narration in ("RD INSTALLMENT 000123 HDFC", "SIP INSTALMENT GROWW"):
        assert money.categorise(money.clean_merchant(narration), narration) == "Investments"
