"""WebSocket event stream: one socket per browser tab, fed from the user's Redis channel so any
API replica can serve any user."""

import asyncio
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from nexus_api.sessions import user_for_token
from nexus_common.events import user_channel

log = logging.getLogger(__name__)
router = APIRouter()


def origin_allowed(websocket: WebSocket) -> bool:
    """Browsers send cookies on cross-site WebSocket handshakes, so check the Origin."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True  # Non-browser clients; the session cookie still has to be valid.
    allowed = websocket.app.state.settings.allowed_origin_list
    if origin.rstrip("/") in allowed:
        return True
    return urlsplit(origin).netloc == websocket.headers.get("host")


@router.websocket("/ws")
async def events(websocket: WebSocket):
    app = websocket.app
    if not origin_allowed(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    async with app.state.sessionmaker() as db:
        user = await user_for_token(
            db, websocket.cookies.get(app.state.settings.session_cookie_name)
        )
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    pubsub = app.state.redis.pubsub()
    await pubsub.subscribe(user_channel(user.id))

    async def forward() -> None:
        async for msg in pubsub.listen():
            if msg["type"] == "message":
                data = msg["data"]
                await websocket.send_text(data.decode() if isinstance(data, bytes) else data)

    async def receive() -> None:
        # The client only sends keep-alive pings; commands go through the REST API.
        while True:
            text = await websocket.receive_text()
            if text == "ping":
                await websocket.send_text('{"type":"pong"}')

    tasks = [asyncio.create_task(forward()), asyncio.create_task(receive())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                log.warning("WebSocket closed with error: %r", exc)
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
