"""Shares held directly: their closing prices from NSE's public daily file (the "bhavcopy").

NSE publishes every listed security's open, high, low and close a little after the market shuts
(around 6 pm IST) as a zipped CSV. Nexus reads it once a night to price the shares people typed
in, and keeps the list of equity symbols in Redis so the add dialog can search it by name,
symbol or ISIN. Nothing about a person's holdings is sent anywhere: the file is the same for
everyone.

A company that was renamed or merged keeps its ISIN, so searching by ISIN (it's on a demat
statement) finds it under its new name.
"""

import csv
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import httpx

BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
)
# Before 2024 NSE's archive has only the older file, with other column names.
OLD_BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/{day:%Y}/{mon}/cm{day:%d}{mon}"
    "{day:%Y}bhav.csv.zip"
)
NEW_FORMAT_FROM = date(2024, 1, 1)
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
# NSE's archive refuses requests without a browser-like User-Agent.
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
# Equity series: EQ (normal), BE/BZ (trade-to-trade), SM/ST (SME platform).
EQUITY_SERIES = {"EQ", "BE", "BZ", "SM", "ST"}
# How many days back to look for the latest file (weekends and market holidays have none).
LOOK_BACK_DAYS = 7
CACHE_KEY = "nexus:money:nse-quotes"
CACHE_SECONDS = 60 * 60 * 36
# One past day's closes by ISIN, kept while a share's history is filled in.
CLOSES_KEY = "nexus:money:nse-closes:{}"
CLOSES_SECONDS = 60 * 60 * 24 * 3


def bhavcopy_url(day: date) -> str:
    if day >= NEW_FORMAT_FROM:
        return BHAVCOPY_URL.format(day=day)
    return OLD_BHAVCOPY_URL.format(day=day, mon=_MONTHS[day.month - 1])


@dataclass
class Quote:
    symbol: str
    name: str
    isin: str
    series: str
    close_paise: int
    day: date


def _field(row: dict, *names: str) -> str:
    for n in names:
        if row.get(n):
            return row[n].strip()
    return ""


def _day(text: str) -> date:
    """2026-11-20 in the newer file, 20-NOV-2026 in the older one."""
    if "-" in text and text[:4].isdigit():
        return date.fromisoformat(text)
    d, m, y = text.split("-")
    return date(int(y), _MONTHS.index(m.upper()) + 1, int(d))


def parse_bhavcopy(data: bytes) -> dict[str, Quote]:
    """NSE's zipped bhavcopy (either format) -> {symbol: Quote} for equity series only."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError("no CSV in the bhavcopy")
        text = z.read(names[0]).decode("utf-8", "replace")
    out: dict[str, Quote] = {}
    for row in csv.DictReader(io.StringIO(text)):
        series = _field(row, "SctySrs", "SERIES")
        symbol = _field(row, "TckrSymb", "SYMBOL").upper()
        if series not in EQUITY_SERIES or not symbol:
            continue
        try:
            close = Decimal(_field(row, "ClsPric", "CLOSE"))
            day = _day(_field(row, "TradDt", "TIMESTAMP"))
        except (InvalidOperation, ValueError, IndexError):
            continue
        if close <= 0:
            continue
        # EQ wins when a symbol appears in more than one series.
        if symbol in out and out[symbol].series == "EQ":
            continue
        out[symbol] = Quote(
            symbol=symbol,
            name=" ".join(_field(row, "FinInstrmNm").split())[:160],
            isin=_field(row, "ISIN").upper()[:16],
            series=series,
            close_paise=int((close * 100).quantize(Decimal(1))),
            day=day,
        )
    return out


async def fetch_quotes(today: date, client: httpx.AsyncClient | None = None) -> dict[str, Quote]:
    """The newest bhavcopy published on or before `today`, looking back a week for weekends and
    holidays. Raises httpx.HTTPError or ValueError when none can be read."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=60, follow_redirects=True, headers=HEADERS)
    try:
        last: Exception | None = None
        for back in range(LOOK_BACK_DAYS + 1):
            day = today - timedelta(days=back)
            if day.weekday() >= 5:
                continue
            try:
                resp = await client.get(bhavcopy_url(day), headers=HEADERS)
            except httpx.HTTPError as exc:
                last = exc
                continue
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            quotes = parse_bhavcopy(resp.content)
            if quotes:
                return quotes
        if last:
            raise last
        raise ValueError("no bhavcopy in the last week")
    finally:
        if own:
            await client.aclose()


