"""Advice: what in the person's own data deserves a decision, why, and the one step to take.

Pure rules over what the Money views and the Organiser already hold, so they cost nothing to run,
need no model, and the page, the brief, the assistant and the tests agree. Safety comes first:
cards and bills, then subscriptions, spending and checks; investing isn't advised here at all,
and no rule recommends a product.

Each item has a stable key naming the rule, what it is about and usually the period, so marking
it done, snoozing or dismissing it (AdviceState) holds until something new happens.

The rules of thumb, and where they come from:

- Card use: credit bureaus see a card's balance on its statement date, not the due date, and
  judge each card on its own; under 30% of the limit is the usual guide (CARD_GOOD).
- Autopay: RBI's e-mandate framework (2026) lets a recurring card charge go through without an
  OTP only up to Rs 15,000 a charge (Rs 1 lakh for insurance, mutual funds and card bills). Banks
  may carry e-mandates to a reissued card, but merchants and saved-card tokens often need the new
  number, so a blocked or reissued card can stop every subscription on it.
- Foreign-currency charges: Indian cards usually add a 1-3.5% markup plus 18% GST on it.
- Fraud: an unauthorised charge reported within three working days costs the customer nothing
  under RBI's limited-liability rules.
"""

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from urllib.parse import quote

from nexus_common import money

LEVELS = ("act", "look", "good")
AREAS = ("cards", "subscriptions", "spending", "safety", "life")

CARD_GOOD, CARD_LIMIT = 0.30, 0.50
OTP_LIMIT_PAISE = 15_000_00
OTP_LIMIT_HIGHER_PAISE = 1_00_000_00
FOREX_MARKUP = 0.035 * 1.18  # 3.5% plus GST on it
EXPIRY_DAYS = 45
MANY_SUBS, MANY_SUBS_PAISE = 5, 2_000_00
YEARLY_DECIDE_DAYS, YEARLY_DECIDE_PAISE = 30, 1_000_00
SPIKE_RATIO, SPIKE_PAISE = 1.5, 2_000_00
NEW_BIG_PAISE, NEW_BIG_DAYS = 5_000_00, 7
DOUBLE_DAYS, DOUBLE_MIN_PAISE = 14, 100_00
FEES_DAYS = 90
MOVED_SHOW_DAYS = 14
INBOX_OLD = 3

# Services billed in a foreign currency on Indian cards (merchant_key prefixes).
FOREIGN_BILLED = (
    "openai", "chatgpt", "anthropic", "claude", "midjourney", "github", "notion", "figma",
    "cursor", "perplexity", "canva pty", "dropbox", "digitalocean", "aws", "amazon web services",
    "vercel", "cloudflare", "heroku", "zoom", "slack", "medium", "substack", "patreon", "x corp",
    "twitter", "elevenlabs", "runway", "grammarly", "1password", "bitwarden", "proton",
)  # fmt: skip

# Services that do the same job; two live ones in a group is worth a look.
SERVICE_GROUPS = {
    "video streaming": ("netflix", "amazon prime", "prime video", "hotstar", "jiohotstar",
                        "disney", "sonyliv", "zee5", "sun nxt", "aha", "mubi", "apple tv"),
    "music": ("spotify", "youtube music", "apple music", "jiosaavn", "gaana", "wynk",
              "amazon music"),
    "cloud storage": ("google one", "icloud", "dropbox", "onedrive", "microsoft 365"),
    "AI chat": ("openai", "chatgpt", "anthropic", "claude", "perplexity", "gemini"),
    "food delivery passes": ("swiggy one", "zomato gold"),
}  # fmt: skip


@dataclass
class Item:
    key: str
    area: str
    level: str
    title: str
    body: str
    # How Nexus knows, in a few words.
    why: str
    url: str
    action: str
    amount_paise: int = 0

    def out(self) -> dict:
        return asdict(self)


@dataclass
class Facts:
    today: date
    # accounts_view items, subscriptions_view items, health_view, all optional (members have
    # no Money).
    accounts: list[dict] = field(default_factory=list)
    subs: list[dict] = field(default_factory=list)
    txns: list[money.Txn] = field(default_factory=list)
    health: dict | None = None
    # {"inbox_old": n, "overdue": n} from the Organiser.
    life: dict | None = None


def _r(p: int) -> str:
    return money.rupees(p)[:-3] if p % 100 == 0 else money.rupees(p)


