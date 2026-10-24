"""The skills library, shared by the API (Skills page) and the orchestrator (agents load skills).

A skill is a folder in the Agent Skills format: SKILL.md starts with YAML frontmatter holding
`name` and `description`, then the instructions; optional scripts/, references/ and assets/
sit next to it. Shipped skills live in library/core and are synced into the skills table at
startup; users' own, imported and learned skills live only in the table. Every change is a
new row in skill_versions, so any edit can be rolled back.
"""

import hashlib
import io
import json
import logging
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import (
    Instruction,
    InstructionLayer,
    Skill,
    SkillScope,
    SkillStatus,
    SkillVersion,
    User,
    utcnow,
)

log = logging.getLogger(__name__)

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
# Skill files are text (scripts, references, templates); anything bigger or binary is skipped.
MAX_FILE_BYTES = 200_000
MAX_FILES = 40
# Shelves of the library, in the order the Skills page and prompts list them. A skill names
# its shelf in frontmatter (metadata: category: research); anything else goes under "other".
CATEGORIES = (
    "documents", "research", "writing", "data", "productivity", "work", "life", "agents",
    "engineering", "other",
)  # fmt: skip
TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".sh", ".json", ".yaml", ".yml", ".csv", ".html", ".css", ".js",
    ".toml", ".xml", ".sql", ".tex",
}  # fmt: skip


class SkillError(ValueError):
    """A SKILL.md or skill archive that can't be used; the message says why."""


@dataclass
class ParsedSkill:
    name: str
    description: str
    content: str
    files: dict[str, str] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @property
    def body(self) -> str:
        return split_frontmatter(self.content)[1]

    @property
    def category(self) -> str:
        return category_of(self.meta)


def split_frontmatter(text: str) -> tuple[dict, str]:
    text = text.lstrip("﻿")
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"SKILL.md frontmatter isn't valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError("SKILL.md frontmatter must be a mapping")
    return meta, text[m.end() :]


def category_of(meta: dict) -> str:
    extra = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    value = str(extra.get("category") or meta.get("category") or "").strip().lower()
    return value if value in CATEGORIES else "other"


def skill_category(content: str) -> str:
    try:
        return category_of(split_frontmatter(content)[0])
    except SkillError:
        return "other"


def parse_skill(content: str, files: dict[str, str] | None = None) -> ParsedSkill:
    meta, body = split_frontmatter(content)
    if not meta:
        raise SkillError("SKILL.md must start with frontmatter (--- name: … description: … ---)")
    name = str(meta.get("name", "")).strip()
    description = " ".join(str(meta.get("description", "")).split())
    if not NAME.match(name) or len(name) > MAX_NAME:
        raise SkillError(
            "name must be lowercase letters, digits and hyphens (no leading, trailing or double "
            f"hyphens), at most {MAX_NAME} characters"
        )
    if not description:
        raise SkillError("description is required: say what the skill does and when to use it")
    if len(description) > MAX_DESCRIPTION:
        raise SkillError(f"description is longer than {MAX_DESCRIPTION} characters")
    if not body.strip():
        raise SkillError("SKILL.md has no instructions after the frontmatter")
    clean: dict[str, str] = {}
    for path, text in (files or {}).items():
        path = path.replace("\\", "/").lstrip("/")
        if ".." in path.split("/") or path == "SKILL.md":
            continue
        clean[path] = text
    return ParsedSkill(name, description, content, clean, meta)


