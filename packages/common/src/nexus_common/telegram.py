"""Telegram: a two-way bot, so the assistant is reachable from any phone without the app.

How it fits together:

- The person opens Settings › Telegram and taps Connect. The API makes a one-time code
  (`new_link_code`, ten minutes, kept in Redis) and a t.me deep link that sends `/start <code>`
  to the bot. The orchestrator sees that message, checks the code and links that Telegram chat
  to the account (`TelegramLink`).
- Telegram posts every update to NEXUS_URL/api/telegram/webhook with the secret header set when
  the webhook was registered. The API checks the secret, drops repeats by update id, and queues
  the update; it never waits for the assistant, so Telegram never retries for slowness.
- The orchestrator runs the message as a normal turn in the person's Telegram chat in Nexus
  (visible in the chat list as "Telegram"), then sends the reply back. Voice notes are
  transcribed through LiteLLM first; photos and documents are saved to the Library.

Replies use Telegram's HTML mode: Markdown from the model is converted here (`to_html`), since
Telegram's own Markdown modes reject unbalanced characters that models often produce.

Message text is personal: none of it is logged, only ids, sizes and timings.
"""

import html
import logging
import re
import secrets
import uuid
from dataclasses import dataclass

import httpx
from redis.asyncio import Redis

from nexus_common.settings import Settings

log = logging.getLogger(__name__)

# Telegram's limit is 4096 characters after entities are parsed; stay clear of it.
MAX_CHARS = 3900
LINK_CODE_TTL_S = 600
LINK_KEY = "nexus:telegram:link:"
SEEN_KEY = "nexus:telegram:seen:"
SEEN_TTL_S = 24 * 3600
WEBHOOK_PATH = "/api/telegram/webhook"
# Updates the bot asks for: plain messages only (no edits, channels, inline queries).
ALLOWED_UPDATES = ["message"]


class TelegramError(Exception):
    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


# --- Linking -----------------------------------------------------------------------------------


async def new_link_code(redis: Redis, user_id: uuid.UUID) -> str:
    """A one-time code that links whichever Telegram chat sends it to this account."""
    code = secrets.token_urlsafe(18)  # 24 URL-safe characters; Telegram allows up to 64.
    await redis.set(LINK_KEY + code, str(user_id), ex=LINK_CODE_TTL_S)
    return code


async def redeem_link_code(redis: Redis, code: str) -> uuid.UUID | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", code or ""):
        return None
    raw = await redis.getdel(LINK_KEY + code)
    if raw is None:
        return None
    return uuid.UUID(raw.decode() if isinstance(raw, bytes) else raw)


async def first_time(redis: Redis, update_id: int) -> bool:
    """False when this update was seen already (Telegram re-sends until it gets a 200)."""
    return bool(await redis.set(f"{SEEN_KEY}{update_id}", 1, ex=SEEN_TTL_S, nx=True))


def deep_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start={code}"


# --- Reading updates ---------------------------------------------------------------------------


@dataclass
class Incoming:
    update_id: int
    tg_chat_id: int
    chat_type: str
    text: str
    username: str | None
    name: str | None
    # One of: text, voice, photo, document, other.
    kind: str
    file_id: str | None = None
    file_name: str | None = None
    file_size: int | None = None
    duration: int | None = None
    mime: str | None = None


def parse_update(update: dict) -> Incoming | None:
    """The parts of a Telegram update Nexus uses, or None for anything it ignores."""
    msg = update.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("chat"), dict):
        return None
    chat, sender = msg["chat"], msg.get("from") or {}
    if sender.get("is_bot"):
        return None
    name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or None
    base = dict(
        update_id=int(update.get("update_id", 0)),
        tg_chat_id=int(chat["id"]),
        chat_type=str(chat.get("type", "")),
        text=str(msg.get("text") or msg.get("caption") or ""),
        username=sender.get("username"),
        name=name,
    )
    if voice := msg.get("voice") or msg.get("audio"):
        return Incoming(
            **base,
            kind="voice",
            file_id=voice.get("file_id"),
            file_size=voice.get("file_size"),
            duration=voice.get("duration"),
            mime=voice.get("mime_type") or "audio/ogg",
            file_name=voice.get("file_name"),
        )
    if photos := msg.get("photo"):
        best = max(photos, key=lambda p: p.get("file_size") or p.get("width", 0))
        return Incoming(
            **base,
            kind="photo",
            file_id=best.get("file_id"),
            file_size=best.get("file_size"),
            mime="image/jpeg",
        )
    if doc := msg.get("document"):
        return Incoming(
            **base,
            kind="document",
            file_id=doc.get("file_id"),
            file_size=doc.get("file_size"),
            file_name=doc.get("file_name"),
            mime=doc.get("mime_type"),
        )
    if msg.get("text"):
        return Incoming(**base, kind="text")
    return Incoming(**base, kind="other")


# --- Formatting --------------------------------------------------------------------------------

_FENCE = re.compile(r"```[^\n`]*\n?(.*?)```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_ITALIC = re.compile(
    r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])"
)
_STRIKE = re.compile(r"~~(.+?)~~")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.M)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.M)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", re.M)


