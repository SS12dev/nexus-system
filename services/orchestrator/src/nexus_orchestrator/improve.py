"""Self-review: every few days Nexus looks at how it has been used and drafts improvements to
itself, which the owner approves or denies on the Admin page's Improvements tab.

What it looks at, for the time since the last review:

- counts: chat turns and agent runs, and how many failed; each tool's calls, failures and
  time; how teams were designed (roles, tools, topologies); which skills were used and which
  weren't; model calls and cost by purpose (from LiteLLM's spend logs);
- failure messages, with anything that looks like data (numbers, ids, quoted text, addresses)
  taken out, grouped and counted;
- with IMPROVE_READ_REQUESTS on, request topics: the topic model reads each request once (and
  the start of its reply) and names its topic in a few generic words, plus what the assistant
  lacked when it couldn't help. Only the counts of topics are kept, and only topics asked at
  least IMPROVE_MIN_COUNT times. People who turned memory off in Settings are left out.

None of it carries a name, an email, a user id or chat text: `findings` is shown to the owner
as is. The proposal model gets the findings, what Nexus can already do (tools, shared skills,
roles, settings) and earlier proposals with the owner's reasons for denials, and drafts up to
IMPROVE_MAX_PROPOSALS: skills, instructions, settings, tools or code changes. Each is checked
(nexus_common/improve.py) and stored as pending; nothing applies until the owner approves.

An approved code or tool proposal comes back here (`open_pr`) to become a draft pull request on
IMPROVE_REPO with the spec under docs/proposals/, for the owner (or a dev team) to implement
and merge.
"""

import json
import logging
import re
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from nexus_common import config, mail
from nexus_common.improve import UNTOUCHABLE, ProposalError, check, fingerprint
from nexus_common.litellm import ensure_user_key
from nexus_common.llmstats import PURPOSES, Filters, SpendLogs
from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Chat,
    ImprovementReview,
    Message,
    MessageRole,
    MessageStatus,
    Profile,
    Proposal,
    ProposalStatus,
    ReviewStatus,
    Role,
    RoleName,
    RunEvent,
    RunStatus,
    Skill,
    SkillScope,
    SkillStatus,
    SkillUsage,
    User,
    UserRole,
    UserStatus,
    utcnow,
)
from nexus_common.skills import role_names
from nexus_common.toolinfo import TOOLS
from nexus_orchestrator.github import GitHubError
from nexus_orchestrator.llm import parse_json

log = logging.getLogger(__name__)

MAX_WINDOW = timedelta(days=30)
TOPIC_BATCH = 25
REQUEST_CHARS = 400
REPLY_CHARS = 200
MIN_REQUEST_CHARS = 15
# Earlier proposals the reviewer sees, so it doesn't suggest them again.
HISTORY = timedelta(days=180)

TOPICS = """You label requests people made to an AI assistant, for a usage report. For each \
numbered request give:
- topic: what it is about in 2 to 5 generic lowercase words, such as "trip planning", \
"python debugging", "email drafting", "research summary". Never include names, places, \
companies, numbers, dates or anything else that could identify a person.
- gap: if the reply shows the assistant could not do it (no tool, no access, refused, failed), \
what capability was missing in 2 to 6 generic words, such as "read calendar invites" or \
"edit spreadsheets"; otherwise null.
Reply with JSON only: {"items": [{"i": <number>, "topic": str, "gap": str or null}]}"""

