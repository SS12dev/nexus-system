import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus_common import logs
from nexus_common.deployer import DeployerClient
from nexus_common.embeddings import CachedEmbedder
from nexus_common.events import Event, publish
from nexus_common.google import get_grant, services_in
from nexus_common.library import file_payload
from nexus_common.links import link_payload
from nexus_common.litellm import GatewayError, LiteLLMAdmin, ensure_user_key
from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Attachment,
    Chat,
    File,
    Message,
    MessageLink,
    MessageRole,
    MessageStatus,
    SystemLifetime,
    SystemStatus,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.search import hybrid_search
from nexus_common.settings import Settings
from nexus_common.skills import catalogue, instruction_layers, shelf_index, usable_skills
from nexus_common.storage import Storage
from nexus_common.systems import ACTIVE_RUN, granted_capabilities
from nexus_common.toolinfo import TOOLS, allowed_tools, connected_only
from nexus_orchestrator.context import PICTURE_TYPES, assemble
from nexus_orchestrator.github import GitHubApp
from nexus_orchestrator.graph import RECURSION_LIMIT, build_graph
from nexus_orchestrator.llm import private
from nexus_orchestrator.prompts import system_prompt
from nexus_orchestrator.route import Route, route
from nexus_orchestrator.sandbox import SandboxClient
from nexus_orchestrator.systems.manage import system_tools
from nexus_orchestrator.tools import (
    MAX_IMAGE_BYTES,
    ToolContext,
    build_tools,
    image_part,
    image_type,
)
from nexus_orchestrator.web import WebClient
from nexus_orchestrator.workbench import WorkbenchClient

log = logging.getLogger(__name__)

ModelFactory = Callable[..., BaseChatModel]
# Up to this many skills, Nexus's prompt lists them all; beyond it, only the ones that match the
# message in full, plus every skill's name by shelf, so a big library doesn't cost every turn.
CATALOGUE_LIMIT = 12
RELEVANT_SKILLS = 6

# How a routine run's message starts (routines.turn_prompt); such turns skip the team router.
ROUTINE_PREFIX = '[Routine "'

# Pictures on the user's latest few messages are shown to the model; the latest messages with
# pictures, and the pictures per message, it sees.
SHOW_IMAGE_MESSAGES = 3
MAX_IMAGES_PER_MESSAGE = 6

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
    workbench: WorkbenchClient | None = None
    # The Nexus GitHub App, when it is set up (GITHUB_* settings).
    github: GitHubApp | None = None
    # Runs finished software projects as apps.
    deployer: DeployerClient | None = None
    web: WebClient | None = None
    # Shared client for Google's APIs (Gmail, Calendar, ...); None opens one per call.
    google_http: httpx.AsyncClient | None = None
    # Where agent-system runs keep their LangGraph checkpoints (Postgres in production).
    checkpointer: BaseCheckpointSaver | None = None


async def skills_context(
    db: AsyncSession, embedder: CachedEmbedder, user_id: uuid.UUID, question: str
) -> str:
    skills = await usable_skills(db, user_id)
    if not skills:
        return ""
    head = (
        "<skills>\nSkills in the shared library (load one with skills_load before a task it covers)"
    )
    if len(skills) <= CATALOGUE_LIMIT:
        return f"{head}:\n{catalogue(skills)}\n</skills>"
    by_id = {s.id: s for s in skills}
    picked = []
    if question.strip():
        vec, _ = await embedder.try_embed_one(db, question[:4000])
        hits = await hybrid_search(db, "skills", user_id, question[:4000], vec, RELEVANT_SKILLS)
        picked = [by_id[h.id] for h in hits if h.id in by_id]
    lines = [f"{head}."]
    if picked:
        lines += ["Likely relevant to this message:", catalogue(picked)]
    lines += [
        "The whole library by shelf (skills_search describes any of them):",
        shelf_index(skills),
        "</skills>",
    ]
    return "\n".join(lines)


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
        "steps": msg.steps,
        "recalled": msg.recalled,
        "created_at": msg.created_at.isoformat(),
    }


MAX_STEPS = 60
ARG_CHARS = 200


