"""Self-review: what it gathers (counts and anonymous topics only), the proposals it keeps, the
schedule, and the pull request an approved code proposal becomes."""

import base64
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from nexus_common.memory import get_profile
from nexus_common.models import (
    Chat,
    ImprovementReview,
    Message,
    MessageRole,
    MessageStatus,
    Proposal,
    ProposalKind,
    ProposalStatus,
    ReviewStatus,
    RoleName,
    UserStatus,
    utcnow,
)
from nexus_orchestrator import improve
from nexus_orchestrator.github import GitHubApp
from nexus_orchestrator.turn import TurnDeps


class JsonModel(BaseChatModel):
    """Answers each call with the next reply for its generation (nexus.topics or
    nexus.propose), and records the prompts it was sent."""

    name_: str = ""
    box: tuple = ({}, [])

    @property
    def _llm_type(self) -> str:
        return "json-scripted"

    def bind(self, **kwargs):
        return self

    def _generate(self, *a, **k):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        replies, seen = self.box
        seen.append((self.name_, messages[-1].content))
        reply = replies[self.name_]
        if callable(reply):
            reply = reply(messages[-1].content)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(json.dumps(reply)))])


@pytest.fixture
def replies() -> dict:
    return {}


@pytest.fixture
def prompts() -> list:
    return []


@pytest.fixture
def ideps(settings, sessionmaker, redis, gateway, embedder, enqueued, replies, prompts):
    def factory(*, model, api_key, metadata):
        name = metadata["generation_name"].removeprefix("nexus.")
        return JsonModel(name_=name, box=(replies, prompts))

    return TurnDeps(
        settings=settings.model_copy(update={"improve_min_count": 2}),
        sessionmaker=sessionmaker,
        redis=redis,
        gateway=gateway,
        model_factory=factory,
        embedder=embedder,
        enqueue=enqueued,
    )


async def _user(sessionmaker, email, role=RoleName.member, memory=True):
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        user = await create_user(
            db, email=email, role=role, status=UserStatus.active, name=email.split("@")[0]
        )
        chat = Chat(user_id=user.id, title="t")
        db.add(chat)
        (await get_profile(db, user.id)).memory_enabled = memory
        await db.commit()
        return user, chat.id


async def _turn(sessionmaker, chat_id, ask, answer="Done.", *, failed=False, steps=None):
    async with sessionmaker() as db:
        now = utcnow() - timedelta(hours=1)
        db.add(
            Message(
                chat_id=chat_id,
                role=MessageRole.user,
                content=ask,
                status=MessageStatus.complete,
                created_at=now,
            )
        )
        db.add(
            Message(
                chat_id=chat_id,
                role=MessageRole.assistant,
                content=answer,
                status=MessageStatus.failed if failed else MessageStatus.complete,
                error="Timed out after 120 s talking to 'api.example.com' for shivam@example.com"
                if failed
                else None,
                steps=steps,
                created_at=now + timedelta(seconds=5),
            )
        )
        await db.commit()


async def _review(sessionmaker) -> ImprovementReview:
    async with sessionmaker() as db:
        r = ImprovementReview(
            status=ReviewStatus.running,
            trigger="manual",
            window_start=utcnow() - timedelta(days=7),
            window_end=utcnow(),
            findings={},
            proposals=0,
        )
        db.add(r)
        await db.commit()
        return r


def _topics(prompt: str) -> dict:
    """Label every request in a batch by a word in it."""
    items = []
    for line in prompt.splitlines():
        if line.startswith("[") and "REQUEST:" in line:
            i = int(line[1 : line.index("]")])
            topic = "trip planning" if "trip" in line else "python debugging"
            items.append({"i": i, "topic": topic, "gap": None})
        if "REPLY STARTS: I can't read your calendar" in line:
            items[-1]["gap"] = "read calendar events"
    return {"items": items}


GOOD_SKILL = {
    "kind": "skill",
    "title": "Add a trip planning skill",
    "why": "Trip planning came up 3 times with no skill for it.",
    "evidence": ["trip planning: 3 requests"],
    "impact": "high",
    "effort": "low",
    "skill": {
        "name": "trip-planning",
        "description": "Plan a trip: options, budget and a day-by-day plan.",
        "body": "1. Ask for dates and budget.\n2. Compare options.\n3. Give a day-by-day plan.",
    },
}


