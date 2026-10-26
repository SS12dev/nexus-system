"""Design an agent system: the architect model (nexus-pro by default) plans the flow a request
needs and turns it into an AgentSystemSpec, picks one of the user's saved templates, or points
at a past result that already is what was asked for. The spec is validated twice: by its
Pydantic shape, then against what this user may actually use (tools, skills). One failed attempt
is sent back with the problems for a fix."""

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.devteam import NEEDED_TOOLS
from nexus_common.litellm import ensure_user_key
from nexus_common.memory import get_profile
from nexus_common.models import (
    AgentRun,
    AgentSystem,
    File,
    Project,
    RunStatus,
    SystemLifetime,
    SystemStatus,
    User,
)
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
    # A finished run whose result already is what the request asks for.
    existing: AgentRun | None = None
    # A past result the spec changes or fixes: the team starts from its files.
    based_on: AgentRun | None = None
    # The architect's flow in words, for the logs.
    plan: str = ""


PAST_RESULTS = 15
# Older results whose name or goal matches the request, shown on top of the recent ones, so
# "the solitaire app is broken" finds the solitaire app however long ago it was made.
MATCHES = 5
ANY_WORD = (
    "to_tsquery('english', replace(plainto_tsquery('english', :q)::text, '&', '|')) as q(query)"
)


async def matching_systems(
    db: AsyncSession, user_id: uuid.UUID, request: str, n: int = MATCHES
) -> list[uuid.UUID]:
    """The user's systems whose name or goal shares words with the request, best first."""
    if not request.strip():
        return []
    rows = await db.execute(
        text(
            f"select s.id from agent_systems s, {ANY_WORD} "
            "where s.user_id = :u and s.tsv @@ q.query "
            "order by ts_rank_cd(s.tsv, q.query) desc, s.updated_at desc limit :n"
        ),
        {"q": request[:2000], "u": user_id, "n": n},
    )
    return [r[0] for r in rows.all()]


async def past_results(
    db: AsyncSession, user_id: uuid.UUID, request: str = ""
) -> tuple[list[tuple[str, str, str, list[str]]], dict[str, AgentRun]]:
    """The user's finished runs as (run id, name, goal, file names), and the runs by id: the
    ones matching the request first, then the newest. One per system: a project's latest
    result stands for it."""
    matched = await matching_systems(db, user_id, request)
    done = (AgentRun.user_id == user_id, AgentRun.status == RunStatus.completed)
    found: list[tuple[AgentRun, AgentSystem]] = []
    if matched:
        rows = await db.execute(
            select(AgentRun, AgentSystem)
            .join(AgentSystem, AgentSystem.id == AgentRun.system_id)
            .where(*done, AgentRun.system_id.in_(matched))
            .order_by(AgentRun.finished_at.desc().nulls_last())
        )
        rank = {sid: i for i, sid in enumerate(matched)}
        found += sorted(rows.all(), key=lambda r: rank[r[1].id])
    rows = await db.execute(
        select(AgentRun, AgentSystem)
        .join(AgentSystem, AgentSystem.id == AgentRun.system_id)
        .where(*done)
        .order_by(AgentRun.finished_at.desc().nulls_last())
        .limit(PAST_RESULTS * 3)
    )
    found += rows.all()
    out: list[tuple[str, str, str, list[str]]] = []
    runs: dict[str, AgentRun] = {}
    seen: set[str] = set()
    for run, system in found:
        key = (system.spec or {}).get("project") or str(system.id)
        if key in seen:
            continue
        seen.add(key)
        runs[str(run.id)] = run
        out.append((str(run.id), system.name, system.goal, []))
        if len(out) >= PAST_RESULTS + MATCHES:
            break
    if runs:
        files = await db.execute(
            select(File.run_id, File.name)
            .where(File.run_id.in_([r.id for r in runs.values()]))
            .order_by(File.created_at)
        )
        names: dict[str, list[str]] = {}
        for rid, name in files.all():
            listed = names.setdefault(str(rid), [])
            if name not in listed:
                listed.append(name)
        out = [(i, n, g, names.get(i, [])) for i, n, g, _ in out]
    return out, runs


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


def app_status(row: Project | None) -> str:
    """A project's app in a few words, for the architect and Nexus."""
    if row is None or not row.app_slug:
        return "never deployed"
    if row.deploy_error:
        return f"last deploy failed: {row.deploy_error[:150]}"
    state = row.app_state or "unknown"
    return f"app {state} at {row.live_url}" if row.live_url else f"app {state}"


