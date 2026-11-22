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

import base64
import csv
import hashlib
import io
import re
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from nexus_common.google import _fernets as token_fernets
from nexus_common.settings import Settings

# --- Storage helpers ------------------------------------------------------------------------


def _own_fernets(settings: Settings) -> list[Fernet]:
    keys = [k.strip() for k in settings.money_encryption_keys.split(",") if k.strip()]
    return [
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"nexus-money:" + k.encode()).digest()))
        for k in keys
    ]


def seal(settings: Settings, text: str) -> str:
    """Encrypt a piece of financial text (a narration, an account number, a statement
    password) for the database: under MONEY_ENCRYPTION_KEYS when set, else the token keys."""
    if not text:
        return ""
    own = _own_fernets(settings)
    return (own or token_fernets(settings))[0].encrypt(text.encode()).decode()


def unseal(settings: Settings, value: str | None) -> str:
    """ "" when empty or sealed under a key that is no longer configured."""
    if not value:
        return ""
    try:
        keys = [*_own_fernets(settings), *token_fernets(settings)]
        return MultiFernet(keys).decrypt(value.encode()).decode()
    except InvalidToken:
        return ""


def sealed_current(settings: Settings, value: str) -> bool:
    """Whether a sealed value is under the key `seal` uses now (else it should be moved)."""
    own = _own_fernets(settings)
    try:
        (own or token_fernets(settings))[0].decrypt(value.encode())
        return True
    except InvalidToken:
        return False


def last4(number: str | None) -> str | None:
    """The last four letters or digits of an account, folio or policy number, to show masked."""
    chars = re.sub(r"[^0-9A-Za-z]", "", number or "")
    return chars[-4:].upper() or None


def masked(last: str | None) -> str | None:
    return f"••••{last}" if last else None


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
    "Loans and EMIs",
    "Investments",
    "Transfers",
    "Cash",
    "Fees and charges",
    "Income",
    "Other",
)

# Spending you can't easily cut: a roof, food at home, getting about, health, bills and loans.
# Everything else counted as spending is lifestyle. Used for the essentials, lifestyle and
# savings balance on the Health tab.
ESSENTIAL_CATEGORIES = frozenset(
    {
        "Groceries",
        "Transport",
        "Bills and utilities",
        "Rent and housing",
        "Health",
        "Education",
        "Loans and EMIs",
        "Fees and charges",
    }
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
                       "apple.com/bill", "apple media", "apple services", "google play",
                       "googleplay", "youtube", "icloud", "openai", "chatgpt", "anthropic",
                       "notion", "github", "jiocinema", "sonyliv", "zee5", "audible", "linkedin")),
    ("Food and dining", ("swiggy", "zomato", "eatsure", "dominos", "domino's", "mcdonald",
                         "kfc", "starbucks", "cafe", "restaurant", "pizza", "burger", "chaayos",
                         "third wave", "blue tokai", "haldiram", "barbeque", "waffle", "bakery",
                         "biryani", "juice", "eatclub", "box8", "faasos")),
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
                  "bambu", "robu", "thingiverse", "sony", "apple india", "vijay sales")),
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
                     "recurring deposit", "rd instal", "rd install", "fixed deposit",
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
    if credit and is_emi_conversion(hay):
        # The card gives the purchase back and bills it as instalments instead.
        return "Transfers"
    if credit and _INTEREST.search(hay):
        return "Income"
    if credit and _MATURITY.search(hay):
        # A deposit coming back is your own money returning, not income.
        return "Investments"
    if not credit and _EMI.search(hay) and not _SAVING.search(hay):
        return "Loans and EMIs"
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


_INTEREST = re.compile(r"\bint(?:erest)?\.?\s*(?:pd|paid|cr|credit)|\binterest\b(?!.*charg)", re.I)
_MATURITY = re.compile(
    r"\bmaturity\b|\bmatured\b|premature|\bredemption\b|\bredeemed\b"
    r"|\b(?:fd|td|rd|deposit)\s*(?:a/c\s*)?(?:closure|closed|clos|liquidat)",
    re.I,
)

# Which kind of saving a payment or credit mentions, to tie it to one the person tracks.
_SAVING_KINDS: list[tuple[str, re.Pattern]] = [
    ("rd", re.compile(r"\brd\b|recurring\s*dep|\brd\s*inst", re.I)),
    ("ppf", re.compile(r"\bppf\b|public\s*provident", re.I)),
    ("nps", re.compile(r"\bnps\b|\bpran\b|protean|nsdl\s*cra", re.I)),
    ("fd", re.compile(r"\bfd\b|fixed\s*dep|term\s*dep|\btd\b|\bstdr\b|\bmod\b", re.I)),
    ("gold", re.compile(r"\bsgb\b|sovereign\s*gold|digital\s*gold|augmont|safegold", re.I)),
]


def saving_kind(text: str) -> str | None:
    """ "rd", "ppf", "nps", "fd" or "gold" when the text names one, else None."""
    return next((kind for kind, rx in _SAVING_KINDS if rx.search(text)), None)


def is_self_payee(merchant: str, owner_name: str | None) -> bool:
    """A UPI payment to someone with the person's own first and last name: money moved to
    another of their accounts (often one Money doesn't know about)."""
    names = [w for w in re.findall(r"[a-z]+", (owner_name or "").lower()) if len(w) > 1]
    if len(names) < 2:
        return False
    words = set(re.findall(r"[a-z]+", merchant.lower()))
    return names[0] in words and names[-1] in words


# An instalment of a loan or a card EMI. "emi" only as a word: "premium" isn't one.
_EMI = re.compile(
    r"\bemis?\b|\bloan\s*(?:emi|repayment|instal)|\binstal+ments?\b|bajaj\s*fin|hdb\s*fin"
    r"|home\s*credit|tvs\s*credit|snapmint|\b(?:n?ach)\b.{0,30}\b(?:loan|finance|fin)\b",
    re.I,
)
_EMI_CONVERSION = re.compile(
    r"emi\s*(?:conversion|booking|booked|reversal)|convert(?:ed)?\s*(?:in)?to\s*emi"
    r"|loan\s*booking|(?:smart|easy|instant|flexi)\s*emi\b.{0,20}\b(?:cr|credit|reversal)\b",
    re.I,
)


# An instalment into savings ("RD INSTALLMENT", "SIP") is investing, not a loan.
_SAVING = re.compile(
    r"recurring\s*deposit|\brd\b|fixed\s*deposit|\bfd\b|\bppf\b|\bnps\b|\bsip\b|mutual\s*fund", re.I
)


def is_emi_conversion(text: str) -> bool:
    """A card credit that turns a purchase into EMIs ("SMARTEMI CONVERSION", "LOAN BOOKING")."""
    return bool(_EMI_CONVERSION.search(text))


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
    "nobroker": "NoBroker", "makemytrip": "MakeMyTrip", "dmart": "DMart", "google": "Google",
}  # fmt: skip