async def test_a_review_counts_patterns_and_keeps_valid_proposals(
    ideps, sessionmaker, replies, prompts
):
    _, owner_chat = await _user(sessionmaker, "shivam@example.com", RoleName.owner)
    _, chat = await _user(sessionmaker, "priya@example.com")
    _, private = await _user(sessionmaker, "quiet@example.com", memory=False)
    for city in ("Goa", "Kyoto", "Lisbon"):
        await _turn(sessionmaker, chat, f"Plan a trip to {city} for my anniversary")
    await _turn(
        sessionmaker,
        owner_chat,
        "Why does my python script crash on import?",
        steps=[{"name": "web_fetch", "status": "failed", "ms": 3000}],
    )
    await _turn(
        sessionmaker, owner_chat, "Fix this python traceback please", answer="", failed=True
    )
    await _turn(sessionmaker, private, "Plan a trip to the secret island please")

    replies["topics"] = _topics
    replies["propose"] = {
        "proposals": [
            GOOD_SKILL,
            dict(GOOD_SKILL, title="Add a trip planning skill!"),  # same idea again
            {
                "kind": "instruction",
                "title": "Bad role",
                "instruction": {"layer": "role", "role": "astronaut", "text": "Fly."},
            },
            {
                "kind": "setting",
                "title": "Let anyone sign up",
                "setting": {"name": "signup_policy", "value": "open"},
            },
            {
                "kind": "setting",
                "title": "Cheaper routing model",
                "why": "Routing is costly.",
                "setting": {"name": "route_model", "value": "nexus-nano"},
            },
            {
                "kind": "tool",
                "title": "Add a calendar invite reader",
                "why": "Gaps.",
                "change": {
                    "spec": "A read-only tool that lists calendar invites with their "
                    "organiser and time, for the scheduling skill.",
                    "files": ["services/orchestrator/src/nexus_orchestrator/google_tools.py"],
                },
            },
        ]
    }
    review = await _review(sessionmaker)
    await improve.run_review(ideps, review.id)

    async with sessionmaker() as db:
        review = await db.get(ImprovementReview, review.id)
        props = (await db.scalars(select(Proposal).order_by(Proposal.created_at))).all()
    assert review.status == ReviewStatus.done, review.error
    f = review.findings
    assert f["activity"]["turns"] == 6 and f["activity"]["failed_turns"] == 1
    assert f["activity"]["people"] == 3
    # The person with memory off isn't read.
    assert f["requests_read"] == 5
    assert {t["topic"]: t["count"] for t in f["topics"]} == {
        "trip planning": 3,
        "python debugging": 2,
    }
    assert f["tools"][0] == {"tool": "web_fetch", "calls": 1, "failed": 1, "avg_ms": 3000}
    assert (
        f["failures"][0]["pattern"] == "turn: Timed out after <n> s talking to <text> for <email>"
    )
    blob = json.dumps(f)
    for private_bit in ("Goa", "anniversary", "priya", "shivam@", "secret island"):
        assert private_bit not in blob
    # Proposals see what exists and what came before.
    propose_prompt = next(p for n, p in prompts if n == "propose")
    # The proposal model works from the architecture brief, not guesses about the code.
    assert "HOW NEXUS IS BUILT:\n# Nexus: how it is built" in propose_prompt
    assert "AsyncPostgresSaver" in propose_prompt
    assert "web_search" in propose_prompt and "EARLIER" in propose_prompt
    assert "signup_policy" not in propose_prompt

    kept = {(p.kind, p.title) for p in props}
    assert kept == {
        (ProposalKind.skill, "Add a trip planning skill"),
        (ProposalKind.setting, "Cheaper routing model"),
        (ProposalKind.tool, "Add a calendar invite reader"),
    }
    assert review.proposals == 3
    assert all(p.status == ProposalStatus.pending for p in props)

    # A second review doesn't propose the same things again.
    review2 = await _review(sessionmaker)
    await improve.run_review(ideps, review2.id)
    async with sessionmaker() as db:
        assert (await db.get(ImprovementReview, review2.id)).proposals == 0


async def test_topics_are_off_when_requests_may_not_be_read(ideps, sessionmaker, replies, prompts):
    _, chat = await _user(sessionmaker, "shivam@example.com", RoleName.owner)
    await _turn(sessionmaker, chat, "Plan a trip to Goa please")
    replies["propose"] = {"proposals": []}
    ideps.settings = ideps.settings.model_copy(update={"improve_read_requests": False})
    review = await _review(sessionmaker)
    await improve.run_review(ideps, review.id)
    assert [n for n, _ in prompts] == ["propose"]
    async with sessionmaker() as db:
        r = await db.get(ImprovementReview, review.id)
    assert r.status == ReviewStatus.done and "topics" not in r.findings


