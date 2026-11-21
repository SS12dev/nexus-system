"""Shares: found in NSE's daily list by name, symbol or ISIN; added with a quantity; priced each
night, following a rename by ISIN; counted in savings and net worth."""

import io
import zipfile
from datetime import date

from sqlalchemy import select

from nexus_common import money_stocks
from nexus_common import money_store as store
from nexus_common.models import MoneyStock

HEADER = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,FinInstrmNm,ClsPric"


def _bhavcopy(rows: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("BhavCopy_NSE_CM_0_0_0_20261120_F_0000.csv", "\n".join([HEADER, *rows]))
    return buf.getvalue()


DAY1 = _bhavcopy([
    "2026-11-20,2026-11-20,CM,NSE,STK,1,INE844O01030,GUJGASLTD,EQ,GUJARAT GAS LIMITED,412.35",
    "2026-11-20,2026-11-20,CM,NSE,STK,2,INE040A01034,HDFCBANK,EQ,HDFC BANK LTD,1650.10",
    "2026-11-20,2026-11-20,CM,NSE,STK,3,INE040A01034,HDFCBANK,BL,HDFC BANK LTD,1600",
    "2026-11-20,2026-11-20,CM,NSE,STK,4,IN0020200104,SGBJUN28,GB,GOLDBONDS2028,12487.49",
    "2026-11-20,2026-11-20,CM,NSE,STK,5,INE000X01010,TINYSME,SM,TINY SME LTD,42",
])  # fmt: skip


def test_reads_nse_bhavcopy_and_searches_it():
    q = money_stocks.parse_bhavcopy(DAY1)
    assert set(q) == {"GUJGASLTD", "HDFCBANK", "TINYSME"}  # no bonds
    assert q["GUJGASLTD"].close_paise == 412_35 and q["GUJGASLTD"].day == date(2026, 11, 20)
    assert q["HDFCBANK"].close_paise == 1650_10  # the EQ row, not the block deal
    assert [x.symbol for x in money_stocks.search(q, "gujarat gas")] == ["GUJGASLTD"]
    assert money_stocks.search(q, "INE844O01030")[0].symbol == "GUJGASLTD"
    assert money_stocks.search(q, "hdfc")[0].symbol == "HDFCBANK"
    assert money_stocks.load(money_stocks.dump(q)) == q


async def test_add_price_and_count_shares(owner, redis, sessionmaker):
    await redis.set(money_stocks.CACHE_KEY, money_stocks.dump(money_stocks.parse_bhavcopy(DAY1)))
    found = (await owner.get("/api/money/stocks/search", params={"q": "gujarat"})).json()
    assert found["items"][0]["symbol"] == "GUJGASLTD"

    resp = await owner.post("/api/money/stocks", json={
        "symbol": "gujgasltd", "quantity": "1", "avg_price_paise": 500_00})  # fmt: skip
    assert resp.status_code == 201, resp.text
    s = resp.json()
    assert (s["name"], s["value_paise"], s["gain_paise"]) == ("GUJARAT GAS LIMITED", 412_35, -87_65)
    again = await owner.post("/api/money/stocks", json={"symbol": "GUJGASLTD", "quantity": "2"})
    assert again.status_code == 409
    unknown = await owner.post("/api/money/stocks", json={"symbol": "NOPE", "quantity": "1"})
    assert unknown.status_code == 422

    savings = (await owner.get("/api/money/savings")).json()
    assert savings["stocks_value_paise"] == 412_35
    plan = (await owner.get("/api/money/plan")).json()
    assert plan["start"]["funds_paise"] == 412_35

    # Renamed overnight: same ISIN, new symbol; the nightly price follows it.
    renamed = money_stocks.parse_bhavcopy(_bhavcopy([
        "2026-11-21,2026-11-21,CM,NSE,STK,1,INE844O01030,GUJENERGY,EQ,GUJARAT ENERGY LTD,226.35",
    ]))  # fmt: skip
    async with sessionmaker() as db:
        (row,) = (await db.scalars(select(MoneyStock))).all()
        assert store.reprice(row, renamed) is True
        assert (row.symbol, row.name, row.price_paise, row.price_on) == (
            "GUJENERGY", "GUJARAT ENERGY LTD", 226_35, date(2026, 11, 21)
        )  # fmt: skip
        assert store.reprice(row, renamed) is False
        await db.commit()

    edit = await owner.patch(f"/api/money/stocks/{s['id']}", json={"quantity": "3"})
    assert edit.json()["value_paise"] == 3 * 226_35
    assert (await owner.get("/api/money/stocks")).json()["value_paise"] == 3 * 226_35
    assert (await owner.delete(f"/api/money/stocks/{s['id']}")).status_code == 204
    assert (await owner.get("/api/money/stocks")).json()["items"] == []


async def test_nse_unreachable_says_so(owner, monkeypatch):
    import httpx

    async def down(today, client=None):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(money_stocks, "fetch_quotes", down)
    resp = await owner.get("/api/money/stocks/search", params={"q": "tcs"})
    assert resp.status_code == 503 and "NSE" in resp.json()["detail"]
