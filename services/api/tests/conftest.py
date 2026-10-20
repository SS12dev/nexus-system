import uuid

import httpx
import pytest

from jarvis_api.main import create_app


class RecordingQueue:
    def __init__(self):
        self.turns: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.parses: list[uuid.UUID] = []
        self.profiles: list[uuid.UUID] = []
        self.runs: list[tuple[str, uuid.UUID]] = []
        self.fail = False

    async def enqueue_turn(self, chat_id, assistant_message_id):
        if self.fail:
            raise ConnectionError("redis down")
        self.turns.append((chat_id, assistant_message_id))

    async def enqueue_parse(self, file_id):
        if self.fail:
            raise ConnectionError("redis down")
        self.parses.append(file_id)

    async def enqueue_profile_rebuild(self, user_id):
        self.profiles.append(user_id)

    async def enqueue_run(self, function, run_id):
        self.runs.append((function, run_id))


@pytest.fixture
def queue():
    return RecordingQueue()


@pytest.fixture
async def app(settings, engine, gateway, queue, storage, fake_embedder):
    app = create_app(settings, queue=queue, storage=storage, embedder=fake_embedder)
    async with app.router.lifespan_context(app):
        app.state.gateway = gateway
        yield app


@pytest.fixture
async def client_for(app, settings):
    """Make a client signed in as `email` through dev login (or anonymous if email is None)."""
    clients: list[httpx.AsyncClient] = []

    async def make(email: str | None = None, expect: int = 200) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        clients.append(client)
        if email:
            resp = await client.post(
                "/auth/dev-login", json={"email": email, "token": settings.dev_login_token}
            )
            assert resp.status_code == expect, resp.text
        return client

    yield make
    for client in clients:
        await client.aclose()


@pytest.fixture
async def owner(client_for):
    return await client_for("owner@example.com")


@pytest.fixture
def invite():
    async def make(admin_client, email: str, role: str = "member", **extra) -> dict:
        resp = await admin_client.post(
            "/api/admin/users", json={"email": email, "role": role, **extra}
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return make
