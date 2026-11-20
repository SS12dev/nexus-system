"""Money: a read-only view of the person's finances, built from what already reaches them.

- Bank and card **alert emails** in Gmail ("Rs 250.00 debited from a/c XX1234 to VPA
  swiggy@icici on 20-11-26") are read by `parse_alert`.
- **Statement exports** (CSV or Excel) are read by `parse_statement`.
- **CAMS / KFintech consolidated statements** (the CAS PDF) give mutual fund holdings, valued
  each night from AMFI's public NAV file (`parse_amfi_navs`).

Nothing here moves money or talks to a bank. Everything is parsed on this machine with plain
rules (no model sees a statement or an alert), amounts are whole paise in BigIntegers, the raw
narration of each transaction is encrypted at rest (`seal` / `unseal`), and logs carry counts
only, never amounts, names or merchants.

This module is pure parsing and arithmetic so it can be tested without a database; the API
(routes/money.py) and the orchestrator (money.py) do the storing and fetching.
"""

import csv
import hashlib
import io
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from nexus_common.google import decrypt, encrypt
from nexus_common.settings import Settings

# --- Storage helpers ------------------------------------------------------------------------


def seal(settings: Settings, text: str) -> str:
    """Encrypt a piece of financial text (a narration, a statement password) for the database."""
    return encrypt(settings, text) if text else ""


def unseal(settings: Settings, value: str | None) -> str:
    return (decrypt(settings, value) or "") if value else ""


def paise(value: str | float | Decimal | int) -> int:
    """'1,23,456.78' or 123456.78 -> 12345678. Raises ValueError on anything else."""
    if isinstance(value, int):
        return value * 100
    text = str(value).strip().replace(",", "").replace("₹", "")
    text = re.sub(r"^(?:rs\.?|inr)\s*", "", text, flags=re.I).strip()
    if not text:
        raise ValueError("empty amount")
    try:
        d = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {value!r}") from exc
    return int((d * 100).quantize(Decimal(1)))


def rupees(p: int) -> str:
    """12345678 -> '₹1,23,456.78' (Indian digit grouping)."""
    sign = "-" if p < 0 else ""
    whole, frac = divmod(abs(p), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1]
        digits = f"{head},{tail}"
    return f"{sign}₹{digits}.{frac:02d}"


# --- Categories --------------------------------------------------------------------------------

CATEGORIES = (
    "Food and dining",
    "Groceries",
    "Transport",
    "Shopping",
    "Bills and utilities",
    "Rent and housing",
    "Entertainment",
    "Subscriptions",
    "Health",
    "Travel",
    "Education",
    "Investments",
    "Transfers",
    "Cash",
    "Fees and charges",
    "Income",
    "Other",
)

