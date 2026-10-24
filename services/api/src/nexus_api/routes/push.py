"""Web push: the browser's subscription for this device, and a test notification. Sending
lives in nexus_common/push.py, used by the orchestrator for routines and approvals."""

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import delete, func, select

from nexus_api.deps import CurrentUser, DbDep, SettingsDep
from nexus_api.schemas import PushEndpointIn, PushStateOut, PushSubscriptionIn
from nexus_common import push
from nexus_common.models import PushSubscription, utcnow

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("", response_model=PushStateOut)
async def state(user: CurrentUser, db: DbDep, settings: SettingsDep) -> PushStateOut:
    devices = await db.scalar(select(func.count()).where(PushSubscription.user_id == user.id))
    return PushStateOut(
        enabled=settings.push_enabled,
        public_key=settings.vapid_public_key if settings.push_enabled else None,
        devices=devices or 0,
    )


@router.post("/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe(
    body: PushSubscriptionIn, request: Request, user: CurrentUser, db: DbDep, settings: SettingsDep
) -> None:
    """Keep this browser's subscription. The same endpoint signed in as someone else moves to
    them: a shared device notifies whoever used it last."""
    if not settings.push_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Notifications aren't set up on this Nexus")
    row = await db.scalar(
        select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
    )
    if row is None:
        row = PushSubscription(endpoint=body.endpoint, created_at=utcnow())
        db.add(row)
    row.user_id = user.id
    row.p256dh, row.auth = body.keys.p256dh, body.keys.auth
    row.user_agent = (request.headers.get("user-agent") or "")[:300] or None
    await db.commit()


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(body: PushEndpointIn, user: CurrentUser, db: DbDep) -> None:
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id
        )
    )
    await db.commit()


@router.post("/test")
async def test(user: CurrentUser, db: DbDep, settings: SettingsDep) -> dict:
    """Send a notification to every device of the person's, to check it arrives."""
    if not settings.push_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Notifications aren't set up on this Nexus")
    sent = await push.send(
        db,
        settings,
        user.id,
        push.Notice(
            title=f"{user.assistant} says hi",
            body="Notifications work on this device.",
            url="/settings",
            tag="test",
        ),
    )
    return {"sent": sent}
