"""How many cigarettes, worked out from the packs bought: the pack size read from an item's name
("Gold Flake Kings 10s", "Classic Milds 20 Sticks"), and each pack taken as finished when the
next one is bought. Over a few packs that gives sticks a day, how long a pack lasts, when the
next is due, and what a stick costs; loose buys at a paan shop are turned into sticks at that
price. Suggestions come only from these numbers.

Pure: no database or network here. Nothing goes to an AI model.
"""

import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

# Sizes a pack of cigarettes or a bundle of bidis comes in.
SIZES = range(5, 41)
# A pack whose size isn't written is taken as the size usually bought, or this.
USUAL = 10
# The recent rate looks back this far.
RATE_DAYS = 56

# Tobacco items that aren't something smoked one at a time.
_NOT_STICKS = re.compile(
    r"lighter|zippo|ashtray|rolling\s*papers?|rizla|\bocb\b|smoking\s*paper|pa{1,2}n\s*masala"
    r"|gutk?ha|rajnigandha|vimal|khaini|zarda|hookah|shisha|filter\s*tips?",
    re.I,
)
_SIZE = [
    re.compile(r"pack\s*of\s*(\d{1,2})\b(?!\s*packs)", re.I),
    re.compile(r"\b(\d{1,2})\s*(?:'s|s|sticks?|cigarettes?|cigs|pcs|pieces|nos?)\b", re.I),
    re.compile(r"\b(\d{1,2})\s*-?\s*pack\b", re.I),
    re.compile(r"\b(10|20)\s*$"),
]


def is_sticks(name: str) -> bool:
    """A tobacco item that is cigarettes or bidis (not a lighter or pan masala). An item with no
    name is an order answered as "Cigarettes"."""
    return not _NOT_STICKS.search(name or "")


def pack_size(name: str) -> int | None:
    """The number of sticks in one pack, when the name says it."""
    for rx in _SIZE:
        if (m := rx.search(name or "")) and int(m.group(1)) in SIZES:
            return int(m.group(1))
    return None


@dataclass
class Buy:
    """Cigarettes bought in one order or at one paan-shop payment."""

    on: date
    name: str
    quantity: int
    paise: int | None


@dataclass
class Purchase:
    on: date
    sticks: int
    paise: int | None
    # True when the pack size wasn't written and was assumed.
    assumed: bool


def purchases(buys: list[Buy]) -> tuple[list[Purchase], int, int | None]:
    """Each buy as sticks; returns them, the usual pack size and what a stick usually costs."""
    sizes = [s for b in buys if (s := pack_size(b.name))]
    usual = statistics.mode(sizes) if sizes else USUAL
    per_stick = [b.paise / (s * b.quantity) for b in buys if b.paise and (s := pack_size(b.name))]
    stick = round(statistics.median(per_stick)) if per_stick else None
    out = []
    for b in buys:
        size = pack_size(b.name)
        if size:
            out.append(Purchase(b.on, size * b.quantity, b.paise, False))
        elif not b.name and b.paise and stick:
            # A whole order answered as "Cigarettes": its cost at the usual price a stick.
            out.append(Purchase(b.on, max(round(b.paise / stick), 1), b.paise, True))
        else:
            out.append(Purchase(b.on, usual * max(b.quantity, 1), b.paise, True))
    return out, usual, stick


def _by_day(buys: list[Purchase]) -> list[Purchase]:
    days: dict[date, Purchase] = {}
    for p in sorted(buys, key=lambda p: p.on):
        if (d := days.get(p.on)) is None:
            days[p.on] = Purchase(p.on, p.sticks, p.paise, p.assumed)
        else:
            d.sticks += p.sticks
            d.paise = (d.paise or 0) + (p.paise or 0) if (d.paise or p.paise) else None
            d.assumed = d.assumed or p.assumed
    return list(days.values())


