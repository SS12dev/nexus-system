import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_common.events import Event, publish
from jarvis_common.litellm import GatewayError, LiteLLMAdmin, ensure_user_key
from jarvis_common.models import (
    Chat,
    Message,
    MessageRole,
    MessageStatus,
    User,
    UserStatus,
    utcnow,
)
from jarvis_common.settings import Settings
from jarvis_orchestrator.graph import build_graph
from jarvis_orchestrator.prompts import system_prompt

log = logging.getLogger(__name__)

ModelFactory = Callable[..., BaseChatModel]


@dataclass
class TurnDeps:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    gateway: LiteLLMAdmin
    model_factory: ModelFactory


class TurnFailed(Exception):
    """A failure whose message is safe to show the user."""


def message_payload(msg: Message) -> dict:
    return {
        "id": str(msg.id),
        "chat_id": str(msg.chat_id),
        "role": msg.role,
        "content": msg.content,
        "status": msg.status,
        "model": msg.model,
        "input_tokens": msg.input_tokens,
        "output_tokens": msg.output_tokens,
        "trace_id": msg.trace_id,
        "error": msg.error,
        "created_at": msg.created_at.isoformat(),
    }


def to_langchain(messages: list[Message]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        if m.role == MessageRole.user:
            out.append(HumanMessage(m.content))
        elif m.role == MessageRole.assistant:
            out.append(AIMessage(m.content))
    return out


def friendly_error(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, openai.APIStatusError) and ("budget" in text or "exceeded" in text):
        return "You've reached your model budget for this period. Ask an admin to raise it."
    if isinstance(exc, openai.AuthenticationError):
        return "The model gateway rejected Jarvis's key. An admin needs to check LiteLLM."
    if isinstance(exc, openai.APIConnectionError | openai.APITimeoutError):
        return "Jarvis couldn't reach the model gateway. Try again in a moment."
    return "Something went wrong while Jarvis was answering. Try again."


async def run_turn(deps: TurnDeps, chat_id: uuid.UUID, assistant_message_id: uuid.UUID) -> None:
    """Run one Jarvis turn: stream the reply to the user's channel and store it."""
    async with deps.sessionmaker() as db:
        msg = await db.get(Message, assistant_message_id)
        if msg is None or msg.chat_id != chat_id or msg.status != MessageStatus.streaming:
            log.info("Skipping turn %s: nothing to answer", assistant_message_id)
            return
        chat = await db.get(Chat, chat_id)
        user = await db.get(User, chat.user_id)
        user_id = user.id

        async def emit(type_: str, **data) -> None:
            await publish(deps.redis, user_id, Event(type=type_, chat_id=chat_id, data=data))

        try:
            await emit("jarvis.state", state="thinking")
            content, usage = await _generate(deps, db, chat, user, msg, emit)
        except Exception as exc:
            if not isinstance(exc, TurnFailed):
                log.exception("Turn %s failed", assistant_message_id)
            await db.rollback()
            msg = await db.get(Message, assistant_message_id)
            msg.status = MessageStatus.failed
            msg.error = str(exc) if isinstance(exc, TurnFailed) else friendly_error(exc)
            await db.commit()
            await emit("message.failed", message=message_payload(msg))
            await emit("jarvis.state", state="idle")
            return

        msg.content = content
        msg.status = MessageStatus.complete
        msg.input_tokens = usage.get("input_tokens")
        msg.output_tokens = usage.get("output_tokens")
        chat.updated_at = utcnow()
        await db.commit()
        await emit("message.completed", message=message_payload(msg))
        await emit("jarvis.state", state="idle")


async def _generate(deps, db, chat: Chat, user: User, msg: Message, emit) -> tuple[str, dict]:
    if user.status != UserStatus.active:
        raise TurnFailed("Your account is not active.")
    rows = await db.scalars(
        select(Message)
        .where(
            Message.chat_id == chat.id,
            Message.status == MessageStatus.complete,
            Message.created_at < msg.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(deps.settings.chat_history_limit)
    )
    history = list(reversed(rows.all()))
    try:
        api_key = await ensure_user_key(db, deps.settings, deps.gateway, user)
    except GatewayError as exc:
        log.warning("No gateway key for %s: %s", user.id, exc)
        raise TurnFailed("Jarvis couldn't reach the model gateway. Try again in a moment.") from exc

    model = deps.model_factory(
        model=msg.model or deps.settings.jarvis_chat_model,
        api_key=api_key,
        metadata={
            "trace_id": msg.trace_id,
            "trace_name": "jarvis.turn",
            "generation_name": "jarvis.respond",
            "session_id": str(chat.id),
            "trace_user_id": str(user.id),
            "tags": ["jarvis", "chat", deps.settings.env],
        },
    )
    graph = build_graph(model)
    content = ""
    usage: dict = {}
    async for chunk, _meta in graph.astream(
        {"messages": to_langchain(history), "system_prompt": system_prompt(user.name)},
        stream_mode="messages",
    ):
        if not isinstance(chunk, AIMessageChunk):
            continue
        delta = chunk.text if isinstance(chunk.text, str) else chunk.text()
        if delta:
            if not content:
                await emit("jarvis.state", state="speaking")
            content += delta
            await emit("message.delta", message_id=str(msg.id), delta=delta)
        if chunk.usage_metadata:
            usage = dict(chunk.usage_metadata)
    return content, usage
