"""The Telegram bot in the orchestrator: linking, turns, voice notes, photos and routines."""

import json

import httpx
import pytest
from sqlalchemy import select

from nexus_common import telegram as tg
from nexus_common.models import (
    Attachment,
    Chat,
    File,
    Message,
    MessageRole,
    Routine,
    TelegramLink,
)
from nexus_orchestrator import routines, telegram

TG_CHAT = 777


class FakeTelegram:
    """Answers Bot API and LiteLLM transcription calls; records what was sent."""

    def __init__(self):
        self.sent: list[dict] = []
        self.calls: list[str] = []
        self.transcript = "remind me to call Vishal"
        self.transcribe_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v1/audio/transcriptions"):
            self.calls.append("transcribe")
            assert b"nexus-transcribe" in request.content
            if self.transcribe_status != 200:
                return httpx.Response(self.transcribe_status, json={})
            return httpx.Response(200, json={"text": self.transcript})
        if "/file/bot" in path:
            self.calls.append("download")
            return httpx.Response(200, content=b"\xff\xd8 fake bytes")
        method = path.rsplit("/", 1)[-1]
        self.calls.append(method)
        body = json.loads(request.content or b"{}")
        if method == "sendMessage":
            self.sent.append(body)
        if method == "getFile":
            suffix = ".oga" if body["file_id"].startswith("voice") else ".jpg"
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": f"x/file{suffix}", "file_size": 10}}
            )
        return httpx.Response(200, json={"ok": True, "result": True})

    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent]


@pytest.fixture
async def bot(deps, settings, storage, redis):
    # "Not connected" answers and link codes live in Redis; start each test without them.
    stale = [k async for k in redis.scan_iter("nexus:telegram:*")]
    if stale:
        await redis.delete(*stale)
    fake = FakeTelegram()
    deps.settings = settings.model_copy(
        update={"telegram_bot_token": "1:x", "telegram_webhook_secret": "s" * 32}
    )
    deps.google_http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    deps.storage = storage
    yield fake
    await deps.google_http.aclose()


def update(n: int, **message) -> dict:
    return {
        "update_id": n,
        "message": {
            "chat": {"id": TG_CHAT, "type": "private"},
            "from": {"id": 1, "username": "shivam", "first_name": "Shivam"},
            **message,
        },
    }


async def link(deps, owner) -> None:
    user, _ = owner
    code = await tg.new_link_code(deps.redis, user.id)
    await telegram.handle_update(deps, update(1, text=f"/start {code}"))


async def test_strangers_are_told_how_to_connect_once(deps, bot):
    await telegram.handle_update(deps, update(1, text="hello"))
    await telegram.handle_update(deps, update(2, text="hello again"))
    assert len(bot.sent) == 1 and "isn't connected" in bot.sent[0]["text"]
    await telegram.handle_update(deps, update(3, text="/start wrongcode123"))
    assert "expired" in bot.texts()[-1]


async def test_link_then_talk(deps, bot, owner, model, sessionmaker):
    user, _ = owner
    await link(deps, owner)
    assert "Connected" in bot.texts()[-1]
    async with sessionmaker() as db:
        row = await db.get(TelegramLink, user.id)
    assert (row.tg_chat_id, row.tg_username, row.tg_name) == (TG_CHAT, "shivam", "Shivam")

    model.script = ["**Sure.** Here's the plan:\n- one\n- two"]
    await telegram.handle_update(deps, update(2, text="What's my plan?"))
    reply = bot.sent[-1]
    assert reply["parse_mode"] == "HTML"
    assert reply["text"] == "<b>Sure.</b> Here's the plan:\n• one\n• two"
    assert "sendChatAction" in bot.calls
    # The conversation is a normal Nexus chat called Telegram, which the next message continues.
    async with sessionmaker() as db:
        row = await db.get(TelegramLink, user.id)
        chat = await db.get(Chat, row.chat_id)
        assert chat.title == "Telegram" and chat.user_id == user.id
    await telegram.handle_update(deps, update(3, text="And after that?"))
    async with sessionmaker() as db:
        msgs = (await db.scalars(select(Message).where(Message.chat_id == chat.id))).all()
    assert len(msgs) == 4
    assert {m.content for m in msgs if m.role == MessageRole.user} == {
        "What's my plan?",
        "And after that?",
    }

    # /new starts a fresh chat; /unlink disconnects.
    await telegram.handle_update(deps, update(4, text="/new"))
    await telegram.handle_update(deps, update(5, text="Fresh start"))
    async with sessionmaker() as db:
        row = await db.get(TelegramLink, user.id)
    assert row.chat_id != chat.id
    await telegram.handle_update(deps, update(6, text="/unlink"))
    async with sessionmaker() as db:
        assert await db.get(TelegramLink, user.id) is None


