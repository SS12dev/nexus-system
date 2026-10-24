"""Design an agent system: the architect model (nexus-pro by default) turns a request into an
AgentSystemSpec, or picks one of the user's saved templates. The spec is validated twice: by its
Pydantic shape, then against what this user may actually use (tools, skills). One failed attempt
is sent back with the problems for a fix."""

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.devteam import NEEDED_TOOLS
from nexus_common.litellm import ensure_user_key
from nexus_common.memory import get_profile
from nexus_common.models import AgentSystem, SystemLifetime, SystemStatus, User
from nexus_common.skills import role_names, usable_skills
from nexus_common.spec import AgentSystemSpec, check_spec
from nexus_common.toolinfo import TOOLS
from nexus_orchestrator.llm import parse_json, private
from nexus_orchestrator.systems.prompts import DESIGN, design_request

log = logging.getLogger(__name__)

ATTEMPTS = 2


class DesignFailed(Exception):
    """The architect couldn't produce a usable spec; the message is for Nexus."""


@dataclass
class Design:
    spec: AgentSystemSpec | None = None
    template: AgentSystem | None = None


async def templates(db: AsyncSession, user_id: uuid.UUID) -> list[AgentSystem]:
    rows = await db.scalars(
        select(AgentSystem)
        .where(
            AgentSystem.user_id == user_id,
            AgentSystem.lifetime == SystemLifetime.template,
            AgentSystem.status != SystemStatus.archived,
        )
        .order_by(AgentSystem.updated_at.desc())
        .limit(20)
    )
    return list(rows.all())


async def projects(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[str, str, str]]:
    """The user's software projects, newest first: (project id, name, goal). Runs end with their
    system archived, so archived ones count too."""
    rows = await db.scalars(
        select(AgentSystem)
        .where(AgentSystem.user_id == user_id, AgentSystem.topology == "dev_project")
        .order_by(AgentSystem.updated_at.desc())
        .limit(40)
    )
    out: dict[str, tuple[str, str, str]] = {}
    for s in rows.all():
        project = (s.spec or {}).get("project")
        if project and project not in out:
            out[project] = (project, s.name, s.goal)
    return list(out.values())[:10]


NEEDS_EXEC = (
    "Building software needs the Run code permission, which this account doesn't have. An "
    "admin can grant it on the People page."
)


def _problems(
    raw: dict, allowed: set[str], skills: set[str], software: bool = False
) -> tuple[AgentSystemSpec | None, str]:
    data = raw.get("spec")
    if not isinstance(data, dict):
        return None, 'Reply with {"spec": {...}} or {"template_id": "..."}.'
    try:
        spec = AgentSystemSpec.model_validate(data)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]
        )
        return None, f"The spec is invalid: {errors}"
    if software and spec.topology != "dev_project":
        return None, "This request is software to build, fix or extend: use topology dev_project."
    if spec.topology == "dev_project" and not NEEDED_TOOLS <= allowed:
        # No retry can fix a missing permission.
        raise DesignFailed(NEEDS_EXEC)
    problems = check_spec(spec, allowed, skills)
    if problems:
        return None, "The spec can't run for this user: " + "; ".join(problems)
    return spec, ""


async def design(
    deps,
    db: AsyncSession,
    user: User,
    request: str,
    *,
    allowed: set[str],
    deliverables: list[str] | None = None,
    files: list[tuple[str, str]] | None = None,
    best_model: bool = False,
    current: AgentSystemSpec | None = None,
    trace_id: str | None = None,
    chat_id: uuid.UUID | None = None,
    use_templates: bool = True,
    software: bool = False,
) -> Design:
    """Billed to the user's own key, like the rest of their chat turn."""
    settings = deps.settings
    skills = await usable_skills(db, user.id)
    if software and not NEEDED_TOOLS <= allowed:
        raise DesignFailed(NEEDS_EXEC)
    # A software request is never a saved template: it always gets the house team.
    saved = (
        await templates(db, user.id) if use_templates and current is None and not software else []
    )
    known = await projects(db, user.id)
    profile = await get_profile(db, user.id)
    prompt = design_request(
        request,
        deliverables or [],
        files or [],
        [t for t in TOOLS if t.name in allowed],
        skills,
        [(str(t.id), t.name, t.goal) for t in saved],
        profile.content if profile.memory_enabled else "",
        best_model,
        current.model_dump() if current is not None else None,
        role_names(Path(settings.library_path)),
        known,
        software,
    )
    api_key = await ensure_user_key(db, settings, deps.gateway, user)
    model = deps.model_factory(
        model=settings.design_model,
        api_key=api_key,
        metadata={
            "trace_id": trace_id or uuid.uuid4().hex,
            "trace_name": "nexus.turn",
            "generation_name": "nexus.design_system",
            "session_id": str(chat_id) if chat_id else None,
            "trace_user_id": str(user.id),
            **private(settings, user),
            "tags": ["nexus", "design", settings.env],
        },
    ).bind(response_format={"type": "json_object"})
    # The model call can be slow; don't hold a connection open through it.
    await db.commit()

    messages = [SystemMessage(DESIGN), HumanMessage(prompt)]
    by_id = {str(t.id): t for t in saved}
    slugs = {s.slug for s in skills}
    problem = ""
    for attempt in range(ATTEMPTS):
        reply = await model.ainvoke(messages)
        text = reply.text if isinstance(reply.text, str) else reply.text()
        raw = parse_json(text)
        if raw.get("template_id"):
            template = by_id.get(str(raw["template_id"]))
            if template is not None:
                return Design(template=template)
            problem = "That template id doesn't exist; design a spec instead."
        else:
            spec, problem = _problems(raw, allowed, slugs, software)
            if spec is not None:
                if spec.project and spec.project not in {p for p, _, _ in known}:
                    # Only the user's own projects can be continued.
                    spec.project = None
                if spec.lead.tier == "max" and not best_model:
                    spec.lead.tier = "pro"
                return Design(spec=spec)
        log.info("Design attempt %d rejected: %s", attempt + 1, problem)
        messages += [AIMessage(text), HumanMessage(f"{problem}\nReply with corrected JSON only.")]
    raise DesignFailed(f"Couldn't design a working team for this: {problem}")


def describe(spec: AgentSystemSpec) -> str:
    """A compact summary of a spec for Nexus to explain to the user."""
    lines = [f"{spec.name} ({spec.topology.replace('_', ' ')})"]
    for a in spec.agents:
        extras = ", ".join([*a.tools, *a.skills]) or "no tools"
        lines.append(f"- {a.name} [{a.id}, {a.role}, {a.tier}]: {extras}")
    if spec.deliverables:
        lines.append("Deliverables: " + ", ".join(spec.deliverables))
    return "\n".join(lines)


def spec_json(spec: AgentSystemSpec) -> str:
    return json.dumps(spec.model_dump(), indent=1)