# Lowercase words in the merchant or narration -> category. First match wins, so specific
# names come before generic words.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    # Paying off a card or moving money between your own accounts isn't spending; the card
    # spends themselves are counted.
    ("Transfers", ("credit card bill", "credit card payment", "cc payment", "card bill payment",
                   "cred club", "cred.club", "credclub", "payment received",
                   "thank you for your payment",
                   "self transfer", "own account", "sweep")),
    ("Subscriptions", ("netflix", "spotify", "prime video", "hotstar", "youtube premium",
                       "apple.com/bill", "google play", "icloud", "openai", "chatgpt", "anthropic",
                       "notion", "github", "jiocinema", "sonyliv", "zee5", "audible", "linkedin")),
    ("Food and dining", ("swiggy", "zomato", "eatsure", "dominos", "domino's", "mcdonald",
                         "kfc", "starbucks", "cafe", "restaurant", "pizza", "burger", "chaayos",
                         "third wave", "blue tokai", "haldiram", "barbeque")),
    ("Groceries", ("bigbasket", "blinkit", "zepto", "instamart", "dmart", "jiomart", "grofers",
                   "more retail", "reliance fresh", "nature's basket", "spencer", "milkbasket",
                   "country delight", "kirana", "supermarket")),
    ("Transport", ("uber", "ola", "rapido", "irctc", "metro", "fastag", "petrol", "fuel",
                   "indian oil", "iocl", "hpcl", "bpcl", "shell", "parking", "redbus",
                   "namma yatri")),
    ("Travel", ("makemytrip", "goibibo", "cleartrip", "yatra", "indigo", "air india", "vistara",
                "akasa", "spicejet", "airbnb", "oyo", "booking.com", "agoda", "hotel", "ixigo")),
    ("Shopping", ("amazon", "flipkart", "myntra", "ajio", "nykaa", "meesho", "croma",
                  "reliance digital", "decathlon", "ikea", "tata cliq", "snapdeal", "lenskart",
                  "bambu", "robu", "thingiverse")),
    ("Bills and utilities", ("electricity", "bescom", "msedcl", "tata power", "adani elec",
                             "airtel", "jio", "vodafone", "vi prepaid", "bsnl", "act fibernet",
                             "broadband", "gas", "water bill", "dth", "tatasky", "tata play",
                             "postpaid", "recharge", "bbps", "bill pay", "billdesk")),
    ("Rent and housing", ("rent", "nobroker", "housing.com", "maintenance", "society",
                          "nestaway")),
    ("Entertainment", ("bookmyshow", "pvr", "inox", "district", "steam", "playstation",
                       "xbox", "concert", "paytm insider")),
    ("Health", ("pharmacy", "apollo", "medplus", "netmeds", "1mg", "pharmeasy", "practo",
                "hospital", "clinic", "diagnostic", "lab", "cult.fit", "cultfit", "gym",
                "healthify")),
    ("Education", ("coursera", "udemy", "edx", "school", "college", "university", "tuition",
                   "kindle", "books")),
    ("Investments", ("zerodha", "groww", "kuvera", "coin by", "upstox", "mutual fund",
                     "sip", "nps", "ppf", "camsonline", "kfintech", "indmoney", "smallcase",
                     "icclzerodha", "bse limited", "nse clearing")),
    ("Fees and charges", ("charges", "fee", "penalty", "gst on", "annual fee", "late payment",
                          "interest charged", "sms alert")),
    ("Cash", ("atm", "cash withdrawal", "cwdr", "nwd")),
    ("Income", ("salary", "sal cr", "payroll", "interest credit", "int.pd", "dividend",
                "refund", "cashback", "reversal")),
]  # fmt: skip


def categorise(merchant: str, narration: str = "", *, credit: bool = False,
               rules: dict[str, str] | None = None) -> str:  # fmt: skip
    """The person's own rule for this merchant first, then the built-in word list. Money coming
    in with no better match is Income; payments to a person (a UPI handle) are Transfers."""
    if rules and (own := rules.get(merchant_key(merchant))):
        return own
    hay = f" {merchant} {narration} ".lower()
    for category, words in _RULES:
        if any(w in hay for w in words):
            if credit and category not in ("Income", "Investments", "Transfers"):
                # A refund from Amazon is money back, not shopping.
                return "Income"
            return category
    if credit:
        return "Income"
    if re.search(r"\b[a-z0-9._-]+@(?:ok\w+|ybl|paytm|ibl|axl|upi|apl|icici|hdfcbank|sbi)\b", hay):
        return "Transfers"
    return "Other"


