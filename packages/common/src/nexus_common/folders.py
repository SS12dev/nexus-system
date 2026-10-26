"""Project folders in the library: the files made for one piece of work, kept together. A
team's files go into its work's folder, so a rocket model's .3mf, .step and .stl are one
project, not three loose files. A change to earlier work (a run that starts from that work's
files) and a new version of a file land where the earlier files are, so "fix the rocket" never
starts a second project. The user can rename a folder and move files in or out."""

import re
import uuid
from pathlib import PurePath

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Chat,
    File,
    FileOrigin,
    Folder,
    Project,
    utcnow,
)

# Words a team's name ends with that say nothing about the work ("Rocket model team").
_TEAM_WORDS = re.compile(r"[\s:\-–]*\b(team|crew|squad|pipeline|flow|agents?)\s*$", re.I)


def folder_name(name: str, fallback: str = "Project") -> str:
    """A folder's name from a team's (or chat's) name: the work, not the team."""
    clean = " ".join((name or "").split())
    for _ in range(2):
        clean = _TEAM_WORDS.sub("", clean).strip()
    return (clean or fallback)[:200]


def stem_name(file_name: str) -> str:
    """A readable project name from a file name: rocket_model.stl -> Rocket model."""
    stem = re.sub(r"[_\-.]+", " ", PurePath(file_name).stem).strip()
    return (stem[:1].upper() + stem[1:])[:200] if stem else "Files"


def folder_payload(f: Folder, files: int | None = None) -> dict:
    out = {
        "id": str(f.id),
        "name": f.name,
        "project_id": str(f.project_id) if f.project_id else None,
        "chat_id": str(f.chat_id) if f.chat_id else None,
        "created_at": f.created_at.isoformat(),
        "updated_at": f.updated_at.isoformat(),
    }
    if files is not None:
        out["files"] = files
    return out


async def _lock(db: AsyncSession, key: str) -> None:
    """Agents of one team can save files side by side; this keeps them to one folder."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"folder:{key}"})


async def _owned(db: AsyncSession, user_id: uuid.UUID, folder_id) -> Folder | None:
    if folder_id is None:
        return None
    return await db.scalar(select(Folder).where(Folder.id == folder_id, Folder.user_id == user_id))


async def project_folder(db: AsyncSession, project: Project) -> Folder:
    """The folder of a software project's files (its screenshots), made on first use."""
    await _lock(db, f"project:{project.id}")
    found = await db.scalar(
        select(Folder)
        .where(Folder.user_id == project.user_id, Folder.project_id == project.id)
        .order_by(Folder.created_at)
        .limit(1)
    )
    if found is not None:
        return found
    found = Folder(
        id=uuid.uuid4(),
        user_id=project.user_id,
        name=folder_name(project.name),
        project_id=project.id,
        chat_id=project.chat_id,
    )
    db.add(found)
    await db.flush()
    return found


async def folder_for(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    *,
    run_id: uuid.UUID | None = None,
    chat_id: uuid.UUID | None = None,
    version_of_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> Folder | None:
    """Where a file Nexus or a team just made belongs, making the folder when it is new work.
    In order: beside the file's earlier versions; in its software project's folder; with the
    run's other files; with the earlier work the run started from; in the team's folder; and,
    for a file Nexus saved itself in a chat, with that chat's latest work. None leaves it loose
    (nothing to group it with)."""
    if version_of_id is not None:
        first = await db.get(File, version_of_id)
        found = await _owned(db, user_id, first.folder_id if first else None)
        if found is not None:
            return found
    if project_id is not None:
        project = await db.get(Project, project_id)
        if project is not None and project.user_id == user_id:
            return await project_folder(db, project)
    if run_id is not None:
        run = await db.get(AgentRun, run_id)
        if run is not None and run.user_id == user_id:
            return await _run_folder(db, run, name)
    if chat_id is not None:
        return await _chat_folder(db, user_id, chat_id, name)
    return None


async def _run_folder(db: AsyncSession, run: AgentRun, name: str) -> Folder:
    await _lock(db, f"run:{run.id}")
    # The run already filed something.
    same = await db.scalar(
        select(File.folder_id)
        .where(File.run_id == run.id, File.user_id == run.user_id, File.folder_id.is_not(None))
        .order_by(File.created_at.desc())
        .limit(1)
    )
    if (found := await _owned(db, run.user_id, same)) is not None:
        return found
    system = await db.get(AgentSystem, run.system_id)
    # A dev team's files go with its software project.
    if system is not None and system.topology == "dev_project":
        workbench_id = (system.spec or {}).get("project")
        project = workbench_id and await db.scalar(
            select(Project).where(
                Project.user_id == run.user_id, Project.workbench_id == str(workbench_id)
            )
        )
        if project:
            return await project_folder(db, project)
    # A change to earlier work starts from that work's files: keep it with them.
    ids = [i for i in (run.file_ids or []) if i]
    if ids:
        earlier = await db.scalar(
            select(File.folder_id)
            .where(
                File.id.in_([uuid.UUID(str(i)) for i in ids]),
                File.user_id == run.user_id,
                File.origin == FileOrigin.generated,
                File.folder_id.is_not(None),
            )
            .order_by(File.created_at.desc())
            .limit(1)
        )
        if (found := await _owned(db, run.user_id, earlier)) is not None:
            return found
    # A kept team's later runs share the folder its first run made.
    if system is not None:
        await _lock(db, f"system:{system.id}")
        found = await db.scalar(
            select(Folder)
            .where(Folder.user_id == run.user_id, Folder.system_id == system.id)
            .order_by(Folder.created_at.desc())
            .limit(1)
        )
        if found is not None:
            return found
    found = Folder(
        id=uuid.uuid4(),
        user_id=run.user_id,
        name=folder_name(system.name if system else "", stem_name(name)),
        system_id=system.id if system else None,
        chat_id=run.chat_id,
    )
    db.add(found)
    await db.flush()
    return found


async def _chat_folder(db: AsyncSession, user_id: uuid.UUID, chat_id: uuid.UUID, name: str):
    await _lock(db, f"chat:{chat_id}")
    found = await db.scalar(
        select(Folder)
        .where(Folder.user_id == user_id, Folder.chat_id == chat_id)
        .order_by(Folder.updated_at.desc())
        .limit(1)
    )
    if found is not None:
        return found
    chat = await db.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        return None
    title = (chat.title or "").strip()
    found = Folder(
        id=uuid.uuid4(),
        user_id=user_id,
        name=folder_name(title, stem_name(name))
        if title.lower() != "new chat"
        else stem_name(name),
        chat_id=chat_id,
    )
    db.add(found)
    await db.flush()
    return found


def touch(folder: Folder) -> None:
    folder.updated_at = utcnow()


async def file_counts(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(File.folder_id, func.count())
        .where(File.user_id == user_id, File.folder_id.is_not(None))
        .group_by(File.folder_id)
    )
    return {fid: n for fid, n in rows.all()}