def clean_merchant(raw: str) -> str:
    """'VPA swiggy@icici SWIGGY' -> 'Swiggy'; 'AMAZON PAY INDIA PVT LTD' -> 'Amazon Pay India';
    'rahul.k@okaxis' -> 'rahul.k@okaxis' (a person stays as their handle)."""
    text = " ".join((raw or "").split())
    text = re.sub(r"^(?:vpa|upi|to|at|from|by)\s+", "", text, flags=re.I)
    lowered = text.lower()
    if re.search(r"\bemis?\b", lowered) and "@" not in text:
        # "EMI PRINCIPAL 03/12 SONY INDIA" and "EMI INTEREST 03/12 SONY" are one loan: "EMI Sony".
        rest = re.sub(
            r"\b(?:emis?|principal|prin|interest|int|amount|amt|gst|igst|cgst|sgst|on|of|for|"
            r"smart|easy|instant|flexi|no|cost|loan|pvt|ltd|india|\d+(?:\s*/\s*\d+)?)\b",
            " ",
            text,
            flags=re.I,
        )
        words = [w for w in re.sub(r"[^\w&' ]+", " ", rest).split() if len(w) > 1][:3]
        return f"EMI {' '.join(words).title()}".strip()
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
_PAID_DONE = re.compile(
    r"has been (?:successfully )?(?:paid|debited|deducted|processed)|successfully paid", re.I
)
_NOT_A_TRANSACTION = re.compile(
    r"\b(otp|one time password|will be (?:debited|credited)|is due|minimum amount due|"
    r"statement (?:is|for)|e-?mandate|autopay (?:registration|request)|requested money|"
    r"collect request|payment reminder|declined|failed|unsuccessful)\b",
    re.I,
)
_ACCOUNT = re.compile(
    r"(?:a/?c|acct|account|card)(?:\s*(?:no\.?|number|ending(?:\s*(?:with|in))?))?"
    r"[\s:.]*[x*#]+\s?(\d{3,4})\b|\bending\s*(?:with|in)?\s*(\d{4})\b|\b[xX*]{1,8}(\d{4})\b"
    r"|\b\d{4,6}[xX*]{4,10}(\d{4})\b",
    re.I,
)
_DATES = [
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})[- ]?([A-Za-z]{3})[a-z]*[- ,]?(\d{4}|\d{2})\b"), "dMy"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b([A-Za-z]{3})[a-z]*\.?\s(\d{1,2}),?\s(\d{4})\b"), "Mdy"),
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
    # "Info: VIN*PLAYSTATION": the card network's prefix (VIN*, PYU*, RAZ*) goes.
    re.compile(r"\binfo[-:\s]+(?:upi/)?(?:p2[ma]/)?(?:\d+/)?(?:[A-Z]{2,5}\*)?"
               r"([A-Za-z][\w .&@-]{1,40}?)(?:/|\.|$|\s{2})", re.I),
    # "Sender: RAVI KUMAR (VPA: ...)" in HDFC's UPI credit alerts.
    re.compile(r"\bsender\s*:\s*([A-Za-z][\w .&-]{1,40}?)\s*(?:\(|\.\s|,|$)", re.I),
    # "Your EZVIZ bill, set up through E-mandate, has been successfully paid".
    re.compile(r"\byour\s+([A-Za-z][\w .&-]{1,40}?)\s+bill\b", re.I),
    # "towards VPA x@kotakpay (ZEPTO MARKETPLACE PRIVATE LIMITED) on 25-10-26" (HDFC, 2026).
    re.compile(r"\bvpa\s+[\w.*-]+@[\w.-]+\s*\(([^()]{2,60})\)", re.I),
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
    ("SBI Card", ("sbicard", "sbi card")),
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
BANK_NAMES = tuple(name for name, _ in _BANKS)
# Words in the senders' addresses (alerts@hdfcbank.net, credit_cards@icici.bank.in: Indian
# banks moved to .bank.in in 2025, and "bank.in" alone catches every one of them). Gmail's
# from: matches a word of the address, so one word covers every domain a bank uses.
ALERT_SENDERS = (
    "bank.in", "hdfcbank", "icici", "icicibank", "sbi", "sbicard", "onlinesbi", "axisbank", "kotak",
    "kotakbank", "idfcfirstbank", "yesbank", "indusind", "federalbank", "aubank",
    "bankofbaroda", "bobcard", "pnb", "unionbankofindia", "canarabank", "rblbank",
    "americanexpress", "aexp", "onecard", "scapia", "jupiter", "fi.money",
)  # fmt: skip
# Gmail search for bank and card alerts: anything from a bank's address, or an email that
# reads like one. Wide on purpose (bank offers come too); parse_alert does the real check.
# Promotions isn't skipped: Gmail files some banks' alerts there.
ALERT_QUERY = (
    "({" + " ".join(f"from:{s}" for s in ALERT_SENDERS) + "} OR "
    '((debited OR credited OR spent OR withdrawn OR "transaction alert" OR "has been used") '
    '(Rs OR "Rs." OR INR OR ₹))) -category:social'
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
    # Every account number a statement's heading quotes, best first: the one already known
    # wins over account_mask (a statement can also quote a card, a customer id or a reference).
    mask_options: tuple[str, ...] = ()


def bank_of(text: str) -> str | None:
    low = text.lower()
    for name, keys in _BANKS:
        if any(k in low for k in keys):
            return name
    return None


def _date(text: str, fallback: date, back: int = 45, ahead: int = 1) -> date | None:
    """The first date in the text within [fallback - back, fallback + ahead] days; the fallback
    when there is none (None when the fallback is None)."""
    for pattern, kind in _DATES:
        for m in pattern.finditer(text):
            try:
                if kind == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "dmy":
                    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif kind == "Mdy":
                    mon = _MONTHS.get(m.group(1)[:3].lower())
                    if mon is None:
                        continue
                    d, mo, y = int(m.group(2)), mon, int(m.group(3))
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
            if fallback is None or (
                fallback - timedelta(days=back) <= found <= fallback + timedelta(days=ahead)
            ):
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


# Sign-offs and help lines that read like payees to the patterns ("here to support you in
# every step of the way" became a merchant for every HDFC UPI alert).
_BOILERPLATE = re.compile(
    r"we(?:'re| are) here to (?:support|help) you[^.]*\.?|"
    r"if you did not authori[sz]e this transaction.*?(?=warm regards|$)|"
    r"for more details on service charges.*$|warm regards.*$|greetings from [a-z ]+bank!?|"
    # Safety footers mention OTPs and declined cards without being about either (ICICI).
    r"in case you have not done this transaction.*$|"
    r"(?:please )?never share your.*$|(?:please )?do not share your (?:otp|card|pin|cvv).*$",
    re.I,
)


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
        if (
            name
            and not re.fullmatch(r"[\dxX* ]+", name)
            and name.lower() not in ("your", "you")
            and not name.lower().startswith("your ")
        ):
            return clean_merchant(name)
    return "Unknown"


def parse_alert(subject: str, body: str, received: date, sender: str = "") -> Parsed | None:
    """Read one bank or card alert email. None when it isn't a completed transaction (an OTP, a
    due-date reminder, a declined payment, a promotion). The bank comes from the sender's
    address when there is one, since the text names other banks too ("VPA x@icici")."""
    text = _BOILERPLATE.sub(" ", " ".join(f"{subject}. {body}".split()))[:4000]
    text = " ".join(text.split())
    if (stop := _NOT_A_TRANSACTION.search(text)) and not (
        # An autopay that has gone through is a payment; only its set-up notices aren't.
        re.fullmatch(r"e-?mandate", stop.group(1), re.I) and _PAID_DONE.search(text)
    ):
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
    near = low[max(0, account.start() - 30) : account.end() + 5]
    # A debit card spends from the bank account; only a credit card is an account of its own.
    card = bool(re.search(r"\bcard\b", near)) and not re.search(r"\bdebit\s+card\b", low)
    channel = (
        "autopay" if re.search(r"e-?mandate|auto\s*-?pay|standing instruction|\bnach\b", low)
        else "upi" if re.search(r"\b(upi|vpa)\b", low)
        else "card" if card or "card" in low
        else "atm" if re.search(r"\batm\b|withdrawn", low)
        else "neft" if "neft" in low
        else "imps" if "imps" in low
        else "other"
    )  # fmt: skip
    ref = _REF.search(text)
    merchant = _merchant(text, direction)
    if card and direction == "credit" and re.search(r"\bpayment\b", low):
        # "Payment of Rs 15,230 received towards your card XX5678": paying the bill.
        merchant = "Card bill payment"
    kind = "card" if card else "bank"
    extra: dict = {}
    balance = None
    if (m := _BALANCE.search(text)) and kind == "bank":
        balance = _safe_paise(m.group(1))
    if (m := _LIMIT.search(text)) and kind == "card":
        extra["available_limit_paise"] = _safe_paise(m.group(1))
    return Parsed(
        occurred_on=_date(text, received),
        amount_paise=amount,
        direction=direction,
        merchant=merchant,
        narration=text[:600],
        channel=channel,
        account_mask=mask[-4:],
        account_kind=kind,
        institution=bank_of(sender) or bank_of(re.sub(r"\S+@\S+", " ", text)),
        ref=ref.group(1) if ref else None,
        balance_paise=balance,
        extra=extra,
    )


def _safe_paise(text: str) -> int | None:
    try:
        return paise(text)
    except ValueError:
        return None


_NUM = r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)"
_BALANCE = re.compile(
    r"\b(?:avl\.?|avbl\.?|available|clear|closing)?\s*bal(?:ance)?\b"
    r"(?:\s*in\s+(?:your\s+)?(?:a/c|account))?(?:\s*is)?[\s:.-]*"
    r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)",
    re.I,
)
_LIMIT = re.compile(
    r"\bavail(?:able|\.)?\s*(?:credit\s*)?(?:limit|lmt)\b[^\d₹]{0,30}?" + _NUM, re.I
)


