"""Project folders in the library: files made for one piece of work are kept together

Existing generated files are filed the way new ones are (nexus_common.folders): versions with
their first version, a dev team's files with its software project, a run's files together, a
change to earlier work with that work, a team's runs in its folder, and a file Nexus saved in
a chat with that chat's latest work. Uploads stay out of any folder.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-26
"""

import json
import re
import uuid
from collections.abc import Sequence
from pathlib import PurePath

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEAM_WORDS = re.compile(r"[\s:\-–]*\b(team|crew|squad|pipeline|flow|agents?)\s*$", re.I)


def _name(name, fallback):
    clean = " ".join((name or "").split())
    for _ in range(2):
        clean = _TEAM_WORDS.sub("", clean).strip()
    return (clean or fallback)[:200]


def _stem(file_name):
    stem = re.sub(r"[_\-.]+", " ", PurePath(file_name).stem).strip()
    return (stem[:1].upper() + stem[1:])[:200] if stem else "Files"


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("system_id", sa.Uuid(), sa.ForeignKey("agent_systems.id", ondelete="SET NULL")),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_folders_user", "folders", ["user_id", "updated_at"])
    op.add_column(
        "files",
        sa.Column("folder_id", sa.Uuid(), sa.ForeignKey("folders.id", ondelete="SET NULL")),
    )
    op.create_index("ix_files_folder", "files", ["folder_id"])
    _backfill(op.get_bind())


def _backfill(conn) -> None:
    files = conn.execute(
        sa.text(
            "SELECT f.id, f.user_id, f.name, f.chat_id, f.run_id, f.version_of_id, f.created_at, "
            "r.system_id, r.file_ids, r.chat_id AS run_chat, s.name AS system_name, "
            "s.topology, s.spec, c.title AS chat_title "
            "FROM files f LEFT JOIN agent_runs r ON r.id = f.run_id "
            "LEFT JOIN agent_systems s ON s.id = r.system_id "
            "LEFT JOIN chats c ON c.id = f.chat_id "
            "WHERE f.origin = 'generated' ORDER BY f.created_at"
        )
    ).mappings()
    projects = {
        (str(r.user_id), r.workbench_id): r
        for r in conn.execute(
            sa.text("SELECT id, user_id, workbench_id, name, chat_id FROM projects")
        ).mappings()
    }
    folders: dict[str, dict] = {}  # folder id -> row to insert
    of_file: dict[str, str] = {}
    of_run: dict[str, str] = {}
    of_system: dict[str, str] = {}
    of_project: dict[str, str] = {}
    of_chat: dict[str, str] = {}

    def make(user_id, name, at, *, project_id=None, system_id=None, chat_id=None):
        fid = str(uuid.uuid4())
        folders[fid] = {
            "id": fid,
            "user_id": user_id,
            "name": name,
            "project_id": project_id,
            "system_id": system_id,
            "chat_id": chat_id,
            "created_at": at,
            "updated_at": at,
        }
        return fid

    for f in files:
        fid = None
        user = str(f.user_id)
        if f.version_of_id:
            fid = of_file.get(str(f.version_of_id))
        if fid is None and f.run_id:
            fid = of_run.get(str(f.run_id))
            if fid is None and f.topology == "dev_project":
                spec = f.spec if isinstance(f.spec, dict) else json.loads(f.spec or "{}")
                p = projects.get((user, str(spec.get("project") or "")))
                if p is not None:
                    fid = of_project.get(str(p.id)) or make(
                        user,
                        _name(p.name, "Project"),
                        f.created_at,
                        project_id=str(p.id),
                        chat_id=p.chat_id,
                    )
                    of_project[str(p.id)] = fid
            if fid is None:
                ids = f.file_ids if isinstance(f.file_ids, list) else json.loads(f.file_ids or "[]")
                earlier = [of_file[str(i)] for i in ids if str(i) in of_file]
                fid = earlier[-1] if earlier else None
            if fid is None and f.system_id:
                fid = of_system.get(str(f.system_id))
            if fid is None:
                fid = make(
                    user,
                    _name(f.system_name, _stem(f.name)),
                    f.created_at,
                    system_id=str(f.system_id) if f.system_id else None,
                    chat_id=f.run_chat,
                )
        elif fid is None and f.chat_id:
            fid = of_chat.get(str(f.chat_id))
            if fid is None:
                title = (f.chat_title or "").strip()
                name = _stem(f.name) if title.lower() in ("", "new chat") else _name(title, "Files")
                fid = make(user, name, f.created_at, chat_id=f.chat_id)
        if fid is None:
            continue
        if f.run_id:
            of_run.setdefault(str(f.run_id), fid)
        if f.system_id:
            of_system.setdefault(str(f.system_id), fid)
        if f.chat_id:
            of_chat[str(f.chat_id)] = fid
        of_file[str(f.id)] = fid
        folders[fid]["updated_at"] = max(folders[fid]["updated_at"], f.created_at)
        folders[fid].setdefault("_files", []).append(f.id)

    for row in folders.values():
        ids = row.pop("_files", [])
        conn.execute(
            sa.text(
                "INSERT INTO folders (id, user_id, name, project_id, system_id, chat_id, "
                "created_at, updated_at) VALUES (:id, :user_id, :name, :project_id, :system_id, "
                ":chat_id, :created_at, :updated_at)"
            ),
            row,
        )
        if ids:
            conn.execute(
                sa.text("UPDATE files SET folder_id = :folder WHERE id = ANY(:ids)"),
                {"folder": row["id"], "ids": ids},
            )


def downgrade() -> None:
    op.drop_index("ix_files_folder", table_name="files")
    op.drop_column("files", "folder_id")
    op.drop_index("ix_folders_user", table_name="folders")
    op.drop_table("folders")
