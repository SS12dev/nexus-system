import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import httpx
import openai
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from sqlalchemy import select

from jarvis_common.events import user_channel
from jarvis_common.models import (
    ApiKey,
    Chat,
    Message,
    MessageRole,
    MessageStatus,
    RoleName,
    User,
    UserStatus,
)
from jarvis_orchestrator.turn import TurnDeps, run_turn


class ScriptedModel(BaseChatModel):
    """Streams a fixed reply word by word and records what it was sent."""

    reply: str = "Hello there, Shivam."
    error: Exception | None = None
    seen: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(self.reply))])

    async def _astream(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kw
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.seen.append(messages)
        if self.error:
            raise self.error
        words = self.reply.split(" ")
        for i, word in enumerate(words):
            text = word if i == len(words) - 1 else word + " "
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=text))
            if run_manager:
                await run_manager.on_llm_new_token(text, chunk=chunk)
            yield chunk
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                usage_metadata={"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
            )
        )


@pytest.fixture
async def setup(sessionmaker, settings, gateway, redis):
    """A user with a chat holding one question and a streaming placeholder for the reply."""
    from jarvis_api.users import create_user

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
            model="jarvis-mini",
            trace_id="t1",
        )
        db.add(reply)
        await db.commit()
        ids = (user.id, chat.id, reply.id)

    model = ScriptedModel(seen=[])
    factory_calls: list[dict] = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return model

    deps = TurnDeps(
        settings=settings,
        sessionmaker=sessionmaker,
        redis=redis,
        gateway=gateway,
        model_factory=factory,
    )
    return deps, model, factory_calls, ids


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
    assert types[0] == "jarvis.state" and events[0]["data"]["state"] == "thinking"
    assert events[1]["data"]["state"] == "speaking"
    deltas = [e["data"]["delta"] for e in events if e["type"] == "message.delta"]
    assert "".join(deltas) == "Hello there, Shivam."
    assert types[-2:] == ["message.completed", "jarvis.state"]
    assert events[-1]["data"]["state"] == "idle"

    async with sessionmaker() as db:
        msg = await db.get(Message, msg_id)
        assert msg.status == MessageStatus.complete
        assert msg.content == "Hello there, Shivam."
        assert (msg.input_tokens, msg.output_tokens) == (12, 4)
        key = await db.scalar(select(ApiKey).where(ApiKey.user_id == user_id))

    # The call used the user's own budgeted key and carries Langfuse trace metadata.
    call = factory_calls[0]
    assert call["api_key"] == key.secret and call["model"] == "jarvis-mini"
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
            model="jarvis-mini",
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