@dataclass
class CardBill:
    """A credit card statement email: what's owed and by when."""

    account_mask: str
    institution: str | None
    total_due_paise: int
    min_due_paise: int | None
    due_on: date
    statement_on: date


_TOTAL_DUE = re.compile(
    r"\btotal\s*(?:amount|amt\.?|payment)?\s*(?:due|payable|outstanding)\b[^\d₹]{0,25}?" + _NUM,
    re.I,
)
_MIN_DUE = re.compile(
    r"\bmin(?:imum|\.)?\s*(?:amount|amt\.?|payment)?\s*(?:due|payable)\b[^\d₹]{0,25}?" + _NUM,
    re.I,
)
_DUE_DATE = re.compile(r"\b(?:payment\s*)?due\s*(?:date|by|on)\b[\s:.,-]*(?:is\s*)?(.{6,20})", re.I)
# Gmail search for credit card statement emails; parse_card_bill does the real check.
CARD_BILL_QUERY = (
    '"credit card" statement ("total amount due" OR "total due" OR "total payment due" '
    'OR "total amt due") -category:promotions'
)


# Statement PDFs banks email (an account's e-statement, a card's monthly statement).
STATEMENT_QUERY = (
    "({" + " ".join(f"from:{s}" for s in ALERT_SENDERS) + "}) subject:statement "
    "has:attachment filename:pdf"
)


@dataclass
class StatementMail:
    """A bank or card statement PDF emailed by the bank."""

    institution: str | None
    mask: str | None
    period_from: date | None
    period_to: date | None


# "Bank Statement from 01-03-2026 to 22-11-2026 for XXXXXXXXXX20" (ICICI), "Email Account
# Statement of your HDFC Bank Account ***1298 for the period 01-Apr-2026 TO 21-Nov-2026".
_STATEMENT_SUBJECT = re.compile(r"\bstatement\b", re.I)
_NOT_A_STATEMENT = re.compile(
    r"mini statement|consolidated account statement|\bcas\b|mutual fund|demat|"
    r"interest certificate|tax|form 16|tds|request|reminder|available on|ready to view",
    re.I,
)
_PERIOD = re.compile(r"(?:from|period)\s+(.+?)\s+to\s+(.+)", re.I)
_SUBJECT_DIGITS = re.compile(r"(?:[x*#]{2,}\s?(\d{2,4})\b)", re.I)


def parse_statement_mail(
    subject: str, received: date, sender: str = "", has_pdf: bool = True
) -> StatementMail | None:
    """A statement PDF from a bank (not a CAS, a tax form or a reminder). The period and the
    account digits come from the subject when it says them."""
    if not has_pdf or not _STATEMENT_SUBJECT.search(subject) or _NOT_A_STATEMENT.search(subject):
        return None
    bank = bank_of(sender)
    if bank is None:
        return None
    start = end = None
    if m := _PERIOD.search(subject):
        start = _date(m.group(1), None)
        end = _date(m.group(2), None)
    digits = _SUBJECT_DIGITS.search(subject)
    return StatementMail(
        institution=bank,
        mask=digits.group(1) if digits else None,
        period_from=start,
        period_to=end,
    )


