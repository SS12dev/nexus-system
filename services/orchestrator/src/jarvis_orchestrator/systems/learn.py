"""Learned skills: after a long successful run, Jarvis drafts a skill that captures how the work
was done, so the next team doesn't work it out again. A draft stays disabled until the user
reviews and enables it on the Skills page."""

import json
import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from jarvis_common.events import Event, publish
from jarvis_common.litellm import ensure_user_key
from jarvis_common.models import (
    AgentRun,
    AgentSystem,
    Chat,
    Message,
    MessageRole,
    MessageStatus,
    RunEvent,
    RunStatus,
    Skill,
    SkillScope,
    SkillStatus,
    User,
    UserStatus,
    utcnow,
)
from jarvis_common.skills import SkillError, add_version, content_hash, parse_skill, skill_payload
from jarvis_common.skills import slug as skill_slug
from jarvis_orchestrator.llm import parse_json
from jarvis_orchestrator.turn import message_payload

log = logging.getLogger(__name__)

DRAFT = """You write reusable skills for a team of AI agents, in the Agent Skills format. You \
get a task an agent team finished and the steps they took. If the way they did it is worth \
reusing for similar tasks, write a skill that lets an agent do it faster next time: when to use \
it, the steps that worked, tools and core skills to use, pitfalls they hit, and the shape of \
a good result. Be concrete and general: nothing specific to this one user's data.

name: lowercase words joined by hyphens, at most 64 characters. description: one sentence \
saying what it does and when to use it (it is all an agent sees before loading it). body: \
Markdown instructions, at most 600 words.

If the task was one-off or nothing reusable was learned, reply {"skip": true}.
Reply with JSON only: {"name": str, "description": str, "body": str} or {"skip": true}."""


async def draft_skill(deps, run_id: uuid.UUID) -> Skill | None:
    async with deps.sessionmaker() as db:
        run = await db.get(AgentRun, run_id)
        if run is None or run.status != RunStatus.completed:
            return None
        user = await db.get(User, run.user_id)
        if user is None or user.status != UserStatus.active:
            return None
        if await db.scalar(select(Skill.id).where(Skill.source == f"run:{run_id}")):
            return None
        system = await db.get(AgentSystem, run.system_id)
        events = (
            await db.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.type == "tool.call")
                .order_by(RunEvent.created_at)
                .limit(80)
            )
        ).all()
        steps = "\n".join(
            f"- {e.agent}: {e.data.get('name')} "
            + ", ".join(f"{k}={str(v)[:80]}" for k, v in e.data.items() if k != "name")
            for e in events
        )
        prompt = (
            f"TASK: {system.goal}\n\nTEAM: {system.topology}, agents "
            + ", ".join(f"{a['id']} ({a['role']})" for a in system.spec.get("agents", []))
            + f"\n\nSTEPS TAKEN:\n{steps}\n\nRESULT (start):\n{(run.output or '')[:3000]}"
        )
        api_key = await ensure_user_key(db, deps.settings, deps.gateway, user)
        await db.commit()

    model = deps.model_factory(
        model=deps.settings.design_model,
        api_key=api_key,
        metadata={
            "trace_id": run.trace_id,
            "trace_name": "jarvis.system",
            "generation_name": "jarvis.draft_skill",
            "trace_user_id": str(user.id),
            "tags": ["jarvis", "skills", deps.settings.env],
        },
    ).bind(response_format={"type": "json_object"})
    reply = await model.ainvoke([SystemMessage(DRAFT), HumanMessage(prompt)])
    raw = parse_json(reply.text if isinstance(reply.text, str) else reply.text())
    if raw.get("skip") or not raw.get("name"):
        log.info("No skill learned from run %s", run_id)
        return None
    name = str(raw["name"]).strip().lower().replace(" ", "-")[:64]
    description = str(raw.get("description") or "").replace("\n", " ").strip()[:1024]
    # JSON strings are valid YAML, so a description with colons survives.
    content = (
        f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n\n{raw.get('body', '')}\n"
    )
    try:
        parsed = parse_skill(content)
    except SkillError as exc:
        log.info("Drafted skill from run %s was invalid: %s", run_id, exc)
        return None

    async with deps.sessionmaker() as db:
        taken = await db.scalar(
            select(Skill.id).where(
                Skill.owner_id == user.id,
                Skill.slug == skill_slug(SkillScope.learned, parsed.name),
            )
        )
        if taken:
            return None
        skill = Skill(
            id=uuid.uuid4(),
            scope=SkillScope.learned,
            status=SkillStatus.draft,
            owner_id=user.id,
            source=f"run:{run_id}",
            version=1,
            name=parsed.name,
            slug=skill_slug(SkillScope.learned, parsed.name),
            description=parsed.description,
            content=parsed.content,
            files={},
            content_hash=content_hash(parsed.content, {}),
        )
        db.add(skill)
        await db.flush()
        add_version(db, skill, None, f"Drafted by Jarvis from {system.name}")
        message = None
        if run.chat_id and await db.get(Chat, run.chat_id):
            message = Message(
                chat_id=run.chat_id,
                role=MessageRole.assistant,
                content=(
                    f"I drafted a skill from how **{system.name}** did this: "
                    f"**{skill.name}**, {skill.description} It stays off until you review it on "
                    "the Skills page."
                ),
                status=MessageStatus.complete,
                created_at=utcnow(),
            )
            db.add(message)
        await db.commit()
        await publish(
            deps.redis, user.id, Event(type="skill.updated", data={"skill": skill_payload(skill)})
        )
        if message is not None:
            await publish(
                deps.redis,
                user.id,
                Event(
                    type="message.completed",
                    chat_id=run.chat_id,
                    data={"message": message_payload(message)},
                ),
            )
    return skill