PROPOSE = """You improve an AI assistant platform called Nexus, from a report of how it was \
used. Nexus is a supervisor assistant that answers simple requests itself and builds teams of \
sub-agents with tools, shared skills (SKILL.md playbooks), standing instructions per role, and \
model tiers (nexus-nano < mini < pro < max) behind LiteLLM.

Propose the few changes that would help most, based only on evidence in the report: frequent \
topics with no fitting skill, capability gaps, tools that fail or are slow, teams that keep \
being built the same way, failures that recur, spend that a cheaper tier could cut. Skip \
anything the report doesn't support, anything Nexus already has, and anything proposed before \
(see EARLIER; respect the owner's reasons for denials). Fewer, better proposals beat many.

Kinds, and the field each needs:
- "skill": a reusable playbook agents load when relevant. skill: {"name": lowercase-hyphen \
words, "description": one sentence on what it does and when to use it, "body": Markdown \
instructions, at most 500 words, general and concrete}.
- "instruction": text added to standing instructions. instruction: {"layer": "global" or \
"role", "role": one of ROLES when layer is role, "text": the instruction, a few sentences}.
- "setting": change one runtime setting from SETTINGS. setting: {"name", "value"}.
- "tool": a new tool for agents. change: {"spec": Markdown: the problem, the tool's name, \
arguments, what it returns, risk level (read, write, exec, outbound, destructive), where it \
fits in the code, and tests that prove it works, "files": paths likely touched}.
- "code": any other change to Nexus's code. change: {"spec", "files"} as for a tool.
For tool and code proposals, build on HOW NEXUS IS BUILT: name files from its "Where to change \
things" table (never invented paths), say which part of the system changes and why it fits \
there, and list the README section or docs/ page the change must update.

Every proposal also has: "kind", "title" (imperative, under 80 characters), "why" (two or \
three sentences tied to the report's figures), "evidence" (the figures, one short line each), \
"impact" and "effort" (low, medium or high).
Reply with JSON only: {"proposals": [...]} (an empty list is a fine answer)."""

_NOISE = [
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\S+@\S+\.\w+"), "<email>"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f-]{27,}", re.I), "<id>"),
    (re.compile(r"[\"'“‘][^\"'”’]{1,200}[\"'”’]"), "<text>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<id>"),
    (re.compile(r"\d+(\.\d+)?"), "<n>"),
]


def scrub(error: str) -> str:
    """A failure message as a pattern: what went wrong, without the data it went wrong on."""
    text = (error or "").strip().splitlines()[0] if error else ""
    for pattern, repl in _NOISE:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text)[:140] or "(no message)"


def _label(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 +#./-]", " ", (text or "").lower())).strip()[:60]


async def _owner(db) -> User | None:
    return await db.scalar(
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.name == RoleName.owner, User.status == UserStatus.active)
        .order_by(User.created_at)
        .limit(1)
    )


def _model(deps, model: str, api_key: str, review_id: uuid.UUID, name: str, owner: User):
    return deps.model_factory(
        model=model,
        api_key=api_key,
        metadata={
            "trace_id": review_id.hex,
            "trace_name": "nexus.self_review",
            "generation_name": f"nexus.{name}",
            "trace_user_id": str(owner.id),
            "tags": ["nexus", "improve", deps.settings.env],
        },
    ).bind(response_format={"type": "json_object"})


async def _text(model, system: str, prompt: str) -> dict:
    reply = await model.ainvoke([SystemMessage(system), HumanMessage(prompt)])
    return parse_json(reply.text if isinstance(reply.text, str) else reply.text())


# --- What happened ------------------------------------------------------------------------


