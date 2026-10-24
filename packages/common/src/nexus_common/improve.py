"""Self-review proposals: checking what the reviewer drafted, and applying or undoing the kinds
that change Nexus's own data (a skill, an instruction, a setting). Code and tool proposals
become pull requests instead, opened by the orchestrator (nexus_orchestrator/improve.py).

Nothing here runs without the owner: the API calls `apply` after they press Approve, and
`revert` after Undo. Each apply records what it changed in `Proposal.result`, which is what
`revert` puts back.
"""

import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import config
from nexus_common.models import (
    Instruction,
    InstructionLayer,
    Proposal,
    ProposalKind,
    Skill,
    SkillScope,
    SkillStatus,
    utcnow,
)
from nexus_common.skills import (
    SkillError,
    add_version,
    content_hash,
    parse_skill,
)
from nexus_common.skills import (
    apply as apply_skill,
)
from nexus_common.skills import slug as skill_slug
from nexus_common.spec import AGENT_ID

LEVELS = ("low", "medium", "high")
LOCAL = (ProposalKind.skill, ProposalKind.instruction, ProposalKind.setting)
TITLE_MAX = 200
TEXT_MAX = 4000
SPEC_MAX = 12000
# Settings a proposal may never change: who can sign in, and where code goes.
UNTOUCHABLE = {
    "signup_policy",
    "access_requests_enabled",
    "dev_login_enabled",
    "improve_repo",
    "improve_base_branch",
    "max_tier_roles",
}
_WORDS = re.compile(r"[a-z0-9]+")


class ProposalError(ValueError):
    """The draft or the apply can't go ahead; the message is for the owner."""


def fingerprint(kind: str, title: str) -> str:
    words = " ".join(_WORDS.findall(title.lower()))
    return hashlib.sha256(f"{kind}|{words}".encode()).hexdigest()[:64]


def _level(v: Any) -> str:
    v = str(v or "").lower().strip()
    return v if v in LEVELS else "medium"


def _text(v: Any, limit: int) -> str:
    return str(v or "").strip()[:limit]


def check(raw: dict, roles: list[str]) -> dict:
    """A proposal as the reviewer drafted it, made safe to store: the right fields for its
    kind, within limits. Raises ProposalError when it can't be used."""
    try:
        kind = ProposalKind(str(raw.get("kind", "")).strip().lower())
    except ValueError:
        raise ProposalError(f"Unknown kind {raw.get('kind')!r}") from None
    title = _text(raw.get("title"), TITLE_MAX)
    if len(title) < 5:
        raise ProposalError("No title")
    evidence = [
        _text(e, 300) for e in (raw.get("evidence") or []) if isinstance(e, str | int | float)
    ][:8]
    out = {
        "kind": kind,
        "title": title,
        "summary": _text(raw.get("why") or raw.get("summary"), TEXT_MAX),
        "evidence": [e for e in evidence if e],
        "impact": _level(raw.get("impact")),
        "effort": _level(raw.get("effort")),
    }
    if kind == ProposalKind.skill:
        s = raw.get("skill") or {}
        name = re.sub(r"[^a-z0-9-]+", "-", str(s.get("name", "")).lower()).strip("-")[:64]
        description = _text(s.get("description"), 1024).replace("\n", " ")
        body = _text(s.get("body"), SPEC_MAX)
        content = f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n\n{body}\n"
        try:
            parsed = parse_skill(content)
        except SkillError as exc:
            raise ProposalError(f"Skill draft invalid: {exc}") from None
        out["payload"] = {
            "name": parsed.name,
            "description": parsed.description,
            "content": parsed.content,
        }
    elif kind == ProposalKind.instruction:
        i = raw.get("instruction") or {}
        layer = str(i.get("layer", "global")).lower()
        role = str(i.get("role") or "").lower().strip()
        text = _text(i.get("text"), TEXT_MAX)
        if not text:
            raise ProposalError("Instruction has no text")
        if layer == "role":
            if not AGENT_ID.match(role) or (roles and role not in roles):
                raise ProposalError(f"Unknown role {role!r}")
        elif layer != "global":
            raise ProposalError("Instruction layer must be global or role")
        out["payload"] = {"layer": layer, "role": role if layer == "role" else "", "text": text}
    elif kind == ProposalKind.setting:
        st = raw.get("setting") or {}
        name = str(st.get("name", ""))
        opt = config.BY_NAME.get(name)
        if opt is None or name in UNTOUCHABLE:
            raise ProposalError(f"Not a setting proposals may change: {name!r}")
        try:
            value = config.validate(opt, st.get("value"))
        except config.ConfigError as exc:
            raise ProposalError(f"{opt.label}: {exc}") from None
        out["payload"] = {"name": name, "label": opt.label, "value": value}
    else:
        c = raw.get("change") or {}
        spec = _text(c.get("spec"), SPEC_MAX)
        if len(spec) < 40:
            raise ProposalError("Code proposal has no spec")
        files = [_text(f, 200) for f in (c.get("files") or []) if isinstance(f, str)][:20]
        out["payload"] = {"spec": spec, "files": [f for f in files if f]}
    return out


