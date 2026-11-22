"""The investments graph: stored share and fund values carried between points, deposits worked
out from their terms, today's live value last, and a share's filled-in history redone when
what it was valued from changes."""

import io
import uuid
import zipfile
from datetime import date, timedelta

from sqlalchemy import select

from nexus_common import money_history as history
from nexus_common import money_stocks
from nexus_common.models import MoneyAsset, MoneySnapshot, MoneyStock, User

NEW = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,FinInstrmNm,ClsPric"
OLD = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,"
    "ISIN,"
)


def _zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bhav.csv", text)
    return buf.getvalue()


def test_reads_the_older_bhavcopy_too():
    q = money_stocks.parse_bhavcopy(_zip("\n".join([
        OLD,
        "GUJGASLTD,EQ,637,644,635,641,641.2,634.85,677253,433211862.45,03-JAN-2022,24156,INE844O01030,",
        "1018GS2026,GS,95.05,95.05,95.05,95.05,95.05,100,10,950.5,03-JAN-2022,1,IN0020010081,",
    ])))  # fmt: skip
    assert set(q) == {"GUJGASLTD"}
    assert (q["GUJGASLTD"].close_paise, q["GUJGASLTD"].day) == (641_00, date(2022, 1, 3))
    assert q["GUJGASLTD"].isin == "INE844O01030"
    assert money_stocks.bhavcopy_url(date(2022, 1, 3)).endswith(
        "/EQUITIES/2022/JAN/cm03JAN2022bhav.csv.zip"
    )
    assert "BhavCopy_NSE_CM_0_0_0_20240102" in money_stocks.bhavcopy_url(date(2024, 1, 2))


def test_grid_and_fill_days():
    today = date(2026, 11, 22)
    assert history.grid(today, today) == [today]
    daily = history.grid(today - timedelta(days=30), today)
    assert len(daily) == 31 and daily[-1] == today
    weekly = history.grid(today - timedelta(days=366), today)
    assert weekly[1] - weekly[0] == timedelta(days=7) and weekly[-1] == today
    monthly = history.grid(date(2023, 5, 17), today)
    assert monthly[:3] == [date(2023, 5, 17), date(2023, 6, 1), date(2023, 7, 1)]
    assert monthly[-2:] == [date(2026, 11, 1), today]

    days = history.fill_days(date(2026, 1, 10), today)
    assert days[:3] == [date(2026, 1, 10), date(2026, 2, 1), date(2026, 3, 1)]
    assert days[-1] < today and days[-1] - days[-2] == timedelta(days=7)
    # Never more than five years back.
    assert history.fill_days(date(2010, 1, 1), today)[0] == date(2021, 11, 1)


async def test_history_graph(owner, redis, sessionmaker, queue):
    day = date.today()
    quotes = money_stocks.parse_bhavcopy(_zip("\n".join([
        NEW,
        f"{day},{day},CM,NSE,STK,1,INE844O01030,GUJENERGY,EQ,GUJARAT ENERGY LTD,226.40",
    ])))  # fmt: skip
    await redis.set(money_stocks.CACHE_KEY, money_stocks.dump(quotes))
    bought = day - timedelta(days=400)
    resp = await owner.post("/api/money/stocks", json={
        "symbol": "GUJENERGY", "quantity": "10", "avg_price_paise": 307_10,
        "bought_on": bought.isoformat()})  # fmt: skip
    assert resp.status_code == 201, resp.text
    stock_id = uuid.UUID(resp.json()["id"])
    assert len(queue.share_histories) == 1  # filled in now, not tonight

    async with sessionmaker() as db:
        user_id = (await db.scalars(select(User.id))).one()
        stock = await db.get(MoneyStock, stock_id)
        # Two earlier closes, as the orchestrator would have stored them.
        await history.save_filled(db, stock, [
            (day - timedelta(days=200), 600_00), (day - timedelta(days=30), 250_00)])  # fmt: skip
        db.add(MoneyAsset(
            user_id=user_id, kind="fd", name="SBI FD", amount_paise=100_000_00, rate_bp=700,
            start_on=day - timedelta(days=90), maturity_on=day + timedelta(days=275)))  # fmt: skip
        await db.commit()

    out = (await owner.get("/api/money/investments/history", params={"range": "all"})).json()
    assert out["days"][-1] == day.isoformat()
    assert out["first_on"] == (day - timedelta(days=200)).isoformat()
    assert [s["group"] for s in out["series"]] == ["shares", "deposits"]
    share, fd = out["series"]
    assert share["label"] == "GUJARAT ENERGY LTD"
    assert share["values"][-1] == 10 * 226_40  # live today
    assert share["costs"][-1] == 10 * 307_10
    assert 10 * 600_00 in share["values"] and 10 * 250_00 in share["values"]
    assert fd["values"][0] is None  # before it started
    assert fd["values"][-1] > 100_000_00 and fd["costs"][-1] is None
    assert out["filling"] is True

    # A month back: daily points, the old close carried, the FD there all along.
    month = (await owner.get("/api/money/investments/history", params={"range": "1m"})).json()
    assert len(month["days"]) == 32
    # The close 200 days back is too old to stand in for a month ago; the one 30 days back is.
    assert month["series"][0]["values"][:2] == [None, 10 * 250_00]
    assert all(v is not None for v in month["series"][1]["values"])
    bad = await owner.get("/api/money/investments/history", params={"range": "2w"})
    assert bad.status_code == 422

    # More shares: the filled-in history was at 10, so it's dropped and fetched again.
    await owner.patch(f"/api/money/stocks/{stock_id}", json={"quantity": "12"})
    assert len(queue.share_histories) == 2
    async with sessionmaker() as db:
        assert (await db.scalars(select(MoneySnapshot))).all() == []
        assert (await db.get(MoneyStock, stock_id)).history_from is None
    # A note doesn't.
    await owner.patch(f"/api/money/stocks/{stock_id}", json={"note": "Zerodha"})
    assert len(queue.share_histories) == 2

    async with sessionmaker() as db:
        assert await history.record(db, user_id) == 1
        await db.commit()
    await owner.delete(f"/api/money/stocks/{stock_id}")
    async with sessionmaker() as db:
        assert (await db.scalars(select(MoneySnapshot))).all() == []