def parse_card_bill(subject: str, body: str, received: date, sender: str = "") -> CardBill | None:
    """Read a credit card statement email: total due, minimum due and the due date. None for
    anything else (an ordinary alert, a promotion, a reminder without the amounts)."""
    text = " ".join(f"{subject}. {body}".split())[:6000]
    low = text.lower()
    if "card" not in low or "statement" not in low:
        return None
    total = _TOTAL_DUE.search(text)
    due = _DUE_DATE.search(text)
    account = _ACCOUNT.search(text)
    if not total or not due or not account:
        return None
    total_paise = _safe_paise(total.group(1))
    due_on = _date(due.group(1), None)
    if total_paise is None or due_on is None:
        return None
    if not received - timedelta(days=10) <= due_on <= received + timedelta(days=60):
        return None
    minimum = _MIN_DUE.search(text)
    return CardBill(
        account_mask=next(g for g in account.groups() if g)[-4:],
        institution=bank_of(sender) or bank_of(re.sub(r"\S+@\S+", " ", text)),
        total_due_paise=total_paise,
        min_due_paise=_safe_paise(minimum.group(1)) if minimum else None,
        due_on=due_on,
        statement_on=received,
    )


def bill_status(
    due_paise: int | None,
    due_on: date | None,
    statement_on: date | None,
    paid_on: date | None,
    payments_paise: int,
    today: date,
) -> dict:
    """Where a card's latest bill stands: paid, part paid, due or overdue, and what's left.
    Payments are the card's credits since the statement; marking it paid by hand wins."""
    if not due_paise or not due_on:
        return {"status": "none", "left_paise": 0}
    if paid_on and (statement_on is None or paid_on >= statement_on):
        return {"status": "paid", "left_paise": 0}
    left = max(due_paise - payments_paise, 0)
    if left <= 100:  # within a rupee
        return {"status": "paid", "left_paise": 0}
    if today > due_on:
        return {"status": "overdue", "left_paise": left}
    return {"status": "part" if payments_paise > 0 else "due", "left_paise": left}


@dataclass
class EmiBooking:
    """A purchase turned into EMIs, read from the card's confirmation email."""

    account_mask: str
    institution: str | None
    principal_paise: int
    # None when the email leaves the term to an attached schedule (ICICI does).
    months: int | None
    rate_bp: int
    emi_paise: int | None
    booked_on: date
    merchant: str


_EMI_BOOKED = re.compile(
    r"\b(?:has|have)\s+been\s+(?:successfully\s+)?(?:converted|booked|processed|approved)"
    r"|\bsuccessfully\s+(?:converted|booked)|\bemi\s+(?:is\s+)?(?:booked|confirmed)\b"
    r"|\bconverted\s+(?:in)?to\s+(?:an?\s+)?emi\b|\bwe\s+have\s+converted\b"
    r"|\binto\s+equated\s+monthly\s+instal",
    re.I,
)
_EMI_OFFER = re.compile(
    r"\beligible\b|\bpre-?approved\b|\bconvert\s+now\b|\bavail\b|\boffer\b|\bclick\b"
    r"|\bunsubscribe\b",
    re.I,
)
_TENURE = re.compile(r"\b(\d{1,2})\s*(?:months?|mths?|m)\b(?!\w)", re.I)
_TENURE_WORD = re.compile(r"\btenure\b[^\d]{0,20}(\d{1,2})", re.I)
_RATE = re.compile(
    r"(?:interest|roi|rate)[^\d%]{0,30}(\d{1,2}(?:\.\d{1,2})?)\s*%|(\d{1,2}(?:\.\d{1,2})?)\s*%"
    r"\s*(?:p\.?\s*a\.?|per\s+annum|interest)",
    re.I,
)
_EMI_AMOUNT = re.compile(
    r"\b(?:emi|instal+ment)\s*(?:amount|amt\.?)?\s*(?:of|is|:)?\s*" + _NUM, re.I
)
EMI_QUERY = (
    '(EMI OR SmartEMI OR "Smart EMI" OR "Easy EMI" OR "Instant EMI") '
    '(converted OR booked OR processed OR "loan account") -category:promotions'
)