async def test_voice_notes_are_transcribed(deps, bot, owner, model, sessionmaker):
    user, _ = owner
    await link(deps, owner)
    model.script = ["Done, I'll remind you."]
    await telegram.handle_update(deps, update(2, voice={"file_id": "voice1", "duration": 4}))
    # The transcript is echoed so a mishearing shows, then the answer.
    assert bot.texts()[-2:] == ["<i>“remind me to call Vishal”</i>", "Done, I'll remind you."]
    async with sessionmaker() as db:
        msg = await db.scalar(select(Message).where(Message.role == MessageRole.user))
    assert msg.content == "remind me to call Vishal"

    # Too long: refused before downloading; transcription down: asked to type instead.
    await telegram.handle_update(deps, update(3, voice={"file_id": "voice2", "duration": 3600}))
    assert "longer than" in bot.texts()[-1]
    bot.transcribe_status = 500
    await telegram.handle_update(deps, update(4, voice={"file_id": "voice3", "duration": 3}))
    assert "Could you type it?" in bot.texts()[-1]


async def test_photos_go_to_the_library(deps, bot, owner, model, sessionmaker, enqueued):
    user, _ = owner
    await link(deps, owner)
    model.script = ["A receipt for ₹450."]
    await telegram.handle_update(
        deps, update(2, photo=[{"file_id": "p1", "file_size": 10}], caption="What is this?")
    )
    assert bot.texts()[-1] == "A receipt for ₹450."
    async with sessionmaker() as db:
        f = await db.scalar(select(File).where(File.user_id == user.id))
        att = await db.scalar(select(Attachment).where(Attachment.file_id == f.id))
        msg = await db.get(Message, att.message_id)
    assert f.name.startswith("Telegram photo") and f.name.endswith(".jpg")
    assert msg.content == "What is this?" and f.chat_id == msg.chat_id
    assert ("parse_file", str(f.id)) in enqueued.jobs


async def test_groups_are_ignored_and_failures_are_reported(deps, bot, owner, model):
    await link(deps, owner)
    group = update(2, text="hi")
    group["message"]["chat"]["type"] = "group"
    sent = len(bot.sent)
    await telegram.handle_update(deps, group)
    assert len(bot.sent) == sent
    model.error = RuntimeError("model down")
    await telegram.handle_update(deps, update(3, text="hello"))
    assert "retry" in bot.texts()[-1]
    assert bot.sent[-1].get("parse_mode") == "HTML"


async def test_off_without_settings(deps, owner, sessionmaker):
    await telegram.handle_update(deps, update(1, text="hello"))
    async with sessionmaker() as db:
        assert (await db.scalars(select(Message))).all() == []


async def test_routines_deliver_to_telegram(deps, bot, owner, sessionmaker):
    user, _ = owner
    await link(deps, owner)
    r = Routine(
        user_id=user.id, name="Morning brief", prompt="p", schedule="x", delivery=["telegram"]
    )
    async with sessionmaker() as db:
        db.add(r)
        await db.commit()
    assert await routines.deliver(deps, r, user, "Three meetings today.") == ["telegram"]
    text = bot.texts()[-1]
    assert text.startswith("<b>Morning brief</b>\n\nThree meetings today.")
    assert '<a href="' in text and "Open in Nexus" in text


async def test_in_drops_straight_into_the_organiser_inbox(
    deps, bot, owner, model, sessionmaker, enqueued
):
    from nexus_common.models import OrgItem

    user, _ = owner
    await link(deps, owner)
    await telegram.handle_update(deps, update(2, text="/in [ ] call the bank tomorrow #money"))
    assert bot.texts()[-1].startswith("In your Inbox: task “call the bank”, due ")
    await telegram.handle_update(deps, update(3, text="/todo renew passport"))
    await telegram.handle_update(deps, update(4, text="/in"))
    assert "Add the text" in bot.texts()[-1]
    async with sessionmaker() as db:
        rows = (await db.scalars(select(OrgItem).order_by(OrgItem.created_at))).all()
    assert [(r.type, r.title, r.status, r.source, r.tags) for r in rows] == [
        ("task", "call the bank", "inbox", "telegram", ["money"]),
        ("task", "renew passport", "inbox", "telegram", []),
    ]
    assert [j[0] for j in enqueued.jobs].count("organiser_suggest") == 2
    # No model turn for a capture.
    assert model.seen == []

    await telegram.handle_update(deps, update(5, text="/today"))
    agenda = bot.texts()[-1]
    assert agenda.startswith("Today is") and "2 item(s) waiting in the Inbox" in agenda
    assert "(id " not in agenda