def merchant_key(merchant: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", merchant.lower()).strip()


_HANDLE_NAMES = {
    "swiggy": "Swiggy", "zomato": "Zomato", "amazon": "Amazon", "flipkart": "Flipkart",
    "uber": "Uber", "olacabs": "Ola", "netflix": "Netflix", "spotify": "Spotify",
    "bigbasket": "BigBasket", "blinkit": "Blinkit", "zepto": "Zepto", "irctc": "IRCTC",
    "airtel": "Airtel", "jiomart": "JioMart", "jio": "Jio", "myntra": "Myntra",
    "bookmyshow": "BookMyShow",
    "zerodha": "Zerodha", "groww": "Groww", "phonepe": "PhonePe", "paytm": "Paytm",
    "openai": "OpenAI", "anthropic": "Anthropic", "github": "GitHub", "youtube": "YouTube",
    "nobroker": "NoBroker", "makemytrip": "MakeMyTrip", "dmart": "DMart",
}  # fmt: skip


def clean_merchant(raw: str) -> str:
    """'VPA swiggy@icici SWIGGY' -> 'Swiggy'; 'AMAZON PAY INDIA PVT LTD' -> 'Amazon Pay India';
    'rahul.k@okaxis' -> 'rahul.k@okaxis' (a person stays as their handle)."""
    text = " ".join((raw or "").split())
    text = re.sub(r"^(?:vpa|upi|to|at|from|by)\s+", "", text, flags=re.I)
    lowered = text.lower()
    for key, name in _HANDLE_NAMES.items():
        if re.search(rf"(?<![a-z]){re.escape(key)}", lowered):
            return name
    handle = re.match(r"^([\w.-]+@[\w.-]+)", text)
    if handle:
        return handle.group(1).lower()
    text = re.sub(
        r"\b(?:pvt|private|ltd|limited|llp|inc|india|technologies|services|payments?)\b\.?",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"[^\w&.' -]+", " ", text)
    words = [w for w in text.split() if not re.fullmatch(r"\d{5,}", w)][:5]
    name = " ".join(words).strip(" .-")
    return name.title() if name.isupper() or name.islower() else name or "Unknown"


# --- Alert emails -------------------------------------------------------------------------------

_AMOUNT = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_BARE_AMOUNT = re.compile(
    r"\b(?:debited|credited)\s+(?:by|with|for)\s+([\d,]+(?:\.\d{1,2})?)", re.I
)
_NOT_AMOUNT_BEFORE = re.compile(
    r"(?:bal(?:ance)?|lmt|limit|avl|available|outstanding|due)\W*$", re.I
)
_DEBIT = re.compile(
    r"\b(debited|spent|withdrawn|paid|sent|purchase|deducted|us(?:ed|ing)\b[^.]{0,80}\bfor)\b",
    re.I,
)
_CREDIT = re.compile(r"\b(credited|received|deposited|refund(?:ed)?|reversed)\b", re.I)
_NOT_A_TRANSACTION = re.compile(
    r"\b(otp|one time password|will be (?:debited|credited)|is due|minimum amount due|"
    r"statement (?:is|for)|e-?mandate|autopay (?:registration|request)|requested money|"
    r"collect request|payment reminder|declined|failed|unsuccessful)\b",
    re.I,
)
_ACCOUNT = re.compile(
    r"(?:a/?c|acct|account|card)(?:\s*(?:no\.?|number|ending(?:\s*(?:with|in))?))?"
    r"[\s:.]*[x*#]+\s?(\d{3,4})\b|\bending\s*(?:with|in)?\s*(\d{4})\b|\b[xX*]{1,8}(\d{4})\b",
    re.I,
)
_DATES = [
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})[- ]?([A-Za-z]{3})[a-z]*[- ,]?(\d{4}|\d{2})\b"), "dMy"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
]
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}  # fmt: skip
_REF = re.compile(
    r"(?:ref(?:erence)?\s*(?:no\.?|number|id)?|upi(?:\s*ref)?|refno|txn\s*(?:id|no))"
    r"\s*(?:is)?[\s:.#-]*(\d{6,})",
    re.I,
)
_MERCHANT_PATTERNS = [
    re.compile(r"\binfo[-:\s]+(?:upi/)?(?:p2[ma]/)?(?:\d+/)?"
               r"([A-Za-z][\w .&@-]{1,40}?)(?:/|\.|$|\s{2})", re.I),
    re.compile(r"\bvpa\s+([\w.-]+@[\w.-]+)(?:\s+([A-Z][A-Z0-9 &.]{2,40}?))?"
               r"(?=\s+on\b|\.|\s*$|\s+ref)", re.I),
    re.compile(r";\s*([A-Za-z][\w .&-]{1,40}?)\s+credited\b", re.I),
    re.compile(r"\btrf to\s+([A-Za-z][\w .&-]{1,40}?)(?=\s+ref|\s+on\b|\.|$)", re.I),
    re.compile(r"\bat\s+([A-Za-z0-9][\w .&*'@-]{1,40}?)(?=\s+on\b|\.\s|\s+avl|\s+\(|,|$)", re.I),
    re.compile(r"\b(?:to|towards)\s+([A-Za-z][\w .&@-]{1,40}?)(?=\s+on\b|\.\s|\s+ref|,|$)", re.I),
    re.compile(r"\b(?:from|by)\s+(?:(?:neft|imps|rtgs|upi|transfer)\s+(?:from\s+)?)?"
               r"([A-Za-z][\w .&@/-]{1,40}?)(?=\s+on\b|\.\s|\s+ref|,|$)", re.I),
]  # fmt: skip
_BANKS = [
    ("HDFC Bank", ("hdfc",)),
    ("ICICI Bank", ("icici",)),
    ("State Bank of India", ("sbi", "state bank")),
    ("Axis Bank", ("axis",)),
    ("Kotak Mahindra Bank", ("kotak",)),
    ("IDFC FIRST Bank", ("idfc",)),
    ("Yes Bank", ("yes bank", "yesbank")),
    ("IndusInd Bank", ("indusind",)),
    ("Federal Bank", ("federal",)),
    ("AU Small Finance Bank", ("au bank", "aubank")),
    ("Bank of Baroda", ("bank of baroda", "bobank")),
    ("Punjab National Bank", ("pnb",)),
    ("American Express", ("americanexpress", "amex")),
    ("OneCard", ("onecard",)),
]
# Gmail search that finds most Indian bank and card alerts; parse_alert does the real check.
ALERT_QUERY = (
    '(debited OR credited OR spent OR withdrawn OR "transaction alert") '
    "(Rs OR INR OR ₹) -category:promotions -category:social"
)