def parse_emi_booking(
    subject: str, body: str, received: date, sender: str = ""
) -> EmiBooking | None:
    """Read a "your purchase has been converted to EMI" email: the card, the amount, how many
    months and at what rate. Offers ("eligible for EMI", "convert now") are not bookings."""
    text = " ".join(f"{subject}. {body}".split())[:4000]
    if not re.search(r"\bemi\b", text, re.I) or not _EMI_BOOKED.search(text):
        return None
    # Offers read "eligible", "convert now" up top; a booking's footer advertising other EMI
    # deals ("click here", "EMI Fest Offers") doesn't make it an offer.
    if _EMI_OFFER.search(text[:300]):
        return None
    account = _ACCOUNT.search(text)
    amount = _amount(text)
    tenure = _TENURE_WORD.search(text) or _TENURE.search(text)
    if not account or not amount:
        return None
    months = int(tenure.group(1)) if tenure else None
    if months is not None and not 2 <= months <= 84:
        return None
    rate_bp = 0
    if not re.search(r"no[\s-]*cost", text, re.I) and (r := _RATE.search(text)):
        rate_bp = int(round(float(r.group(1) or r.group(2)) * 100))
    emi = _EMI_AMOUNT.search(text)
    emi_paise = _safe_paise(emi.group(1)) if emi else None
    if emi_paise == amount:
        emi_paise = None
    merchant = _merchant(text, "debit")
    return EmiBooking(
        account_mask=next(g for g in account.groups() if g)[-4:],
        institution=bank_of(sender) or bank_of(re.sub(r"\S+@\S+", " ", text)),
        principal_paise=amount,
        months=months,
        rate_bp=rate_bp if rate_bp < 6000 else 0,
        emi_paise=emi_paise,
        booked_on=_date(text, received) or received,
        merchant=merchant if merchant != "Unknown" else "",
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
    return parse_rows(_rows_from(data, name), name)


def parse_rows(rows: list[list], name: str, *, wrapped: bool = False) -> list[Parsed]:
    """A statement's table, however it was read. `wrapped` (PDFs) joins a description that runs
    onto the next lines back onto its transaction."""
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
    # The heading first; the file name only when the heading names no account.
    options = statement_masks(head) or statement_masks(name)
    mask = options[0] if options else None
    kind = "card" if re.search(r"\bcard\b", head, re.I) else "bank"
    institution = bank_of(" ".join(str(c) for r in rows[: start + 1] for c in r if c)) or bank_of(
        name
    )
    last: Parsed | None = None
    wraps = 0
    signed = "amount" in cols and any(
        str(r[cols["amount"]] or "").strip().startswith("-")
        for r in rows[start:]
        if len(r) > cols["amount"]
    )
    for row in rows[start:]:
        if len(row) <= max(cols.values()):
            row = list(row) + [None] * (max(cols.values()) + 1 - len(row))
        when = _row_date(row[cols["date"]])
        narration = " ".join(str(row[cols["narration"]] or "").split())
        section = _STATEMENT_OF.search(" ".join(str(c or "") for c in row)) if not when else None
        if section and out and section.group(1)[-4:] != mask:
            # ICICI follows the account with its PPF's own transactions: another account, and
            # its deposits are the transfers already read here. Stop at the first account (a
            # heading repeated on each page names the same one).
            break
        if when is None:
            # A description's second line: text under the description and nothing else.
            others = [
                c for i, c in enumerate(row) if i != cols["narration"] and str(c or "").strip()
            ]
            if (
                wrapped and last is not None and narration and not others
                and not _NOT_A_WRAP.match(narration) and wraps < MAX_WRAP_LINES
            ):  # fmt: skip
                last.narration = f"{last.narration} {narration}"[:600]
                last.merchant = statement_merchant(last.narration)
                wraps += 1
            else:
                last = None
            continue
        wraps = 0
        try:
            if "amount" in cols and not {"debit", "credit"} <= cols.keys():
                value = _cell_paise(row[cols["amount"]])
                drcr = str(row[cols["type"]] or "").strip().lower() if "type" in cols else ""
                raw = str(row[cols["amount"]] or "").strip().lower().rstrip(".")
                if drcr:
                    is_credit = drcr.startswith(("c", "deposit"))
                elif raw.endswith(("cr", "dr")):
                    is_credit = raw.endswith("cr")
                elif signed:
                    # A signed amount column: positive is money in.
                    is_credit = value > 0
                else:
                    # Unsigned (card statements): a payment out unless marked "Cr".
                    is_credit = False
                amount, direction = abs(value), "credit" if is_credit else "debit"
            else:
                debit = _cell_paise(row[cols["debit"]])
                credit = _cell_paise(row[cols["credit"]])
                amount, direction = (debit, "debit") if debit else (credit, "credit")
            balance = _cell_paise(row[cols["balance"]]) if "balance" in cols else None
        except ValueError:
            last = None
            continue
        if not amount:
            last = None
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
        last = Parsed(
            occurred_on=when,
            amount_paise=abs(amount),
            direction=direction,
            merchant=statement_merchant(narration),
            narration=narration[:600],
            channel=channel,
            account_mask=mask,
            account_kind=kind,
            institution=institution,
            mask_options=tuple(options),
            ref=str(row[cols["ref"]]).strip() or None
            if "ref" in cols and row[cols["ref"]]
            else None,
            balance_paise=balance,
        )
        out.append(last)
    out.sort(key=lambda p: p.occurred_on)
    return out


# --- PDF statements ---------------------------------------------------------------------------
# Banks email statements as PDFs, usually locked with a password made from the customer's own
# details. The password is used once, in memory, to open the file and is never stored or logged.

# A year of a busy account runs to about 100 pages; past this a file takes too long to read in
# one request (a reader works through about two to four pages a second).
MAX_PDF_PAGES = 250
_CAS_PDF = re.compile(
    r"consolidated account statement|\bcams\b|kfintech|kfin technologies|karvy", re.I
)
_AMOUNT_WORD = re.compile(r"^-?[\d,]+\.\d{1,2}(?:cr|dr)?$", re.I)
# Lines that end a table: page footers and summaries, never part of a description.
_NOT_A_WRAP = re.compile(
    r"^(?:page\s*\d|statement\s+summary|opening\s+balance|closing\s+balance|"
    r"total|generated|this\s+is\s+a\s+(?:computer|system)|\*)",
    re.I,
)
# A description rarely runs past this many extra lines; more is a footer.
MAX_WRAP_LINES = 3


class PdfLocked(StatementError):
    """The PDF needs a password, or none of the ones tried opened it."""


def _password_error(exc: BaseException) -> bool:
    seen: BaseException | None = exc
    for _ in range(6):
        if seen is None:
            return False
        text = f"{type(seen).__name__} {seen}".lower()
        if "password" in text or "encrypt" in text:
            return True
        nested = [a for a in getattr(seen, "args", ()) if isinstance(a, BaseException)]
        seen = seen.__cause__ or seen.__context__ or (nested[0] if nested else None)
    return False


def open_pdf(data: bytes, passwords: Sequence[str] = ()) -> tuple[str, str]:
    """Open a PDF with no password, then each of `passwords`. Returns what it is ("cas" for a
    CAMS or KFintech statement, else "statement") and the password that opened it."""
    import pdfplumber

    tried = list(dict.fromkeys(["", *(p for p in passwords if p)]))
    for pw in tried:
        try:
            with pdfplumber.open(io.BytesIO(data), password=pw) as pdf:
                first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        except Exception as exc:  # pdf libraries raise many kinds of error
            if _password_error(exc):
                continue
            raise StatementError("That PDF couldn't be opened.") from exc
        return ("cas" if _CAS_PDF.search(first[:4000]) else "statement"), pw
    if len(tried) > 1:
        raise PdfLocked("That password didn't open the PDF. Check it and try again.")
    raise PdfLocked("This PDF is locked. Type its password; it's used once to open the file.")


def _pdf_lines(page) -> list[list[dict]]:
    """A page's words grouped into lines, left to right."""
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _cells(words: list[dict], gap: float = 4) -> list[dict]:
    """Words merged into header cells: those that overlap left to right or sit within `gap`
    points (about a space) of each other, so a header split over two lines ("Withdrawal /
    Amt.") is one cell and two headers a little apart are two."""
    cells: list[dict] = []
    for w in sorted(words, key=lambda w: w["x0"]):
        if cells and w["x0"] <= cells[-1]["x1"] + gap:
            c = cells[-1]
            c["x1"] = max(c["x1"], w["x1"])
            c["words"].append(w)
        else:
            cells.append({"x0": w["x0"], "x1": w["x1"], "words": [w]})
    for c in cells:
        c["text"] = " ".join(
            w["text"] for w in sorted(c["words"], key=lambda w: (round(w["top"]), w["x0"]))
        )
    return cells


def _column_of(header: list[dict], w: dict) -> int:
    """Which column a word belongs to: amounts line up with their header's right edge, text
    with its left edge."""
    if _AMOUNT_WORD.match(w["text"]):
        return min(range(len(header)), key=lambda i: abs(header[i]["x1"] - w["x1"]))
    best = 0
    for i, h in enumerate(header):
        if h["x0"] <= w["x0"] + 3:
            best = i
    return best


def _layout_rows(pdf) -> list[list[str]]:
    """Rows from a PDF whose table has no ruling lines: find the header line (or two), then put
    each word under the column it lines up with."""
    out: list[list[str]] = []
    header: list[dict] | None = None
    header_text: list[str] = []
    # Rows under the header with their page and height on it, to place wrapped descriptions.
    placed: list[tuple[int, float, list[str]]] = []
    for page_no, page in enumerate(pdf.pages[:MAX_PDF_PAGES]):
        lines = _pdf_lines(page)
        i = 0
        while i < len(lines):
            line = lines[i]
            if header is None:
                for span in (1, 2):
                    cells = _cells([w for ln in lines[i : i + span] for w in ln])
                    if _columns([c["text"] for c in cells]):
                        # Narrow columns wrap their header ("Value / Dt"): take the next line
                        # too when it's close below and holds no date or amount.
                        nxt = lines[i + span] if span == 1 and i + 1 < len(lines) else None
                        if (
                            nxt
                            and nxt[0]["top"] - line[0]["top"] <= 12
                            and not any(
                                _row_date(w["text"]) or _AMOUNT_WORD.match(w["text"]) for w in nxt
                            )
                        ):
                            cells, span = _cells(line + nxt), 2
                        header, header_text = cells, [c["text"] for c in cells]
                        out.append(header_text)
                        i += span
                        break
                else:
                    out.append([" ".join(w["text"] for w in line)])
                    i += 1
                continue
            # The header repeats at the top of each page.
            if _norm_header(" ".join(w["text"] for w in line)).startswith(
                _norm_header(" ".join(header_text))[:20]
            ):
                i += 1
                continue
            row = [""] * len(header)
            for w in line:
                col = _column_of(header, w)
                row[col] = f"{row[col]} {w['text']}".strip()
            placed.append((page_no, line[0]["top"], row))
            i += 1
    cols = _columns(header_text) if header else None
    if cols and "narration" in cols and "date" in cols:
        return out + _centred(placed, cols["date"], cols["narration"])
    return out + [row for _, _, row in placed]


# An opening balance carried over: a dated line, but not a transaction.
_CARRIED = re.compile(r"^(?:b/f|brought\s+forward|opening\s+balance)\b", re.I)
# How far (points) a wrapped description line can sit from its transaction's line.
WRAP_REACH = 30


def _centred(
    placed: list[tuple[int, float, list[str]]], date_col: int, text_col: int
) -> list[list[str]]:
    """Some banks (ICICI) centre each row's cells vertically, so a description that wraps has
    as many lines above its transaction's date line as below it. When the first transaction
    has a description line above it, split the lines between each two transactions that way:
    the first as many as the upper one had above go below it, the rest above the lower one.
    (Going by distance alone fails: the lines are evenly spaced, so a line between two rows
    sits about as close to either.) Otherwise leave the rows as they are (parse_rows joins
    lines below)."""

    def dated(row: list[str]) -> bool:
        return _row_date(row[date_col]) is not None

    def carried(row: list[str]) -> bool:
        return any(_CARRIED.match(c) for c in row if c)

    def loose(row: list[str]) -> bool:
        return bool(row[text_col]) and not any(c for i, c in enumerate(row) if i != text_col and c)

    # The opening balance line ("B/F") has no description to wrap, so look at the first real
    # transaction after it.
    first = next((k for k, (_, _, r) in enumerate(placed) if dated(r) and not carried(r)), None)
    if not first or not loose(placed[first - 1][2]):
        return [row for _, _, row in placed]

    def near(k: int, j: int) -> bool:
        return placed[k][0] == placed[j][0] and abs(placed[k][1] - placed[j][1]) <= WRAP_REACH

    above: dict[int, list[int]] = {}
    below: dict[int, list[int]] = {}
    prev: int | None = None  # the transaction the lines so far may belong under
    gap: list[int] = []

    def settle(nxt: int | None) -> None:
        """Share the loose lines in `gap` between `prev` (below it) and `nxt` (above it)."""
        share = len(above.get(prev, [])) if prev is not None else 0
        for n, k in enumerate(gap):
            if n < share and prev is not None and near(k, prev):
                below.setdefault(prev, []).append(k)
            elif nxt is not None and near(k, nxt):
                above.setdefault(nxt, []).append(k)
            elif prev is not None and near(k, prev):
                below.setdefault(prev, []).append(k)
        gap.clear()

    for k, (_, _, row) in enumerate(placed):
        if loose(row):
            gap.append(k)
        elif dated(row) and not carried(row):
            settle(k)
            prev = k
        else:
            settle(None)
            prev = None
    settle(None)
    taken = {k for ks in (*above.values(), *below.values()) for k in ks}
    out = []
    for k, (_, _, row) in enumerate(placed):
        if k in taken:
            continue
        row = list(row)
        if k in above or k in below:
            text = [placed[i][2][text_col] for i in above.get(k, [])]
            text += [row[text_col]] + [placed[i][2][text_col] for i in below.get(k, [])]
            row[text_col] = " ".join(t for t in text if t)
        out.append(row)
    return out


def _table_rows(pdf, first_text: str) -> list[list[str]]:
    """Rows from ruled tables, after the first page's text above the table (it names the bank
    and account). The page's own transactions are left out: their references and card numbers
    aren't the account's."""
    rows: list[list[str]] = []
    for line in first_text.splitlines()[:40]:
        low = line.lower()
        if "date" in low and any(w in low for w in ("balance", "withdraw", "debit", "amount")):
            break
        rows.append([line])
    for page in pdf.pages[:MAX_PDF_PAGES]:
        for table in page.extract_tables():
            rows.extend([" ".join(str(c or "").split()) for c in r] for r in table)
    return rows


def read_pdf_statement(data: bytes, password: str, name: str) -> list[Parsed]:
    """Transactions from a bank or card statement PDF, opened with `password` ("" for none)."""
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data), password=password) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise StatementError(
                    f"That PDF has {len(pdf.pages)} pages; up to {MAX_PDF_PAGES} can be read. "
                    "Download it in two halves (say April to September, then October to March)."
                )
            first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
            attempts = (lambda: _table_rows(pdf, first), lambda: _layout_rows(pdf))
            for rows_of in attempts:
                try:
                    found = parse_rows(rows_of(), name, wrapped=True)
                except StatementError:
                    continue
                if found:
                    return found
    except StatementError:
        raise
    except Exception as exc:  # pdf libraries raise many kinds of error
        if _password_error(exc):
            raise PdfLocked("That password didn't open the PDF.") from exc
        raise StatementError("That PDF couldn't be read.") from exc
    raise StatementError(
        "No transactions were found in that PDF. If it's a scanned copy, download the statement "
        "from net banking instead (PDF, CSV or Excel)."
    )


