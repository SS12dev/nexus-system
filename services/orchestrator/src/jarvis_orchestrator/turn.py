import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_common.embeddings import CachedEmbedder
from jarvis_common.events import Event, publish
from jarvis_common.library import file_payload
from jarvis_common.litellm import GatewayError, LiteLLMAdmin, ensure_user_key
from jarvis_common.models import (
    AgentRun,
    AgentSystem,
    Attachment,
    Chat,
    Message,
    MessageRole,
    MessageStatus,
    SystemLifetime,
    SystemStatus,
    User,
    UserStatus,
    utcnow,
)
from jarvis_common.settings import Settings
from jarvis_common.skills import catalogue, instruction_layers, usable_skills
from jarvis_common.storage import Storage
from jarvis_common.systems import ACTIVE_RUN, granted_capabilities
from jarvis_common.toolinfo import TOOLS, allowed_tools
from jarvis_orchestrator.context import assemble
from jarvis_orchestrator.graph import RECURSION_LIMIT, build_graph
from jarvis_orchestrator.prompts import system_prompt
from jarvis_orchestrator.sandbox import SandboxClient
from jarvis_orchestrator.systems.manage import system_tools
from jarvis_orchestrator.tools import ToolContext, build_tools
from jarvis_orchestrator.web import WebClient

log = logging.getLogger(__name__)

ModelFactory = Callable[..., BaseChatModel]
# Puts a job on the orchestrator queue: enqueue(function_name, *args, **arq_options).
Enqueue = Callable[..., Awaitable[object]]


@dataclass
class TurnDeps:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    gateway: LiteLLMAdmin
    model_factory: ModelFactory
    embedder: CachedEmbedder
    enqueue: Enqueue | None = None
    storage: Storage | None = None
    sandbox: SandboxClient | None = None
    web: WebClient | None = None
    # Where agent-system runs keep their LangGraph checkpoints (Postgres in production).
    checkpointer: BaseCheckpointSaver | None = None


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
            content, usage, learn, saved = await _generate(deps, db, chat, user, msg, emit)
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
        # Files Jarvis made in this turn (save_file, run_code) are attached to its reply.
        for f in saved:
            db.add(Attachment(message_id=msg.id, file_id=f.id))
        await db.commit()
        payload = message_payload(msg)
        if saved:
            payload["attachments"] = [file_payload(f) for f in saved]
        await emit("message.completed", message=payload)
        await emit("jarvis.state", state="idle")

    if learn and deps.enqueue is not None:
        try:
            await deps.enqueue(
                "extract_memories",
                str(chat_id),
                str(assistant_message_id),
                _job_id=f"memory:{assistant_message_id}",
            )
        except Exception:
            log.exception("Could not queue memory extraction")


async def systems_context(db, user_id: uuid.UUID, chat_id: uuid.UUID) -> str:
    """Saved systems and templates, and runs from this chat, so Jarvis can refer to them."""
    saved = (
        await db.scalars(
            select(AgentSystem)
            .where(
                AgentSystem.user_id == user_id,
                AgentSystem.lifetime != SystemLifetime.ephemeral,
                AgentSystem.status != SystemStatus.archived,
            )
            .order_by(AgentSystem.updated_at.desc())
            .limit(12)
        )
    ).all()
    runs = (
        await db.execute(
            select(AgentRun, AgentSystem)
            .join(AgentSystem, AgentSystem.id == AgentRun.system_id)
            .where(AgentRun.chat_id == chat_id, AgentRun.user_id == user_id)
            .order_by(AgentRun.created_at.desc())
            .limit(6)
        )
    ).all()
    lines = [f"- {s.name} (system {s.id}, {s.lifetime})" for s in saved]
    for r, s in runs:
        state = "in progress" if r.status in ACTIVE_RUN else "ended"
        lines.append(
            f"- run {r.id} of {s.name} (system {s.id}, {s.lifetime}, {s.status}): {r.status}, "
            f"{state}, ${float(r.cost_usd or 0):.2f} of ${float(r.budget_usd):.2f}"
        )
    return "<agent_systems>\n" + "\n".join(lines) + "\n</agent_systems>" if lines else ""


async def _generate(
    deps, db, chat: Chat, user: User, msg: Message, emit
) -> tuple[str, dict, bool, list]:
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
    truncated = len(history) >= deps.settings.chat_history_limit
    question = next((m.content for m in reversed(history) if m.role == MessageRole.user), "")
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
    context = await assemble(db, deps.settings, deps.embedder, chat, question, truncated)
    allowed = allowed_tools(user, await granted_capabilities(db, user.id))
    # Outbound and destructive tools are for agents in a system, where every call is approved.
    names = {t.name for t in TOOLS if t.jarvis and t.name in allowed} | {"remember"}
    extra = []
    layers = await instruction_layers(db, Path(deps.settings.library_path), user, None)
    if layers.user:
        extra.append(f"<user_instructions>\n{layers.user}\n</user_instructions>")
    skills = await usable_skills(db, user.id)
    if skills:
        extra.append(
            "<skills>\nSkills in the shared library (load one with skills_load before a task "
            f"it covers):\n{catalogue(skills)}\n</skills>"
        )
    if allowed:
        extra.append(await systems_context(db, user.id, chat.id))
    rendered = "\n\n".join(x for x in [context.render(), *extra] if x)
    # Release the connection while the model runs; tools open their own sessions.
    await db.commit()
    ctx = ToolContext(
        deps=deps,
        user_id=user.id,
        chat_id=chat.id,
        emit=emit,
        memory_enabled=context.memory_enabled,
        file_ids=[f.id for f in context.chat_files],
    )
    tools = build_tools(ctx, names)
    if allowed:
        tools += system_tools(ctx, user, allowed, msg.trace_id)
    graph = build_graph(model, tools)
    content = ""
    usage = {"input_tokens": 0, "output_tokens": 0}
    async for chunk, meta in graph.astream(
        {
            "messages": to_langchain(history),
            "system_prompt": system_prompt(user.name, rendered),
        },
        {"recursion_limit": RECURSION_LIMIT},
        stream_mode="messages",
    ):
        # Tools that call a model themselves (designing an agent system) stream too; only
        # Jarvis's own answer belongs in the chat, and their cost is billed where they run.
        if not isinstance(chunk, AIMessageChunk) or meta.get("langgraph_node") != "respond":
            continue
        delta = chunk.text if isinstance(chunk.text, str) else chunk.text()
        if delta:
            if not content:
                await emit("jarvis.state", state="speaking")
            content += delta
            await emit("message.delta", message_id=str(msg.id), delta=delta)
        if chunk.usage_metadata:
            # One turn can make several model calls (tool rounds); count them all.
            usage["input_tokens"] += chunk.usage_metadata.get("input_tokens", 0)
            usage["output_tokens"] += chunk.usage_metadata.get("output_tokens", 0)
    return content, usage, context.memory_enabled, ctx.saved