@dataclass
class Parsed:
    """One transaction read from an alert or a statement row."""

    occurred_on: date
    amount_paise: int
    direction: str  # "debit" or "credit"
    merchant: str
    narration: str
    channel: str = "other"
    account_mask: str | None = None
    account_kind: str = "bank"
    institution: str | None = None
    ref: str | None = None
    balance_paise: int | None = None
    extra: dict = field(default_factory=dict)


def bank_of(text: str) -> str | None:
    low = text.lower()
    for name, keys in _BANKS:
        if any(k in low for k in keys):
            return name
    return None


def _date(text: str, fallback: date) -> date:
    for pattern, kind in _DATES:
        for m in pattern.finditer(text):
            try:
                if kind == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "dmy":
                    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                else:
                    mon = _MONTHS.get(m.group(2)[:3].lower())
                    if mon is None:
                        continue
                    d, mo, y = int(m.group(1)), mon, int(m.group(3))
                if y < 100:
                    y += 2000
                found = date(y, mo, d)
            except ValueError:
                continue
            # An alert is about the last few weeks; anything else is a misread.
            if fallback - timedelta(days=45) <= found <= fallback + timedelta(days=1):
                return found
    return fallback


def _amount(text: str) -> int | None:
    for m in _AMOUNT.finditer(text):
        if _NOT_AMOUNT_BEFORE.search(text[max(0, m.start() - 24) : m.start()]):
            continue
        try:
            value = paise(m.group(1))
        except ValueError:
            continue
        if value > 0:
            return value
    m = _BARE_AMOUNT.search(text)
    if m:
        try:
            return paise(m.group(1))
        except ValueError:
            return None
    return None


def _merchant(text: str, direction: str) -> str:
    for pattern in _MERCHANT_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        if pattern.pattern.startswith(r"\b(?:from|by)") and direction == "debit":
            continue
        name = m.group(1)
        if m.lastindex and m.lastindex >= 2 and m.group(2):
            # "VPA swiggy@icici SWIGGY": the name after the handle reads better.
            name = m.group(2)
        name = re.sub(r"\b(?:a/?c|account|acct)\b.*$", "", name, flags=re.I).strip()
        if name and not re.fullmatch(r"[\dxX* ]+", name) and name.lower() not in ("your", "you"):
            return clean_merchant(name)
    return "Unknown"