def dump(quotes: dict[str, Quote]) -> str:
    return json.dumps(
        [{**asdict(q), "day": q.day.isoformat()} for q in quotes.values()], separators=(",", ":")
    )


def load(text: str | bytes | None) -> dict[str, Quote]:
    if not text:
        return {}
    try:
        rows = json.loads(text)
        return {r["symbol"]: Quote(**{**r, "day": date.fromisoformat(r["day"])}) for r in rows}
    except (ValueError, KeyError, TypeError):
        return {}


async def cached_quotes(redis, today: date) -> dict[str, Quote]:
    """The latest quotes from Redis, fetched from NSE and stored when missing."""
    quotes = load(await redis.get(CACHE_KEY))
    if quotes:
        return quotes
    quotes = await fetch_quotes(today)
    await redis.set(CACHE_KEY, dump(quotes), ex=CACHE_SECONDS)
    return quotes


async def closes_from(
    redis, day: date, today: date, client: httpx.AsyncClient
) -> tuple[date, dict[str, int]] | None:
    """The first trading day's closes on or after `day` (within a week, before `today`), by
    ISIN, from Redis or NSE. None when NSE has no file for that week. Raises httpx.HTTPError
    when NSE can't be reached."""
    for ahead in range(7):
        d = day + timedelta(days=ahead)
        if d >= today:
            return None
        if d.weekday() >= 5:
            continue
        key = CLOSES_KEY.format(d.isoformat())
        cached = await redis.get(key)
        if cached is not None:
            closes = json.loads(cached)
            if closes:
                return d, closes
            continue
        resp = await client.get(bhavcopy_url(d), headers=HEADERS)
        closes: dict[str, int] = {}
        if resp.status_code != 404:
            resp.raise_for_status()
            try:
                closes = {q.isin: q.close_paise for q in parse_bhavcopy(resp.content).values()
                          if q.isin}  # fmt: skip
            except (ValueError, zipfile.BadZipFile):
                closes = {}
        # A holiday is remembered too, so it isn't asked for again.
        await redis.set(key, json.dumps(closes, separators=(",", ":")), ex=CLOSES_SECONDS)
        if closes:
            return d, closes
    return None


_WORD = re.compile(r"[a-z0-9&]+")
# Words in NSE's short names that add nothing to a search.
_FILLER = {"ltd", "limited", "l", "the", "co", "india", "ind", "and"}


def search(quotes: dict[str, Quote], q: str, limit: int = 12) -> list[Quote]:
    """Shares whose symbol, ISIN or name matches `q`, best first: an exact symbol or ISIN, then
    a symbol that starts with it, then names that contain every word, then most words."""
    q = q.strip()
    if not q:
        return []
    upper = q.upper().replace(" ", "")
    exact = quotes.get(upper)
    by_isin = [x for x in quotes.values() if x.isin == upper]
    words = [w for w in _WORD.findall(q.lower()) if w not in _FILLER] or _WORD.findall(q.lower())
    scored: list[tuple[float, str, Quote]] = []
    for x in quotes.values():
        if x is exact or x in by_isin:
            continue
        name_words = _WORD.findall(x.name.lower())
        hits = sum(any(n.startswith(w) for n in name_words) for w in words)
        score = 0.0
        if x.symbol.startswith(upper):
            score = 3 + len(upper) / max(len(x.symbol), 1)
        elif words and hits == len(words):
            score = 2
        elif hits:
            # The first word usually names the company ("gujarat gas"), so it counts extra.
            first = any(n.startswith(words[0]) for n in name_words)
            score = hits / len(words) + (0.25 if first else 0)
        if score:
            scored.append((score, x.symbol, x))
    scored.sort(key=lambda s: (-s[0], s[1]))
    top = ([exact] if exact else []) + by_isin + [x for _, _, x in scored]
    return top[:limit]


def value_paise(quantity: Decimal, price_paise: int | None) -> int | None:
    if price_paise is None:
        return None
    return int((quantity * price_paise).quantize(Decimal(1)))
