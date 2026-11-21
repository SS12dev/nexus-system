"""Order emails: a food order, a cab ride, an online purchase. Each says the shop, what it came to
and often how it was paid, which bank alerts don't: a UPI alert says "paid to PAYU" and a
cash-on-delivery order makes no alert at all.

An order becomes a transaction (source "receipt") unless a bank or card payment of the same
amount is already in Money, in which case it only gives that payment the shop's name. When the
bank's alert arrives later it joins the order's row instead of adding a second one
(money_store.ingest). An order nobody's payment explains is asked about: "how did you pay?".

Pure: no database or network here. Only the shop, total, date, order number and payment mode are
kept; never the items, address or name in the email.
"""

import re
from dataclasses import dataclass
from datetime import date

from nexus_common import money

# Sender address fragment -> (shop, category). The category is the usual one; the person's own
# rule for the shop still wins (money.categorise).
SHOPS: dict[str, tuple[str, str]] = {
    "swiggy": ("Swiggy", "Food and dining"),
    "zomato": ("Zomato", "Food and dining"),
    "dominos": ("Domino's Pizza", "Food and dining"),
    "pizzahut": ("Pizza Hut", "Food and dining"),
    "mcdonalds": ("McDonald's", "Food and dining"),
    "kfc": ("KFC", "Food and dining"),
    "starbucks": ("Starbucks", "Food and dining"),
    "eatsure": ("EatSure", "Food and dining"),
    "blinkit": ("Blinkit", "Groceries"),
    "zeptonow": ("Zepto", "Groceries"),
    "zepto": ("Zepto", "Groceries"),
    "bigbasket": ("BigBasket", "Groceries"),
    "jiomart": ("JioMart", "Groceries"),
    "dunzo": ("Dunzo", "Groceries"),
    "amazon": ("Amazon", "Shopping"),
    "flipkart": ("Flipkart", "Shopping"),
    "myntra": ("Myntra", "Shopping"),
    "ajio": ("AJIO", "Shopping"),
    "nykaa": ("Nykaa", "Shopping"),
    "meesho": ("Meesho", "Shopping"),
    "tatacliq": ("Tata CLiQ", "Shopping"),
    "croma": ("Croma", "Shopping"),
    "decathlon": ("Decathlon", "Shopping"),
    "ikea": ("IKEA", "Shopping"),
    "uber": ("Uber", "Transport"),
    "olacabs": ("Ola", "Transport"),
    "rapido": ("Rapido", "Transport"),
    "bookmyshow": ("BookMyShow", "Entertainment"),
    "irctc": ("IRCTC", "Travel"),
    "makemytrip": ("MakeMyTrip", "Travel"),
    "goibibo": ("Goibibo", "Travel"),
    "redbus": ("redBus", "Travel"),
    "ixigo": ("ixigo", "Travel"),
    "1mg": ("Tata 1mg", "Health"),
    "pharmeasy": ("PharmEasy", "Health"),
    "netmeds": ("Netmeds", "Health"),
    "apollo": ("Apollo", "Health"),
}

# Gmail search: anything from a known shop, or an email whose subject reads like an order or
# receipt and that states a total. parse_order makes the real decision.
ORDER_QUERY = (
    "({" + " ".join(f"from:{s}" for s in SHOPS) + "} OR "
    "(subject:(order OR receipt OR invoice OR booking OR ride OR trip) "
    '("grand total" OR "order total" OR "amount paid" OR "total paid" OR "total amount"))) '
    "-category:social"
)

