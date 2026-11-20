"""The Telegram bot's side in the orchestrator: link accounts, turn messages into Nexus turns and
send the replies back (the flow is described in nexus_common/telegram.py)."""

import asyncio
import contextlib
import hashlib
import logging
import uuid
from datetime import timedelta
from pathlib import PurePath

import httpx
from sqlalchemy import delete, select

from nexus_common import logs, telegram
from nexus_common.events import Event, publish
from nexus_common.library import MODEL_EXTENSIONS, SUPPORTED_EXTENSIONS, content_type_for
from nexus_common.litellm import ensure_user_key
from nexus_common.models import (
    Attachment,
    AuditLog,
    Chat,
    File,
    FileStatus,
    Message,
    MessageRole,
    MessageStatus,
    TelegramLink,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.queues import DOCS_QUEUE
from nexus_common.storage import file_key
from nexus_common.telegram import Bot, Incoming, TelegramError
from nexus_orchestrator.turn import TurnDeps, run_turn

log = logging.getLogger(__name__)

CHAT_TITLE = "Telegram"
# One message at a time per person: a second waits for the first reply, up to this long.
LOCK_TIMEOUT_S = 300
LOCK_WAIT_S = 240
# "Not linked" answers to a stranger, at most one an hour.
STRANGER_TTL_S = 3600
# Telegram's bots may download files up to 20 MB.
MAX_DOWNLOAD = 20 * 1024 * 1024

HELP = (
    "Send me anything, as text, a voice note, a photo or a document, and I'll answer here. "
    "Everything also shows in Nexus, in the chat called Telegram.\n\n"
    "/new starts a fresh chat\n/unlink disconnects this Telegram account\n/help shows this"
)


def _client(deps: TurnDeps) -> httpx.AsyncClient | None:
    return deps.google_http


@contextlib.asynccontextmanager
async def _bot(deps: TurnDeps):
    if (http := _client(deps)) is not None:
        yield Bot(deps.settings, http)
        return
    async with httpx.AsyncClient() as http:
        yield Bot(deps.settings, http)


async def register_webhook(deps: TurnDeps) -> None:
    """Point Telegram at NEXUS_URL/api/telegram/webhook. Safe to repeat; done at start."""
    if not deps.settings.telegram_enabled:
        return
    url = deps.settings.nexus_url.rstrip("/") + telegram.WEBHOOK_PATH
    if not url.startswith("https://"):
        log.warning("telegram.webhook_skipped", extra={"reason": "NEXUS_URL is not https"})
        return
    try:
        async with _bot(deps) as bot:
            await bot.set_webhook(url)
        log.info("telegram.webhook_set")
    except TelegramError as exc:
        log.warning("telegram.webhook_failed", extra={"error": str(exc)})


async def handle_update(deps: TurnDeps, update: dict) -> None:
    if not deps.settings.telegram_enabled:
        return
    inc = telegram.parse_update(update)
    if inc is None:
        return
    logs.bind(telegram_update=inc.update_id)
    async with _bot(deps) as bot:
        if inc.chat_type != "private":
            # Groups would let other people talk to your assistant: never.
            log.info("telegram.ignored_group")
            return
        command, _, arg = inc.text.strip().partition(" ")
        command = command.split("@", 1)[0].lower() if command.startswith("/") else ""
        if command == "/start":
            await _start(deps, bot, inc, arg.strip())
            return

        async with deps.sessionmaker() as db:
            link = await db.scalar(
                select(TelegramLink).where(TelegramLink.tg_chat_id == inc.tg_chat_id)
            )
            user = await db.get(User, link.user_id) if link else None
        if link is None or user is None:
            await _stranger(deps, bot, inc)
            return
        logs.bind(user_id=user.id)
        if user.status != UserStatus.active:
            await bot.send(inc.tg_chat_id, "Your Nexus account isn't active, so I can't help here.")
            return
        if command in ("/help", "/settings"):
            await bot.send(inc.tg_chat_id, HELP)
            return
        if command == "/new":
            async with deps.sessionmaker() as db:
                link = await db.get(TelegramLink, user.id)
                link.chat_id = None
                await db.commit()
            await bot.send(inc.tg_chat_id, "Done. Your next message starts a new chat.")
            return
        if command == "/unlink":
            async with deps.sessionmaker() as db:
                await db.execute(delete(TelegramLink).where(TelegramLink.user_id == user.id))
                db.add(AuditLog(actor_id=user.id, action="telegram.unlinked", target=str(user.id)))
                await db.commit()
            await bot.send(
                inc.tg_chat_id,
                "Disconnected. Connect again any time from Settings › Telegram in Nexus.",
            )
            return
        if inc.kind == "other":
            await bot.send(
                inc.tg_chat_id, "I can read text, voice notes, photos and documents for now."
            )
            return
        lock = deps.redis.lock(
            f"nexus:telegram:busy:{user.id}", timeout=LOCK_TIMEOUT_S, blocking_timeout=LOCK_WAIT_S
        )
        if not await lock.acquire():
            await bot.send(inc.tg_chat_id, "Still working on your last message. Try again soon.")
            return
        try:
            await _converse(deps, bot, inc, user)
        finally:
            with contextlib.suppress(Exception):
                await lock.release()


async def _start(deps: TurnDeps, bot: Bot, inc: Incoming, code: str) -> None:
    user_id = await telegram.redeem_link_code(deps.redis, code) if code else None
    async with deps.sessionmaker() as db:
        existing = await db.scalar(
            select(TelegramLink).where(TelegramLink.tg_chat_id == inc.tg_chat_id)
        )
        if user_id is None:
            if existing is not None:
                await bot.send(inc.tg_chat_id, "You're already connected. " + HELP)
            else:
                await bot.send(
                    inc.tg_chat_id,
                    "Hi! To talk to your assistant here, open Nexus, go to Settings › Telegram "
                    "and tap Connect. That link brings you back here, connected."
                    if not code
                    else "That link has expired or was already used. Make a new one in Nexus, "
                    "under Settings › Telegram.",
                )
            return
        user = await db.get(User, user_id)
        if user is None or user.status != UserStatus.active:
            await bot.send(inc.tg_chat_id, "That Nexus account isn't active.")
            return
        # One Telegram chat per person and one person per Telegram chat: the newest link wins.
        await db.execute(
            delete(TelegramLink).where(
                (TelegramLink.user_id == user.id) | (TelegramLink.tg_chat_id == inc.tg_chat_id)
            )
        )
        db.add(
            TelegramLink(
                user_id=user.id,
                tg_chat_id=inc.tg_chat_id,
                tg_username=(inc.username or "")[:64] or None,
                tg_name=(inc.name or "")[:128] or None,
            )
        )
        db.add(AuditLog(actor_id=user.id, action="telegram.linked", target=str(user.id)))
        await db.commit()
        assistant = user.assistant
    await publish(deps.redis, user.id, Event(type="telegram.linked"))
    log.info("telegram.linked", extra={"user_id": str(user.id)})
    await bot.send(
        inc.tg_chat_id,
        f"Connected. I'm {assistant}, the same assistant as in Nexus, with the same memory. "
        + HELP,
    )


async def _stranger(deps: TurnDeps, bot: Bot, inc: Incoming) -> None:
    if await deps.redis.set(
        f"nexus:telegram:stranger:{inc.tg_chat_id}", 1, ex=STRANGER_TTL_S, nx=True
    ):
        await bot.send(
            inc.tg_chat_id,
            "This Telegram account isn't connected to Nexus. Open Nexus, go to "
            "Settings › Telegram and tap Connect.",
        )
    log.info("telegram.unlinked_message")


async def _keep_typing(bot: Bot, chat_id: int) -> None:
    # Telegram shows "typing…" for about five seconds per call.
    while True:
        await bot.typing(chat_id)
        await asyncio.sleep(4.5)


async def _content(
    deps: TurnDeps, bot: Bot, inc: Incoming, user: User
) -> tuple[str, list[File]] | None:
    """What the person sent, as message text and files. None when it couldn't be read (the
    person has been told why)."""
    s = deps.settings
    if inc.kind == "text":
        return inc.text, []
    if inc.kind == "voice":
        if inc.duration and inc.duration > s.telegram_max_voice_s:
            await bot.send(
                inc.tg_chat_id,
                f"That voice note is longer than {s.telegram_max_voice_s // 60} minutes. "
                "Send a shorter one, or type it.",
            )
            return None
        await bot.typing(inc.tg_chat_id, "record_voice")
        try:
            audio, path = await bot.download(inc.file_id or "", MAX_DOWNLOAD)
            async with deps.sessionmaker() as db:
                key = await ensure_user_key(db, s, deps.gateway, user)
                await db.commit()
            name = inc.file_name or f"voice{PurePath(path).suffix or '.ogg'}"
            text = await telegram.transcribe(s, bot.http, key, audio, name)
        except TelegramError as exc:
            log.warning("telegram.voice_failed", extra={"error": str(exc)})
            await bot.send(
                inc.tg_chat_id,
                "I couldn't make out that voice note (speech to text isn't working right "
                "now). Could you type it?",
            )
            return None
        if not text:
            await bot.send(inc.tg_chat_id, "I couldn't hear any words in that voice note.")
            return None
        # So a mishearing is caught before the answer.
        await bot.send(inc.tg_chat_id, f"_“{text}”_")
        return text, []
    # Photo or document: kept in the Library like an upload in a chat.
    limit = min(MAX_DOWNLOAD, s.max_upload_mb * 1024 * 1024)
    if inc.file_size and inc.file_size > limit:
        await bot.send(inc.tg_chat_id, f"That file is over {limit // (1024 * 1024)} MB.")
        return None
    try:
        data, path = await bot.download(inc.file_id or "", limit)
    except TelegramError as exc:
        log.warning("telegram.download_failed", extra={"error": str(exc)})
        await bot.send(inc.tg_chat_id, "I couldn't download that file from Telegram. Try again?")
        return None
    suffix = PurePath(path).suffix.lower() or ".jpg"
    name = (inc.file_name or f"Telegram photo {utcnow():%Y-%m-%d %H%M}{suffix}")[:200]
    ext = PurePath(name).suffix.lower()
    readable = ext in SUPPORTED_EXTENSIONS
    if not readable and ext not in MODEL_EXTENSIONS:
        await bot.send(inc.tg_chat_id, f"I can't read {ext or 'that kind of'} files yet.")
        return None
    content_type = content_type_for(name, inc.mime)
    f = File(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        content_type=content_type,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        status=FileStatus.processing if readable else FileStatus.ready,
    )
    f.storage_key = file_key(user.id, f.id)
    if deps.storage is None:
        raise RuntimeError("storage is not configured")
    await deps.storage.put(f.storage_key, data, content_type)
    async with deps.sessionmaker() as db:
        db.add(f)
        await db.commit()
    if readable and deps.enqueue is not None:
        try:
            await deps.enqueue(
                "parse_file", str(f.id), _job_id=f"parse:{f.id}", _queue_name=DOCS_QUEUE
            )
        except Exception:
            log.exception("Could not queue parse of %s", f.id)
    return inc.text, [f]


async def _converse(deps: TurnDeps, bot: Bot, inc: Incoming, user: User) -> None:
    typing = asyncio.create_task(_keep_typing(bot, inc.tg_chat_id))
    try:
        got = await _content(deps, bot, inc, user)
        if got is None:
            return
        text, files = got
        now = utcnow()
        async with deps.sessionmaker() as db:
            link = await db.get(TelegramLink, user.id)
            chat = await db.get(Chat, link.chat_id) if link and link.chat_id else None
            if chat is None:
                chat = Chat(user_id=user.id, title=CHAT_TITLE)
                db.add(chat)
                await db.flush()
                if link:
                    link.chat_id = chat.id
            if link:
                link.last_message_at = now
            user_msg = Message(
                chat_id=chat.id,
                role=MessageRole.user,
                content=text,
                status=MessageStatus.complete,
                created_at=now,
            )
            reply = Message(
                chat_id=chat.id,
                role=MessageRole.assistant,
                content="",
                status=MessageStatus.streaming,
                model=deps.settings.chat_model,
                trace_id=uuid.uuid4().hex,
                created_at=now + timedelta(microseconds=1),
            )
            chat.updated_at = now
            db.add_all([user_msg, reply])
            await db.flush()
            for f in files:
                db.add(Attachment(message_id=user_msg.id, file_id=f.id))
                await db.execute(
                    File.__table__.update().where(File.id == f.id).values(chat_id=chat.id)
                )
            await db.commit()
            chat_id, reply_id = chat.id, reply.id
            chat_out = {
                "id": str(chat.id),
                "title": chat.title,
                "created_at": chat.created_at.isoformat(),
                "updated_at": chat.updated_at.isoformat(),
            }
        # The chat list in the app moves this chat to the top (or shows it, when new).
        await publish(deps.redis, user.id, Event(type="chat.updated", data={"chat": chat_out}))
        log.info("telegram.turn_started", extra={"kind": inc.kind, "chat_id": str(chat_id)})
        await run_turn(deps, chat_id, reply_id)
        async with deps.sessionmaker() as db:
            done = await db.get(Message, reply_id)
            made = (
                await db.scalars(
                    select(File.name)
                    .join(Attachment, Attachment.file_id == File.id)
                    .where(Attachment.message_id == reply_id)
                )
            ).all()
    finally:
        typing.cancel()
    if done is None or done.status != MessageStatus.complete:
        await bot.send(
            inc.tg_chat_id,
            (done.error if done and done.error else "Something went wrong answering that.")
            + " You can retry here or in Nexus.",
        )
        return
    answer = done.content or "(No text in the reply.)"
    if made:
        url = f"{deps.settings.nexus_url.rstrip('/')}/c/{chat_id}"
        names = ", ".join(made[:5]) + (f" and {len(made) - 5} more" if len(made) > 5 else "")
        answer += f"\n\nFiles: {names}. Open them in Nexus: {url}"
    await bot.send(inc.tg_chat_id, answer)
    log.info("telegram.replied", extra={"chars": len(answer)})
