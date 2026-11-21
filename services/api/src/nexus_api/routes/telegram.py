"""Telegram: link a Telegram chat to your account, and receive the bot's updates
(nexus_common/telegram.py has the whole flow)."""

import hmac
import logging
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import delete

from nexus_api.deps import CurrentUser, DbDep, MemberUser, QueueDep, RedisDep, SettingsDep
from nexus_api.ratelimit import hit, too_many
from nexus_common import telegram
from nexus_common.models import AuditLog, TelegramLink, utcnow

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])

# A Telegram update is a few KB; a photo's is still small (the bytes are fetched separately).
MAX_UPDATE_BYTES = 256 * 1024
LINKS_PER_HOUR = 20


class TelegramLinkOut(BaseModel):
    tg_username: str | None
    tg_name: str | None
    chat_id: str | None
    linked_at: datetime
    last_message_at: datetime | None


class TelegramOut(BaseModel):
    # Whether the owner has set Telegram up on this Nexus at all.
    enabled: bool
    bot_username: str | None
    link: TelegramLinkOut | None


class LinkCodeOut(BaseModel):
    code: str
    url: str | None
    expires_at: datetime


async def _bot_username(settings, redis) -> str | None:
    async with httpx.AsyncClient() as http:
        return await telegram.bot_username(redis, telegram.Bot(settings, http))


def _link_out(link: TelegramLink | None) -> TelegramLinkOut | None:
    if link is None:
        return None
    return TelegramLinkOut(
        tg_username=link.tg_username,
        tg_name=link.tg_name,
        chat_id=str(link.chat_id) if link.chat_id else None,
        linked_at=link.linked_at,
        last_message_at=link.last_message_at,
    )


@router.get("", response_model=TelegramOut)
async def telegram_status(user: CurrentUser, db: DbDep, settings: SettingsDep, redis: RedisDep):
    link = await db.get(TelegramLink, user.id)
    username = await _bot_username(settings, redis) if settings.telegram_enabled else None
    return TelegramOut(
        enabled=settings.telegram_enabled, bot_username=username, link=_link_out(link)
    )


@router.post("/link", response_model=LinkCodeOut, status_code=status.HTTP_201_CREATED)
async def start_link(request: Request, user: MemberUser, settings: SettingsDep, redis: RedisDep):
    """A one-time code (ten minutes) and a t.me link that sends it to the bot."""
    if not settings.telegram_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Telegram isn't set up on this Nexus yet.")
    if wait := await hit(redis, "telegram-link", str(user.id), LINKS_PER_HOUR, 3600):
        raise too_many(wait, "link codes")
    code = await telegram.new_link_code(redis, user.id)
    username = await _bot_username(settings, redis)
    return LinkCodeOut(
        code=code,
        url=telegram.deep_link(username, code) if username else None,
        expires_at=utcnow() + timedelta(seconds=telegram.LINK_CODE_TTL_S),
    )


@router.delete("/link", status_code=status.HTTP_204_NO_CONTENT)
async def unlink(user: CurrentUser, db: DbDep):
    removed = await db.execute(delete(TelegramLink).where(TelegramLink.user_id == user.id))
    if removed.rowcount:
        db.add(AuditLog(actor_id=user.id, action="telegram.unlinked", target=str(user.id)))
        log.info("telegram.unlinked")
    await db.commit()


@router.post("/webhook", include_in_schema=False)
async def webhook(
    request: Request,
    settings: SettingsDep,
    redis: RedisDep,
    queue: QueueDep,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """Where Telegram posts updates. Answers at once; the orchestrator does the work."""
    if not settings.telegram_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        x_telegram_bot_api_secret_token.encode(), settings.telegram_webhook_secret.encode()
    ):
        log.warning("telegram.bad_secret")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    body = await request.body()
    if len(body) > MAX_UPDATE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE)
    try:
        update = await request.json()
        update_id = int(update["update_id"])
    except (ValueError, KeyError, TypeError):
        # Malformed: say OK so Telegram doesn't keep re-sending it.
        log.warning("telegram.bad_update")
        return Response(status_code=status.HTTP_200_OK)
    if not await telegram.first_time(redis, update_id):
        return Response(status_code=status.HTTP_200_OK)
    await queue.enqueue_telegram(update)
    log.info("telegram.update_queued", extra={"update_id": update_id})
    return Response(status_code=status.HTTP_200_OK)