def parse_alert(subject: str, body: str, received: date, sender: str = "") -> Parsed | None:
    """Read one bank or card alert email. None when it isn't a completed transaction (an OTP, a
    due-date reminder, a declined payment, a promotion). The bank comes from the sender's
    address when there is one, since the text names other banks too ("VPA x@icici")."""
    text = " ".join(f"{subject}. {body}".split())[:4000]
    if _NOT_A_TRANSACTION.search(text):
        return None
    amount = _amount(text)
    if not amount:
        return None
    debit, credit = _DEBIT.search(text), _CREDIT.search(text)
    if not debit and not credit:
        return None
    direction = "debit" if debit and (not credit or debit.start() < credit.start()) else "credit"
    account = _ACCOUNT.search(text)
    if account is None:
        return None
    mask = next(g for g in account.groups() if g)
    low = text.lower()
    card = bool(re.search(r"\bcard\b", low[max(0, account.start() - 30) : account.end() + 5]))
    channel = (
        "upi" if re.search(r"\b(upi|vpa)\b", low)
        else "card" if card or "card" in low
        else "atm" if re.search(r"\batm\b|withdrawn", low)
        else "neft" if "neft" in low
        else "imps" if "imps" in low
        else "other"
    )  # fmt: skip
    ref = _REF.search(text)
    return Parsed(
        occurred_on=_date(text, received),
        amount_paise=amount,
        direction=direction,
        merchant=_merchant(text, direction),
        narration=text[:600],
        channel=channel,
        account_mask=mask[-4:],
        account_kind="card" if card else "bank",
        institution=bank_of(sender) or bank_of(re.sub(r"\S+@\S+", " ", text)),
        ref=ref.group(1) if ref else None,
    )


# --- Statement exports -------------------------------------------------------------------------

_HEAD = {
    "date": ("txn date", "transaction date", "tran date", "date", "value date", "value dt"),
    "narration": ("narration", "description", "particulars", "transaction remarks", "remarks",
                  "details", "transaction details"),
    "debit": ("withdrawal amt", "withdrawal amount", "withdrawal", "debit amount", "debit", "dr"),
    "credit": ("deposit amt", "deposit amount", "deposit", "credit amount", "credit", "cr"),
    "amount": ("amount", "transaction amount", "amount (inr)"),
    "type": ("dr/cr", "cr/dr", "type", "transaction type", "debit/credit"),
    "balance": ("closing balance", "balance", "available balance"),
    "ref": ("chq./ref.no.", "ref no./cheque no.", "cheque number", "chq/ref number",
            "reference no", "ref no", "utr"),
}  # fmt: skip


class StatementError(ValueError):
    """The file isn't a statement this can read; the message says what to do."""


def _norm_header(cell: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[()\[\]]|inr|₹|\.$", "", str(cell or "").lower())).strip()


def _columns(row: list[str]) -> dict[str, int] | None:
    cells = [_norm_header(c) for c in row]
    found: dict[str, int] = {}
    for key, names in _HEAD.items():
        for name in (_norm_header(n) for n in names):
            # Short names ("dr", "cr") must match exactly, or "dr/cr" would read as a debit column.
            hit = next(
                (
                    i
                    for i, cell in enumerate(cells)
                    if i not in found.values()
                    and (cell == name or (len(name) > 3 and cell.startswith(name)))
                ),
                None,
            )
            if hit is not None:
                found[key] = hit
                break
    if "date" not in found or "narration" not in found:
        return None
    if not ({"debit", "credit"} <= found.keys() or "amount" in found):
        return None
    return found


_ROW_DATES = ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%Y", "%d-%m-%y", "%d-%b-%Y", "%d-%b-%y",
              "%d %b %Y", "%d %b %y", "%Y-%m-%d", "%d.%m.%Y", "%d %B %Y", "%b %d, %Y")  # fmt: skip


def _row_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in _ROW_DATES:
        try:
            return datetime.strptime(text[:20].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _cell_paise(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float, Decimal)):
        return paise(Decimal(str(value)))
    text = str(value).strip()
    if not text or text in ("-", "--"):
        return 0
    text = re.sub(r"\s*(?:cr|dr)\.?$", "", text, flags=re.I)
    return paise(text)


def _rows_from(data: bytes, name: str) -> list[list]:
    low = name.lower()
    if low.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise StatementError("That Excel file couldn't be opened.") from exc
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)][:20000]
    if low.endswith(".xls"):
        raise StatementError(
            "Old .xls files can't be read. Open it in Excel or Numbers and save as .xlsx or .csv."
        )
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)][:20000]


