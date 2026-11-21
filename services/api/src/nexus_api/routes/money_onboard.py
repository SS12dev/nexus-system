"""Getting Money started without net banking: add accounts by hand (so alert emails land on
them), read banking-app screenshots with the on-device vision model, and read further back
through old alert emails.

Owner only. A screenshot is read in memory, sent only to Ollama on this machine, and dropped;
nothing is saved until the person confirms the rows. Logs carry counts and ids, never amounts,
merchants or what an image showed.
"""

import logging
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from nexus_api.deps import DbDep, OwnerUser, QueueDep, RedisDep, SettingsDep
from nexus_api.ratelimit import hit, too_many
from nexus_common import money, money_vision
from nexus_common import money_store as store
from nexus_common.models import MoneyAccount, MoneyTxn, utcnow
from nexus_common.organiser import local_today

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/money", tags=["money"])

MAX_IMAGE_MB = 8
READS_PER_HOUR = 120
HISTORY_PER_DAY = 3
Paise = Annotated[int, Field(ge=0, le=10**13)]


@router.get("/banks")
async def banks(user: OwnerUser) -> dict:
    """The banks Money recognises in alert emails, for the add-account form."""
    return {"banks": list(money.BANK_NAMES)}


# --- Adding an account by hand -------------------------------------------------------------------


class AccountIn(BaseModel):
    kind: Literal["bank", "card"]
    institution: str = Field(min_length=1, max_length=60)
    mask: str
    name: str = Field(default="", max_length=120)
    balance_paise: Paise | None = None
    credit_limit_paise: Paise | None = None

    @field_validator("mask")
    @classmethod
    def four_digits(cls, v: str) -> str:
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) != 4:
            raise ValueError("the last four digits of the account or card number")
        return digits

    @field_validator("institution", "name")
    @classmethod
    def tidy(cls, v: str) -> str:
        return " ".join(v.split())


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def add_account(body: AccountIn, user: OwnerUser, db: DbDep, redis: RedisDep) -> dict:
    """An account or card typed in. Alert emails and statements with the same bank and last
    four digits then land on it."""
    institution = money.bank_of(body.institution) or body.institution
    existing = (
        await db.scalars(
            select(MoneyAccount).where(
                MoneyAccount.user_id == user.id,
                MoneyAccount.mask == body.mask,
                MoneyAccount.kind == body.kind,
            )
        )
    ).all()
    for row in existing:
        if not row.institution or row.institution == institution:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{store.account_label(row)} is already in Money. Edit it on the Accounts tab.",
            )
    today = local_today(user)
    row = MoneyAccount(
        user_id=user.id,
        kind=body.kind,
        institution=institution,
        mask=body.mask,
        name=body.name,
        created_at=utcnow(),
    )
    if body.kind == "bank" and body.balance_paise is not None:
        row.balance_paise, row.balance_on = body.balance_paise, today
    if body.kind == "card" and body.credit_limit_paise:
        row.credit_limit_paise = body.credit_limit_paise
    db.add(row)
    await db.commit()
    log.info("money.account_added", extra={"user_id": str(user.id), "account_id": str(row.id)})
    await store.changed(redis, user.id)
    return {"id": str(row.id), "label": store.account_label(row)}


# --- Screenshots ---------------------------------------------------------------------------------


def _image_kind(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


async def _seen(
    db, user_id: uuid.UUID, account_id: uuid.UUID | None, rows: list[money_vision.Row]
) -> list[bool]:
    """Which rows look like ones already in Money (same amount and direction, within two days,
    same account when known), so the review can leave them unticked."""
    if not rows:
        return []
    lo = min(r.occurred_on for r in rows)
    hi = max(r.occurred_on for r in rows)
    q = select(MoneyTxn).where(
        MoneyTxn.user_id == user_id,
        MoneyTxn.occurred_on >= date.fromordinal(lo.toordinal() - store.MATCH_DAYS),
        MoneyTxn.occurred_on <= date.fromordinal(hi.toordinal() + store.MATCH_DAYS),
    )
    have = list((await db.scalars(q)).all())
    used: set[uuid.UUID] = set()
    out = []
    for r in rows:
        twin = next(
            (
                t
                for t in have
                if t.id not in used
                and t.amount_paise == r.amount_paise
                and t.direction == r.direction
                and abs((t.occurred_on - r.occurred_on).days) <= store.MATCH_DAYS
                and (account_id is None or t.account_id in (None, account_id))
            ),
            None,
        )
        if twin is not None:
            used.add(twin.id)
        out.append(twin is not None)
    return out


@router.post("/screenshots/read")
async def read_screenshot(
    user: OwnerUser,
    db: DbDep,
    cfg: SettingsDep,
    redis: RedisDep,
    file: Annotated[UploadFile, File()],
) -> dict:
    """Read one screenshot of a banking, UPI or card app. Returns what was read for review;
    saves nothing."""
    if wait := await hit(redis, "money-shot", str(user.id), READS_PER_HOUR, 3600):
        raise too_many(wait, "screenshots")
    data = await file.read(MAX_IMAGE_MB * 1024 * 1024 + 1)
    if len(data) > MAX_IMAGE_MB * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"Screenshots up to {MAX_IMAGE_MB} MB."
        )
    if _image_kind(data) is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "That isn't a PNG, JPEG or WebP image. Phone screenshots are PNG or JPEG.",
        )
    today = local_today(user)
    prefs = await store.settings_for(db, user.id)
    rules = prefs.rules or {}
    await db.commit()
    try:
        raw = await money_vision.read(cfg, data, today)
    except money_vision.VisionError as exc:
        log.info("money.screenshot_unread", extra={"user_id": str(user.id)})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    got = money_vision.clean(raw, today, rules)
    account = None
    if got["mask"]:
        for row in (
            await db.scalars(
                select(MoneyAccount).where(
                    MoneyAccount.user_id == user.id, MoneyAccount.mask == got["mask"]
                )
            )
        ).all():
            if row.kind == got["kind"] and (
                not got["known_bank"] or not row.institution or row.institution == got["bank"]
            ):
                account = row
                break
    seen = await _seen(db, user.id, account.id if account else None, got["rows"])
    log.info(
        "money.screenshot_read",
        extra={"user_id": str(user.id), "rows": len(got["rows"]), "dropped": got["dropped"]},
    )
    return {
        **{k: v for k, v in got.items() if k != "rows"},
        "account_id": str(account.id) if account else None,
        "account_label": store.account_label(account) if account else None,
        "rows": [
            {
                "occurred_on": r.occurred_on,
                "merchant": r.merchant,
                "amount_paise": r.amount_paise,
                "direction": r.direction,
                "category": r.category,
                "seen": s,
            }
            for r, s in zip(got["rows"], seen, strict=True)
        ],
    }