# A masked number (XXXXXXXX6320, ****6320) is the clearest sign of an account number.
_MASKED = re.compile(r"(?<![A-Za-z0-9*])[xX*]{2,}[ -]?(\d{4})\b")
# A number after an account or card label: one run of digits (with masking), or a card's
# groups of four ("4375 XXXX XXXX 1234"). Never runs on into a date or the next field.
_LABELLED = re.compile(
    r"\b(?:a/c|acct|account|card)\b\.?\s*(?:no\.?|number|#)?\s*[:.-]?\s*"
    r"([xX*\d]{4,20}(?:[ -][xX*\d]{4}){0,3})(?![\d/-]\d)",
    re.I,
)


# The account a statement's transactions are for, named above the table ("Statement of
# Transactions in Savings Account Number: 000101234567"); a summary above it lists others too.
_STATEMENT_OF = re.compile(
    r"statement\s+of\s+(?:transactions|account)\b.{0,40}?\b(?:account|a/c)\s*"
    r"(?:no\.?|number)?\s*:?\s*[xX*]*(\d{4,20})\b",
    re.I,
)


def statement_masks(text: str) -> list[str]:
    """The last four digits of every account number a statement's heading or file name
    quotes, best first: labelled ones ('Account No : 50100123456789', 'Card No. 4375 XXXX
    XXXX 1234'), then masked ones anywhere ('XX1234')."""
    out: list[str] = [m.group(1)[-4:] for m in _STATEMENT_OF.finditer(text)]
    for m in _LABELLED.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        # A label followed by a date or a short code isn't an account number.
        if len(digits) >= 4 and (len(m.group(1)) >= 8 or re.search(r"[xX*]", m.group(1))):
            out.append(digits[-4:])
    out += [m.group(1) for m in _MASKED.finditer(text)]
    return list(dict.fromkeys(out))