async def gather(db, deps, start, end) -> dict:
    """Counts over the window, with no names, ids or chat text."""
    in_window = and_(Message.created_at >= start, Message.created_at < end)
    turns = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(Message.status == MessageStatus.failed),
                func.count(func.distinct(Chat.user_id)),
            )
            .join(Chat, Chat.id == Message.chat_id)
            .where(Message.role == MessageRole.assistant, in_window)
        )
    ).one()
    runs = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(AgentRun.status == RunStatus.failed),
                func.coalesce(func.sum(AgentRun.cost_usd), 0),
            ).where(AgentRun.created_at >= start, AgentRun.created_at < end)
        )
    ).one()

    # Tools: the assistant's own steps in chat, then agents' calls in runs.
    tools: dict[str, dict] = {}
    steps = await db.scalars(
        select(Message.steps).where(
            Message.role == MessageRole.assistant, in_window, Message.steps.is_not(None)
        )
    )
    for row in steps:
        for s in row or []:
            t = tools.setdefault(str(s.get("name")), {"calls": 0, "failed": 0, "ms": 0, "timed": 0})
            t["calls"] += 1
            t["failed"] += s.get("status") == "failed"
            if isinstance(s.get("ms"), int | float):
                t["ms"] += s["ms"]
                t["timed"] += 1
    tool_name = RunEvent.data["name"].as_string()
    agent_calls = await db.execute(
        select(tool_name, func.count())
        .where(
            RunEvent.type == "tool.call", RunEvent.created_at >= start, RunEvent.created_at < end
        )
        .group_by(tool_name)
    )
    for name, n in agent_calls.all():
        tools.setdefault(str(name), {"calls": 0, "failed": 0, "ms": 0, "timed": 0})["calls"] += n
    tool_rows = sorted(
        (
            {
                "tool": name,
                "calls": t["calls"],
                "failed": t["failed"],
                "avg_ms": round(t["ms"] / t["timed"]) if t["timed"] else None,
            }
            for name, t in tools.items()
            if name != "None"
        ),
        key=lambda r: (-r["failed"], -r["calls"]),
    )[:25]

    # Failures, as patterns.
    errors = Counter()
    for (e,) in (
        await db.execute(
            select(Message.error).where(
                Message.status == MessageStatus.failed, in_window, Message.error.is_not(None)
            )
        )
    ).all():
        errors["turn: " + scrub(e)] += 1
    for (e,) in (
        await db.execute(
            select(AgentRun.error).where(
                AgentRun.status == RunStatus.failed,
                AgentRun.created_at >= start,
                AgentRun.created_at < end,
                AgentRun.error.is_not(None),
            )
        )
    ).all():
        errors["team run: " + scrub(e)] += 1

    # Teams.
    roles, team_tools, topologies = Counter(), Counter(), Counter()
    systems = (
        await db.execute(
            select(AgentSystem.spec, AgentSystem.topology).where(
                AgentSystem.created_at >= start, AgentSystem.created_at < end
            )
        )
    ).all()
    for spec, topology in systems:
        topologies[topology] += 1
        for a in (spec or {}).get("agents", []):
            roles[str(a.get("role", "?"))] += 1
            for t in a.get("tools", []) or []:
                team_tools[str(t)] += 1

    # Skills: shared ones only (a person's own skills are theirs).
    used = (
        await db.execute(
            select(
                Skill.slug,
                func.count(),
                func.count().filter(SkillUsage.outcome == "failure"),
            )
            .join(SkillUsage, SkillUsage.skill_id == Skill.id)
            .where(
                Skill.owner_id.is_(None),
                SkillUsage.created_at >= start,
                SkillUsage.created_at < end,
            )
            .group_by(Skill.slug)
            .order_by(func.count().desc())
        )
    ).all()
    used_slugs = {u[0] for u in used}
    shared = (
        await db.scalars(
            select(Skill.slug).where(Skill.owner_id.is_(None), Skill.status == SkillStatus.active)
        )
    ).all()
    drafts = await db.scalar(
        select(func.count()).where(
            Skill.scope == SkillScope.learned, Skill.status == SkillStatus.draft
        )
    )

    findings = {
        "activity": {
            "turns": turns[0],
            "failed_turns": turns[1],
            "people": turns[2],
            "team_runs": runs[0],
            "failed_team_runs": runs[1],
            "team_cost_usd": round(float(runs[2]), 2),
            "teams_designed": len(systems),
        },
        "tools": tool_rows,
        "failures": [{"pattern": k, "count": n} for k, n in errors.most_common(12)],
        "teams": {
            "roles": [{"role": k, "count": n} for k, n in roles.most_common(15)],
            "tools": [{"tool": k, "count": n} for k, n in team_tools.most_common(15)],
            "topologies": [{"topology": k, "count": n} for k, n in topologies.most_common()],
        },
        "skills": {
            "used": [{"skill": s, "uses": n, "failed": f} for s, n, f in used[:20]],
            "unused": sorted(s for s in shared if s not in used_slugs)[:40],
            "learned_drafts_waiting": drafts or 0,
        },
    }
    findings["models"] = await _model_costs(deps, start, end)
    return findings


async def _model_costs(deps, start, end) -> list[dict]:
    spend_logs = SpendLogs(deps.settings.spend_logs_url)
    try:
        report = await spend_logs.report(Filters(start, end))
    finally:
        await spend_logs.aclose()
    if not report.get("available"):
        return []
    return [
        {
            "purpose": PURPOSES.get(r["key"], r["key"]),
            "calls": r["calls"],
            "failed": r["failed"],
            "spend_usd": round(r["spend_usd"], 4),
            "cache_hit_rate": r["cache_hit_rate"],
            "p95_ms": r["p95_ms"],
        }
        for r in report["by_purpose"]
    ] + [
        {
            "model": r["key"],
            "calls": r["calls"],
            "failed": r["failed"],
            "spend_usd": round(r["spend_usd"], 4),
        }
        for r in report["by_model"]
    ]