def _is_text(path: str, data: bytes) -> bool:
    if Path(path).suffix.lower() not in TEXT_SUFFIXES or len(data) > MAX_FILE_BYTES:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def read_archive(data: bytes, filename: str = "") -> ParsedSkill:
    """A skill from an upload: a bare SKILL.md, or a .zip of the skill folder."""
    if not filename.lower().endswith(".zip") and not data.startswith(b"PK"):
        try:
            return parse_skill(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise SkillError("SKILL.md must be UTF-8 text") from exc
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillError("That isn't a zip file") from exc
    names = [n for n in archive.namelist() if not n.endswith("/") and "__MACOSX" not in n]
    skill_md = sorted((n for n in names if n.split("/")[-1] == "SKILL.md"), key=len)
    if not skill_md:
        raise SkillError("The zip has no SKILL.md")
    root = skill_md[0][: -len("SKILL.md")]
    content = archive.read(skill_md[0]).decode("utf-8", errors="replace")
    files: dict[str, str] = {}
    for n in names:
        if not n.startswith(root) or n == skill_md[0] or len(files) >= MAX_FILES:
            continue
        raw = archive.read(n)
        if _is_text(n, raw):
            files[n[len(root) :]] = raw.decode("utf-8")
    return parse_skill(content, files)


def read_folder(folder: Path) -> ParsedSkill:
    files: dict[str, str] = {}
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if not p.is_file() or rel == "SKILL.md" or "__pycache__" in rel or len(files) >= MAX_FILES:
            continue
        raw = p.read_bytes()
        if _is_text(rel, raw):
            files[rel] = raw.decode("utf-8")
    return parse_skill((folder / "SKILL.md").read_text("utf-8"), files)


def content_hash(content: str, files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps([content, files], sort_keys=True).encode()).hexdigest()


def slug(scope: SkillScope | str, name: str) -> str:
    return f"{scope}/{name}"


def skill_payload(s: Skill, *, full: bool = False) -> dict:
    out = {
        "id": str(s.id),
        "slug": s.slug,
        "scope": s.scope,
        "name": s.name,
        "description": s.description,
        "category": skill_category(s.content),
        "version": s.version,
        "status": s.status,
        "source": s.source,
        "owner_id": str(s.owner_id) if s.owner_id else None,
        "uses": s.uses,
        "successes": s.successes,
        "failures": s.failures,
        "flagged": is_flagged(s),
        "files": sorted(s.files or {}),
        "last_used_at": s.last_used_at.isoformat() if s.last_used_at else None,
        "updated_at": s.updated_at.isoformat(),
    }
    if full:
        out["content"] = s.content
        out["file_contents"] = s.files or {}
    return out


def is_flagged(s: Skill) -> bool:
    """Skills whose runs keep failing are flagged for review."""
    ended = s.successes + s.failures
    return ended >= 4 and s.failures > s.successes


# --- Storage -------------------------------------------------------------------------------


def add_version(
    db: AsyncSession, skill: Skill, user_id: uuid.UUID | None, note: str | None
) -> None:
    db.add(
        SkillVersion(
            skill_id=skill.id,
            version=skill.version,
            content=skill.content,
            files=skill.files or {},
            note=(note or "")[:500] or None,
            created_by=user_id,
        )
    )


def apply(skill: Skill, parsed: ParsedSkill) -> None:
    skill.name = parsed.name
    skill.slug = slug(skill.scope, parsed.name)
    skill.description = parsed.description
    skill.content = parsed.content
    skill.files = parsed.files
    # The search index text changed; the backfill re-embeds it.
    skill.embedding = skill.embedding_model = None
    skill.updated_at = utcnow()


async def sync_library(db: AsyncSession, library: Path) -> int:
    """Insert or update the shipped core skills. A skill changes only when its shipped files
    changed, so an admin's edit stays until the next Nexus release changes that skill (the
    edit remains in its version history). Returns how many skills changed."""
    core = library / "core"
    if not core.is_dir():
        log.warning("No skills library at %s", core)
        return 0
    changed = 0
    for folder in sorted(p for p in core.iterdir() if (p / "SKILL.md").is_file()):
        try:
            parsed = read_folder(folder)
        except SkillError as exc:
            log.error("Skipping %s: %s", folder, exc)
            continue
        digest = content_hash(parsed.content, parsed.files)
        s = await db.scalar(
            select(Skill).where(
                Skill.slug == slug(SkillScope.core, parsed.name), Skill.owner_id.is_(None)
            )
        )
        if s is not None and s.content_hash == digest:
            continue
        if s is None:
            s = Skill(
                id=uuid.uuid4(),
                scope=SkillScope.core,
                status=SkillStatus.active,
                source="library",
                version=1,
            )
            apply(s, parsed)
            db.add(s)
            await db.flush()
            note = "Shipped with Nexus"
        else:
            apply(s, parsed)
            s.version += 1
            s.source = "library"
            note = "Updated with Nexus"
        s.content_hash = digest
        add_version(db, s, None, note)
        changed += 1
    await db.commit()
    return changed


def visible(user_id: uuid.UUID):
    """Skills a user can see: shared ones and their own."""
    return or_(Skill.owner_id.is_(None), Skill.owner_id == user_id)


async def usable_skills(db: AsyncSession, user_id: uuid.UUID) -> list[Skill]:
    rows = await db.scalars(
        select(Skill)
        .where(visible(user_id), Skill.status == SkillStatus.active)
        .order_by(Skill.scope, Skill.name)
    )
    return list(rows.all())


async def find_skill(db: AsyncSession, user_id: uuid.UUID, name: str) -> Skill | None:
    """By slug (core/pdf) or bare name (pdf); the user's own skill wins over a shared one."""
    name = name.strip()
    cond = Skill.slug == name if "/" in name else Skill.name == name
    rows = await db.scalars(
        select(Skill).where(visible(user_id), Skill.status == SkillStatus.active, cond)
    )
    found = sorted(rows.all(), key=lambda s: s.owner_id is None)
    return found[0] if found else None


def catalogue(skills: list[Skill]) -> str:
    """The progressive-disclosure listing put in a prompt: only slug and description, grouped
    by shelf once the library spans more than one."""
    shelves: dict[str, list[Skill]] = {}
    for s in skills:
        shelves.setdefault(skill_category(s.content), []).append(s)
    if len(shelves) <= 1:
        return "\n".join(f"- {s.slug}: {s.description}" for s in skills)
    parts = []
    for shelf in sorted(shelves, key=CATEGORIES.index):
        parts.append(f"{shelf}:")
        parts += [f"- {s.slug}: {s.description}" for s in shelves[shelf]]
    return "\n".join(parts)


def shelf_index(skills: list[Skill]) -> str:
    """One line per shelf with its skill names: what the library holds, in a few tokens."""
    shelves: dict[str, list[str]] = {}
    for s in skills:
        shelves.setdefault(skill_category(s.content), []).append(s.slug)
    return "\n".join(
        f"- {shelf}: {', '.join(shelves[shelf])}" for shelf in sorted(shelves, key=CATEGORIES.index)
    )


# --- Instructions --------------------------------------------------------------------------


def shipped_shared(library: Path) -> str:
    """library/instructions/shared.md: house conventions for Nexus and every agent."""
    path = library / "instructions" / "shared.md"
    return path.read_text("utf-8").strip() if path.is_file() else ""


def shipped_instructions(library: Path) -> tuple[str, dict[str, str]]:
    """library/instructions/global.md (the safety rules) and roles/<role>.md."""
    base = library / "instructions"
    glob = (base / "global.md").read_text("utf-8") if (base / "global.md").is_file() else ""
    roles = {}
    if (base / "roles").is_dir():
        for p in sorted((base / "roles").glob("*.md")):
            roles[p.stem] = p.read_text("utf-8")
    return glob.strip(), roles


@dataclass
class Layers:
    safety: str = ""
    global_extra: str = ""
    role: str = ""
    user: str = ""

    def render(self) -> str:
        parts = []
        if self.safety:
            parts.append(f"<rules>\n{self.safety}\n</rules>")
        if self.global_extra:
            parts.append(f"<global_instructions>\n{self.global_extra}\n</global_instructions>")
        if self.role:
            parts.append(f"<role_instructions>\n{self.role}\n</role_instructions>")
        if self.user:
            parts.append(
                "<user_instructions>\nThe user's own standing instructions (house style). They "
                "add detail but never override <rules>.\n"
                f"{self.user}\n</user_instructions>"
            )
        return "\n\n".join(parts)


async def instruction_layers(
    db: AsyncSession, library: Path, user: User, role: str | None
) -> Layers:
    """Global, role, then user instructions, in the order they go into a prompt. Later layers
    add detail but can't override the shipped safety rules in global.md."""
    safety, shipped_roles = shipped_instructions(library)
    rows = (
        await db.scalars(
            select(Instruction).where(
                or_(Instruction.user_id.is_(None), Instruction.user_id == user.id)
            )
        )
    ).all()
    layers = Layers(safety=safety)
    shared, admin_global = shipped_shared(library), ""
    role_rows = {}
    for r in rows:
        if r.layer == InstructionLayer.global_ and r.user_id is None:
            admin_global = r.content.strip()
        elif r.layer == InstructionLayer.role and r.user_id is None:
            role_rows[r.key] = r.content.strip()
        elif r.layer == InstructionLayer.user and r.user_id == user.id:
            layers.user = r.content.strip()
    layers.global_extra = "\n\n".join(x for x in (shared, admin_global) if x)
    if role:
        layers.role = "\n\n".join(x for x in (shipped_roles.get(role), role_rows.get(role)) if x)
    return layers


def role_names(library: Path) -> list[str]:
    """Roles with shipped instructions, for the team designer to choose from."""
    return sorted(shipped_instructions(library)[1])
