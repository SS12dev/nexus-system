"""Sending the morning brief (nexus_common/brief.py) at each person's chosen time.

Every five minutes `send_due` looks for briefs that are due in the person's time zone. A brief
is claimed before it is built (last_sent_on is set in the same UPDATE that checks it), so two
workers, or a slow run overlapping the next, never send it twice. Then it goes out:

- a notice in the bell, pushed to their phone: the greeting and a one-line day, with no event
  titles or amounts, since a lock screen is public;
- the whole brief to Telegram when it's linked, with the Money lines only if they said so.

`send_now` is the Brief page's "Send me one now", to try the channels; it doesn't count as the
day's brief. Logs carry ids and counts, never the brief's contents.
"""

import contextlib
import logging
import uuid
from datetime import datetime

import httpx
from sqlalchemy import or_, select, update

from nexus_common import brief
from nexus_common.models import (
    BriefSettings,
    NotificationKind,
    TelegramLink,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.notices import notify
from nexus_common.organiser import local_today
from nexus_common.telegram import Bot, TelegramError
from nexus_orchestrator.turn import TurnDeps

log = logging.getLogger(__name__)

URL = "/brief"


@contextlib.asynccontextmanager
async def _http(deps: TurnDeps):
    if deps.google_http is not None:
        yield deps.google_http
        return
    async with httpx.AsyncClient(timeout=20) as http:
        yield http


async def deliver(deps: TurnDeps, user_id: uuid.UUID, now: datetime | None = None) -> dict:
    """Build one person's brief and send it on their channels. Returns where it went."""
    now = now or utcnow()
    sent = {"notice": False, "telegram": False}
    async with deps.sessionmaker() as db, _http(deps) as http:
        user = await db.get(User, user_id)
        if user is None or user.status != UserStatus.active:
            return sent
        prefs = await brief.settings_for(db, user)
        b = await brief.build(db, deps.settings, user, prefs, http=http, redis=deps.redis, now=now)
        link = await db.get(TelegramLink, user.id)
        if prefs.push or not (prefs.telegram and link):
            title, body = brief.notice(b)
            await notify(
                db, deps.redis, deps.settings, user.id, NotificationKind.reminder, title, body,
                url=URL, send_push=bool(prefs.push), tag=f"brief-{b['date']}",
            )  # fmt: skip
            sent["notice"] = True
        if prefs.telegram and link is not None and deps.settings.telegram_enabled:
            url = deps.settings.nexus_url.rstrip("/") + URL
            text = brief.as_text(b, with_money=bool(prefs.telegram_money), url=url)
            try:
                await Bot(deps.settings, http).send(link.tg_chat_id, text)
                sent["telegram"] = True
            except TelegramError as exc:
                log.warning(
                    "brief.telegram_failed",
                    extra={"user_id": str(user.id), "error": str(exc)[:120]},
                )
        await db.commit()
    log.info(
        "brief.sent",
        extra={
            "user_id": str(user_id),
            **sent,
            "events": len((b.get("calendar") or {}).get("events") or []),
            "failed_parts": sorted(b["errors"]),
        },
    )
    return sent


async def send_due(deps: TurnDeps, now: datetime | None = None) -> int:
    """Send every brief that is due now. Runs every five minutes."""
    now = now or utcnow()
    async with deps.sessionmaker() as db:
        rows = (
            await db.execute(
                select(BriefSettings, User)
                .join(User, User.id == BriefSettings.user_id)
                .where(BriefSettings.enabled, User.status == UserStatus.active)
            )
        ).all()
        due = []
        for prefs, user in rows:
            if not brief.due_now(prefs, user, now):
                continue
            today = local_today(user, now)
            claimed = await db.execute(
                update(BriefSettings)
                .where(
                    BriefSettings.user_id == user.id,
                    or_(BriefSettings.last_sent_on.is_(None), BriefSettings.last_sent_on != today),
                )
                .values(last_sent_on=today)
            )
            await db.commit()
            if claimed.rowcount:
                due.append(user.id)
    sent = 0
    for user_id in due:
        try:
            await deliver(deps, user_id, now)
            sent += 1
        except Exception:
            log.exception("brief.failed", extra={"user_id": str(user_id)})
    return sent