async def topics(db, deps, model, start, end) -> tuple[list[dict], list[dict], int]:
    """(topics, gaps, requests read). Each request is read once by the topic model; only topic
    counts asked at least IMPROVE_MIN_COUNT times come back."""
    reply = aliased(Message)
    next_reply = (
        select(func.left(reply.content, REPLY_CHARS))
        .where(
            reply.chat_id == Message.chat_id,
            reply.role == MessageRole.assistant,
            reply.created_at > Message.created_at,
        )
        .order_by(reply.created_at)
        .limit(1)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(func.left(Message.content, REQUEST_CHARS), next_reply)
            .join(Chat, Chat.id == Message.chat_id)
            .outerjoin(Profile, Profile.user_id == Chat.user_id)
            .where(
                Message.role == MessageRole.user,
                Message.created_at >= start,
                Message.created_at < end,
                func.length(Message.content) >= MIN_REQUEST_CHARS,
                or_(Profile.user_id.is_(None), Profile.memory_enabled.is_(True)),
            )
            .order_by(Message.created_at.desc())
            .limit(deps.settings.improve_max_requests)
        )
    ).all()
    found, gaps = Counter(), Counter()
    for i in range(0, len(rows), TOPIC_BATCH):
        batch = rows[i : i + TOPIC_BATCH]
        prompt = "\n\n".join(
            f"[{n}] REQUEST: {req}\nREPLY STARTS: {rep or '(none)'}"
            for n, (req, rep) in enumerate(batch)
        )
        try:
            data = await _text(model, TOPICS, prompt)
        except Exception:  # noqa: BLE001 - one bad batch shouldn't sink the review
            log.warning("Topic batch failed", exc_info=True)
            continue
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            if topic := _label(item.get("topic")):
                found[topic] += 1
            if (gap := _label(item.get("gap") or "")) and gap not in ("null", "none"):
                gaps[gap] += 1
    least = max(1, deps.settings.improve_min_count)
    return (
        [{"topic": t, "count": n} for t, n in found.most_common(30) if n >= least],
        [{"gap": g, "count": n} for g, n in gaps.most_common(15) if n >= least],
        len(rows),
    )


# --- Proposals ----------------------------------------------------------------------------


async def _context(db, deps) -> str:
    skills = (
        await db.execute(
            select(Skill.slug, Skill.description).where(
                Skill.owner_id.is_(None), Skill.status != SkillStatus.disabled
            )
        )
    ).all()
    earlier = (
        await db.execute(
            select(Proposal.kind, Proposal.title, Proposal.status, Proposal.note).where(
                Proposal.created_at >= utcnow() - HISTORY
            )
        )
    ).all()
    settings = [
        f"- {o.name} ({o.label}): {getattr(deps.settings, o.name)!r}"
        + (f", one of {', '.join(o.choices)}" if o.choices else "")
        for o in config.OPTIONS
        if o.name not in UNTOUCHABLE and o.group != "Self-review"
    ]
    return (
        "HOW NEXUS IS BUILT:\n"
        + architecture(_library(deps))
        + "\n\nTOOLS:\n"
        + "\n".join(f"- {t.name}: {t.description}" for t in TOOLS)
        + "\n\nSHARED SKILLS:\n"
        + "\n".join(f"- {s}: {d[:160]}" for s, d in skills)
        + "\n\nROLES: "
        + ", ".join(role_names(_library(deps)))
        + "\n\nSETTINGS:\n"
        + "\n".join(settings)
        + "\n\nEARLIER:\n"
        + (
            "\n".join(
                f"- [{k}] {t}: {s}" + (f" (owner said: {n})" if n else "") for k, t, s, n in earlier
            )
            or "(none)"
        )
    )


def _library(deps) -> Path:
    return Path(deps.settings.library_path)


# The architecture brief that agents load for tasks about Nexus (docs/INDEX.md points to it).
ARCHITECTURE = Path("core/nexus-system/SKILL.md")
ARCHITECTURE_CHARS = 9000


