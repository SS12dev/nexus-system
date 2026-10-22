import asyncio
import json
import uuid

import httpx
import openai
import pytest
from sqlalchemy import select

from nexus_common.events import user_channel
from nexus_common.models import (
    ApiKey,
    Chat,
    Message,
    MessageRole,
    MessageStatus,
    RoleName,
    User,
    UserStatus,
)
from nexus_orchestrator import turn
from nexus_orchestrator.turn import finished, run_turn, track_step


@pytest.fixture
async def setup(sessionmaker, deps, model):
    """A user with a chat holding one question and a streaming placeholder for the reply."""
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        user = await create_user(
            db,
            email="shivam@example.com",
            role=RoleName.owner,
            status=UserStatus.active,
            name="Shivam",
        )
        chat = Chat(user_id=user.id, title="t")
        db.add(chat)
        await db.flush()
        db.add(
            Message(
                chat_id=chat.id,
                role=MessageRole.user,
                content="Hi Jarvis",
                status=MessageStatus.complete,
            )
        )
        await db.flush()
        reply = Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="",
            status=MessageStatus.streaming,
            model="nexus-mini",
            trace_id="t1",
        )
        db.add(reply)
        await db.commit()
        ids = (user.id, chat.id, reply.id)

    return deps, model, deps.factory_calls, ids


async def run_and_collect(deps, redis, user_id, chat_id, msg_id) -> list[dict]:
    pubsub = redis.pubsub()
    await pubsub.subscribe(user_channel(user_id))
    await pubsub.get_message(timeout=1)  # subscribe confirmation
    await run_turn(deps, chat_id, msg_id)
    events = []
    while True:
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg is None:
            break
        events.append(json.loads(msg["data"]))
    await pubsub.aclose()
    return events


async def test_turn_streams_and_stores_reply(setup, redis, sessionmaker):
    deps, model, factory_calls, (user_id, chat_id, msg_id) = setup
    events = await run_and_collect(deps, redis, user_id, chat_id, msg_id)

    types = [e["type"] for e in events]
    assert types[0] == "assistant.state" and events[0]["data"]["state"] == "thinking"
    assert events[1]["data"]["state"] == "speaking"
    deltas = [e["data"]["delta"] for e in events if e["type"] == "message.delta"]
    assert "".join(deltas) == "Hello there, Shivam."
    assert types[-2:] == ["message.completed", "assistant.state"]
    assert events[-1]["data"]["state"] == "idle"

    async with sessionmaker() as db:
        msg = await db.get(Message, msg_id)
        assert msg.status == MessageStatus.complete
        assert msg.content == "Hello there, Shivam."
        assert (msg.input_tokens, msg.output_tokens) == (12, 4)
        key = await db.scalar(select(ApiKey).where(ApiKey.user_id == user_id))

    # The call used the user's own budgeted key and carries Langfuse trace metadata.
    call = factory_calls[0]
    assert call["api_key"] == key.secret and call["model"] == "nexus-mini"
    assert call["metadata"]["trace_id"] == "t1"
    assert call["metadata"]["session_id"] == str(chat_id)
    assert call["metadata"]["trace_user_id"] == str(user_id)
    # System prompt first, then the conversation so far (not the empty placeholder).
    sent = model.seen[0]
    assert "Jarvis" in sent[0].content and "Shivam" in sent[0].content
    assert [m.content for m in sent[1:]] == ["Hi Jarvis"]


async def test_key_is_minted_once(setup, redis, sessionmaker, gateway):
    deps, _model, _calls, (user_id, chat_id, msg_id) = setup
    await run_turn(deps, chat_id, msg_id)
    async with sessionmaker() as db:
        reply = Message(
            chat_id=chat_id,
            role=MessageRole.assistant,
            content="",
            status=MessageStatus.streaming,
            model="nexus-mini",
        )
        db.add(reply)
        await db.commit()
    await run_turn(deps, chat_id, reply.id)
    assert [c[0] for c in gateway.calls] == ["generate"]


async def test_budget_exhaustion_fails_the_reply_with_a_clear_message(setup, redis, sessionmaker):
    deps, model, _calls, (user_id, chat_id, msg_id) = setup
    request = httpx.Request("POST", "http://litellm/v1/chat/completions")
    model.error = openai.BadRequestError(
        "ExceededBudget: User is over budget",
        response=httpx.Response(400, request=request),
        body=None,
    )
    events = await run_and_collect(deps, redis, user_id, chat_id, msg_id)
    failed = next(e for e in events if e["type"] == "message.failed")
    assert "budget" in failed["data"]["message"]["error"]
    async with sessionmaker() as db:
        assert (await db.get(Message, msg_id)).status == MessageStatus.failed


async def test_finished_messages_are_not_rerun(setup, redis, sessionmaker):
    deps, model, _calls, (user_id, chat_id, msg_id) = setup
    await run_turn(deps, chat_id, msg_id)
    await run_turn(deps, chat_id, msg_id)
    assert len(model.seen) == 1


async def test_disabled_user_gets_no_answer(setup, redis, sessionmaker):
    deps, model, _calls, (user_id, chat_id, msg_id) = setup
    async with sessionmaker() as db:
        (await db.get(User, user_id)).status = UserStatus.disabled
        await db.commit()
    await run_turn(deps, chat_id, msg_id)
    assert model.seen == []
    async with sessionmaker() as db:
        msg = await db.get(Message, msg_id)
        assert msg.status == MessageStatus.failed and "not active" in msg.error