async def test_a_quiet_week_asks_no_model(ideps, sessionmaker, prompts):
    await _user(sessionmaker, "shivam@example.com", RoleName.owner)
    review = await _review(sessionmaker)
    await improve.run_review(ideps, review.id)
    assert prompts == []
    async with sessionmaker() as db:
        assert (await db.get(ImprovementReview, review.id)).status == ReviewStatus.done


async def test_the_schedule_waits_for_the_interval(ideps, sessionmaker, replies):
    await _user(sessionmaker, "shivam@example.com", RoleName.owner)
    first = await improve.scheduled(ideps)
    assert first is not None
    assert await improve.scheduled(ideps) is None
    ideps.settings = ideps.settings.model_copy(update={"improve_every_days": 0})
    async with sessionmaker() as db:
        r = await db.get(ImprovementReview, first)
        r.created_at = utcnow() - timedelta(days=30)
        await db.commit()
    assert await improve.scheduled(ideps) is None
    ideps.settings = ideps.settings.model_copy(update={"improve_every_days": 7})
    assert await improve.scheduled(ideps) is not None


def test_scrub_keeps_the_pattern_not_the_data():
    assert improve.scrub("File 'Q3 numbers.xlsx' failed: 404 at https://x.io/a?b=1") == (
        "File <text> failed: <n> at <url>"
    )
    assert improve.scrub("") == "(no message)"


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_B64 = base64.b64encode(
    KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
).decode()


class FakeRepoHost:
    def __init__(self, installed=True):
        self.installed = installed
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        self.calls.append((method, path, body))
        repo = "/repos/SS12dev/nexus-system"
        if path == f"{repo}/installation":
            if not self.installed:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"id": 999})
        if path == "/app/installations/999/access_tokens":
            assert body["repositories"] == ["nexus-system"]
            return httpx.Response(
                201, json={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
            )
        assert request.headers["authorization"] == "Bearer ghs_x"
        if path == f"{repo}/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "abc"}})
        if path == f"{repo}/git/refs":
            return httpx.Response(201, json={})
        if path.startswith(f"{repo}/contents/"):
            return httpx.Response(404 if method == "GET" else 201, json={})
        if path == f"{repo}/pulls" and method == "POST":
            assert body["draft"] is True
            return httpx.Response(
                201, json={"html_url": "https://github.com/SS12dev/nexus-system/pull/77"}
            )
        return httpx.Response(500, json={"message": f"unexpected {method} {path}"})


async def _approved_tool(sessionmaker) -> Proposal:
    async with sessionmaker() as db:
        p = Proposal(
            kind=ProposalKind.tool,
            status=ProposalStatus.applying,
            title="Add a calendar invite reader",
            summary="People keep asking about invites.",
            evidence=["read calendar events: 4 requests"],
            payload={"spec": "A read-only tool.", "files": ["google_tools.py"]},
            fingerprint=uuid.uuid4().hex,
            result={},
        )
        db.add(p)
        await db.commit()
        return p


def _gh_deps(ideps, host):
    settings = ideps.settings.model_copy(
        update={
            "github_apps_org": "servhub-apps",
            "github_app_id": "1",
            "github_app_installation_id": "2",
            "github_app_private_key_b64": KEY_B64,
            "github_api_url": "https://api.github.test",
            "improve_repo": "SS12dev/nexus-system",
        }
    )
    ideps.settings = settings
    ideps.github = GitHubApp(settings, httpx.AsyncClient(transport=httpx.MockTransport(host)))
    return ideps


async def test_an_approved_code_proposal_becomes_a_draft_pull_request(ideps, sessionmaker):
    host = FakeRepoHost()
    deps = _gh_deps(ideps, host)
    p = await _approved_tool(sessionmaker)
    await improve.open_pr(deps, p.id)
    async with sessionmaker() as db:
        p = await db.get(Proposal, p.id)
    assert p.status == ProposalStatus.applied
    assert p.result["pr_url"].endswith("/pull/77")
    assert p.result["branch"].startswith("nexus/proposal-add-a-calendar-invite-reader-")
    put = next(b for m, path, b in host.calls if m == "PUT")
    spec = base64.b64decode(put["content"]).decode()
    assert spec.startswith("# Add a calendar invite reader") and "A read-only tool." in spec
    assert "Read `docs/INDEX.md`" in spec


async def test_a_pull_request_that_cant_be_opened_says_why(ideps, sessionmaker):
    deps = _gh_deps(ideps, FakeRepoHost(installed=False))
    p = await _approved_tool(sessionmaker)
    await improve.open_pr(deps, p.id)
    async with sessionmaker() as db:
        p = await db.get(Proposal, p.id)
    assert p.status == ProposalStatus.failed
    assert "isn't installed on SS12dev/nexus-system" in p.error