def architecture(library: Path) -> str:
    """The brief's body, so proposals name real files and parts instead of guessed ones."""
    try:
        text = (library / ARCHITECTURE).read_text(encoding="utf-8")
    except OSError:
        return "(the architecture brief isn't available)"
    body = text.split("---", 2)[-1].strip() if text.startswith("---") else text.strip()
    return body[:ARCHITECTURE_CHARS]


async def _store(db, review: ImprovementReview, drafts: list, roles: list[str], limit: int) -> int:
    seen = set(
        (
            await db.scalars(
                select(Proposal.fingerprint).where(
                    or_(
                        Proposal.status == ProposalStatus.pending,
                        Proposal.created_at >= utcnow() - HISTORY,
                    )
                )
            )
        ).all()
    )
    stored = 0
    for raw in drafts:
        if stored >= limit or not isinstance(raw, dict):
            continue
        try:
            clean = check(raw, roles)
        except ProposalError as exc:
            log.info("Dropped a proposal: %s", exc)
            continue
        fp = fingerprint(clean["kind"], clean["title"])
        if fp in seen:
            continue
        seen.add(fp)
        db.add(
            Proposal(
                review_id=review.id,
                status=ProposalStatus.pending,
                fingerprint=fp,
                result={},
                **clean,
            )
        )
        stored += 1
    return stored


async def run_review(deps, review_id: uuid.UUID) -> None:
    async with deps.sessionmaker() as db:
        review = await db.get(ImprovementReview, review_id)
        if review is None or review.status != ReviewStatus.running:
            return
        owner = await _owner(db)
        if owner is None:
            review.status, review.error = ReviewStatus.failed, "There is no active owner."
            review.finished_at = utcnow()
            await db.commit()
            return
        try:
            stored = await _review(deps, db, review, owner)
        except Exception as exc:
            await db.rollback()
            review = await db.get(ImprovementReview, review_id)
            review.status, review.error = ReviewStatus.failed, f"{type(exc).__name__}: {exc}"[:500]
            review.finished_at = utcnow()
            await db.commit()
            log.exception("Self-review failed")
            return
    log.info("Self-review done: %d proposals", stored, extra={"review_id": str(review_id)})
    if stored:
        await _email(deps, stored)


async def _review(deps, db, review: ImprovementReview, owner: User) -> int:
    settings = deps.settings
    api_key = await ensure_user_key(db, settings, deps.gateway, owner)
    await db.commit()
    start, end = review.window_start, review.window_end
    findings = await gather(db, deps, start, end)
    findings["requests_read"] = 0
    if settings.improve_read_requests:
        model = _model(deps, settings.improve_topic_model, api_key, review.id, "topics", owner)
        found, gaps, read = await topics(db, deps, model, start, end)
        findings |= {"topics": found, "gaps": gaps, "requests_read": read}
    review.findings = findings
    act = findings["activity"]
    if not (act["turns"] or act["team_runs"]):
        review.status, review.finished_at = ReviewStatus.done, utcnow()
        await db.commit()
        return 0

    roles = role_names(_library(deps))
    prompt = (
        f"REPORT ({start:%d %b} to {end:%d %b %Y}):\n{json.dumps(findings, indent=1)}\n\n"
        + await _context(db, deps)
        + f"\n\nPropose at most {settings.improve_max_proposals}."
    )
    model = _model(deps, settings.improve_model, api_key, review.id, "propose", owner)
    data = await _text(model, PROPOSE, prompt)
    stored = await _store(
        db, review, data.get("proposals") or [], roles, settings.improve_max_proposals
    )
    review.status, review.proposals, review.finished_at = ReviewStatus.done, stored, utcnow()
    await db.commit()
    return stored