async def projects(
    db: AsyncSession, user_id: uuid.UUID, request: str = ""
) -> list[tuple[str, str, str, str]]:
    """The user's software projects, the ones matching the request first, then the newest:
    (project id, name, goal, app status). Runs end with their system archived, so archived
    ones count too."""
    matched = await matching_systems(db, user_id, request)
    rows = await db.scalars(
        select(AgentSystem)
        .where(AgentSystem.user_id == user_id, AgentSystem.topology == "dev_project")
        .order_by(AgentSystem.updated_at.desc())
        .limit(40)
    )
    systems = list(rows.all())
    if matched:
        extra = await db.scalars(
            select(AgentSystem).where(
                AgentSystem.id.in_(matched), AgentSystem.topology == "dev_project"
            )
        )
        rank = {sid: i for i, sid in enumerate(matched)}
        systems = sorted(extra.all(), key=lambda s: rank[s.id]) + systems
    out: dict[str, tuple[str, str, str]] = {}
    for s in systems:
        project = (s.spec or {}).get("project")
        if project and project not in out:
            out[project] = (project, s.name, s.goal)
    listed = list(out.values())[:10]
    rows = await db.scalars(
        select(Project).where(
            Project.user_id == user_id, Project.workbench_id.in_([p for p, _, _ in listed])
        )
    )
    apps = {r.workbench_id: r for r in rows.all()}
    return [(p, n, g, app_status(apps.get(p))) for p, n, g in listed]


NEEDS_EXEC = (
    "Building software needs the Run code permission, which this account doesn't have. An "
    "admin can grant it on the People page."
)


def _problems(
    raw: dict, allowed: set[str], skills: set[str], software: bool = False
) -> tuple[AgentSystemSpec | None, str]:
    data = raw.get("spec")
    if not isinstance(data, dict):
        return (
            None,
            'Reply with {"plan": "...", "spec": {...}}, {"template_id": "..."} or '
            '{"existing": "..."}.',
        )
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
    use_existing: bool = True,
    based_on: str = "",
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
    known = await projects(db, user.id, request)
    # Something already made is opened and changed rather than built again; not when this is
    # already a change to one (based_on) or to a saved system (current).
    past, past_runs = (
        await past_results(db, user.id, request)
        if use_existing and current is None and not based_on
        else ([], {})
    )
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
        past,
        based_on,
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
        plan = str(raw.get("plan") or "")[:2000]
        if raw.get("existing"):
            run = past_runs.get(str(raw["existing"]).strip())
            if run is not None:
                return Design(existing=run, plan=plan)
            problem = "That run id isn't in PAST RESULTS; design a spec instead."
        elif raw.get("template_id"):
            template = by_id.get(str(raw["template_id"]))
            if template is not None:
                return Design(template=template)
            problem = "That template id doesn't exist; design a spec instead."
        else:
            spec, problem = _problems(raw, allowed, slugs, software)
            if spec is not None:
                if spec.project and spec.project not in {p for p, *_ in known}:
                    # Only the user's own projects can be continued.
                    spec.project = None
                if spec.lead.tier == "max" and not best_model:
                    spec.lead.tier = "pro"
                base = past_runs.get(str(raw.get("based_on") or "").strip())
                if raw.get("based_on") and base is None:
                    log.info("Design named an unknown based_on %s; ignored", raw["based_on"])
                if spec.topology == "dev_project":
                    base = None  # Software is continued through its project instead.
                if plan:
                    log.info("Designed %s (%s): %s", spec.name, spec.topology, plan)
                return Design(spec=spec, plan=plan, based_on=base)
        log.info("Design attempt %d rejected: %s", attempt + 1, problem)
        messages += [AIMessage(text), HumanMessage(f"{problem}\nReply with corrected JSON only.")]
    raise DesignFailed(f"Couldn't design a working team for this: {problem}")


def describe(spec: AgentSystemSpec) -> str:
    """A compact summary of a spec for Nexus to explain to the user."""
    lines = [f"{spec.name} ({spec.topology.replace('_', ' ')})"]
    for a in spec.agents:
        extras = ", ".join([*a.tools, *a.skills]) or "no tools"
        wiring = ""
        if spec.topology == "flow":
            wiring = f", checks {a.checks}" if a.checks else ""
            wiring = wiring or (f", after {', '.join(a.after)}" if a.after else ", starts")
        lines.append(f"- {a.name} [{a.id}, {a.role}, {a.tier}{wiring}]: {extras}")
    if spec.deliverables:
        lines.append("Deliverables: " + ", ".join(spec.deliverables))
    return "\n".join(lines)


def spec_json(spec: AgentSystemSpec) -> str:
    return json.dumps(spec.model_dump(), indent=1)
