"""Reading a banking-app screenshot with the vision model in Ollama on this machine.

Most people check their money in a phone app, not net banking, so a screenshot of the app's
transaction list, balance or card summary is the easiest history to give. The image goes to
Ollama on the same machine (never a cloud model), the answer comes back as structured JSON, and
`clean` checks every field before anything is shown. Nothing is saved until the person has
reviewed the rows; the image itself is never stored or logged.
"""

import base64
import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

from nexus_common import money
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

MAX_ROWS = 80

SCHEMA = {
    "type": "object",
    "properties": {
        "screen": {
            "type": "string",
            "enum": ["transactions", "balance", "card", "other"],
        },
        "bank": {"type": "string"},
        "account_last4": {"type": "string"},
        "account_kind": {"type": "string", "enum": ["bank", "card", "unknown"]},
        "balance": {"type": ["number", "null"]},
        "card_limit": {"type": ["number", "null"]},
        "card_due": {"type": ["number", "null"]},
        "card_due_date": {"type": ["string", "null"]},
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "description": {"type": "string"},
                    "amount": {"type": "number"},
                    "direction": {"type": "string", "enum": ["debit", "credit"]},
                },
                "required": ["date", "description", "amount", "direction"],
            },
        },
    },
    "required": ["screen", "bank", "account_last4", "account_kind", "transactions"],
}

SYSTEM = (
    "You read screenshots of Indian banking, UPI and credit card apps and copy out exactly "
    "what is on screen as JSON. Never invent or guess a value that isn't visible: use an empty "
    "string or null instead. Amounts are in rupees as plain numbers (1,234.50 -> 1234.5). "
    "A debit is money going out (paid, sent, spent, withdrawn, a minus sign or red); a credit "
    "is money coming in (received, refund, cashback, salary, a plus sign or green)."
)


def prompt(today: date) -> str:
    return (
        f"Today is {today:%A %d %B %Y}. Read this screenshot.\n"
        "- screen: 'transactions' for a list of payments, 'balance' for an account balance, "
        "'card' for a credit card summary, otherwise 'other'.\n"
        "- bank: the bank, card or app's name as shown (HDFC Bank, ICICI, SBI Card, PhonePe...).\n"
        "- account_last4: the last four digits of the account or card if shown, else ''.\n"
        "- account_kind: 'card' for a credit card, 'bank' for a savings or current account.\n"
        "- balance: the available account balance if shown. card_limit, card_due and "
        "card_due_date (YYYY-MM-DD) for a credit card summary.\n"
        "- transactions: every payment row visible, top to bottom: date as YYYY-MM-DD (turn "
        "'Today', 'Yesterday' or '12 Nov' into a full date using today's date; a date without "
        "a year is the most recent one not after today), description as shown (the merchant "
        "or person), amount, direction."
    )


class VisionError(Exception):
    """Something the person can act on: Ollama isn't running, the model can't see images."""


@dataclass
class Row:
    occurred_on: date
    merchant: str
    amount_paise: int
    direction: str
    category: str


def _date(text: str | None, today: date) -> date | None:
    try:
        day = date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None
    # A model sometimes puts this year on a date from December last year.
    if day > today + timedelta(days=1):
        try:
            day = day.replace(year=day.year - 1)
        except ValueError:
            return None
    if day > today or day < today - timedelta(days=3 * 366):
        return None
    return day


def _paise(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        p = round(abs(float(value)) * 100)
    except (TypeError, ValueError):
        return None
    return p if 0 < p < 10**13 else None


def clean(raw: dict, today: date, rules: dict[str, str] | None = None) -> dict:
    """What the model read, checked: a known bank name, four digits, real dates no later than
    today, positive amounts. Rows that don't pass are dropped and counted."""
    raw = raw if isinstance(raw, dict) else {}
    bank_text = str(raw.get("bank") or "").strip()
    digits = "".join(c for c in str(raw.get("account_last4") or "") if c.isdigit())
    kind = raw.get("account_kind")
    institution = money.bank_of(bank_text)
    if kind not in ("bank", "card"):
        kind = "card" if institution in ("SBI Card", "American Express", "OneCard") else "bank"
    rows: list[Row] = []
    dropped = 0
    for item in (raw.get("transactions") or [])[:MAX_ROWS]:
        if not isinstance(item, dict):
            dropped += 1
            continue
        day = _date(item.get("date"), today)
        amount = _paise(item.get("amount"))
        direction = item.get("direction")
        desc = " ".join(str(item.get("description") or "").split())[:120]
        if day is None or amount is None or direction not in ("debit", "credit") or not desc:
            dropped += 1
            continue
        merchant = money.clean_merchant(desc) or desc
        credit = direction == "credit"
        rows.append(
            Row(day, merchant, amount, direction,
                money.categorise(merchant, desc, credit=credit, rules=rules))
        )  # fmt: skip
    return {
        "screen": raw.get("screen")
        if raw.get("screen") in SCHEMA["properties"]["screen"]["enum"]
        else "other",  # fmt: skip
        "bank": institution or bank_text[:60] or None,
        "known_bank": institution is not None,
        "mask": digits[-4:] if len(digits) >= 4 else None,
        "kind": kind,
        "balance_paise": _paise(raw.get("balance")),
        "card_limit_paise": _paise(raw.get("card_limit")),
        "card_due_paise": _paise(raw.get("card_due")),
        "card_due_on": _date_any(raw.get("card_due_date")),
        "rows": rows,
        "dropped": dropped,
    }


def _date_any(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


async def read(
    settings: Settings, image: bytes, today: date, *, http: httpx.AsyncClient | None = None
) -> dict:
    """Ask the on-device vision model to read one screenshot. Returns the raw JSON answer."""
    base = settings.ollama_base_url.rstrip("/")
    model = settings.money_vision_model or settings.graph_local_model
    body = {
        "model": model,
        "stream": False,
        "format": SCHEMA,
        "think": False,
        "keep_alive": "5m",
        "options": {"temperature": 0, "num_ctx": 16384},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": prompt(today),
                "images": [base64.b64encode(image).decode()],
            },
        ],
    }
    own = http is None
    client = http or httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5))
    try:
        r = await client.post(f"{base}/api/chat", json=body)
        if r.status_code == 400 and "think" in r.text:
            del body["think"]
            r = await client.post(f"{base}/api/chat", json=body)
    except httpx.HTTPError as exc:
        raise VisionError(
            "The on-device model didn't answer. Check Ollama is running on the Mac "
            f"(ollama serve) and has {model} (ollama pull {model})."
        ) from exc
    finally:
        if own:
            await client.aclose()
    if r.status_code != 200:
        text = r.text.lower()
        if "image" in text or "vision" in text or "multimodal" in text:
            raise VisionError(
                f"{model} can't read images. Set a vision model in Admin › Configuration "
                "(Money screenshot model), for example qwen3.5:9b or qwen2.5vl:7b."
            )
        if "not found" in text:
            raise VisionError(f"Ollama doesn't have {model} yet: run ollama pull {model}.")
        log.warning("money.vision_failed", extra={"status": r.status_code})
        raise VisionError("The on-device model couldn't read that screenshot. Try again.")
    try:
        data = json.loads(r.json().get("message", {}).get("content") or "")
    except ValueError as exc:
        raise VisionError(
            "The model's answer wasn't readable. Try a clearer or smaller screenshot."
        ) from exc
    if not isinstance(data, dict):
        raise VisionError("The model's answer wasn't readable. Try a clearer screenshot.")
    return data
