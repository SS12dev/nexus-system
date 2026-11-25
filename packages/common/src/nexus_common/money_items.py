"""What was bought: the items in a quick-commerce order (Zepto, Blinkit, Instamart...), sorted
into kinds finer than a category, so Money can say where the money goes inside "Groceries":
staples and fresh food, or snacks, cold drinks and delivery fees.

Items come from the order email when it lists them (read without asking), or from the person's
one-tap answer to "what did you buy?" (money_suggest). Kinds come from a word list; nothing here
goes to an AI model.

Pure: no database or network here.
"""

import re
from dataclasses import dataclass

from nexus_common import money

# Kind -> (label, need). "Need" kinds are the basics a household buys anyway; the rest are
# the treats, convenience and fees where small cuts add up. Order is how they're shown.
KINDS: dict[str, tuple[str, bool]] = {
    "staples": ("Staples and cooking", True),
    "fresh": ("Fruit, veg and meat", True),
    "dairy": ("Milk, bread and eggs", True),
    "household": ("Household and cleaning", True),
    "personal": ("Personal care", True),
    "health": ("Medicines and health", True),
    "pets": ("Pet supplies", True),
    "snacks": ("Snacks and sweets", False),
    "drinks": ("Cold drinks and juices", False),
    "instant": ("Instant and frozen food", False),
    "meals": ("Ready meals", False),
    "sweets": ("Desserts and bakes", False),
    "gadgets": ("Gadgets, gifts and other", False),
    "fees": ("Delivery, handling and tips", False),
    "mixed": ("A bit of everything", True),
    "other": ("Other items", True),
}
TREATS = frozenset(k for k, (_, need) in KINDS.items() if not need and k != "gadgets")

# Shops where one order is a basket of different things: these get "what did you buy?".
# Matched against the merchant's key (money.merchant_key).
BASKET_SHOPS = (
    "zepto", "blinkit", "grofers", "instamart", "bigbasket", "bb now", "bbnow", "jiomart",
    "dunzo", "flipkart minutes", "amazon fresh", "amazon now", "swiggy instamart",
    "big basket", "kiranapro", "dmart ready",
)  # fmt: skip


def is_basket(merchant: str) -> bool:
    key = money.merchant_key(merchant)
    return any(s in key for s in BASKET_SHOPS)


