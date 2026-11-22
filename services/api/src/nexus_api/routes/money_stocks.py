"""Shares held directly: search NSE's list, add, edit and remove holdings. Prices come from
NSE's public end-of-day file each night (nexus_common/money_stocks.py); nothing about the
person's holdings leaves Nexus.

Owner only, and only ever the owner's own rows. Logs carry ids and counts, never symbols,
quantities or amounts.
"""

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from nexus_api.deps import DbDep, OwnerUser, QueueDep, RedisDep
from nexus_common import money_history, money_stocks
from nexus_common import money_store as store
from nexus_common.models import MoneyStock, utcnow
from nexus_common.organiser import local_today

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/money/stocks", tags=["money"])

Paise = Annotated[int, Field(ge=0, le=10**13)]
Quantity = Annotated[Decimal, Field(gt=0, le=10**9, max_digits=20, decimal_places=4)]
UNAVAILABLE = (
    "NSE's price list couldn't be read just now. Try again in a minute; if it keeps failing, "
    "NSE may be blocking requests for a while."
)


class StockIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9&\-]+$")
    quantity: Quantity
    avg_price_paise: Paise | None = None
    bought_on: date | None = None
    note: str | None = Field(default=None, max_length=300)


class StockPatch(BaseModel):
    quantity: Quantity | None = None
    avg_price_paise: Paise | None = None
    bought_on: date | None = None
    note: str | None = Field(default=None, max_length=300)


async def _quotes(redis, today: date) -> dict[str, money_stocks.Quote]:
    try:
        return await money_stocks.cached_quotes(redis, today)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("money.quotes_unavailable", extra={"error": type(exc).__name__})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE) from exc


async def _own(db, user_id: uuid.UUID, stock_id: uuid.UUID) -> MoneyStock:
    row = await db.get(MoneyStock, stock_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found.")
    return row


@router.get("")
async def list_stocks(user: OwnerUser, db: DbDep) -> dict:
    return await store.stocks_view(db, user.id)


@router.get("/search")
async def search(
    user: OwnerUser, redis: RedisDep, q: Annotated[str, Query(min_length=1, max_length=60)]
) -> dict:
    quotes = await _quotes(redis, local_today(user))
    found = money_stocks.search(quotes, q)
    return {
        "items": [
            {
                "symbol": x.symbol,
                "name": x.name,
                "isin": x.isin,
                "price_paise": x.close_paise,
                "price_on": x.day,
            }
            for x in found
        ],
        "price_on": max((x.day for x in quotes.values()), default=None),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_stock(
    body: StockIn, user: OwnerUser, db: DbDep, redis: RedisDep, queue: QueueDep
) -> dict:
    symbol = body.symbol.upper()
    quotes = await _quotes(redis, local_today(user))
    quote = quotes.get(symbol)
    if quote is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"NSE doesn't list {symbol} today. Search by the company's name or its ISIN.",
        )
    row = await db.scalar(
        select(MoneyStock).where(
            MoneyStock.user_id == user.id,
            MoneyStock.exchange == "NSE",
            MoneyStock.symbol == symbol,
        )
    )
    if row is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{quote.name} is already in your shares; edit it instead."
        )
    row = MoneyStock(
        user_id=user.id,
        symbol=symbol,
        exchange="NSE",
        name=quote.name,
        isin=quote.isin or None,
        quantity=body.quantity,
        avg_price_paise=body.avg_price_paise,
        bought_on=body.bought_on,
        price_paise=quote.close_paise,
        price_on=quote.day,
        note=" ".join((body.note or "").split()),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(row)
    await db.commit()
    log.info("money.stock_added", extra={"user_id": str(user.id)})
    await store.changed(redis, user.id)
    if row.bought_on:
        await queue.enqueue_share_history(user.id)
    return store.stock_out(row)


@router.patch("/{stock_id}")
async def edit_stock(
    stock_id: uuid.UUID,
    body: StockPatch,
    user: OwnerUser,
    db: DbDep,
    redis: RedisDep,
    queue: QueueDep,
) -> dict:
    row = await _own(db, user.id, stock_id)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("quantity", 1) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Say how many shares you hold.")
    if "note" in fields:
        fields["note"] = " ".join((fields["note"] or "").split())
    # The filled-in history was valued from these; fill it again from the new ones.
    refill = any(
        getattr(row, k) != fields[k]
        for k in ("quantity", "avg_price_paise", "bought_on")
        if k in fields
    )
    for name, value in fields.items():
        setattr(row, name, value)
    if refill:
        await money_history.forget(db, user.id, row.id, filled_only=True)
        row.history_from = None
    row.updated_at = utcnow()
    await db.commit()
    await store.changed(redis, user.id)
    if refill and row.bought_on:
        await queue.enqueue_share_history(user.id)
    return store.stock_out(row)


@router.delete("/{stock_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stock(stock_id: uuid.UUID, user: OwnerUser, db: DbDep, redis: RedisDep):
    row = await _own(db, user.id, stock_id)
    await money_history.forget(db, user.id, row.id)
    await db.delete(row)
    await db.commit()
    log.info("money.stock_removed", extra={"user_id": str(user.id)})
    await store.changed(redis, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