async def scheduled(deps) -> uuid.UUID | None:
    """The daily check: start a review when the last one is IMPROVE_EVERY_DAYS old."""
    every = deps.settings.improve_every_days
    if every <= 0:
        return None
    now = utcnow()
    async with deps.sessionmaker() as db:
        last = await db.scalar(
            select(ImprovementReview)
            .where(ImprovementReview.status != ReviewStatus.failed)
            .order_by(ImprovementReview.created_at.desc())
            .limit(1)
        )
        # An hour's slack, so a review a day late doesn't wait another day.
        if last is not None and last.created_at > now - timedelta(days=every) + timedelta(hours=1):
            return None
        start = last.window_end if last else now - timedelta(days=every)
        review = ImprovementReview(
            status=ReviewStatus.running,
            trigger="schedule",
            window_start=max(start, now - MAX_WINDOW),
            window_end=now,
            findings={},
            proposals=0,
        )
        db.add(review)
        await db.commit()
    await run_review(deps, review.id)
    return review.id


async def _email(deps, count: int) -> None:
    settings = deps.settings
    if not settings.smtp_enabled:
        return
    async with deps.sessionmaker() as db:
        to = list(
            (
                await db.scalars(
                    select(User.email)
                    .join(UserRole, UserRole.user_id == User.id)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(Role.name == RoleName.owner, User.status == UserStatus.active)
                )
            ).all()
        )
    if not to:
        return
    noun = "improvement" if count == 1 else "improvements"
    body = (
        f"Nexus reviewed how it has been used and drafted {count} {noun} for you to approve "
        f"or deny:\n{settings.nexus_url.rstrip('/')}/admin?tab=improvements\n\n"
        "Nothing changes until you approve."
    )
    try:
        await mail.send(
            settings, mail.message(settings, to, f"Nexus: {count} {noun} to review", body)
        )
    except mail.MailError as exc:
        log.warning("Could not email about proposals: %s", exc)


# --- Approved code proposals ----------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-") or "change"


def pr_files(p: Proposal) -> tuple[str, dict[str, str]]:
    """The branch and the spec file an approved code or tool proposal is committed as."""
    slug = _slug(p.title)
    evidence = "\n".join(f"- {e}" for e in p.evidence) or "- (none listed)"
    files = "\n".join(f"- `{f}`" for f in p.payload.get("files", [])) or "- (not listed)"
    spec = (
        f"# {p.title}\n\n"
        f"_Kind: {p.kind} · impact {p.impact} · effort {p.effort} · drafted by Nexus's "
        f"self-review on {p.created_at:%d %b %Y}, approved on the Admin page._\n\n"
        f"## Why\n\n{p.summary}\n\n## Evidence\n\n{evidence}\n\n"
        f"## Spec\n\n{p.payload.get('spec', '')}\n\n## Files likely touched\n\n{files}\n\n"
        "## Before you start\n\nRead `docs/INDEX.md` and the architecture brief in "
        "`library/core/nexus-system/SKILL.md`, follow the README section for this area, and "
        "update the docs this change affects (and the brief, if the architecture changes). "
        "Check with `./scripts/check.sh`.\n"
    )
    name = f"docs/proposals/{p.created_at:%Y-%m-%d}-{slug}.md"
    return f"nexus/proposal-{slug}-{p.id.hex[:8]}", {name: spec}


async def open_pr(deps, proposal_id: uuid.UUID) -> None:
    settings = deps.settings
    async with deps.sessionmaker() as db:
        p = await db.get(Proposal, proposal_id)
        if p is None or p.status != ProposalStatus.applying:
            return
        branch, files = pr_files(p)
        try:
            if deps.github is None or not settings.improve_repo:
                raise GitHubError("GitHub isn't set up, or IMPROVE_REPO is empty.")
            body = (
                f"{p.summary}\n\n**Evidence**\n\n"
                + "\n".join(f"- {e}" for e in p.evidence)
                + f"\n\nThe spec is in `{next(iter(files))}`. Nexus's self-review drafted it and "
                "the owner approved it on the Admin page. Implement it on this branch (or close "
                "this pull request to drop it), then mark it ready and merge."
            )
            url = await deps.github.open_pull_request(
                settings.improve_repo,
                base=settings.improve_base_branch,
                branch=branch,
                title=p.title,
                body=body,
                files=files,
                message=f"Proposal: {p.title}",
            )
        except GitHubError as exc:
            p.status, p.error = ProposalStatus.failed, str(exc)
            await db.commit()
            log.warning("Proposal pull request failed: %s", exc)
            return
        p.status, p.error = ProposalStatus.applied, None
        p.result = {"pr_url": url, "branch": branch, "repo": settings.improve_repo}
        await db.commit()