def parse_statement(data: bytes, name: str) -> list[Parsed]:
    """Rows of a bank or card statement export (CSV or .xlsx), oldest first. The header row is
    found wherever it is (banks put a few lines of account details above it)."""
    rows = _rows_from(data, name)
    cols = None
    start = 0
    for i, row in enumerate(rows[:60]):
        cols = _columns([str(c) if c is not None else "" for c in row])
        if cols:
            start = i + 1
            break
    if not cols:
        raise StatementError(
            "Couldn't find the columns. The file needs a header row with a date, a description "
            "and either withdrawal and deposit columns or an amount column."
        )
    out: list[Parsed] = []
    head = " ".join(str(c) for r in rows[: max(0, start - 1)] for c in r if c)
    mask = statement_mask(f"{head} {name}")
    kind = "card" if re.search(r"\bcard\b", head, re.I) else "bank"
    institution = bank_of(" ".join(str(c) for r in rows[: start + 1] for c in r if c)) or bank_of(
        name
    )
    for row in rows[start:]:
        if len(row) <= max(cols.values()):
            row = list(row) + [None] * (max(cols.values()) + 1 - len(row))
        when = _row_date(row[cols["date"]])
        if when is None:
            continue
        narration = " ".join(str(row[cols["narration"]] or "").split())
        try:
            if "amount" in cols and not {"debit", "credit"} <= cols.keys():
                value = _cell_paise(row[cols["amount"]])
                kind = str(row[cols["type"]] or "").strip().lower() if "type" in cols else ""
                raw = str(row[cols["amount"]] or "").strip().lower().rstrip(".")
                if kind:
                    is_credit = kind.startswith(("c", "deposit"))
                elif raw.endswith(("cr", "dr")):
                    is_credit = raw.endswith("cr")
                else:
                    # A signed amount column: positive is money in.
                    is_credit = value > 0
                amount, direction = abs(value), "credit" if is_credit else "debit"
            else:
                debit = _cell_paise(row[cols["debit"]])
                credit = _cell_paise(row[cols["credit"]])
                amount, direction = (debit, "debit") if debit else (credit, "credit")
            balance = _cell_paise(row[cols["balance"]]) if "balance" in cols else None
        except ValueError:
            continue
        if not amount:
            continue
        low = narration.lower()
        channel = (
            "upi" if "upi" in low
            else "card" if re.search(r"\b(pos|card|ecom)\b", low)
            else "atm" if re.search(r"\b(atm|nwd|cwdr)\b", low)
            else "neft" if "neft" in low
            else "imps" if "imps" in low
            else "other"
        )  # fmt: skip
        out.append(
            Parsed(
                occurred_on=when,
                amount_paise=abs(amount),
                direction=direction,
                merchant=statement_merchant(narration),
                narration=narration[:600],
                channel=channel,
                account_mask=mask,
                account_kind=kind,
                institution=institution,
                ref=str(row[cols["ref"]]).strip() or None
                if "ref" in cols and row[cols["ref"]]
                else None,
                balance_paise=balance,
            )
        )
    out.sort(key=lambda p: p.occurred_on)
    return out