# Lowercase words -> kind. First match wins, so specific phrases come before general words
# ("milk chocolate" is a snack, "cold coffee" a drink, "hair oil" personal care, "coffee
# powder" a staple).
_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("fees", ("delivery fee", "delivery charge", "handling fee", "handling charge",
              "small cart", "small order", "platform fee", "convenience fee", "packaging",
              "packing charge", "late night", "surge", "rain fee", "tip", "delivery partner",
              "service fee", "cart fee")),
    ("drinks", ("cold coffee", "iced tea", "energy drink", "soft drink", "cold drink",
                "coca cola", "coca-cola", "coke", "pepsi", "sprite", "thums up", "thumsup",
                "fanta", "7up", "7 up", "mountain dew", "limca", "maaza", "frooti", "slice",
                "red bull", "redbull", "monster", "sting", "tropicana", "real juice", "juice",
                "soda", "tonic", "lassi", "buttermilk", "chaas", "milkshake", "shake",
                "kombucha", "coconut water", "beer", "wine")),
    ("snacks", ("milk chocolate", "chocolate", "choco", "dairy milk", "kitkat", "kit kat",
                "snickers", "ferrero", "munch", "5 star", "perk", "chips", "crisps", "lays",
                "lay's", "kurkure", "bingo", "pringles", "doritos", "nachos", "uncle chips",
                "namkeen", "bhujia", "mixture", "biscuit", "cookie", "cookies", "oreo",
                "bourbon", "good day", "hide & seek", "hide and seek", "dark fantasy", "cake",
                "pastry", "brownie", "muffin", "donut", "doughnut", "ice cream", "icecream",
                "kulfi", "cornetto", "magnum", "sweets", "mithai", "ladoo", "laddu",
                "gulab jamun", "rasgulla", "candy", "toffee", "gum", "popcorn", "makhana",
                "peanut butter", "nutella", "chikki", "wafer", "puff", "khakhra", "mathri",
                "cheese balls", "marshmallow")),
    ("instant", ("maggi", "noodles", "yippee", "top ramen", "ramen", "cup noodle",
                 "instant", "ready to eat", "ready to cook", "frozen", "fries", "nuggets",
                 "momos", "momo", "frozen paratha", "pizza", "burger", "sandwich", "pasta",
                 "macaroni", "soup", "oats cup", "poha cup", "upma cup", "mccain", "sumeru",
                 "itc master chef", "kissan", "ketchup", "mayonnaise", "mayo", "sauce")),
    ("personal", ("shampoo", "conditioner", "soap", "body wash", "bodywash", "face wash",
                  "facewash", "toothpaste", "toothbrush", "colgate", "sensodyne", "pepsodent",
                  "mouthwash", "deodorant", "deo", "perfume", "razor", "shaving", "gillette",
                  "trimmer", "lotion", "moisturiser", "moisturizer", "sunscreen",
                  "face cream", "cold cream", "hair cream", "shaving cream",
                  "hair oil", "comb", "sanitary", "pads", "whisper", "stayfree", "tampon",
                  "diaper", "wipes", "cotton", "lip balm", "nivea", "dove", "lux", "dettol",
                  "savlon", "hand wash", "handwash", "sanitiser", "sanitizer", "condom")),
    ("household", ("detergent", "surf excel", "ariel", "tide", "rin", "wheel", "comfort",
                   "fabric", "dishwash", "dish wash", "vim", "pril", "exo", "harpic",
                   "lizol", "colin", "domex", "phenyl", "cleaner", "floor", "toilet",
                   "tissue", "napkin", "kitchen towel", "garbage bag", "trash bag",
                   "dustbin", "foil", "cling", "scrub", "sponge", "mop", "broom", "bulb",
                   "battery", "batteries", "candle", "agarbatti", "incense", "matchbox",
                   "mosquito", "good knight", "all out", "odonil", "air freshener",
                   "freshener", "hanger", "bucket", "zip lock", "ziplock")),
    ("health", ("tablet", "tablets", "capsule", "syrup", "paracetamol", "dolo", "crocin",
                "calpol", "combiflam", "vicks", "strepsils", "ors", "electral", "band-aid",
                "bandaid", "band aid", "plaster", "antiseptic", "vitamin", "multivitamin",
                "protein", "whey", "supplement", "thermometer", "mask", "medicine",
                "digene", "eno", "pudin hara", "glucon")),
    ("pets", ("dog food", "cat food", "pedigree", "whiskas", "drools", "pet ", "cat litter",
              "litter")),
    ("dairy", ("milk", "curd", "dahi", "yoghurt", "yogurt", "paneer", "butter", "ghee",
               "cheese", "cream", "egg", "eggs", "bread", "pav", "bun", "tofu")),
    ("meals", ("thali", "biryani", "meal", "combo", "wrap", "roll", "salad bowl")),
    ("gadgets", ("charger", "cable", "usb", "earphone", "earbuds", "headphone", "power bank",
                 "adapter", "phone case", "cover", "screen guard", "mouse", "keyboard",
                 "toy", "game", "gift", "flowers", "bouquet", "card", "pen", "notebook",
                 "stationery", "tape", "glue", "playing cards")),
    ("fresh", ("banana", "apple", "mango", "orange", "grapes", "papaya", "pomegranate",
               "watermelon", "melon", "guava", "kiwi", "pear", "berries", "strawberry",
               "lemon", "lime", "onion", "potato", "tomato", "garlic", "ginger", "chilli",
               "chili", "coriander", "dhania", "mint", "pudina", "spinach", "palak", "cabbage",
               "cauliflower", "carrot", "cucumber", "capsicum", "beans", "peas", "okra",
               "bhindi", "brinjal", "baingan", "gourd", "lauki", "pumpkin", "mushroom",
               "broccoli", "lettuce", "beetroot", "radish", "corn", "sweet potato",
               "vegetable", "veggies", "fruit", "sprouts", "chicken", "mutton", "fish",
               "prawn", "meat")),
    ("staples", ("atta", "flour", "maida", "besan", "rice", "basmati", "dal", "daal",
                 "toor", "moong", "masoor", "chana", "rajma", "urad", "pulses", "oil",
                 "sugar", "jaggery", "gur", "salt", "masala", "spice", "turmeric", "haldi",
                 "jeera", "cumin", "mustard", "pepper", "poha", "suji", "rava", "sooji",
                 "oats", "cornflakes", "muesli", "cereal", "vermicelli", "tea", "chai",
                 "coffee", "honey", "jam", "pickle", "achar", "vinegar", "dry fruit",
                 "almond", "badam", "cashew", "kaju", "raisin", "walnut", "dates", "peanut",
                 "groundnut", "soya", "sauce", "papad")),
]  # fmt: skip


def kind_of(name: str) -> str:
    """The kind an item's name reads as; "other" when no word fits."""
    hay = f" {name.lower()} "
    for kind, words in _WORDS:
        for w in words:
            if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", hay):
                return kind
    return "other"


