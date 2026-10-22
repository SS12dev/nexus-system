"""The Skills page: browse the shared skills library and your own skills, write or import one,
edit with version history and rollback, enable or disable, and review skills Nexus drafted.

Who can change what: your personal and learned skills are yours; core (shipped with Nexus) and
domain (shared) skills are changed by admins. Imported and learned skills start as drafts and
stay off until someone who may edit them enables them. Core skills can be disabled, not deleted.
"""

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.deps import AdminUser, CurrentUser, DbDep, EmbedderDep, MemberUser, RedisDep
from nexus_api.schemas import SkillCreateIn, SkillRollbackIn, SkillStatusIn, SkillUpdateIn
from nexus_common.events import Event, publish
from nexus_common.models import (
    AuditLog,
    RoleName,
    Skill,
    SkillScope,
    SkillStatus,
    SkillUsage,
    SkillVersion,
    User,
)
from nexus_common.search import hybrid_search
from nexus_common.skills import (
    SkillError,
    add_version,
    apply,
    content_hash,
    parse_skill,
    read_archive,
    skill_payload,
)
from nexus_common.skills import slug as skill_slug

router = APIRouter(prefix="/api/skills", tags=["skills"])

MAX_ARCHIVE = 5 * 1024 * 1024


def can_edit(user: User, s: Skill) -> bool:
    if s.owner_id is not None:
        return s.owner_id == user.id
    return user.has_role(RoleName.admin)


def listed(user: User):
    """Your own skills in any state; shared ones that are on (admins also see the rest)."""
    shared = Skill.owner_id.is_(None)
    if not user.has_role(RoleName.admin):
        shared = shared & (Skill.status == SkillStatus.active)
    return or_(shared, Skill.owner_id == user.id)


async def get_skill(db: AsyncSession, user: User, skill_id: uuid.UUID) -> Skill:
    s = await db.scalar(select(Skill).where(Skill.id == skill_id, listed(user)))
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
    return s


async def editable(db: AsyncSession, user: User, skill_id: uuid.UUID) -> Skill:
    s = await get_skill(db, user, skill_id)
    if not can_edit(user, s):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can change shared skills")
    return s


def out(s: Skill, user: User, *, full: bool = False) -> dict:
    return {**skill_payload(s, full=full), "can_edit": can_edit(user, s)}


async def announce(redis, user: User, s: Skill) -> None:
    # Shared skills change for everyone, but only the editor's open pages need refreshing now.
    await publish(redis, user.id, Event(type="skill.updated", data={"skill": skill_payload(s)}))


async def _unique(db: AsyncSession, user: User, scope: SkillScope, name: str) -> None:
    owner = None if scope in (SkillScope.core, SkillScope.domain) else user.id
    cond = Skill.owner_id.is_(None) if owner is None else Skill.owner_id == owner
    if await db.scalar(select(Skill.id).where(cond, Skill.slug == skill_slug(scope, name))):
        raise HTTPException(status.HTTP_409_CONFLICT, f"A {scope} skill called {name} exists")


