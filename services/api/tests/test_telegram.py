"""Telegram linking and the webhook (routes/telegram.py)."""

import pytest
from sqlalchemy import select

from nexus_common import telegram
from nexus_common.models import TelegramLink, User

SECRET = "s" * 32


@pytest.fixture
def tg(settings, monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(settings, "telegram_webhook_secret", SECRET)

    async def name(_redis, _bot):
        return "nexus_test_bot"

    monkeypatch.setattr(telegram, "bot_username", name)


async def test_off_until_set_up(owner):
    body = (await owner.get("/api/telegram")).json()
    assert body == {"enabled": False, "bot_username": None, "link": None}
    assert (await owner.post("/api/telegram/link")).status_code == 409
    assert (await owner.post("/api/telegram/webhook", json={"update_id": 1})).status_code == 404
    assert "telegram" not in (await owner.get("/api/me")).json()["channels"]


async def test_link_code_status_and_unlink(tg, owner, redis, sessionmaker):
    assert "telegram" in (await owner.get("/api/me")).json()["channels"]
    resp = await owner.post("/api/telegram/link")
    assert resp.status_code == 201
    code = resp.json()["code"]
    assert resp.json()["url"] == f"https://t.me/nexus_test_bot?start={code}"
    # The code names this account, once.
    async with sessionmaker() as db:
        uid = await db.scalar(select(User.id).where(User.email == "owner@example.com"))
    assert await telegram.redeem_link_code(redis, code) == uid
    assert await telegram.redeem_link_code(redis, code) is None
    assert await telegram.redeem_link_code(redis, "bad code!") is None

    async with sessionmaker() as db:
        db.add(TelegramLink(user_id=uid, tg_chat_id=42, tg_username="shivam"))
        await db.commit()
    body = (await owner.get("/api/telegram")).json()
    assert body["enabled"] and body["bot_username"] == "nexus_test_bot"
    assert body["link"]["tg_username"] == "shivam"
    assert (await owner.delete("/api/telegram/link")).status_code == 204
    assert (await owner.get("/api/telegram")).json()["link"] is None


async def test_webhook_checks_the_secret_and_drops_repeats(tg, client_for, queue):
    anon = await client_for()
    update = {"update_id": 7, "message": {"chat": {"id": 1, "type": "private"}, "text": "hi"}}
    assert (await anon.post("/api/telegram/webhook", json=update)).status_code == 401
    wrong = {"X-Telegram-Bot-Api-Secret-Token": "x" * 32}
    resp = await anon.post("/api/telegram/webhook", json=update, headers=wrong)
    assert resp.status_code == 401
    right = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
    assert (await anon.post("/api/telegram/webhook", json=update, headers=right)).status_code == 200
    assert (await anon.post("/api/telegram/webhook", json=update, headers=right)).status_code == 200
    assert queue.telegram == [update]
    # Garbage is acknowledged (so Telegram stops sending it) but not queued.
    resp = await anon.post("/api/telegram/webhook", content=b"nope", headers=right)
    assert resp.status_code == 200 and len(queue.telegram) == 1


async def test_a_short_secret_keeps_telegram_off(settings, monkeypatch, owner):
    monkeypatch.setattr(settings, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "short")
    assert (await owner.get("/api/telegram")).json()["enabled"] is False


def test_markdown_becomes_telegram_html():
    md = (
        "## Plan\n\n**Bold** and *italic*, `a<b>` and [Nexus](https://nexus.servhub.cc).\n"
        "- one\n- two\n\n```python\nif a < b:\n    pass\n```\n| a | b |\n|---|---|\n| 1 | 2 |"
    )
    out = telegram.to_html(md)
    assert "<b>Plan</b>" in out and "<b>Bold</b>" in out and "<i>italic</i>" in out
    assert "<code>a&lt;b&gt;</code>" in out
    assert '<a href="https://nexus.servhub.cc">Nexus</a>' in out
    assert "• one\n• two" in out
    assert "<pre>if a &lt; b:\n    pass</pre>" in out
    assert "|---|" not in out
    # Raw HTML from the model can't open a tag.
    assert telegram.to_html("<script>x</script> 2 < 3") == "&lt;script&gt;x&lt;/script&gt; 2 &lt; 3"


def test_long_replies_split_cleanly():
    text = ("word " * 300 + "\n\n") * 5
    parts = telegram.split(text, 1000)
    assert all(len(p) <= 1000 for p in parts)
    assert " ".join(" ".join(parts).split()) == " ".join(text.split())


def test_parse_update_kinds():
    base = {"update_id": 1, "message": {"chat": {"id": 5, "type": "private"}, "from": {}}}

    def with_(**msg):
        return {**base, "message": {**base["message"], **msg}}

    assert telegram.parse_update(with_(text="hi")).kind == "text"
    voice = telegram.parse_update(with_(voice={"file_id": "v", "duration": 3}))
    assert (voice.kind, voice.file_id, voice.duration) == ("voice", "v", 3)
    photo = telegram.parse_update(
        with_(
            photo=[{"file_id": "s", "file_size": 1}, {"file_id": "l", "file_size": 9}], caption="c"
        )
    )
    assert (photo.kind, photo.file_id, photo.text) == ("photo", "l", "c")
    assert telegram.parse_update(with_(sticker={})).kind == "other"
    assert telegram.parse_update(with_(text="x", **{"from": {"is_bot": True}})) is None
    assert telegram.parse_update({"update_id": 2, "edited_message": {}}) is None