def _rate(gaps: list[tuple[date, int, int]], lo: date, hi: date) -> float | None:
    """Sticks a day over the packs started in [lo, hi): each pack's sticks over the days until
    the next one."""
    used = [(s, g) for on, s, g in gaps if lo <= on < hi]
    days = sum(g for _, g in used)
    return sum(s for s, _ in used) / days if days else None


def consumption(
    buys: list[Buy],
    *,
    start: date,
    end: date,
    stop: date,
    last_start: date,
    today: date,
    loose_paise: int = 0,
    budget_paise: int | None = None,
) -> dict:
    """Sticks a day and how packs come and go, for the month [start, end) up to `stop`."""
    bought, usual, stick = purchases(buys)
    days = _by_day([p for p in bought if p.on <= today])
    gaps = [
        (a.on, a.sticks, max((b.on - a.on).days, 1)) for a, b in zip(days, days[1:], strict=False)
    ]
    rate = _rate(gaps, stop - timedelta(days=RATE_DAYS), stop)
    this = _rate(gaps, start, end)
    last = _rate(gaps, last_start, start)
    latest = days[-1] if days else None
    next_on = None
    if rate and latest:
        next_on = latest.on + timedelta(days=max(round(latest.sticks / rate), 1))
    month = [p for p in days if start <= p.on < stop]
    loose = loose_paise // stick if stick and loose_paise else 0
    out = {
        "packs": [
            {"on": p.on, "sticks": p.sticks, "assumed": p.assumed} for p in reversed(days[-8:])
        ],
        "usual_size": usual,
        "sticks_month": sum(p.sticks for p in month),
        "per_day": round(rate, 1) if rate else None,
        "per_day_month": round(this, 1) if this else None,
        "per_day_last_month": round(last, 1) if last else None,
        "pack_days": round(usual / rate, 1) if rate else None,
        "next_on": next_on,
        "stick_paise": stick,
        "loose_sticks": loose,
        "assumed": any(p.assumed for p in month),
    }
    out["tips"] = _tips(out, budget_paise)
    return out


def _n(x: float) -> str:
    return f"{x:.0f}" if x >= 10 or x == int(x) else f"{x:.1f}"


def _tips(c: dict, budget_paise: int | None) -> list[str]:
    """What the numbers suggest, in plain words. Nothing without the numbers behind it."""
    tips: list[str] = []
    rate, size, stick = c["per_day"], c["usual_size"], c["stick_paise"]
    if not rate:
        return tips
    now, before = c["per_day_month"], c["per_day_last_month"]
    if now and before and abs(now - before) / before >= 0.15:
        word = "up" if now > before else "down"
        tips.append(f"About {_n(now)} a day this month, {word} from {_n(before)} last month.")
    pack_days = c["pack_days"]
    if pack_days and pack_days < 30:
        # One more day per pack: the same packs spread over more days.
        slower = size / (pack_days + 1)
        fewer = (rate - slower) * 30
        line = (
            f"A {size}-pack lasts you about {_n(pack_days)} days. Making each one last a day "
            f"longer comes to about {fewer:.0f} fewer a month"
        )
        if stick:
            line += f", ₹{fewer * stick / 100:,.0f} kept"
        tips.append(line + ".")
    if rate >= 4:
        tips.append(
            f"Cutting one a day from {_n(rate)} is a {1 / rate:.0%} step, small enough to hold; "
            "the ones out of habit (after meals, with tea, on breaks) are the easiest to drop "
            "first."
        )
    if budget_paise and stick:
        allowed = budget_paise / stick / 30
        if allowed < rate:
            tips.append(
                f"Your monthly limit covers about {_n(allowed)} a day, against {_n(rate)} now."
            )
    if c["loose_sticks"]:
        tips.append(
            f"Loose buys at paan shops this month come to about {c['loose_sticks']} more, at "
            f"₹{stick / 100:.0f} a stick, so the real count is higher than the packs show."
        )
    return tips
