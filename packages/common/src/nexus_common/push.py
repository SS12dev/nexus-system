"""Web push notifications to people's phones and browsers (the installed PWA).

Each browser that allows notifications gives a subscription (an endpoint at its push service,
plus two keys); `push_subscriptions` keeps one row per endpoint. Messages are encrypted for the
browser and signed with Nexus's VAPID key (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY), so a push
service accepts them without any account. A subscription the push service says is gone (404 or
410) is deleted.

Make a key pair once with `uv run python -m nexus_common.push`.
"""

import asyncio
import base64
import json
import logging
import uuid
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import PushSubscription, utcnow
from nexus_common.settings import Settings

log = logging.getLogger(__name__)

# Push services keep a message this long while the device is offline.
TTL_S = 12 * 3600
BODY_CHARS = 180


@dataclass
class Notice:
    title: str
    body: str
    # Where tapping it opens, relative to the app ("/c/<chat id>").
    url: str = "/"
    # Notices with the same tag replace each other on the device instead of piling up.
    tag: str | None = None

    def payload(self) -> str:
        body = " ".join(self.body.split())
        if len(body) > BODY_CHARS:
            body = body[: BODY_CHARS - 1].rsplit(" ", 1)[0] + "…"
        return json.dumps({"title": self.title, "body": body, "url": self.url, "tag": self.tag})


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_keys() -> tuple[str, str]:
    """(public, private) VAPID keys: the public one is the browser's applicationServerKey, the
    private one the raw 32-byte P-256 key; both base64url without padding."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64url(public), b64url(private)


def _send(settings: Settings, sub: dict, data: str) -> int | None:
    """Send one message; returns the push service's status when it refused, else None."""
    try:
        webpush(
            subscription_info=sub,
            data=data,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject or f"mailto:{settings.smtp_from}"},
            ttl=TTL_S,
            timeout=15,
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else 0
        log.warning("push.refused %s: %s", status, str(exc)[:200])
        return status
    return None


async def send(db: AsyncSession, settings: Settings, user_id: uuid.UUID, notice: Notice) -> int:
    """Send `notice` to every device the person subscribed. Returns how many got it."""
    if not settings.push_enabled:
        return 0
    rows = (
        await db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id))
    ).all()
    data, sent, gone = notice.payload(), 0, []
    for row in rows:
        sub = {"endpoint": row.endpoint, "keys": {"p256dh": row.p256dh, "auth": row.auth}}
        try:
            status = await asyncio.to_thread(_send, settings, sub, data)
        except Exception:
            log.warning("push.failed", exc_info=True)
            continue
        if status in (404, 410):
            gone.append(row.id)
        elif status is None:
            row.last_used_at = utcnow()
            sent += 1
    if gone:
        await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
    await db.commit()
    return sent


if __name__ == "__main__":
    public, private = generate_keys()
    print(f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}")