def payload(p: Proposal) -> dict:
    return {
        "id": p.id,
        "review_id": p.review_id,
        "kind": p.kind,
        "status": p.status,
        "title": p.title,
        "summary": p.summary,
        "evidence": p.evidence,
        "impact": p.impact,
        "effort": p.effort,
        "payload": p.payload,
        "note": p.note,
        "result": p.result,
        "error": p.error,
        "created_at": p.created_at,
        "decided_at": p.decided_at,
    }


async def _instruction(db: AsyncSession, layer: InstructionLayer, key: str) -> Instruction | None:
    return await db.scalar(
        select(Instruction).where(
            Instruction.layer == layer, Instruction.key == key, Instruction.user_id.is_(None)
        )
    )


async def apply(db: AsyncSession, p: Proposal, actor_id: uuid.UUID) -> tuple[dict, dict | None]:
    """Make a skill, instruction or setting proposal real. Returns what to keep in `result`,
    and for a setting the new overrides and version, which the caller applies and announces
    after it commits."""
    data = p.payload
    if p.kind == ProposalKind.skill:
        slug = skill_slug(SkillScope.domain, data["name"])
        if await db.scalar(select(Skill.id).where(Skill.owner_id.is_(None), Skill.slug == slug)):
            raise ProposalError(f"A shared skill called {data['name']} already exists.")
        parsed = parse_skill(data["content"])
        s = Skill(
            id=uuid.uuid4(),
            scope=SkillScope.domain,
            status=SkillStatus.active,
            owner_id=None,
            source=f"proposal:{p.id}",
            version=1,
            created_by=actor_id,
        )
        apply_skill(s, parsed)
        s.content_hash = content_hash(parsed.content, parsed.files)
        db.add(s)
        await db.flush()
        add_version(db, s, actor_id, "Approved from a self-review proposal")
        return {"skill_id": str(s.id), "skill_slug": s.slug}, None
    if p.kind == ProposalKind.instruction:
        layer = InstructionLayer.role if data["layer"] == "role" else InstructionLayer.global_
        row = await _instruction(db, layer, data.get("role", ""))
        before = row.content if row else None
        if row is None:
            row = Instruction(layer=layer, key=data.get("role", ""), user_id=None, content="")
            db.add(row)
        row.content = "\n\n".join(x for x in (row.content.strip(), data["text"].strip()) if x)
        row.updated_by, row.updated_at = actor_id, utcnow()
        return {"before": before, "after": row.content}, None
    if p.kind == ProposalKind.setting:
        values, version, before = await config.save(db, {data["name"]: data["value"]}, actor_id)
        result = {
            "name": data["name"],
            "had_override": data["name"] in before,
            "before": before.get(data["name"]),
        }
        return result, {"values": values, "version": version}
    raise ProposalError("This kind is opened as a pull request.")


async def revert(db: AsyncSession, p: Proposal, actor_id: uuid.UUID) -> dict | None:
    """Undo an applied local proposal. Returns the config overrides and version for a setting
    (the caller applies and announces them), else None."""
    res = p.result or {}
    if p.kind == ProposalKind.skill:
        s = await db.get(Skill, uuid.UUID(res["skill_id"])) if res.get("skill_id") else None
        if s is not None:
            s.status = SkillStatus.disabled
            s.updated_at = utcnow()
        return None
    if p.kind == ProposalKind.instruction:
        data = p.payload
        layer = InstructionLayer.role if data["layer"] == "role" else InstructionLayer.global_
        row = await _instruction(db, layer, data.get("role", ""))
        if row is not None:
            if row.content != res.get("after"):
                raise ProposalError(
                    "Those instructions were edited since, so undo them on the Skills page's "
                    "Instructions tab instead."
                )
            row.content = res.get("before") or ""
            row.updated_by, row.updated_at = actor_id, utcnow()
        return None
    if p.kind == ProposalKind.setting:
        back = res.get("before") if res.get("had_override") else None
        values, version, _ = await config.save(db, {res["name"]: back}, actor_id)
        return {"values": values, "version": version}
    raise ProposalError("Close the pull request on GitHub to undo a code proposal.")