async def test_unknown_message_is_ignored(setup):
    deps, model, _calls, (_u, chat_id, _m) = setup
    await asyncio.wait_for(run_turn(deps, chat_id, uuid.uuid4()), 5)
    assert model.seen == []


async def ask(sessionmaker, chat_id, text: str) -> uuid.UUID:
    """Add a user message and a streaming reply placeholder; return the placeholder's id."""
    async with sessionmaker() as db:
        db.add(
            Message(
                chat_id=chat_id, role=MessageRole.user, content=text, status=MessageStatus.complete
            )
        )
        await db.flush()
        reply = Message(
            chat_id=chat_id,
            role=MessageRole.assistant,
            content="",
            status=MessageStatus.streaming,
            model="nexus-mini",
            trace_id="t2",
        )
        db.add(reply)
        await db.commit()
        return reply.id


RESEARCH = (
    "Can you help me prepare research gaps in the latest LLM training and fine-tuning for a "
    "paper on reasoning?"
)


async def test_router_hands_team_work_to_build_agent_system(setup, settings, sessionmaker):
    deps, model, factory_calls, (_u, chat_id, _m) = setup
    deps.settings = settings.model_copy(update={"route_model": "nexus-nano"})
    await run_turn(deps, chat_id, _m)  # "Hi Jarvis" is too short to route
    assert [c["model"] for c in factory_calls] == ["nexus-mini"]

    model.seen.clear()
    model.script = ['{"route": "team", "reason": "literature research"}', "On it."]
    reply_id = await ask(sessionmaker, chat_id, RESEARCH)
    await run_turn(deps, chat_id, reply_id)
    assert factory_calls[-1]["model"] == "nexus-nano"
    assert factory_calls[-1]["metadata"]["generation_name"] == "nexus.route"
    # The router saw the request; Nexus's first call was made to call build_agent_system.
    assert RESEARCH in model.seen[0][-1].content
    assert {"tool_choice": "build_agent_system"} in model.binds
    assert "build_agent_system" in model.tools


async def test_router_leaves_other_messages_to_the_assistant(setup, settings, sessionmaker):
    deps, model, _calls, (_u, chat_id, _m) = setup
    deps.settings = settings.model_copy(update={"route_model": "nexus-nano"})
    model.script = ['{"route": "self", "reason": "calendar question"}', "Two meetings."]
    reply_id = await ask(sessionmaker, chat_id, "What is on my calendar for the rest of this week?")
    await run_turn(deps, chat_id, reply_id)
    assert not any("tool_choice" in b for b in model.binds)
    async with sessionmaker() as db:
        assert (await db.get(Message, reply_id)).content == "Two meetings."


async def test_router_runs_while_the_context_is_gathered(
    setup, settings, sessionmaker, monkeypatch
):
    deps, model, _calls, (_u, chat_id, _m) = setup
    deps.settings = settings.model_copy(update={"route_model": "nexus-nano"})
    model.script = ['{"route": "self", "reason": "calendar question"}', "Two meetings."]
    real_assemble = turn.assemble
    routed_during_assembly = []

    async def slow_assemble(*args, **kwargs):
        # Give the router a moment; it should already be on its way.
        for _ in range(50):
            if model.seen:
                break
            await asyncio.sleep(0.01)
        routed_during_assembly.append(bool(model.seen))
        return await real_assemble(*args, **kwargs)

    monkeypatch.setattr(turn, "assemble", slow_assemble)
    reply_id = await ask(sessionmaker, chat_id, "What is on my calendar for the rest of this week?")
    await run_turn(deps, chat_id, reply_id)
    assert routed_during_assembly == [True]
    async with sessionmaker() as db:
        assert (await db.get(Message, reply_id)).content == "Two meetings."


async def test_router_failure_falls_back_to_the_assistant(setup, settings, sessionmaker):
    deps, model, _calls, (_u, chat_id, _m) = setup
    deps.settings = settings.model_copy(update={"route_model": "nexus-nano"})
    model.script = ["not json at all", "Here you go."]
    reply_id = await ask(sessionmaker, chat_id, RESEARCH)
    await run_turn(deps, chat_id, reply_id)
    assert not any("tool_choice" in b for b in model.binds)
    async with sessionmaker() as db:
        assert (await db.get(Message, reply_id)).content == "Here you go."


def test_track_step_pairs_calls_with_their_outcome():
    steps: list[dict] = []
    track_step(steps, "tool.call", {"name": "web_search", "call_id": "a", "query": "x" * 500})
    track_step(steps, "tool.call", {"name": "run_code", "call_id": "b", "code": ["not kept"]})
    track_step(steps, "message.delta", {"delta": "hi"})
    track_step(steps, "tool.done", {"name": "run_code", "call_id": "b", "ok": False, "ms": 9})
    track_step(steps, "tool.done", {"name": "web_search", "call_id": "a", "ok": True, "ms": 4})
    # A tool that never announced itself still shows up once it ends.
    track_step(steps, "tool.done", {"name": "skills_load", "call_id": "c", "ok": True, "ms": 1})
    assert [(s["id"], s["status"]) for s in steps] == [
        ("a", "done"),
        ("b", "failed"),
        ("c", "done"),
    ]
    assert len(steps[0]["args"]["query"]) == 200 and steps[1]["args"] == {}
    # Steps still open when the turn ends never reported back.
    track_step(steps, "tool.call", {"name": "web_fetch", "call_id": "d"})
    assert finished(steps)[-1]["status"] == "failed"