# A food-delivery order is meals, with desserts and drinks the parts worth seeing apart.
_FOOD_SWEETS = (
    "donut", "doughnut", "brownie", "cake", "pastry", "macaron", "cupcake", "ice cream",
    "icecream", "mcflurry", "sundae", "churros", "tres leches", "medovik", "croissant",
    "pain au chocolat", "muffin", "cookie", "waffle", "pancake", "gulab jamun", "rasgulla",
    "rasmalai", "kheer", "halwa", "jalebi", "falooda", "kulfi", "mousse", "tiramisu",
    "cheesecake", "dessert", "sweet", "laddoo", "ladoo", "barfi", "baklava", "kunafa",
)  # fmt: skip
_FOOD_DRINKS = (
    "coke", "pepsi", "thums up", "sprite", "fanta", "7up", "mountain dew", "soda", "fizz",
    "mojito", "lassi", "chaas", "buttermilk", "ayran", "laban", "juice", "shake",
    "milkshake", "cold coffee", "iced tea", "iced coffee", "iced latte", "smoothie",
    "lemonade", "nimbu", "mocktail", "cooler", "water bottle", "coca cola", "red bull",
)  # fmt: skip


def food_kind(name: str) -> str:
    """The kind of one thing in a food-delivery order: a dessert, a drink, a fee or the meal."""
    hay = f" {name.lower()} "
    if kind_of(name) == "fees":
        return "fees"
    for kind, words in (("drinks", _FOOD_DRINKS), ("sweets", _FOOD_SWEETS)):
        if any(re.search(rf"(?<![a-z]){re.escape(w)}", hay) for w in words):
            # A combo ("Shawarma & Pepsi Combo", "Pizza Puff+Coke") is still a meal.
            if kind == "drinks" and re.search(r"\bcombo\b|\+", hay):
                return "meals"
            return kind
    return "meals"


@dataclass
class Item:
    name: str
    price_paise: int | None
    quantity: int = 1
    kind: str = "other"


# --- Reading items from an order email -------------------------------------------------------

_PRICE = r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)"
_LINE_PRICE = re.compile(rf"{_PRICE}\s*$", re.I)
_BARE_PRICE = re.compile(r"^\s*(?:₹|rs\.?|inr)?\s*([\d,]+\.\d{2})\s*$", re.I)
_QTY_PREFIX = re.compile(r"^\s*(\d{1,2})\s*[x×*]\s+", re.I)
_QTY_SUFFIX = re.compile(r"\s*(?:[x×*]\s*(\d{1,2})|\(\s*(?:qty|quantity)?\s*:?\s*(\d{1,2})\s*\)|"
                         r"qty\s*:?\s*(\d{1,2}))\s*$", re.I)  # fmt: skip
_COUNTED = re.compile(r"^\s*(\d{1,2})\s*[xX×]\s+(\S.{1,150})$")
_QTY_ONLY = re.compile(r"^\s*(?:qty\s*:?\s*)?[x×]?\s*(\d{1,2})\s*$", re.I)
# Lines that are totals, discounts or headings, never an item.
_NOT_ITEM = re.compile(
    r"\b(sub\s*-?\s*total|item\s*total|items?\s*total|grand\s*total|order\s*total|bill\s*total|"
    r"total\s*(?:amount|paid|payable|savings?|bill)?|to\s*pay|amount\s*paid|paid\s*via|"
    r"discount|saving|saved|coupon|promo|offer|cashback|wallet|refund|gst|cgst|sgst|igst|"
    r"tax(?:es)?|round(?:ing)?\s*off|mrp|invoice|order\s*(?:id|no|number)|payment|balance|"
    r"you\s*pay|net\s*amount|price|qty|quantity|item\s*name|items?)\b",
    re.I,
)
_FREE = re.compile(r"\bfree\b", re.I)
MAX_ITEMS = 60


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" -:|•·*\t")
    return name[:120]


def _price(text: str) -> int | None:
    try:
        value = money.paise(text)
    except ValueError:
        return None
    return value if 0 <= value <= 10_00_000_00 else None


def _fee(name: str) -> bool:
    return kind_of(name) == "fees"


