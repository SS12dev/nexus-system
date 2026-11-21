"""Getting Money started: accounts added by hand, banking-app screenshots read by the
on-device model (faked here), and reading old alert emails."""

import json
import logging
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from nexus_common import money, money_vision
from nexus_common import money_store as store
from nexus_common.models import MoneyAccount, MoneyTxn, User
from nexus_common.organiser import local_today

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TODAY = date(2026, 11, 21)


# --- What the model read, checked ----------------------------------------------------------------


def test_clean_keeps_what_was_really_read():
    raw = {
        "screen": "transactions",
        "bank": "HDFC Bank",
        "account_last4": "XX 1234",
        "account_kind": "bank",
        "balance": 45210.5,
        "transactions": [
            {"date": "2026-11-20", "description": "UPI/Swiggy", "amount": 349,
             "direction": "debit"},
            {"date": "2026-11-19", "description": "Acme Pvt Ltd SALARY", "amount": "1,25,000",
             "direction": "credit"},
            # A December date read with this year is last December's.
            {"date": "2026-12-28", "description": "Netflix", "amount": 649, "direction": "debit"},
            {"date": "2027-03-01", "description": "Future", "amount": 10, "direction": "debit"},
            {"date": "yesterday", "description": "Bad date", "amount": 10, "direction": "debit"},
            {"date": "2026-11-18", "description": "Zero", "amount": 0, "direction": "debit"},
            {"date": "2026-11-18", "description": "", "amount": 5, "direction": "debit"},
            {"date": "2026-11-18", "description": "Odd", "amount": 5, "direction": "sideways"},
        ],
    }  # fmt: skip
    got = money_vision.clean(raw, TODAY)
    assert (got["bank"], got["known_bank"], got["mask"], got["kind"]) == (
        "HDFC Bank", True, "1234", "bank",
    )  # fmt: skip
    assert got["balance_paise"] == 4521050
    # A date past today was read with the wrong year: it's the year before.
    assert [(r.occurred_on, r.merchant, r.amount_paise, r.direction) for r in got["rows"]] == [
        (date(2026, 11, 20), "Swiggy", 34900, "debit"),
        (date(2025, 12, 28), "Netflix", 64900, "debit"),
        (date(2026, 3, 1), "Future", 1000, "debit"),
    ]
    # "1,25,000" as text isn't a number the schema allows: dropped, not guessed. So are a
    # date that isn't one, a zero amount, an empty description and an unknown direction.
    assert got["dropped"] == 5
    assert got["rows"][0].category == "Food and dining"

    # Nothing useful: no bank, no digits, an unknown kind.
    empty = money_vision.clean({"bank": "PhonePe", "account_last4": "12"}, TODAY)
    assert empty["mask"] is None and not empty["known_bank"] and empty["kind"] == "bank"
    assert money_vision.clean("not a dict", TODAY)["rows"] == []  # type: ignore[arg-type]