def statement_mask(text: str) -> str | None:
    """The best guess at the account a statement is for (see statement_masks)."""
    found = statement_masks(text)
    return found[0] if found else None


# ICICI cuts the payee in a UPI line to ten characters ("UPI/ANITA MENO/...") and puts the
# whole name last ("/ANITA MENON"); "ALIAS ..." after it repeats the name.
_SHORT_NAME = 10
# A company's bulk credit names the payer last ("CMS/ REGULAR SALARY NOV26/ACME TECHNOL").
_NAMED_LAST = re.compile(r"/([A-Za-z][A-Za-z.&' ]{1,60}?)\s*$")


def _whole_name(short: str, last: str) -> str:
    """The whole payee name in an ICICI line's last part, which can run straight on from a
    reference ("...a1b2c3ANITA MENON"); `short` when it isn't there."""
    at = last.lower().find(short.lower())
    if at < 0:
        return short
    whole = re.split(r"\s+alias\b", last[at:], maxsplit=1, flags=re.I)[0].strip()
    return whole if len(whole) <= 60 and not re.search(r"\d", whole) else short


def statement_merchant(narration: str) -> str:
    """The payee inside a statement narration: 'UPI-SWIGGY-swiggy@icici-ICIC0001-4321...'
    -> 'Swiggy'; 'POS 1234XXXX AMAZON PAY' -> 'Amazon'; 'CMS/ SALARY NOV/ACME' -> 'Acme'."""
    text = re.sub(r"^(?:to|by)\s+transfer-", "", narration.strip(), flags=re.I)
    if re.match(r"^(?:nfs|atm)/?\s*cash\s*wdl|^atm\s+(?:wdl|cash)", text, re.I):
        return "ATM cash"
    if re.match(r"^cms\s*/", text, re.I) and (m := _NAMED_LAST.search(text)):
        # A company's bulk credit (salary): the payer comes last.
        return clean_merchant(m.group(1))
    m = re.match(r"^(?:upi|imps|neft|rtgs|ach|nach|vin)(?:\s*cr|\s*dr)?[-/:]", text, re.I)
    if m:
        # The first part that names someone: not a bank's IFSC, a reference or a direction.
        for part in re.split(r"[-/]", text[m.end() :]):
            part = part.strip()
            if len(part) >= 2 and not re.fullmatch(
                r"[A-Za-z]{4}0[A-Za-z0-9]{6}|[\d ]+|p2[am]|[dc]r|upi(?:\s*transaction)?",
                part,
                re.I,
            ):
                if len(part) == _SHORT_NAME:
                    part = _whole_name(part, text.rsplit("/", 1)[-1])
                return clean_merchant(part)
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
    account_id: str | None = None
    # How it was paid; "autopay" (an e-mandate or standing instruction) marks a repeating one.
    channel: str = "other"


def month_bounds(month: str) -> tuple[date, date]:
    """'2026-11' -> (2026-11-01, 2026-12-01)."""
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return start, end


# Categories that aren't spending: money in, money moved, money saved.
NOT_SPENDING = frozenset({"Income", "Transfers", "Investments"})


def counted(t: Txn) -> bool:
    """Transfers between your own accounts and hidden rows don't count as spending or income."""
    return not t.excluded and t.category != "Transfers"


def spending(t: Txn) -> bool:
    """Money out that is spent: not a transfer, a hidden row, or money put into investments
    (that is counted on its own, as invested)."""
    return t.direction == "debit" and counted(t) and t.category != "Investments"


def refund(t: Txn) -> bool:
    """Money back in a spending category (a refund paired with its purchase, or a friend's share
    of a bill): it lowers that category's spending rather than counting as income."""
    return t.direction == "credit" and counted(t) and t.category not in NOT_SPENDING


def income(t: Txn) -> bool:
    """Money in that is earned. A deposit or fund coming back (Investments) is your own money
    returning, and a refund is spending undone."""
    return t.direction == "credit" and counted(t) and t.category == "Income"