def parse_items(body: str) -> list[Item]:
    """The items an order email lists, with their prices and the fees charged on top. Reads two
    layouts: one line per item ("2 x Amul Taaza Milk 500 ml ₹54") and a table flattened to
    lines (name, then quantity and/or price on the lines after). [] when it lists none."""
    lines = [" ".join(line.replace("\t", " ").split()) for line in body.splitlines()]
    lines = [line for line in lines if line]
    items: list[Item] = []
    i = 0
    while i < len(lines) and len(items) < MAX_ITEMS:
        line = lines[i]
        i += 1
        # One line: name (with an optional quantity) and a price at the end.
        if m := _LINE_PRICE.search(line):
            name = line[: m.start()].strip()
            qty = 1
            if q := _QTY_PREFIX.match(name):
                qty, name = int(q.group(1)), name[q.end() :]
            if q := _QTY_SUFFIX.search(name):
                qty, name = int(next(g for g in q.groups() if g)), name[: q.start()]
            name = _clean_name(name)
            if _usable(name):
                price = _price(m.group(1))
                if price is not None and (price or _fee(name)):
                    items.append(Item(name, price, qty, kind_of(name)))
            continue
        # "2 X Paneer Paratha": a quantity and a name, no price (Zomato lists items so).
        if q := _COUNTED.match(line):
            name = _clean_name(q.group(2))
            if _usable(name):
                items.append(Item(name, None, int(q.group(1)), kind_of(name)))
            continue
        # A table row flattened: the name alone, then quantity and/or price lines.
        name = _clean_name(line)
        if not _usable(name) or _BARE_PRICE.match(line) or _QTY_ONLY.match(line):
            continue
        qty, price, used = 1, None, 0
        for nxt in lines[i : i + 3]:
            if price is None and (q := _QTY_ONLY.match(nxt)) and used < 2:
                qty, used = int(q.group(1)), used + 1
                continue
            if price is None and (p := _BARE_PRICE.match(nxt) or _LINE_PRICE.fullmatch(nxt)):
                price, used = _price(p.group(1)), used + 1
                continue
            if price is None and _FREE.fullmatch(nxt.strip()):
                price, used = 0, used + 1
            break
        if price is not None and (price or _fee(name)):
            items.append(Item(name, price, qty, kind_of(name)))
            i += used
    return items


def _usable(name: str) -> bool:
    """An item's name: some letters, not a total or a heading."""
    if len(name) < 2 or not re.search(r"[A-Za-z]{2}", name):
        return False
    if _fee(name):
        return True
    return not _NOT_ITEM.search(name)


# --- Reading what the person typed -------------------------------------------------------------

_TYPED_PRICE = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rs|₹|/-)?\s*$", re.I)


def parse_typed(text: str) -> list[Item]:
    """What the person typed: "milk 60, chips 40, coke" or one item a line. A number at the
    end of an item is its price; items without one share what's left of the order."""
    parts = re.split(r"[,\n;]+|\s+\band\b\s+|\s+&\s+|\s+\+\s+", text)
    out: list[Item] = []
    for part in parts:
        part = part.strip(" .-:")
        if not part:
            continue
        price = None
        if (m := _TYPED_PRICE.search(part)) and m.start() > 0:
            price = _price(m.group(1))
            part = part[: m.start()].strip(" .-:")
        qty = 1
        if q := _QTY_PREFIX.match(part):
            qty, part = int(q.group(1)), part[q.end() :]
        name = _clean_name(part)
        if not re.search(r"[A-Za-z]", name):
            continue
        out.append(Item(name, price, qty, kind_of(name)))
        if len(out) >= MAX_ITEMS:
            break
    return out


# --- Adding up ---------------------------------------------------------------------------------


def shares(items: list[tuple], paid_paise: int) -> dict[str, int]:
    """Split what an order really cost (after discounts) across its items' kinds, in proportion
    to their prices; items without a price share what the priced ones leave, by quantity. Fees
    are taken as charged. Each item is (kind, price) or (kind, price, quantity). Sums to
    `paid_paise`."""
    if not items:
        return {}
    rows = [(it[0], it[1], it[2] if len(it) > 2 else 1) for it in items]
    fees = min(sum(p or 0 for k, p, _ in rows if k == "fees"), paid_paise)
    rest = paid_paise - fees
    priced = [(k, p) for k, p, _ in rows if k != "fees" and p]
    unpriced = [(k, max(q, 1)) for k, p, q in rows if k != "fees" and not p]
    out: dict[str, int] = {}
    if fees:
        out["fees"] = fees
    listed = sum(p for _, p in priced)
    if unpriced and listed >= rest:
        # The priced items already account for everything: the unpriced ones get nothing.
        unpriced = []
    pool = rest if not unpriced else min(listed, rest)
    given = 0
    for k, p in priced:
        share = pool * p // listed if listed else 0
        out[k] = out.get(k, 0) + share
        given += share
    left = rest - given
    if unpriced:
        weight = sum(q for _, q in unpriced)
        spread = 0
        for k, q in unpriced:
            share = left * q // weight
            out[k] = out.get(k, 0) + share
            spread += share
        left -= spread
    if left:
        # Rounding pennies go to the biggest kind.
        big = max((k for k in out if k != "fees"), key=lambda k: out[k], default=None)
        if big is not None:
            out[big] += left
        elif out:
            out["fees"] += left
        else:
            out["other"] = left
    return out