# Emails about an order that aren't the purchase: it was cancelled, refunded, returned, or the
# email is a nudge to buy.
_NOT_A_PURCHASE = re.compile(
    r"\b(cancel(?:led|ed|lation)?|refund(?:ed)?|return(?:ed)?|failed|declined|unsuccessful|"
    r"abandon|still in your cart|left in your cart|wishlist|price drop|rate your|"
    r"how was your|review your)\b",
    re.I,
)
_ORDERISH = re.compile(
    r"\b(order|receipt|invoice|booking|booked|ride|trip|purchase|bill|payment)\b", re.I
)
_TOTALS = (
    "grand total",
    "order total",
    "total amount paid",
    "amount paid",
    "total paid",
    "total payable",
    "net payable",
    "amount payable",
    "bill total",
    "total amount",
    "total fare",
    "total",
)
_NUMBER = r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)"
_ORDER_NO = re.compile(
    r"\border\s*(?:no\.?|number|id|#)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-|/]{3,40})", re.I
)
_PAY_LABEL = re.compile(
    r"\b(?:payment\s*(?:mode|method|type)|mode\s+of\s+payment|paid\s+(?:via|using|by|with|"
    r"through))\s*[:\-]?\s*([^\n.]{2,30})",
    re.I,
)
_CASH = re.compile(r"\b(cash|cod|pay\s+on\s+delivery|cash\s+on\s+delivery)\b", re.I)
_UPI = re.compile(r"\b(upi|gpay|google\s+pay|phonepe|bhim|paytm\s+upi)\b", re.I)
_CARD = re.compile(r"\b(credit\s+card|debit\s+card|card|visa|mastercard|rupay|amex)\b", re.I)
_WALLET = re.compile(r"\b(wallet|amazon\s+pay\s+balance|paytm\s+balance|mobikwik|money)\b", re.I)
_NETBANKING = re.compile(r"\b(net\s*banking|netbanking)\b", re.I)
_CARD_DIGITS = re.compile(r"(?:ending|ending\s+in|xx+|\*{2,}|x{2,})\s*(\d{4})\b", re.I)


@dataclass
class Order:
    occurred_on: date
    amount_paise: int
    shop: str
    category: str | None
    # cash, upi, card, wallet, netbanking or unknown.
    paid_by: str
    card_mask: str | None = None
    order_no: str | None = None


def shop_of(sender: str) -> tuple[str, str | None] | None:
    """The shop an order email is from: a known one from its address, otherwise the sender's
    display name ("Dominos India <do-not-reply@...>" -> "Dominos India")."""
    low = sender.lower()
    address = low.rsplit("<", 1)[-1]
    for key, (name, category) in SHOPS.items():
        if key in address:
            return name, category
    name = sender.split("<", 1)[0].strip().strip('"').strip()
    if not name or "@" in name:
        return None
    name = re.sub(r"\b(team|support|no.?reply|noreply|orders?|india|pvt|ltd|limited)\b", "", name,
                  flags=re.I)  # fmt: skip
    name = " ".join(name.split()).strip(" -|,")
    return (name[:60], None) if len(name) >= 2 else None


def _total(text: str) -> int | None:
    low = text.lower()
    for label in _TOTALS:
        for m in re.finditer(rf"\b{re.escape(label)}\b\s*[:\-]?\s*{_NUMBER}", low):
            if label == "total" and low[max(0, m.start() - 5) : m.start()].rstrip(" -").endswith(
                "sub"
            ):
                continue
            try:
                value = money.paise(m.group(1))
            except ValueError:
                continue
            if 0 < value <= 10_00_000_00:
                return value
    return None


def paid_by(text: str) -> tuple[str, str | None]:
    """How the order was paid, from the payment line when there is one, else anywhere."""
    m = _PAY_LABEL.search(text)
    where = m.group(1) if m else text
    if _CASH.search(where):
        return "cash", None
    digits = _CARD_DIGITS.search(text[m.start() : m.end() + 40] if m else text)
    if _NETBANKING.search(where):
        return "netbanking", None
    if _UPI.search(where):
        return "upi", None
    if _CARD.search(where) or (m and digits):
        return "card", digits.group(1) if digits else None
    if m and _WALLET.search(where):
        return "wallet", None
    return "unknown", None


def parse_order(subject: str, body: str, received: date, sender: str = "") -> Order | None:
    """Read one order or receipt email. None for anything else: a bank alert, a cancellation or
    refund, a promotion, or an email with no total."""
    if money.bank_of(sender.rsplit("<", 1)[-1]) or any(
        s in sender.lower() for s in money.ALERT_SENDERS
    ):
        return None
    if _NOT_A_PURCHASE.search(subject):
        return None
    shop = shop_of(sender)
    if shop is None:
        return None
    text = " ".join(f"{subject}\n{body}".replace("\t", " ").split())[:20000]
    if not _ORDERISH.search(subject) and not _ORDER_NO.search(text):
        return None
    amount = _total(text)
    if amount is None:
        return None
    how, mask = paid_by(text)
    m = _ORDER_NO.search(text)
    return Order(
        occurred_on=received,
        amount_paise=amount,
        shop=shop[0],
        category=shop[1],
        paid_by=how,
        card_mask=mask,
        order_no=m.group(1)[:40] if m else None,
    )


CHANNEL = {"cash": "cash", "upi": "upi", "card": "card", "netbanking": "neft"}
