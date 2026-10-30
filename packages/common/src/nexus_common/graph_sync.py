"""Files the app's own records into each user's knowledge graph without any model: software
projects (and their running apps), library project folders and routines become entities in
the activity shell, tied to the user. Runs with the worker's ten-minute jobs, so a new project
or routine is in the graph within minutes, and one that is deleted or put in Trash leaves it.

An entity the model already made under the same name ("Ledgerly" from a chat) is adopted
rather than duplicated, so what chats said about it stays attached.

Also keeps the ontology tables seeded and gives relations from before the ontology their
canonical predicate and family.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexus_common import ontology
from nexus_common.graph import add_evidence, norm, self_entity
from nexus_common.models import (
    Entity,
    Folder,
    Mention,
    Profile,
    Project,
    Relation,
    Routine,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.trash import visible

log = logging.getLogger(__name__)

RELATIONS_PER_RUN = 2000


@dataclass(frozen=True)
class Record:
    """One app record as the graph sees it."""

    kind: str  # project, folder or routine
    id: uuid.UUID
    name: str
    type: str  # ontology type
    predicate: str  # how the user relates to it
    summary: str
    chat_id: uuid.UUID | None = None


async def records(db: AsyncSession, user_id: uuid.UUID) -> list[Record]:
    """The user's projects, folders and routines that should be in the graph."""
    out: list[Record] = []
    for p in (
        await db.scalars(
            select(Project).where(
                Project.user_id == user_id,
                visible("project", Project.id),
                visible("app", Project.id),
            )
        )
    ).all():
        live = p.live_url and p.app_state != "failed"
        out.append(
            Record(
                "project",
                p.id,
                p.name,
                "app" if live else "project",
                "built",
                f"App at {p.live_url}"
                if live
                else f"Software project{f' ({p.stack})' if p.stack else ''}",
                p.chat_id,
            )
        )
    for f in (
        await db.scalars(
            select(Folder).where(
                Folder.user_id == user_id,
                # A dev project's folder is that project.
                Folder.project_id.is_(None),
                visible("folder", Folder.id),
            )
        )
    ).all():
        out.append(
            Record(
                "folder",
                f.id,
                f.name,
                "project",
                "works on",
                "Project folder in the library",
                f.chat_id,
            )
        )
    for r in (await db.scalars(select(Routine).where(Routine.user_id == user_id))).all():
        state = "" if r.enabled else " (paused)"
        out.append(
            Record("routine", r.id, r.name, "routine", "runs", f"Routine{state}: {r.schedule}")
        )
    return out


async def sync_user(db: AsyncSession, user: User) -> int:
    """Bring one user's app-record entities in line with their records. Returns changes."""
    me = await self_entity(db, user)
    wanted = await records(db, user.id)
    existing = {
        (e.source_kind, e.source_id): e
        for e in (
            await db.scalars(
                select(Entity).where(Entity.user_id == user.id, Entity.source_kind.is_not(None))
            )
        ).all()
    }
    unsourced = {
        (norm(e.name), ontology.shell_of(e.type)): e
        for e in (
            await db.scalars(
                select(Entity).where(
                    Entity.user_id == user.id,
                    Entity.source_kind.is_(None),
                    ~Entity.is_self,
                )
            )
        ).all()
    }
    now, changes = utcnow(), 0
    for rec in wanted:
        e = existing.pop((rec.kind, rec.id), None)
        if e is None:
            # A project the chats already talked about: adopt it.
            e = unsourced.pop((norm(rec.name), 3), None)
            if e is None:
                e = Entity(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    name=rec.name,
                    aliases=[],
                    summary=rec.summary,
                    created_at=now,
                    seen_at=now,
                )
                db.add(e)
            e.source_kind, e.source_id, e.origin = rec.kind, rec.id, "app"
            changes += 1
        if (e.name, e.type, e.shell) != (rec.name, rec.type, 3):
            e.name, e.type, e.shell = rec.name, rec.type, 3
            changes += 1
        if not e.summary:
            e.summary = rec.summary
        await db.flush()
        changes += await _tie(db, user.id, me, e, rec, now)

    # Records that are gone or in Trash: drop their entity unless chats or memories mention
    # it, in which case it stays as an ordinary entity.
    for e in existing.values():
        mentioned = await db.scalar(select(Mention.id).where(Mention.entity_id == e.id).limit(1))
        if mentioned is None:
            await db.execute(delete(Entity).where(Entity.id == e.id))
        else:
            e.source_kind = e.source_id = None
            e.origin = "model"
        changes += 1
    await db.flush()
    return changes


async def _tie(db: AsyncSession, user_id, me: Entity, e: Entity, rec: Record, now) -> int:
    """The user's relation to the record (USER built / works on / runs it)."""
    canon = ontology.canonical(rec.predicate)
    rel = await db.scalar(
        select(Relation).where(
            Relation.user_id == user_id,
            Relation.src_id == me.id,
            Relation.dst_id == e.id,
            Relation.predicate == canon.predicate,
            Relation.valid_to.is_(None),
        )
    )
    if rel is not None:
        return 0
    rel = Relation(
        id=uuid.uuid4(),
        user_id=user_id,
        src_id=me.id,
        dst_id=e.id,
        predicate=canon.predicate,
        family=canon.family,
        label=canon.label,
        fact="",
        confidence=1.0,
        weight=0.8,
        evidence_count=1 if rec.chat_id else 0,
        origin="app",
        valid_from=now,
        updated_at=now,
        last_seen=now,
    )
    db.add(rel)
    await db.flush()
    if rec.chat_id:
        await add_evidence(db, user_id, rel.id, chat_id=rec.chat_id)
    return 1


async def canonicalise(db: AsyncSession, limit: int = RELATIONS_PER_RUN) -> int:
    """Give relations made before the ontology their canonical predicate and family; the old
    wording is already their label."""
    rows = (await db.scalars(select(Relation).where(Relation.family.is_(None)).limit(limit))).all()
    for r in rows:
        canon = ontology.canonical(r.label or r.predicate)
        r.predicate, r.family = canon.predicate, canon.family
        r.label = r.label or canon.label
    return len(rows)


async def shells(db: AsyncSession) -> int:
    """Entities whose shell doesn't match their type (a type changed, or rows from before)."""
    changed = 0
    for kind, (shell, _label) in ontology.TYPES.items():
        result = await db.execute(
            update(Entity)
            .where(and_(Entity.type == kind, Entity.shell != shell, ~Entity.is_self))
            .values(shell=shell)
        )
        changed += result.rowcount or 0
    return changed


async def sync_all(maker: async_sessionmaker[AsyncSession], max_users: int = 100) -> int:
    """The ten-minute pass: ontology seeded, old relations canonical, every active user's app
    records in the graph."""
    async with maker() as db:
        await ontology.seed(db)
        await ontology.load(db)
        done = await canonicalise(db) + await shells(db)
        await db.commit()
        users = (
            await db.scalars(
                select(User)
                .outerjoin(Profile, Profile.user_id == User.id)
                .where(User.status == UserStatus.active, Profile.memory_enabled.is_not(False))
                .limit(max_users)
            )
        ).all()
    for user in users:
        try:
            async with maker() as db:
                done += await sync_user(db, await db.get(User, user.id))
                await db.commit()
        except Exception:
            log.exception("Filing app records into the graph failed for %s", user.id)
    return done