def track_step(steps: list[dict], type_: str, data: dict) -> None:
    """Keep the tools Nexus used in a turn, in order, so the reply can show them after a
    reload. tool.call opens a step and tool.done closes the step with the same call id."""
    if type_ not in ("tool.call", "tool.done"):
        return
    call_id = data.get("call_id")
    step = next((s for s in steps if call_id and s["id"] == call_id), None)
    if type_ == "tool.call":
        if step is not None or len(steps) >= MAX_STEPS:
            return
        args = {
            k: v[:ARG_CHARS] if isinstance(v, str) else v
            for k, v in data.items()
            if k not in ("name", "call_id") and isinstance(v, str | int | float | bool)
        }
        steps.append(
            {
                "id": call_id or uuid.uuid4().hex[:12],
                "name": data.get("name"),
                "args": args,
                "status": "running",
            }
        )
        return
    if step is None:
        if len(steps) >= MAX_STEPS:
            return
        step = {"id": call_id, "name": data.get("name"), "args": {}}
        steps.append(step)
    step.update(
        status="done" if data.get("ok", True) else "failed",
        ms=data.get("ms"),
        summary=data.get("summary") or "",
    )


def finished(steps: list[dict]) -> list[dict]:
    """Steps as stored once the turn is over: any still open never reported back."""
    return [s if s.get("status") != "running" else {**s, "status": "failed"} for s in steps]


async def user_images(deps, db, history: list[Message]) -> tuple[dict, dict]:
    """Pictures attached to the user's messages: the ones on the latest SHOW_IMAGE_MESSAGES
    messages with pictures, as (name, media type, bytes) by message id, to show the model
    (every Nexus tier reads images), and the names of older ones, by message id, so a long chat
    doesn't pay for every earlier screenshot on every turn."""
    users = [m.id for m in history if m.role == MessageRole.user]
    if not users or deps.storage is None:
        return {}, {}
    rows = await db.execute(
        select(Attachment.message_id, File)
        .join(File, File.id == Attachment.file_id)
        .where(Attachment.message_id.in_(users), File.content_type.in_(PICTURE_TYPES))
        .order_by(File.created_at)
    )
    by_message: dict[uuid.UUID, list[File]] = {}
    for message_id, f in rows:
        by_message.setdefault(message_id, []).append(f)
    recent = [i for i in reversed(users) if i in by_message][:SHOW_IMAGE_MESSAGES]
    shown: dict[uuid.UUID, list[tuple[str, str, bytes]]] = {}
    older: dict[uuid.UUID, list[str]] = {}
    for message_id, files in by_message.items():
        if message_id not in recent:
            older[message_id] = [f.name for f in files]
            continue
        for i, f in enumerate(files):
            data = b""
            if i < MAX_IMAGES_PER_MESSAGE and f.size <= MAX_IMAGE_BYTES:
                try:
                    data = await deps.storage.get(f.storage_key)
                except Exception:
                    log.warning("Couldn't load picture %s for the model", f.id, exc_info=True)
            kind = image_type(data)
            if kind is None or len(data) > MAX_IMAGE_BYTES:
                # Named instead, like an older picture.
                older.setdefault(message_id, []).append(f.name)
                continue
            shown.setdefault(message_id, []).append((f.name, kind, data))
    return shown, older


def to_langchain(
    messages: list[Message],
    images: dict[uuid.UUID, list[tuple[str, str, bytes]]] | None = None,
    older: dict[uuid.UUID, list[str]] | None = None,
) -> list[BaseMessage]:
    images, older = images or {}, older or {}
    out: list[BaseMessage] = []
    for m in messages:
        if m.role == MessageRole.user:
            text = m.content
            if names := older.get(m.id):
                text += (
                    f"\n\n(Picture{'s' if len(names) > 1 else ''} attached here, no longer shown: "
                    f"{', '.join(names)}. Call view_image to look again.)"
                )
            if pictures := images.get(m.id):
                parts: list = [{"type": "text", "text": text}]
                for name, kind, data in pictures:
                    parts += [{"type": "text", "text": f"Attached picture {name}:"}]
                    parts.append(image_part(kind, data))
                out.append(HumanMessage(parts))
            else:
                out.append(HumanMessage(text))
        elif m.role == MessageRole.assistant:
            out.append(AIMessage(m.content))
    return out