async def _create(
    db: AsyncSession, user: User, content: str, files: dict, scope: str, *, source: str
) -> Skill:
    try:
        parsed = parse_skill(content, files)
        scope_ = SkillScope(scope)
    except (SkillError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if scope_ not in (SkillScope.personal, SkillScope.domain):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "scope: personal or domain")
    if scope_ == SkillScope.domain and not user.has_role(RoleName.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can add shared skills")
    await _unique(db, user, scope_, parsed.name)
    # What you write yourself is on straight away; imports and shared skills wait for review.
    active = source == "editor" and scope_ == SkillScope.personal
    s = Skill(
        id=uuid.uuid4(),
        scope=scope_,
        status=SkillStatus.active if active else SkillStatus.draft,
        owner_id=user.id if scope_ == SkillScope.personal else None,
        source=source,
        version=1,
    )
    apply(s, parsed)
    s.content_hash = content_hash(parsed.content, parsed.files)
    db.add(s)
    await db.flush()
    add_version(db, s, user.id, "Created" if source == "editor" else "Imported")
    db.add(
        AuditLog(
            actor_id=user.id,
            action="skill.create",
            target=s.slug,
            data={"source": source, "status": s.status},
        )
    )
    await db.commit()
    return s


@router.get("")
async def list_skills(
    user: CurrentUser,
    db: DbDep,
    embedder: EmbedderDep,
    q: str | None = Query(default=None, max_length=300),
    scope: SkillScope | None = None,
    status_: SkillStatus | None = Query(default=None, alias="status"),
):
    if q:
        vec, _ = await embedder.try_embed_one(db, q)
        hits = await hybrid_search(db, "skills", user.id, q, vec, 30)
        rows = await db.scalars(select(Skill).where(Skill.id.in_([h.id for h in hits])))
        by_id = {s.id: s for s in rows.all()}
        return [out(by_id[h.id], user) for h in hits if h.id in by_id]
    query = select(Skill).where(listed(user))
    if scope is not None:
        query = query.where(Skill.scope == scope)
    if status_ is not None:
        query = query.where(Skill.status == status_)
    rows = await db.scalars(query.order_by(Skill.scope, Skill.name))
    return [out(s, user) for s in rows.all()]


@router.get("/{skill_id}")
async def get_one(skill_id: uuid.UUID, user: CurrentUser, db: DbDep):
    s = await get_skill(db, user, skill_id)
    recent = (
        await db.scalars(
            select(SkillUsage)
            .where(SkillUsage.skill_id == s.id, SkillUsage.user_id == user.id)
            .order_by(SkillUsage.created_at.desc())
            .limit(20)
        )
    ).all()
    return {
        **out(s, user, full=True),
        "recent_uses": [
            {
                "run_id": str(u.run_id) if u.run_id else None,
                "agent": u.agent,
                "outcome": u.outcome,
                "at": u.created_at.isoformat(),
            }
            for u in recent
        ],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_skill(body: SkillCreateIn, user: MemberUser, db: DbDep, redis: RedisDep):
    s = await _create(db, user, body.content, body.files, body.scope, source="editor")
    await announce(redis, user, s)
    return out(s, user, full=True)


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_skill(
    user: MemberUser,
    db: DbDep,
    redis: RedisDep,
    file: UploadFile = File(...),
    scope: str = Form("personal"),
):
    """A SKILL.md, or a .zip of a skill folder (SKILL.md plus scripts/ and references/)."""
    data = await file.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Skills are limited to 5 MB")
    try:
        parsed = read_archive(data, file.filename or "")
    except SkillError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    s = await _create(db, user, parsed.content, parsed.files, scope, source="import")
    await announce(redis, user, s)
    return out(s, user, full=True)


@router.put("/{skill_id}")
async def edit_skill(
    skill_id: uuid.UUID, body: SkillUpdateIn, user: MemberUser, db: DbDep, redis: RedisDep
):
    s = await editable(db, user, skill_id)
    files = s.files or {} if body.files is None else body.files
    try:
        parsed = parse_skill(body.content, files)
    except SkillError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if parsed.name != s.name:
        await _unique(db, user, SkillScope(s.scope), parsed.name)
    if content_hash(parsed.content, parsed.files) == content_hash(s.content, s.files or {}):
        return out(s, user, full=True)
    apply(s, parsed)
    s.version += 1
    add_version(db, s, user.id, body.note or "Edited")
    db.add(
        AuditLog(actor_id=user.id, action="skill.edit", target=s.slug, data={"version": s.version})
    )
    await db.commit()
    await announce(redis, user, s)
    return out(s, user, full=True)


@router.post("/{skill_id}/status")
async def set_status(
    skill_id: uuid.UUID, body: SkillStatusIn, user: MemberUser, db: DbDep, redis: RedisDep
):
    """Enable (this is also how a draft is approved) or disable a skill."""
    s = await editable(db, user, skill_id)
    s.status = SkillStatus(body.status)
    db.add(AuditLog(actor_id=user.id, action=f"skill.{body.status}", target=s.slug))
    await db.commit()
    await announce(redis, user, s)
    return out(s, user)


@router.post("/{skill_id}/share")
async def share_skill(skill_id: uuid.UUID, user: AdminUser, db: DbDep, redis: RedisDep):
    """Move one of your own skills (written, imported or learned) into the shared library, on
    for everyone. It keeps its version history and usage; from then on admins edit it."""
    s = await get_skill(db, user, skill_id)
    if s.owner_id != user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only your own skills can be shared")
    try:
        parse_skill(s.content, s.files or {})
    except SkillError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await _unique(db, user, SkillScope.domain, s.name)
    old = s.slug
    s.scope = SkillScope.domain
    s.owner_id = None
    s.slug = skill_slug(SkillScope.domain, s.name)
    s.status = SkillStatus.active
    s.version += 1
    add_version(db, s, user.id, f"Shared with everyone (was {old})")
    db.add(AuditLog(actor_id=user.id, action="skill.share", target=s.slug, data={"from": old}))
    await db.commit()
    await announce(redis, user, s)
    return out(s, user, full=True)


@router.get("/{skill_id}/versions")
async def versions(skill_id: uuid.UUID, user: CurrentUser, db: DbDep):
    s = await get_skill(db, user, skill_id)
    rows = await db.scalars(
        select(SkillVersion)
        .where(SkillVersion.skill_id == s.id)
        .order_by(SkillVersion.version.desc())
    )
    return [
        {
            "version": v.version,
            "note": v.note,
            "content": v.content,
            "files": sorted(v.files or {}),
            "created_at": v.created_at.isoformat(),
            "created_by": str(v.created_by) if v.created_by else None,
        }
        for v in rows.all()
    ]


@router.post("/{skill_id}/rollback")
async def rollback(
    skill_id: uuid.UUID, body: SkillRollbackIn, user: MemberUser, db: DbDep, redis: RedisDep
):
    """Bring back an earlier version, as a new version (history is never rewritten)."""
    s = await editable(db, user, skill_id)
    v = await db.scalar(
        select(SkillVersion).where(
            SkillVersion.skill_id == s.id, SkillVersion.version == body.version
        )
    )
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such version")
    try:
        parsed = parse_skill(v.content, v.files or {})
    except SkillError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    apply(s, parsed)
    s.version += 1
    add_version(db, s, user.id, f"Rolled back to version {body.version}")
    db.add(
        AuditLog(
            actor_id=user.id,
            action="skill.rollback",
            target=s.slug,
            data={"to": body.version, "version": s.version},
        )
    )
    await db.commit()
    await announce(redis, user, s)
    return out(s, user, full=True)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(skill_id: uuid.UUID, user: MemberUser, db: DbDep):
    s = await editable(db, user, skill_id)
    if s.scope == SkillScope.core:
        raise HTTPException(status.HTTP_409_CONFLICT, "Core skills can be disabled, not deleted")
    db.add(AuditLog(actor_id=user.id, action="skill.delete", target=s.slug))
    await db.delete(s)
    await db.commit()