def _round_up(p: int, step: int = 100_00) -> int:
    return -(-p // step) * step


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _matches(key: str, prefixes: tuple[str, ...]) -> bool:
    return any(key == p or key.startswith(p + " ") for p in prefixes)


def _live(s: dict) -> bool:
    return s["status"] in ("new", "active")


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _week(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def next_statement(statement_on: date | None, today: date) -> date | None:
    """The next statement date after today, from the latest one."""
    if statement_on is None or statement_on > today:
        return statement_on
    for n in range(1, 25):
        nxt = money.add_months(statement_on, n)
        if nxt > today:
            return nxt
    return None


def month_end(first: date) -> date:
    return money.add_months(first.replace(day=1), 1) - timedelta(days=1)


# --- Cards --------------------------------------------------------------------------------------


def _cards(f: Facts) -> list[dict]:
    return [a for a in f.accounts if a["kind"] == "card" and not a.get("hidden")]


def card_use(f: Facts) -> list[Item]:
    out = []
    for c in _cards(f):
        limit, owed = c.get("credit_limit_paise"), c.get("owed_paise")
        if not limit or owed is None or owed <= limit * CARD_GOOD:
            continue
        share = owed / limit
        pay = _round_up(owed - int(limit * CARD_GOOD))
        nxt = next_statement(c.get("statement_on"), f.today)
        when = f"before the statement on {nxt:%d %b}" if nxt else "before the next statement"
        out.append(Item(
            key=f"card-use:{c['id']}:{nxt or _month(f.today)}",
            area="cards",
            level="act" if share > CARD_LIMIT else "look",
            title=f"{c['label']} is at {share:.0%} of its limit",
            body=f"Pay {_r(pay)} {when} so it shows under 30%. Credit bureaus see the balance "
            "on the statement date, not the due date, and judge each card on its own.",
            why="The limit you typed and what's owed from the latest alert",
            url="/money/accounts",
            action="See the card",
            amount_paise=pay,
        ))  # fmt: skip
    return out


def card_overdue(f: Facts) -> list[Item]:
    out = []
    for c in _cards(f):
        bill = c.get("bill") or {}
        if bill.get("status") != "overdue":
            continue
        left = bill["left_paise"]
        out.append(Item(
            key=f"card-overdue:{c['id']}:{c.get('due_on')}",
            area="cards",
            level="act",
            title=f"{c['label']} bill is overdue",
            body=f"{_r(left)} is still unpaid after {c['due_on']:%d %b}. Pay it in full now: "
            "a late fee and interest on the whole bill (often 3-3.75% a month) start once the "
            "due date passes, and a late payment stays on the credit report.",
            why="The statement's bill and the payments seen since",
            url="/money/accounts",
            action="See the bill",
            amount_paise=left,
        ))  # fmt: skip
    return out


def _subs_by_account(f: Facts) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for s in f.subs:
        if _live(s) and s.get("account_id") and s["group"] == "subscription":
            out[s["account_id"]].append(s)
    return out


def card_expiry(f: Facts) -> list[Item]:
    out = []
    by = _subs_by_account(f)
    for c in _cards(f):
        if not c.get("expires_on"):
            continue
        last = month_end(c["expires_on"])
        days = (last - f.today).days
        if days > EXPIRY_DAYS or days < -30:
            continue
        subs = by.get(c["id"], [])
        names = ", ".join(s["merchant"] for s in subs[:4]) + (" and more" if len(subs) > 4 else "")
        out.append(Item(
            key=f"card-expiry:{c['id']}:{c['expires_on']}",
            area="cards",
            level="act" if subs else "look",
            title=f"{c['label']} expires in {_plural(days, 'day')}"
            if days >= 0
            else f"{c['label']} has expired",
            body=(f"{_plural(len(subs), 'subscription')} ({names}) are charged to it. When the "
                  "new card arrives, check each one's next charge goes through, and update the "
                  "card where it doesn't." if subs else
                  "The bank sends a new card before then. Activate it and keep the old one until "
                  "the new one works."),
            why="The expiry month you typed",
            url="/money/subscriptions?by=card",
            action="See what's on it",
        ))  # fmt: skip
    return out


def card_many(f: Facts) -> list[Item]:
    out = []
    by = _subs_by_account(f)
    labels = {a["id"]: a for a in f.accounts}
    for account_id, subs in by.items():
        a = labels.get(account_id)
        if a is None or a["kind"] != "card":
            continue
        monthly = sum(s["monthly_paise"] for s in subs)
        if len(subs) < MANY_SUBS and monthly < MANY_SUBS_PAISE:
            continue
        out.append(Item(
            key=f"card-many:{account_id}",
            area="cards",
            level="look",
            title=f"{_plural(len(subs), 'subscription')} ({_r(monthly)} a month) are on "
            f"{a['label']}",
            body="Handy in one place, but if this card is blocked, lost or reissued they can all "
            "stop at once. Banks may carry autopays to a reissued card, yet merchants and saved "
            "cards often need the new number. The card-wise list has each one's manage link.",
            why="Where each subscription's latest charge landed",
            url="/money/subscriptions?by=card",
            action="See the list",
            amount_paise=monthly,
        ))  # fmt: skip
    return out


def subs_on_bank(f: Facts) -> list[Item]:
    if not _cards(f):
        return []
    banks = {a["id"]: a for a in f.accounts if a["kind"] == "bank"}
    subs = [s for account_id, xs in _subs_by_account(f).items() if account_id in banks for s in xs]
    subs = [s for s in subs if not s.get("moving")]
    if not subs:
        return []
    monthly = sum(s["monthly_paise"] for s in subs)
    names = ", ".join(s["merchant"] for s in subs[:4]) + (" and more" if len(subs) > 4 else "")
    return [Item(
        key=f"subs-on-bank:{len(subs)}",
        area="cards",
        level="look",
        title=f"{_plural(len(subs), 'subscription')} still come out of a bank account",
        body=f"{names}: {_r(monthly)} a month. On a credit card they would sit with the rest, get "
        "an interest-free period, and a card blocked for fraud wouldn't touch your salary "
        "account. Use \"Move to card\" on each to track the switch.",
        why="Where each subscription's latest charge landed",
        url="/money/subscriptions?by=card",
        action="Move them",
        amount_paise=monthly,
    )]  # fmt: skip


def forex(f: Facts) -> list[Item]:
    subs = [s for s in f.subs if _live(s) and _matches(s["key"], FOREIGN_BILLED)]
    if not subs:
        return []
    yearly = sum(s["monthly_paise"] for s in subs) * 12
    cost = int(yearly * FOREX_MARKUP)
    if cost < 100_00:
        return []
    keys = "+".join(sorted(s["key"] for s in subs))
    return [Item(
        key=f"forex:{keys}"[:160],
        area="cards",
        level="look",
        title=f"{_plural(len(subs), 'subscription')} likely charged in dollars",
        body=f"{', '.join(s['merchant'] for s in subs[:5])}. Most Indian cards add a 1-3.5% "
        f"markup plus GST on foreign-currency charges: up to about {_r(_round_up(cost))} a year "
        "here. A card without a forex markup would save that; your card's terms say what it "
        "charges.",
        why="Services known to bill in a foreign currency",
        url="/money/subscriptions?by=card",
        action="See them",
        amount_paise=cost,
    )]  # fmt: skip


def otp_limit(f: Facts) -> list[Item]:
    out = []
    for s in f.subs:
        if not _live(s) or s["amount_paise"] < OTP_LIMIT_PAISE or not s.get("account_kind"):
            continue
        if s["account_kind"] != "card":
            continue
        if s["category"] == "Investments" and s["amount_paise"] <= OTP_LIMIT_HIGHER_PAISE:
            continue
        out.append(Item(
            key=f"otp:{s['key']}:{s.get('next_on')}",
            area="cards",
            level="look",
            title=f"{s['merchant']} needs your OK each time",
            body=f"At {_r(s['amount_paise'])} a charge it is over RBI's Rs 15,000 limit for "
            "autopay without an OTP, so each renewal waits for you to approve it and fails if "
            "you miss the message.",
            why="RBI's e-mandate limit and this subscription's amount",
            url="/money/subscriptions",
            action="See it",
            amount_paise=s["amount_paise"],
        ))  # fmt: skip
    return out


def moves(f: Facts) -> list[Item]:
    out = []
    for s in f.subs:
        m = s.get("moving")
        if m and m.get("stuck_on"):
            out.append(Item(
                key=f"move-stuck:{s['key']}:{m['stuck_on']}",
                area="cards",
                level="act",
                title=f"{s['merchant']} still charged {m.get('from_label') or 'the old card'}",
                body=f"The charge on {m['stuck_on']:%d %b} didn't land on {m['to_label']}. "
                "Change the card in the service's billing settings.",
                why="Where the charge after your move landed",
                url="/money/subscriptions?by=card",
                action="Open its settings",
            ))  # fmt: skip
        moved = s.get("moved_on")
        if moved and 0 <= (f.today - moved).days <= MOVED_SHOW_DAYS:
            out.append(Item(
                key=f"moved:{s['key']}:{moved}",
                area="cards",
                level="good",
                title=f"{s['merchant']} moved to {s.get('account_label') or 'the new card'}",
                body=f"Its charge on {moved:%d %b} came from the new card.",
                why="The first charge after the move",
                url="/money/subscriptions?by=card",
                action="See it",
            ))  # fmt: skip
    return out


# --- Subscriptions ------------------------------------------------------------------------------


def price_rises(f: Facts) -> list[Item]:
    out = []
    for s in f.subs:
        rise = s.get("price_change_paise") or 0
        last = s.get("last_on")
        if not _live(s) or rise <= 0 or not last or (f.today - last).days > 45:
            continue
        yearly = money.per_month(rise, s["cadence"]) * 12
        out.append(Item(
            key=f"price:{s['key']}:{last}",
            area="subscriptions",
            level="look",
            title=f"{s['merchant']} went up by {_r(rise)}",
            body=f"From {_r(s['previous_paise'])} to {_r(s['amount_paise'])}: about {_r(yearly)} "
            "more a year. Check the plan is still the one you want, or cancel.",
            why="The last two charges",
            url="/money/subscriptions",
            action="Decide",
            amount_paise=yearly,
        ))  # fmt: skip
    return out


def duplicates(f: Facts) -> list[Item]:
    out = []
    for group, prefixes in SERVICE_GROUPS.items():
        subs = [s for s in f.subs if _live(s) and _matches(s["key"], prefixes)]
        if len(subs) < 2:
            continue
        monthly = sum(s["monthly_paise"] for s in subs)
        out.append(Item(
            key=f"dupes:{group}:{'+'.join(sorted(s['key'] for s in subs))}"[:160],
            area="subscriptions",
            level="look",
            title=f"{_plural(len(subs), group + ' subscription')}",
            body=f"{', '.join(s['merchant'] for s in subs)}: {_r(monthly)} a month together. If "
            "you mostly use one, cancelling the other saves "
            f"{_r(min(s['monthly_paise'] for s in subs) * 12)} or more a year.",
            why="Services that do the same job",
            url="/money/subscriptions",
            action="Review them",
            amount_paise=monthly,
        ))  # fmt: skip
    return out


def yearly_renewals(f: Facts) -> list[Item]:
    out = []
    for s in f.subs:
        nxt = s.get("next_on")
        if not _live(s) or s["cadence"] != "yearly" or not nxt:
            continue
        days = (nxt - f.today).days
        if not 0 <= days <= YEARLY_DECIDE_DAYS or s["amount_paise"] < YEARLY_DECIDE_PAISE:
            continue
        out.append(Item(
            key=f"renews:{s['key']}:{nxt}",
            area="subscriptions",
            level="look",
            title=f"{s['merchant']} renews for a year on {nxt:%d %b}",
            body=f"{_r(s['amount_paise'])} in one go. Decide now whether you still want it: "
            "cancelling after a yearly renewal rarely gets the money back.",
            why="Last year's charge",
            url="/money/subscriptions",
            action="Decide",
            amount_paise=s["amount_paise"],
        ))  # fmt: skip
    return out


def after_cancel(f: Facts) -> list[Item]:
    out = []
    for s in f.subs:
        c = s.get("charged_after_cancel")
        if not c:
            continue
        out.append(Item(
            key=f"after-cancel:{s['key']}:{c['on']}",
            area="subscriptions",
            level="act",
            title=f"{s['merchant']} charged after you cancelled",
            body=f"{_r(c['amount_paise'])} on {c['on']:%d %b}. Check the cancellation went "
            "through and ask the service or your bank for the money back.",
            why="A charge dated after the day you marked it cancelled",
            url="/money/subscriptions",
            action="See it",
            amount_paise=c["amount_paise"],
        ))  # fmt: skip
    return out


def to_review(f: Facts) -> list[Item]:
    new = [s for s in f.subs if s["status"] == "new"]
    if not new:
        return []
    return [Item(
        key=f"review-subs:{len(new)}",
        area="subscriptions",
        level="look",
        title=f"{_plural(len(new), 'repeating payment')} to confirm",
        body="Keep, cancel or mark each as not a subscription. Kept ones are watched for price "
        "rises and charges after cancelling.",
        why="Payments that repeat about monthly or yearly",
        url="/money/subscriptions",
        action="Review",
    )]  # fmt: skip


# --- Spending and checks ------------------------------------------------------------------------


def spikes(f: Facts) -> list[Item]:
    start = f.today.replace(day=1)
    if f.today.day < 5:
        return []
    months = [money.add_months(start, -n) for n in (3, 2, 1)]
    if not any(t.occurred_on < months[0] for t in f.txns):
        return []  # less than three full months of history

    def by_cat(lo: date, hi: date) -> dict[str, int]:
        s = money.summarise([t for t in f.txns if lo <= t.occurred_on < hi])
        return {c["category"]: c["spent_paise"] for c in s["categories"]}

    past = [by_cat(m, money.add_months(m, 1)) for m in months]
    now = by_cat(start, f.today + timedelta(days=1))
    out = []
    for cat, spent in now.items():
        typical = sum(p.get(cat, 0) for p in past) // 3
        if typical <= 0 or spent < typical * SPIKE_RATIO or spent - typical < SPIKE_PAISE:
            continue
        out.append(Item(
            key=f"spike:{cat}:{_month(f.today)}",
            area="spending",
            level="look",
            title=f"{cat} is {f'{spent / typical:.1f}'.removesuffix('.0')}x a usual month already",
            body=f"{_r(spent)} so far this month against about {_r(typical // 100 * 100)} in a "
            "usual month. "
            "Worth a look at what changed.",
            why="This month against the last three",
            url=f"/money/transactions?category={quote(cat)}",
            action="See the payments",
            amount_paise=spent - typical,
        ))  # fmt: skip
    return out


def new_big(f: Facts) -> list[Item]:
    out = []
    since = f.today - timedelta(days=NEW_BIG_DAYS)
    for t in f.txns:
        if t.occurred_on < since or not money.spending(t) or t.amount_paise < NEW_BIG_PAISE:
            continue
        if t.merchant == "Unknown" or t.category in ("Loans and EMIs", "Rent and housing"):
            continue
        key = money.merchant_key(t.merchant)
        if any(money.merchant_key(x.merchant) == key and x.occurred_on < t.occurred_on
               for x in f.txns):  # fmt: skip
            continue
        out.append(Item(
            key=f"new-big:{key}:{t.occurred_on}:{t.amount_paise}"[:160],
            area="spending",
            level="look",
            title=f"{_r(t.amount_paise)} to {t.merchant}, a first payment there",
            body=f"On {t.occurred_on:%d %b}. If you don't recognise it, block the card in your "
            "bank's app and report it: within three working days, RBI's rules mean you don't "
            "bear the loss.",
            why="A large payment to a merchant not seen before",
            url="/money/transactions",
            action="See it",
            amount_paise=t.amount_paise,
        ))  # fmt: skip
    return out


def doubles(f: Facts) -> list[Item]:
    since = f.today - timedelta(days=DOUBLE_DAYS)
    seen: dict[tuple, int] = defaultdict(int)
    for t in f.txns:
        if t.occurred_on < since or not money.spending(t) or t.amount_paise < DOUBLE_MIN_PAISE:
            continue
        seen[(money.merchant_key(t.merchant), t.merchant, t.occurred_on, t.amount_paise,
              t.account_id)] += 1  # fmt: skip
    out = []
    for (key, merchant, on, amount, _), n in seen.items():
        if n < 2 or merchant == "Unknown":
            continue
        out.append(Item(
            key=f"double:{key}:{on}:{amount}"[:160],
            area="spending",
            level="look",
            title=f"{merchant} charged {_r(amount)} {_plural(n, 'time')} on {on:%d %b}",
            body="Could be two real orders, or one charged twice. If it's a double charge, the "
            "merchant or your bank can reverse it.",
            why="The same amount from the same merchant on the same day",
            url="/money/transactions",
            action="See them",
            amount_paise=amount * (n - 1),
        ))  # fmt: skip
    return out


def fees(f: Facts) -> list[Item]:
    since = f.today - timedelta(days=FEES_DAYS)
    paid = [t for t in f.txns if t.occurred_on >= since and money.spending(t)
            and t.category == "Fees and charges"]  # fmt: skip
    total = sum(t.amount_paise for t in paid)
    if total < 100_00:
        return []
    return [Item(
        key=f"fees:{_month(f.today)}",
        area="spending",
        level="look",
        title=f"{_r(total)} in bank fees and charges in three months",
        body="Late fees, card interest, SMS and minimum-balance charges add up. Most can be "
        "avoided, and a first late fee is often waived if you ask the bank.",
        why="Payments sorted as fees and charges",
        url=f"/money/transactions?category={quote('Fees and charges')}",
        action="See them",
        amount_paise=total,
    )]  # fmt: skip


def balance_gaps(f: Facts) -> list[Item]:
    out = []
    for a in f.accounts:
        check = a.get("balance_check")
        if a["kind"] != "bank" or a.get("hidden") or not check or not check["gap_count"]:
            continue
        last = check["gaps"][-1]
        out.append(Item(
            key=f"gaps:{a['id']}:{last['on']}",
            area="safety",
            level="look",
            title=f"{a['label']}: money moved that no payment explains",
            body=f"{_plural(check['gap_count'], 'day')} in four months where the bank's balance "
            "doesn't match the payments Money has. Usually an alert that never came; reading "
            "the month's statement fills it in.",
            why="Balances the bank printed against the payments",
            url="/money/accounts",
            action="See the account",
        ))  # fmt: skip
    return out


def health(f: Facts) -> list[Item]:
    if not f.health:
        return []
    out = []
    month = _month(f.today)
    good = 0
    for c in f.health["checks"]:
        if c["key"] == "cards":
            continue  # the card rules say it more exactly
        if c["status"] == "act":
            out.append(Item(
                key=f"health:{c['key']}:{month}",
                area="safety",
                level="act",
                title=c["title"],
                body=c["body"],
                why="The Health tab's check",
                url="/money/health",
                action="See the check",
            ))  # fmt: skip
        elif c["status"] == "good" and good < 4:
            good += 1
            out.append(Item(
                key=f"good:{c['key']}:{month}",
                area="safety",
                level="good",
                title=c["title"],
                body=c["body"],
                why="The Health tab's check",
                url="/money/health",
                action="See the check",
            ))  # fmt: skip
    return out


# --- Life ----------------------------------------------------------------------------------------


def life(f: Facts) -> list[Item]:
    if not f.life:
        return []
    out = []
    week = _week(f.today)
    if f.life.get("inbox_old", 0) >= INBOX_OLD:
        n = f.life["inbox_old"]
        out.append(Item(
            key=f"inbox:{week}",
            area="life",
            level="look",
            title=f"{_plural(n, 'thing')} in the Inbox for over a week",
            body="Ten minutes to file, do or drop them keeps the Inbox something you trust.",
            why="Inbox items older than seven days",
            url="/organiser/inbox",
            action="Open the Inbox",
        ))  # fmt: skip
    if f.life.get("overdue", 0):
        n = f.life["overdue"]
        out.append(Item(
            key=f"overdue:{week}",
            area="life",
            level="look",
            title=f"{_plural(n, 'task')} past their due date",
            body="Do one now, give the rest a new date, or drop what no longer matters.",
            why="Tasks with a due date before today",
            url="/organiser/tasks",
            action="See them",
        ))  # fmt: skip
    return out


RULES = (
    card_overdue, card_use, card_expiry, moves, otp_limit, card_many, subs_on_bank, forex,
    after_cancel, price_rises, yearly_renewals, duplicates, to_review,
    health, balance_gaps, new_big, doubles, spikes, fees, life,
)  # fmt: skip


def build(f: Facts) -> list[Item]:
    """Every item the rules find, most urgent first: act, then look, then good; within each,
    by area and then by how much money it touches."""
    items: list[Item] = []
    seen: set[str] = set()
    for rule in RULES:
        for item in rule(f):
            if item.key not in seen:
                seen.add(item.key)
                items.append(item)
    items.sort(key=lambda i: (LEVELS.index(i.level), AREAS.index(i.area), -i.amount_paise))
    return items


def visible(
    items: list[Item], states: dict[str, tuple[str, date | None]], today: date
) -> tuple[list[Item], int]:
    """Items not marked done or dismissed, and not snoozed past today; and how many are hidden."""
    shown, hidden = [], 0
    for i in items:
        state = states.get(i.key)
        if state and (state[0] in ("done", "dismissed") or (state[1] and state[1] > today)):
            hidden += 1
            continue
        shown.append(i)
    return shown, hidden