def summarise(txns: list[Txn]) -> dict:
    back = [t for t in txns if refund(t)]
    spent = sum(t.amount_paise for t in txns if spending(t)) - sum(t.amount_paise for t in back)
    essential = sum(
        t.amount_paise for t in txns if spending(t) and t.category in ESSENTIAL_CATEGORIES
    ) - sum(t.amount_paise for t in back if t.category in ESSENTIAL_CATEGORIES)
    income_paise = sum(t.amount_paise for t in txns if income(t))
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
    for t in back:
        by_cat[t.category] -= t.amount_paise
        if t.merchant in by_merchant:
            by_merchant[t.merchant][0] -= t.amount_paise
    by_cat = {c: v for c, v in by_cat.items() if v > 0}
    return {
        "spent_paise": spent,
        "essential_paise": essential,
        "income_paise": income_paise,
        "invested_paise": invested,
        "net_paise": income_paise - spent - invested,
        "categories": sorted(
            ({"category": c, "spent_paise": v} for c, v in by_cat.items()),
            key=lambda x: -x["spent_paise"],
        ),
        "merchants": sorted(
            (
                {"merchant": m, "spent_paise": v[0], "count": v[1]}
                for m, v in by_merchant.items()
                if v[0] > 0
            ),
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
    # The latest payment and the one before, to spot a price change.
    last_paise: int = 0
    previous_paise: int = 0

    @property
    def price_change_paise(self) -> int:
        """How much the latest payment differs from the one before (0 within 2%)."""
        if not self.previous_paise:
            return 0
        diff = self.last_paise - self.previous_paise
        return diff if abs(diff) > self.previous_paise * 0.02 else 0


# Where to manage or cancel well-known subscriptions (merchant_key -> account page).
MANAGE_URLS = {
    "netflix": "https://www.netflix.com/account",
    "spotify": "https://www.spotify.com/account/overview/",
    "youtube": "https://www.youtube.com/paid_memberships",
    "youtube premium": "https://www.youtube.com/paid_memberships",
    "google play": "https://play.google.com/store/account/subscriptions",
    "google": "https://play.google.com/store/account/subscriptions",
    "apple": "https://apps.apple.com/account/subscriptions",
    "apple com bill": "https://apps.apple.com/account/subscriptions",
    "icloud": "https://apps.apple.com/account/subscriptions",
    "amazon prime": "https://www.amazon.in/gp/primecentral",
    "prime video": "https://www.amazon.in/gp/primecentral",
    "openai": "https://chatgpt.com/",
    "chatgpt": "https://chatgpt.com/",
    "anthropic": "https://claude.ai/settings/billing",
    "github": "https://github.com/settings/billing",
    "linkedin": "https://www.linkedin.com/premium/manage/",
    "audible": "https://www.audible.in/account/overview",
}
# Repeating payments that are bills rather than subscriptions to review.
BILL_CATEGORIES = ("Rent and housing", "Bills and utilities", "Investments")
CADENCE_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}


def manage_url(merchant: str) -> str | None:
    key = merchant_key(merchant)
    return MANAGE_URLS.get(key) or next(
        (url for k, url in MANAGE_URLS.items() if key.startswith(k + " ")), None
    )


def per_month(amount_paise: int, cadence: str) -> int:
    return amount_paise // CADENCE_MONTHS.get(cadence, 1)


def roll_forward(day: date, cadence: str, today: date) -> date:
    """The first renewal on or after today, for a date that has passed."""
    step = CADENCE_MONTHS.get(cadence, 1)
    n, nxt = 0, day
    while nxt < today and n < 600:
        n += 1
        nxt = add_months(day, step * n)
    return nxt


def add_months(day: date, months: int) -> date:
    """The same day of the month `months` later, or the month's last day if it's shorter."""
    n = day.year * 12 + day.month - 1 + months
    y, m = divmod(n, 12)
    m += 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(y, m, min(d, day.day))
        except ValueError:
            continue
    return date(y, m, 28)


def find_subscriptions(txns: list[Txn], today: date) -> list[Subscription]:
    """Payments that repeat (subscriptions, rent, bills, SIPs): the same merchant at least
    three times about a month apart (or twice about a year apart), for about the same amount
    (within 15%; the latest may differ, which is a price change). Lapsed ones (no payment for
    two cycles) and card bill payments are left out. A payment the bank says was taken by
    autopay (an e-mandate) is a subscription from its first charge, for the person to confirm."""
    by: dict[str, list[Txn]] = defaultdict(list)
    for t in txns:
        if (
            t.direction == "debit"
            and not t.excluded
            and t.merchant != "Unknown"
            and t.category not in ("Transfers", "Income", "Loans and EMIs")
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
            # The earlier payments must agree; the latest may be a new price (up to double).
            older = run[1:]
            usual = int(statistics.median(t.amount_paise for t in older))
            if any(abs(t.amount_paise - usual) > usual * 0.15 for t in older):
                continue
            latest = run[0].amount_paise
            if not usual / 2 <= latest <= usual * 2:
                continue
            changed = abs(latest - usual) > usual * 0.15
            typical = latest if changed else int(statistics.median(t.amount_paise for t in run))
            last = run[0].occurred_on
            nxt = add_months(last, 1 if cadence == "monthly" else 12)
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
                    last_paise=latest,
                    previous_paise=run[1].amount_paise,
                )
            )
            break
        else:
            auto = [t for t in items if t.channel == "autopay"]
            if auto and (today - auto[-1].occurred_on).days <= 45:
                last = auto[-1]
                found.append(
                    Subscription(
                        merchant=merchant,
                        category=last.category,
                        cadence="monthly",
                        typical_paise=last.amount_paise,
                        last_on=last.occurred_on,
                        next_on=add_months(last.occurred_on, 1),
                        count=len(auto),
                        monthly_paise=last.amount_paise,
                        last_paise=last.amount_paise,
                        previous_paise=auto[-2].amount_paise if len(auto) > 1 else 0,
                    )
                )
    return sorted(found, key=lambda s: -s.monthly_paise)


@dataclass
class EmiSeen:
    """EMI payments seen on an account: a loan worth tracking if it isn't yet."""

    merchant: str
    account_id: str | None
    monthly_paise: int
    months_seen: int
    first_on: date
    last_on: date


def find_emis(txns: list[Txn], today: date) -> list[EmiSeen]:
    """EMIs paid in the last two months, grouped by loan ("EMI Sony" on one card). The monthly
    figure is the latest month's total, so principal, interest and its GST add up."""
    by: dict[tuple[str, str | None], list[Txn]] = defaultdict(list)
    for t in txns:
        if t.direction == "debit" and not t.excluded and t.category == "Loans and EMIs":
            by[(t.merchant, t.account_id)].append(t)
    found = []
    for (merchant, account_id), items in by.items():
        items.sort(key=lambda t: t.occurred_on)
        last = items[-1].occurred_on
        if (today - last).days > 62:
            continue
        month = (last.year, last.month)
        latest = sum(t.amount_paise for t in items if (t.occurred_on.year, t.occurred_on.month)
                     == month)  # fmt: skip
        months = {(t.occurred_on.year, t.occurred_on.month) for t in items}
        found.append(EmiSeen(merchant, account_id, latest, len(months), items[0].occurred_on, last))
    return sorted(found, key=lambda e: -e.monthly_paise)


# --- Balance check -------------------------------------------------------------------------------


@dataclass
class Gap:
    """Money the balances say moved on a day that no transaction here explains: a missing
    row (positive: something came in; negative: something went out) or a duplicate."""

    on: date
    gap_paise: int


def reconcile(rows: list[tuple[date, int, int | None]]) -> tuple[int, list[Gap]]:
    """Check an account's transactions against the balances the bank printed beside them.

    `rows` are (day, signed amount, balance after or None), oldest day first. Within a day the
    bank's order isn't known, so the day's closing balance is the printed balance no other row
    follows from. Each day's opening balance plus its movements should give that closing
    balance; any difference is a gap. Returns (days checked, gaps)."""
    by_day: dict[date, list[tuple[int, int | None]]] = defaultdict(list)
    for on, signed, balance in rows:
        by_day[on].append((signed, balance))
    running: int | None = None
    checked = 0
    gaps: list[Gap] = []
    for on in sorted(by_day):
        day = by_day[on]
        printed = [(s_, b) for s_, b in day if b is not None]
        flow = sum(s_ for s_, _ in day)
        if not printed:
            if running is not None:
                running += flow
            continue
        opens = {b - s_ for s_, b in printed}
        closing = next(
            (b for _, b in reversed(printed) if b not in opens),
            printed[-1][1],
        )
        if running is None:
            # First balance seen: start from it, nothing to check yet.
            running = closing
            continue
        checked += 1
        gap = closing - (running + flow)
        if abs(gap) >= 100:
            gaps.append(Gap(on, gap))
        running = closing
    return checked, gaps
