import warnings
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migrations_form_one_chain():
    """Two branches merged with the same next number leave Alembic with two heads, and the
    migrate container then fails on deploy. Each revision id must be unique, with one head."""
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # "Revision X is present more than once"
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
    assert len(heads) == 1, f"several migration heads: {heads}"


async def test_existing_files_are_filed_into_project_folders(engine, sessionmaker):
    """0029 files what teams already made the way new files are filed (nexus_common.folders)."""
    import importlib.util
    import uuid
    from decimal import Decimal

    from sqlalchemy import select

    from nexus_api.users import create_user
    from nexus_common.models import (
        AgentRun,
        AgentSystem,
        Chat,
        File,
        FileOrigin,
        FileStatus,
        Folder,
        RoleName,
        RunStatus,
        SystemLifetime,
        SystemStatus,
        UserStatus,
    )

    path = Path(__file__).parents[1] / "alembic/versions/0029_project_folders.py"
    spec = importlib.util.spec_from_file_location("m0029", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async with sessionmaker() as db:
        user = await create_user(
            db, email="s@example.com", role=RoleName.owner, status=UserStatus.active, name="S"
        )
        chat = Chat(id=uuid.uuid4(), user_id=user.id, title="Rocket ideas")
        db.add(chat)

        def system(name):
            s = AgentSystem(
                id=uuid.uuid4(), user_id=user.id, name=name, goal="g", spec={}, topology="flow",
                lifetime=SystemLifetime.ephemeral, status=SystemStatus.idle,
            )  # fmt: skip
            db.add(s)
            return s

        def run(s, file_ids=()):
            r = AgentRun(
                id=uuid.uuid4(), system_id=s.id, user_id=user.id, input="",
                status=RunStatus.completed, budget_usd=Decimal(1), trace_id="t",
                file_ids=[str(i) for i in file_ids],
            )  # fmt: skip
            db.add(r)
            return r

        def file(name, *, run=None, chat_id=None, origin=FileOrigin.generated, version_of=None):
            f = File(
                id=uuid.uuid4(), user_id=user.id, name=name, content_type="model/stl", size=1,
                sha256="x", storage_key="k", status=FileStatus.ready, origin=origin,
                run_id=run.id if run else None, chat_id=chat_id,
                version_of_id=version_of.id if version_of else None,
            )  # fmt: skip
            db.add(f)
            return f

        rocket_team = system("Rocket model team")
        await db.flush()
        r1 = run(rocket_team)
        await db.flush()
        parts = [file(f"rocket.{e}", run=r1) for e in ("3mf", "step", "stl")]
        await db.flush()
        fixer = system("Fins fix")
        await db.flush()
        fix = run(fixer, file_ids=[parts[2].id])
        await db.flush()
        fins = file("fins.stl", run=fix)
        notes = file("notes.md", chat_id=chat.id)
        upload = file("photo.png", origin=FileOrigin.upload, chat_id=chat.id)
        await db.commit()

    async with engine.begin() as conn:
        await conn.run_sync(migration._backfill)

    async with sessionmaker() as db:
        folders = {f.id: f for f in (await db.scalars(select(Folder))).all()}
        where = {f.name: f.folder_id for f in (await db.scalars(select(File))).all()}
    rocket = where["rocket.stl"]
    assert folders[rocket].name == "Rocket model"
    assert where["rocket.3mf"] == where["rocket.step"] == where["fins.stl"] == rocket
    assert folders[where["notes.md"]].name == "Rocket ideas"
    assert where["photo.png"] is None and len(folders) == 2
    assert fins and notes and upload