def statement_mask(text: str) -> str | None:
    """The last four digits of the account a statement is for, from the lines above its table
    or its file name ('Account No : 50100123456789', 'XX1234', 'Card No. 4375 XXXX XXXX 1234')."""
    m = re.search(
        r"(?:a/?c|account|card)\s*(?:no\.?|number|#)?\s*[:.-]?\s*([x*\d][x*\d -]{5,24}\d)",
        text,
        re.I,
    ) or re.search(r"[xX*]{2,}(\d{4})\b", text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return digits[-4:] if len(digits) >= 4 else None


def statement_merchant(narration: str) -> str:
    """The payee inside a statement narration: 'UPI-SWIGGY-swiggy@icici-ICIC0001-4321...'
    -> 'Swiggy'; 'POS 1234XXXX AMAZON PAY' -> 'Amazon'."""
    text = re.sub(r"^(?:to|by)\s+transfer-", "", narration.strip(), flags=re.I)
    m = re.match(
        r"^(?:upi|imps|neft|rtgs|ach|nach)(?:\s*cr|\s*dr)?[-/:](?:p2[am]/|[dc]r/)?(?:\d+/)?([^-/]+)",
        text,
        re.I,
    )
    if m:
        return clean_merchant(m.group(1))
    m = re.match(r"^(?:pos|ecom|ecs)\s+(?:\d+x+\d*\s+)?(.+)$", text, re.I)
    if m:
        return clean_merchant(m.group(1))
    return clean_merchant(re.split(r"[/|]", text)[0])


# --- Deduplication --------------------------------------------------------------------------


def dedupe_key(p: Parsed, account_id: str | None) -> str:
    """Stable id for one transaction from one source, so importing twice adds nothing."""
    basis = "|".join(
        str(x)
        for x in (
            account_id or p.account_mask or "",
            p.occurred_on.isoformat(),
            p.amount_paise,
            p.direction,
            p.ref or "",
            p.balance_paise if p.balance_paise is not None else "",
            "" if p.ref or p.balance_paise is not None else p.narration[:120].lower(),
        )
    )
    return hashlib.sha256(basis.encode()).hexdigest()[:40]


# --- Mutual funds ---------------------------------------------------------------------------


@dataclass
class Holding:
    folio: str
    amc: str
    scheme: str
    isin: str | None
    amfi_code: str | None
    units: Decimal
    nav: Decimal | None
    nav_date: date | None
    value_paise: int | None
    cost_paise: int | None


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def holdings_from_cas(data) -> tuple[list[Holding], date | None]:
    """Turn casparser's output (a dict, or its pydantic model) into holdings. Schemes with no
    units left are skipped."""
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    period = _get(data, "statement_period") or {}
    as_of = _row_date(_get(period, "to")) if period else None
    out: list[Holding] = []
    for folio in _get(data, "folios") or []:
        for s in _get(folio, "schemes") or []:
            units = Decimal(str(_get(s, "close") or _get(s, "close_calculated") or 0))
            if units <= 0:
                continue
            val = _get(s, "valuation") or {}
            nav = _get(val, "nav")
            value = _get(val, "value")
            cost = _get(val, "cost")
            out.append(
                Holding(
                    folio=str(_get(folio, "folio") or "").strip()[:40],
                    amc=str(_get(folio, "amc") or "").strip()[:120],
                    scheme=" ".join(str(_get(s, "scheme") or "").split())[:300],
                    isin=(_get(s, "isin") or None),
                    amfi_code=str(_get(s, "amfi")) if _get(s, "amfi") else None,
                    units=units,
                    nav=Decimal(str(nav)) if nav is not None else None,
                    nav_date=_row_date(_get(val, "date")),
                    value_paise=paise(Decimal(str(value))) if value is not None else None,
                    cost_paise=paise(Decimal(str(cost))) if cost is not None else None,
                )
            )
    return out, as_of


def read_cas(data: bytes, password: str) -> tuple[list[Holding], date | None]:
    """Holdings from a CAMS or KFintech consolidated account statement PDF."""
    import casparser
    from casparser.exceptions import CASParseError, IncorrectPasswordError, ParserException

    try:
        parsed = casparser.read_cas_pdf(io.BytesIO(data), password, output="dict")
    except IncorrectPasswordError as exc:
        raise StatementError(
            "The PDF password didn't work. A CAMS or KFintech statement's password is the one "
            "you chose when you asked for it (often your PAN, in capitals)."
        ) from exc
    except (CASParseError, ParserException) as exc:
        raise StatementError(
            "That PDF isn't a consolidated account statement this can read. Ask CAMS for a "
            "'Detailed' CAS (camsonline.com, Statements, CAS - CAMS+KFintech)."
        ) from exc
    except Exception as exc:  # pdf libraries raise many kinds of error for a broken file
        raise StatementError("That PDF couldn't be opened.") from exc
    return holdings_from_cas(parsed)


AMFI_NAV_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"


def parse_amfi_navs(text: str) -> dict[str, tuple[Decimal, date]]:
    """AMFI's daily NAV file -> {isin or scheme code: (nav, date)}. Lines look like
    'code;ISIN growth;ISIN reinvest;Scheme name;NAV;20-Nov-2026'."""
    out: dict[str, tuple[Decimal, date]] = {}
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) < 6 or not parts[0].strip().isdigit():
            continue
        try:
            nav = Decimal(parts[4].strip())
            when = datetime.strptime(parts[5].strip(), "%d-%b-%Y").date()
        except (InvalidOperation, ValueError):
            continue
        for key in (parts[0], parts[1], parts[2]):
            key = key.strip()
            if key and key != "-":
                out[key] = (nav, when)
    return out


