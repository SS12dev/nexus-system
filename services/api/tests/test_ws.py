import json
import uuid

import pytest
from redis import Redis
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nexus_api.main import create_app
from nexus_common.events import Event, user_channel


@pytest.fixture
def ws_client(settings, engine, queue, gateway):
    # TestClient runs the app (and its lifespan) on its own event loop in a thread.
    with TestClient(create_app(settings, queue=queue), base_url="http://testserver") as client:
        yield client


def sign_in(client: TestClient, settings, email="owner@example.com") -> str:
    resp = client.post("/auth/dev-login", json={"email": email, "token": settings.dev_login_token})
    assert resp.status_code == 200
    return resp.json()["id"]


def test_events_reach_the_signed_in_user(ws_client, settings):
    user_id = sign_in(ws_client, settings)
    redis = Redis.from_url(settings.redis_url)
    with ws_client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
        ws.send_text("ping")
        assert json.loads(ws.receive_text()) == {"type": "pong"}
        chat_id = uuid.uuid4()
        event = Event(type="message.delta", chat_id=chat_id, data={"delta": "Hi"})
        redis.publish(user_channel(user_id), event.model_dump_json())
        got = json.loads(ws.receive_text())
        assert got["type"] == "message.delta" and got["data"] == {"delta": "Hi"}
        assert got["chat_id"] == str(chat_id)
    redis.close()


def test_anonymous_socket_is_refused(ws_client):
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect("/ws") as ws:
            ws.receive_text()


def test_cross_site_origin_is_refused(ws_client, settings):
    sign_in(ws_client, settings)
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