async def test_read_asks_the_local_model_with_the_image(settings):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        answer = {"screen": "balance", "bank": "Axis", "account_last4": "3456",
                  "account_kind": "bank", "balance": 1000, "transactions": []}  # fmt: skip
        return httpx.Response(200, json={"message": {"content": json.dumps(answer)}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        raw = await money_vision.read(settings, PNG, TODAY, http=http)
    assert raw["account_last4"] == "3456"
    (body,) = seen
    assert body["model"] == (settings.money_vision_model or settings.graph_local_model)
    assert body["messages"][1]["images"] and body["format"]["type"] == "object"
    assert body["options"]["temperature"] == 0

    def blind(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text='{"error":"model does not support images"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(blind)) as http:
        with pytest.raises(money_vision.VisionError, match="can't read images"):
            await money_vision.read(settings, PNG, TODAY, http=http)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        with pytest.raises(money_vision.VisionError, match="didn't answer"):
            await money_vision.read(settings, PNG, TODAY, http=http)


# --- The API -----------------------------------------------------------------------------------


async def _user(db) -> User:
    return await db.scalar(select(User).where(User.email == "owner@example.com"))


async def test_members_cannot_onboard(owner, client_for, invite):
    await invite(owner, "friend@example.com")
    friend = await client_for("friend@example.com")
    assert (await friend.get("/api/money/banks")).status_code == 403
    assert (await friend.post("/api/money/accounts", json={})).status_code in (403, 422)
    files = {"file": ("s.png", PNG, "image/png")}
    assert (await friend.post("/api/money/screenshots/read", files=files)).status_code == 403


async def test_add_account_then_alerts_land_on_it(owner, sessionmaker, settings):
    banks = (await owner.get("/api/money/banks")).json()["banks"]
    assert "HDFC Bank" in banks and "SBI Card" in banks
    resp = await owner.post(
        "/api/money/accounts",
        json={"kind": "bank", "institution": "hdfc", "mask": "xx1234", "name": "Salary",
              "balance_paise": 5000000},
    )  # fmt: skip
    assert resp.status_code == 201, resp.text
    assert resp.json()["label"] == "Salary"
    again = await owner.post(
        "/api/money/accounts", json={"kind": "bank", "institution": "HDFC Bank", "mask": "1234"}
    )
    assert again.status_code == 409
    bad = await owner.post(
        "/api/money/accounts", json={"kind": "bank", "institution": "HDFC Bank", "mask": "12"}
    )
    assert bad.status_code == 422
    card = await owner.post(
        "/api/money/accounts",
        json={"kind": "card", "institution": "ICICI Bank", "mask": "9012",
              "credit_limit_paise": 20000000},
    )  # fmt: skip
    assert card.status_code == 201

    async with sessionmaker() as db:
        user = await _user(db)
        p = money.Parsed(local_today(user), 25000, "debit", "Swiggy", "SWIGGY",
                         account_mask="1234", institution="HDFC Bank")  # fmt: skip
        await store.ingest(db, settings, user.id, [p], source="alert")
        await db.commit()
        accounts = (await db.scalars(select(MoneyAccount))).all()
        assert len(accounts) == 2  # the alert used the typed-in account
        txn = await db.scalar(select(MoneyTxn))
        assert str(txn.account_id) == resp.json()["id"]
    view = (await owner.get("/api/money/balances")).json()
    by = {a["mask"]: a for a in view["items"]}
    assert by["1234"]["balance_paise"] == 5000000
    assert by["9012"]["credit_limit_paise"] == 20000000


async def test_screenshots_are_reviewed_then_saved(
    owner, sessionmaker, settings, monkeypatch, caplog
):
    caplog.set_level(logging.INFO)
    async with sessionmaker() as db:
        user = await _user(db)
        today = local_today(user)
        # An alert already has the Swiggy order.
        p = money.Parsed(today - timedelta(days=1), 34900, "debit", "Swiggy", "SWIGGY",
                         account_mask="1234", institution="HDFC Bank")  # fmt: skip
        await store.ingest(db, settings, user.id, [p], source="alert")
        await db.commit()

    answer = {
        "screen": "transactions", "bank": "HDFC", "account_last4": "1234",
        "account_kind": "bank", "balance": 45210.5,
        "transactions": [
            {"date": (today - timedelta(days=1)).isoformat(), "description": "Swiggy",
             "amount": 349, "direction": "debit"},
            {"date": (today - timedelta(days=2)).isoformat(), "description": "Zepto Marketplace",
             "amount": 812.4, "direction": "debit"},
            {"date": (today - timedelta(days=3)).isoformat(), "description": "Rahul Sharma",
             "amount": 2000, "direction": "credit"},
        ],
    }  # fmt: skip

    async def fake_read(cfg, image, day, **kw):
        assert image == PNG
        return answer

    monkeypatch.setattr(money_vision, "read", fake_read)
    files = {"file": ("s.png", PNG, "image/png")}
    resp = await owner.post("/api/money/screenshots/read", files=files)
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["mask"] == "1234" and got["bank"] == "HDFC Bank" and got["account_id"]
    assert [r["seen"] for r in got["rows"]] == [True, False, False]
    assert got["rows"][1]["category"] == "Groceries"

    not_image = {"file": ("s.txt", b"hello", "text/plain")}
    assert (await owner.post("/api/money/screenshots/read", files=not_image)).status_code == 415

    # Save everything, the already-seen row too: it is matched, not added twice.
    rows = [{k: r[k] for k in ("occurred_on", "merchant", "amount_paise", "direction")}
            for r in got["rows"]]  # fmt: skip
    saved = await owner.post(
        "/api/money/screenshots/save",
        json={"account_id": got["account_id"], "kind": "bank", "balance_paise": 4521050,
              "rows": rows, "images": 2},
    )  # fmt: skip
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["added"] == 2 and out["matched"] + out["skipped"] == 1
    # The same screenshots again add nothing.
    again = await owner.post(
        "/api/money/screenshots/save",
        json={"account_id": got["account_id"], "kind": "bank", "rows": rows},
    )
    assert again.json()["added"] == 0
    async with sessionmaker() as db:
        txns = (await db.scalars(select(MoneyTxn).order_by(MoneyTxn.occurred_on))).all()
        assert sorted(t.source for t in txns) == ["alert", "screenshot", "screenshot"]
        account = await db.get(MoneyAccount, txns[0].account_id)
        assert account.balance_paise == 4521050

    # A new card from screenshots, made from the digits typed in review.
    future = await owner.post(
        "/api/money/screenshots/save",
        json={"kind": "card", "institution": "SBI Card", "mask": "4321",
              "rows": [{**rows[1], "occurred_on": (today + timedelta(days=3)).isoformat()}]},
    )  # fmt: skip
    assert future.status_code == 422
    card = await owner.post(
        "/api/money/screenshots/save",
        json={"kind": "card", "institution": "SBI Card", "mask": "4321",
              "card_limit_paise": 15000000, "rows": [rows[1]]},
    )  # fmt: skip
    assert card.json()["account_label"] == "SBI Card card ••4321"
    imports = (await owner.get("/api/money/imports")).json()
    assert imports[0]["kind"] == "screenshot"

    logged = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    for secret in ("Zepto", "Rahul", "34900", "4521050"):
        assert secret not in logged, secret


async def test_screenshot_model_missing_says_what_to_do(owner, monkeypatch):
    async def down(cfg, image, day, **kw):
        raise money_vision.VisionError("The on-device model didn't answer. Check Ollama.")

    monkeypatch.setattr(money_vision, "read", down)
    resp = await owner.post(
        "/api/money/screenshots/read", files={"file": ("s.png", PNG, "image/png")}
    )
    assert resp.status_code == 503 and "Ollama" in resp.json()["detail"]


async def test_reading_old_alerts(owner, sessionmaker, queue):
    resp = await owner.post("/api/money/sync/history", json={"months": 12})
    assert resp.status_code == 400  # alert emails are off
    async with sessionmaker() as db:
        user = await _user(db)
        prefs = await store.settings_for(db, user.id)
        prefs.gmail_sync = True
        await db.commit()
    resp = await owner.post("/api/money/sync/history", json={"months": 12})
    assert resp.status_code == 202
    assert queue.money_history[-1] == 12 * 31
    assert (await owner.post("/api/money/sync/history", json={"months": 99})).status_code == 422