class ShotRow(BaseModel):
    occurred_on: date
    merchant: str = Field(min_length=1, max_length=120)
    amount_paise: int = Field(gt=0, le=10**13)
    direction: Literal["debit", "credit"]


class ShotSave(BaseModel):
    account_id: uuid.UUID | None = None
    institution: str | None = Field(default=None, max_length=60)
    mask: str | None = Field(default=None, max_length=8)
    kind: Literal["bank", "card"] = "bank"
    balance_paise: Paise | None = None
    card_limit_paise: Paise | None = None
    card_due_paise: Paise | None = None
    card_due_on: date | None = None
    rows: list[ShotRow] = Field(default_factory=list, max_length=500)
    images: int = Field(default=1, ge=1, le=50)

    @field_validator("mask")
    @classmethod
    def four_digits(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) != 4:
            raise ValueError("the last four digits of the account or card number")
        return digits


@router.post("/screenshots/save")
async def save_screenshots(
    body: ShotSave, user: OwnerUser, db: DbDep, cfg: SettingsDep, redis: RedisDep
) -> dict:
    """Save the rows the person kept from their screenshots, on one account."""
    today = local_today(user)
    if any(r.occurred_on > today for r in body.rows):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A date is in the future.")
    account: MoneyAccount | None = None
    if body.account_id:
        account = await db.get(MoneyAccount, body.account_id)
        if account is None or account.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    elif body.mask:
        institution = money.bank_of(body.institution or "") or (body.institution or None)
        account = await store.account_for(db, user.id, institution, body.mask, body.kind)
    if account is not None:
        if account.kind == "bank" and body.balance_paise is not None:
            account.balance_paise, account.balance_on = body.balance_paise, today
        if account.kind == "card":
            if body.card_limit_paise:
                account.credit_limit_paise = body.card_limit_paise
            if body.card_due_paise and body.card_due_on:
                account.due_paise, account.due_on = body.card_due_paise, body.card_due_on
    rows = [
        money.Parsed(
            r.occurred_on, r.amount_paise, r.direction, " ".join(r.merchant.split()),
            r.merchant, channel="app",
            account_mask=account.mask if account else None,
            account_kind=account.kind if account else body.kind,
            institution=account.institution if account else None,
        )
        for r in body.rows
    ]  # fmt: skip
    counts = await store.ingest(
        db, cfg, user.id, rows, source="screenshot",
        account_id=account.id if account else None,
    )  # fmt: skip
    await store.record_import(
        db, cfg, user.id, kind="screenshot", origin="upload",
        filename=f"{body.images} screenshot{'s' if body.images != 1 else ''}",
        status="done", counts=counts, account_id=account.id if account else None,
    )  # fmt: skip
    await db.commit()
    log.info("money.screenshots_saved", extra={"user_id": str(user.id), **counts})
    await store.changed(redis, user.id, **counts)
    return {
        **counts,
        "account_id": str(account.id) if account else None,
        "account_label": store.account_label(account) if account else None,
    }


# --- Reading further back through alert emails ---------------------------------------------------


class HistoryIn(BaseModel):
    months: int = Field(default=12, ge=1, le=24)


@router.post("/sync/history", status_code=status.HTTP_202_ACCEPTED)
async def read_history(
    body: HistoryIn, user: OwnerUser, db: DbDep, redis: RedisDep, queue: QueueDep
) -> dict:
    """Read old alert emails as far back as `months` once, to fill in history. Anything already
    read is skipped."""
    prefs = await store.settings_for(db, user.id)
    if not prefs.gmail_sync:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Switch on reading alert emails first, then read back."
        )
    if wait := await hit(redis, "money-history", str(user.id), HISTORY_PER_DAY, 86400):
        raise too_many(wait, "history reads")
    await queue.enqueue_money_sync(user.id, back_days=body.months * 31)
    log.info("money.history_queued", extra={"user_id": str(user.id), "months": body.months})
    return {"queued": True, "months": body.months}