def friendly_error(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, openai.APIStatusError) and ("budget" in text or "exceeded" in text):
        return "You've reached your model budget for this period. Ask an admin to raise it."
    if isinstance(exc, openai.AuthenticationError):
        return "The model gateway rejected the assistant's key. An admin needs to check LiteLLM."
    if isinstance(exc, openai.APIConnectionError | openai.APITimeoutError):
        return "Couldn't reach the model gateway. Try again in a moment."
    return "Something went wrong while answering. Try again."


async def run_turn(
    deps: TurnDeps, chat_id: uuid.UUID, assistant_message_id: uuid.UUID, *, learn: bool = True
) -> None:
    """Run one Nexus turn: stream the reply to the user's channel and store it. learn=False
    skips memory extraction (routines: a news digest says nothing new about the person)."""
    async with deps.sessionmaker() as db:
        msg = await db.get(Message, assistant_message_id)
        if msg is None or msg.chat_id != chat_id or msg.status != MessageStatus.streaming:
            log.info("Skipping turn %s: nothing to answer", assistant_message_id)
            return
        chat = await db.get(Chat, chat_id)
        user = await db.get(User, chat.user_id)
        user_id = user.id
        logs.bind(user_id=user_id, chat_id=chat_id, trace_id=msg.trace_id)

        steps: list[dict] = []
        recalled: dict | None = None

        async def emit(type_: str, **data) -> None:
            nonlocal recalled
            track_step(steps, type_, data)
            if type_ == "memory.recalled":
                recalled = data
            await publish(deps.redis, user_id, Event(type=type_, chat_id=chat_id, data=data))

        try:
            await emit("assistant.state", state="thinking")
            content, usage, memory_on, saved, links = await _generate(
                deps, db, chat, user, msg, emit
            )
        except Exception as exc:
            if not isinstance(exc, TurnFailed):
                log.exception("Turn %s failed", assistant_message_id)
            await db.rollback()
            msg = await db.get(Message, assistant_message_id)
            msg.status = MessageStatus.failed
            msg.error = str(exc) if isinstance(exc, TurnFailed) else friendly_error(exc)
            msg.steps = finished(steps) or None
            msg.recalled = recalled
            await db.commit()
            await emit("message.failed", message=message_payload(msg))
            await emit("assistant.state", state="idle")
            return

        msg.content = content
        msg.status = MessageStatus.complete
        msg.input_tokens = usage.get("input_tokens")
        msg.output_tokens = usage.get("output_tokens")
        msg.steps = finished(steps) or None
        msg.recalled = recalled
        chat.updated_at = utcnow()
        # Files Nexus made in this turn (save_file, run_code) are attached to its reply.
        for f in saved:
            db.add(Attachment(message_id=msg.id, file_id=f.id))
        # So are the links it saved (save_link).
        for link in links:
            db.add(MessageLink(message_id=msg.id, link_id=link.id))
        await db.commit()
        payload = message_payload(msg)
        if saved:
            payload["attachments"] = [file_payload(f) for f in saved]
        if links:
            payload["links"] = [link_payload(x) for x in links]
        await emit("message.completed", message=payload)
        await emit("assistant.state", state="idle")

    if learn and memory_on and deps.enqueue is not None:
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
    """Saved systems and templates, and runs from this chat, so Nexus can refer to them."""
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


async def _no_route() -> Route:
    return Route()