# --- Analysis -------------------------------------------------------------------------------


@dataclass
class Txn:
    """What the analysis needs from a stored transaction."""

    occurred_on: date
    amount_paise: int
    direction: str
    merchant: str
    category: str
    excluded: bool = False


def month_bounds(month: str) -> tuple[date, date]:
    """'2026-11' -> (2026-11-01, 2026-12-01)."""
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return start, end


def counted(t: Txn) -> bool:
    """Transfers between your own accounts and hidden rows don't count as spending or income."""
    return not t.excluded and t.category != "Transfers"


def spending(t: Txn) -> bool:
    """Money out that is spent: not a transfer, a hidden row, or money put into investments
    (that is counted on its own, as invested)."""
    return t.direction == "debit" and counted(t) and t.category != "Investments"


def summarise(txns: list[Txn]) -> dict:
    spent = sum(t.amount_paise for t in txns if spending(t))
    income = sum(t.amount_paise for t in txns if t.direction == "credit" and counted(t))
    invested = sum(
        t.amount_paise
        for t in txns
        if t.direction == "debit" and counted(t) and t.category == "Investments"
    )
    by_cat: dict[str, int] = defaultdict(int)
    by_merchant: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for t in txns:
        if spending(t):
            by_cat[t.category] += t.amount_paise
            by_merchant[t.merchant][0] += t.amount_paise
            by_merchant[t.merchant][1] += 1
    return {
        "spent_paise": spent,
        "income_paise": income,
        "invested_paise": invested,
        "net_paise": income - spent - invested,
        "categories": sorted(
            ({"category": c, "spent_paise": v} for c, v in by_cat.items()),
            key=lambda x: -x["spent_paise"],
        ),
        "merchants": sorted(
            ({"merchant": m, "spent_paise": v[0], "count": v[1]} for m, v in by_merchant.items()),
            key=lambda x: -x["spent_paise"],
        )[:10],
        "count": len(txns),
    }


@dataclass
class Subscription:
    merchant: str
    category: str
    cadence: str  # "monthly" or "yearly"
    typical_paise: int
    last_on: date
    next_on: date
    count: int
    monthly_paise: int


def find_subscriptions(txns: list[Txn], today: date) -> list[Subscription]:
    """Payments that repeat (subscriptions, rent, bills, SIPs): the same merchant at least
    three times about a month apart (or twice about a year apart), for about the same amount
    (within 15%). Lapsed ones (no payment for two cycles) and card bill payments are left out."""
    by: dict[str, list[Txn]] = defaultdict(list)
    for t in txns:
        if (
            t.direction == "debit"
            and not t.excluded
            and t.merchant != "Unknown"
            and t.category not in ("Transfers", "Income")
        ):
            by[t.merchant].append(t)
    found: list[Subscription] = []
    for merchant, items in by.items():
        items.sort(key=lambda t: t.occurred_on)
        for cadence, lo, hi, need, step in (
            ("monthly", 25, 35, 3, 30),
            ("yearly", 350, 380, 2, 365),
        ):
            # The longest recent run of similar amounts at this spacing.
            run = [items[-1]]
            for t in reversed(items[:-1]):
                gap = (run[-1].occurred_on - t.occurred_on).days
                if gap < lo:
                    continue
                if gap > hi:
                    break
                run.append(t)
            if len(run) < need:
                continue
            typical = int(statistics.median(t.amount_paise for t in run))
            if any(abs(t.amount_paise - typical) > typical * 0.15 for t in run):
                continue
            last = run[0].occurred_on
            nxt = last + timedelta(days=step)
            if (today - last).days > step * 2:
                continue
            found.append(
                Subscription(
                    merchant=merchant,
                    category=items[-1].category,
                    cadence=cadence,
                    typical_paise=typical,
                    last_on=last,
                    next_on=nxt,
                    count=len(run),
                    monthly_paise=typical if cadence == "monthly" else typical // 12,
                )
            )
            break
    return sorted(found, key=lambda s: -s.monthly_paise)