def to_html(markdown: str) -> str:
    """Model Markdown as Telegram HTML (b, i, s, code, pre, a). Code is kept verbatim; the rest
    is escaped first, so nothing in the text can open a tag of its own."""
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    text = markdown.replace("\r\n", "\n")
    text = _FENCE.sub(lambda m: keep(f"<pre>{html.escape(m.group(1).rstrip())}</pre>"), text)
    text = _INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = _LINK.sub(
        lambda m: keep(
            f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'
        ),
        text,
    )
    text = html.escape(text, quote=False)
    text = _TABLE_RULE.sub("", text)
    text = _HEADING.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = _STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = _BULLET.sub(lambda m: f"{m.group(1)}• ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text).strip()


def split(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Plain text in pieces under the limit, broken at paragraphs, then lines, then spaces."""
    parts: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        cut = max(rest.rfind("\n\n", 0, limit), rest.rfind("\n", 0, limit))
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return parts


def html_pieces(markdown: str) -> list[str]:
    """A long reply as several Telegram messages. Splitting happens on the Markdown, before the
    conversion, so no tag is ever cut in half."""
    return [to_html(piece) for piece in split(markdown)] or ["…"]


# --- Bot API -----------------------------------------------------------------------------------


class Bot:
    """The few Bot API calls Nexus makes. One instance per worker; pass a shared client."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http
        self.base = f"{settings.telegram_api_url.rstrip('/')}/bot{settings.telegram_bot_token}"
        self.files = (
            f"{settings.telegram_api_url.rstrip('/')}/file/bot{settings.telegram_bot_token}"
        )

    async def call(self, method: str, **params) -> dict | list | bool:
        try:
            resp = await self.http.post(f"{self.base}/{method}", json=params, timeout=30)
        except httpx.HTTPError as exc:
            raise TelegramError(f"{method}: {type(exc).__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code == 200 and body.get("ok"):
            return body.get("result")
        # 429 and 5xx are worth retrying; 400 and 403 (blocked by the user) are not. The
        # description never contains the token, but the URL would: don't log the URL.
        transient = resp.status_code == 429 or resp.status_code >= 500
        raise TelegramError(
            f"{method}: {resp.status_code} {body.get('description', '')}"[:300],
            transient=transient,
        )

    async def me(self) -> dict:
        return await self.call("getMe")  # type: ignore[return-value]

    async def set_webhook(self, url: str) -> None:
        await self.call(
            "setWebhook",
            url=url,
            secret_token=self.settings.telegram_webhook_secret,
            allowed_updates=ALLOWED_UPDATES,
            drop_pending_updates=False,
            max_connections=10,
        )

    async def send(self, chat_id: int, markdown: str) -> None:
        """Send a reply, in as many messages as it takes. Falls back to plain text when
        Telegram refuses the HTML (it is strict about nesting)."""
        for piece in split(markdown):
            try:
                await self.call(
                    "sendMessage",
                    chat_id=chat_id,
                    text=to_html(piece),
                    parse_mode="HTML",
                    link_preview_options={"is_disabled": True},
                )
            except TelegramError as exc:
                if exc.transient:
                    raise
                await self.call("sendMessage", chat_id=chat_id, text=piece)

    async def typing(self, chat_id: int, action: str = "typing") -> None:
        try:
            await self.call("sendChatAction", chat_id=chat_id, action=action)
        except TelegramError:
            pass  # Only a hint; never worth failing a reply over.

    async def download(self, file_id: str, max_bytes: int) -> tuple[bytes, str]:
        """A file's bytes and its path on Telegram's side (the path's suffix names its type)."""
        info = await self.call("getFile", file_id=file_id)
        path = str((info or {}).get("file_path", ""))  # type: ignore[union-attr]
        size = (info or {}).get("file_size") or 0  # type: ignore[union-attr]
        if not path:
            raise TelegramError("getFile: no path", transient=False)
        if size and size > max_bytes:
            raise TelegramError("too large", transient=False)
        try:
            resp = await self.http.get(f"{self.files}/{path}", timeout=60)
        except httpx.HTTPError as exc:
            raise TelegramError(f"download: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise TelegramError(f"download: {resp.status_code}", transient=resp.status_code >= 500)
        if len(resp.content) > max_bytes:
            raise TelegramError("too large", transient=False)
        return resp.content, path


async def bot_username(redis: Redis, bot: Bot) -> str | None:
    """The bot's @name, for deep links; asked of Telegram once a day."""
    key = "nexus:telegram:username"
    if cached := await redis.get(key):
        return cached.decode() if isinstance(cached, bytes) else cached
    try:
        me = await bot.me()
    except TelegramError as exc:
        log.warning("telegram.getme_failed", extra={"error": str(exc)})
        return None
    name = me.get("username")
    if name:
        await redis.set(key, name, ex=24 * 3600)
    return name


async def transcribe(
    settings: Settings, http: httpx.AsyncClient, api_key: str, audio: bytes, filename: str
) -> str:
    """Speech to text through LiteLLM (the nexus-transcribe alias), billed to the person's key."""
    try:
        resp = await http.post(
            f"{settings.litellm_base_url.rstrip('/')}/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key or 'unset'}"},
            data={"model": settings.transcribe_model},
            files={"file": (filename, audio, "application/octet-stream")},
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise TelegramError(f"transcribe: {type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise TelegramError(f"transcribe: {resp.status_code}", transient=resp.status_code >= 500)
    return str(resp.json().get("text", "")).strip()
