import uuid

import httpx
import pytest

from nexus_api.main import create_app


class RecordingQueue:
    def __init__(self):
        self.turns: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.parses: list[uuid.UUID] = []
        self.profiles: list[uuid.UUID] = []
        self.runs: list[tuple[str, uuid.UUID]] = []
        self.deploys: list[uuid.UUID] = []
        self.link_previews: list[uuid.UUID] = []
        self.access_notices: list[tuple[str, uuid.UUID]] = []
        self.reviews: list[uuid.UUID] = []
        self.proposal_prs: list[uuid.UUID] = []
        self.recall_checks: list[uuid.UUID] = []
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

    async def enqueue_deploy(self, project_id):
        self.deploys.append(project_id)

    async def enqueue_link_preview(self, link_id):
        self.link_previews.append(link_id)

    async def enqueue_access_notice(self, kind, request_id):
        if self.fail:
            raise ConnectionError("redis down")
        self.access_notices.append((kind, request_id))

    async def enqueue_review(self, review_id):
        self.reviews.append(review_id)

    async def enqueue_proposal_pr(self, proposal_id):
        self.proposal_prs.append(proposal_id)

    async def enqueue_recall_check(self, eval_id):
        self.recall_checks.append(eval_id)


@pytest.fixture(autouse=True)
async def clean_counters(redis):
    """Rate-limit windows and caches would otherwise carry over between tests."""
    yield
    keys = [k async for k in redis.scan_iter("nexus:*")]
    if keys:
        await redis.delete(*keys)


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