async def route_to_team(
    deps, user: User, chat: Chat, msg: Message, history: list[Message], api_key: str
) -> Route:
    """Ask the cheapest model whether the latest message is work for an agent team, and whether
    it is software. Short messages ("hi", "thanks", "go") never are, so they skip the call."""
    if not deps.settings.route_model or not history or history[-1].role != MessageRole.user:
        return Route()
    question = history[-1].content.strip()
    # A routine run is answered directly: a team every morning would eat the person's budget.
    if len(question) < deps.settings.route_min_chars or question.startswith(ROUTINE_PREFIX):
        return Route()
    previous = next(
        (m.content for m in reversed(history[:-1]) if m.role == MessageRole.assistant), ""
    )
    model = deps.model_factory(
        model=deps.settings.route_model,
        api_key=api_key,
        metadata={
            "trace_id": msg.trace_id,
            "trace_name": "nexus.turn",
            "generation_name": "nexus.route",
            "session_id": str(chat.id),
            "trace_user_id": str(user.id),
            **private(deps.settings, user),
            "tags": ["nexus", "route", deps.settings.env],
        },
    )
    return await route(model, question, previous, msg.trace_id)


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
    # The latest message with words: a screenshot sent on its own is about the one before it.
    question = next(
        (m.content for m in reversed(history) if m.role == MessageRole.user and m.content.strip()),
        "",
    )
    try:
        api_key = await ensure_user_key(db, deps.settings, deps.gateway, user)
    except GatewayError as exc:
        log.warning("No gateway key for %s: %s", user.id, exc)
        raise TurnFailed("Couldn't reach the model gateway. Try again in a moment.") from exc

    model = deps.model_factory(
        model=msg.model or deps.settings.chat_model,
        api_key=api_key,
        metadata={
            "trace_id": msg.trace_id,
            "trace_name": "nexus.turn",
            "generation_name": "nexus.respond",
            "session_id": str(chat.id),
            "trace_user_id": str(user.id),
            **private(deps.settings, user),
            "tags": ["nexus", "chat", deps.settings.env],
        },
    )
    allowed = allowed_tools(user, await granted_capabilities(db, user.id))
    grant = await get_grant(db, deps.settings, user.id)
    allowed = connected_only(allowed, services_in(grant.scopes) if grant else [])
    # The router is a model call that needs nothing from the database, so it runs while the
    # context is gathered rather than after it.
    routing = asyncio.create_task(
        route_to_team(deps, user, chat, msg, history, api_key) if allowed else _no_route()
    )
    try:
        context = await assemble(db, deps.settings, deps.embedder, chat, question, truncated)
        if context.memories or context.entities:
            # What it remembered for this reply, for the chat and the orb to show.
            await emit(
                "memory.recalled",
                memories=[str(m.id) for m in context.memories],
                entities=[
                    {"id": str(e.id), "name": e.name, "type": e.type} for e in context.entities
                ],
            )
        # Outbound and destructive tools are for agents in a system, where every call is approved.
        names = {t.name for t in TOOLS if t.assistant and t.name in allowed} | {"remember"}
        extra = []
        layers = await instruction_layers(db, Path(deps.settings.library_path), user, None)
        if layers.global_extra:
            extra.append(f"<shared_instructions>\n{layers.global_extra}\n</shared_instructions>")
        if layers.user:
            extra.append(f"<user_instructions>\n{layers.user}\n</user_instructions>")
        if skills_block := await skills_context(db, deps.embedder, user.id, question):
            extra.append(skills_block)
        if allowed:
            extra.append(await systems_context(db, user.id, chat.id))
        rendered = "\n\n".join(x for x in [context.render(), *extra] if x)
        images, older = await user_images(deps, db, history)
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
        # When the router says this is work for a team, the assistant's first move is to build one.
        decision = await routing
    finally:
        # Only still running when something above failed; a finished task ignores this.
        routing.cancel()
    if allowed:
        tools += system_tools(
            ctx, user, allowed, msg.trace_id, asked=question, software=decision.software
        )
    force_tool = "build_agent_system" if decision.team else None
    graph = build_graph(model, tools, ctx)
    content = ""
    usage = {"input_tokens": 0, "output_tokens": 0}
    async for chunk, meta in graph.astream(
        {
            "messages": to_langchain(history, images, older),
            "system_prompt": system_prompt(user.name, user.assistant, rendered),
            "force_tool": force_tool,
        },
        {"recursion_limit": RECURSION_LIMIT},
        stream_mode="messages",
    ):
        # Tools that call a model themselves (designing an agent system) stream too; only
        # Nexus's own answer belongs in the chat, and their cost is billed where they run.
        if not isinstance(chunk, AIMessageChunk) or meta.get("langgraph_node") != "respond":
            continue
        delta = chunk.text if isinstance(chunk.text, str) else chunk.text()
        if delta:
            if not content:
                await emit("assistant.state", state="speaking")
            content += delta
            await emit("message.delta", message_id=str(msg.id), delta=delta)
        if chunk.usage_metadata:
            # One turn can make several model calls (tool rounds); count them all.
            usage["input_tokens"] += chunk.usage_metadata.get("input_tokens", 0)
            usage["output_tokens"] += chunk.usage_metadata.get("output_tokens", 0)
    return content, usage, context.memory_enabled, ctx.saved, ctx.links
