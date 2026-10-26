"""Files Nexus and its teams make are filed into project folders on their own
(nexus_common.folders), so the orb and the Library show one project, not loose files."""

import uuid
from decimal import Decimal

from sqlalchemy import select

from nexus_common.folders import folder_name, stem_name
from nexus_common.models import (
    AgentRun,
    AgentSystem,
    Chat,
    File,
    Folder,
    Project,
    RunStatus,
    SystemLifetime,
    SystemStatus,
)
from nexus_orchestrator.artifacts import save_generated


async def team_run(deps, owner, name, *, topology="flow", spec=None, file_ids=(), system_id=None):
    user, chat_id = owner
    async with deps.sessionmaker() as db:
        if system_id is None:
            system = AgentSystem(
                id=uuid.uuid4(),
                user_id=user.id,
                chat_id=chat_id,
                name=name,
                goal="g",
                spec=spec or {},
                topology=topology,
                lifetime=SystemLifetime.ephemeral,
                status=SystemStatus.idle,
            )
            db.add(system)
            await db.flush()
            system_id = system.id
        run = AgentRun(
            id=uuid.uuid4(),
            system_id=system_id,
            user_id=user.id,
            chat_id=chat_id,
            input="make it",
            file_ids=[str(i) for i in file_ids],
            status=RunStatus.running,
            budget_usd=Decimal("1"),
            trace_id="t",
        )
        db.add(run)
        await db.commit()
        return system_id, run.id


async def folder_of(deps, f: File) -> Folder | None:
    async with deps.sessionmaker() as db:
        fid = await db.scalar(select(File.folder_id).where(File.id == f.id))
        return fid and await db.get(Folder, fid)


def test_folder_names_name_the_work_not_the_team():
    assert folder_name("Rocket Model Team") == "Rocket Model"
    assert folder_name("3D print pipeline - team") == "3D print"
    assert folder_name("Team") == "Project"
    assert stem_name("rocket_model.stl") == "Rocket model"


async def test_a_teams_files_are_one_project(deps, owner, storage):
    user, _ = owner
    deps.storage = storage
    _, run_id = await team_run(deps, owner, "Rocket model team")
    made = [
        await save_generated(deps, user.id, f"rocket_model.{ext}", b"x", run_id=run_id)
        for ext in ("3mf", "step", "stl")
    ]
    folders = {(await folder_of(deps, f)).id for f in made}
    assert len(folders) == 1
    assert (await folder_of(deps, made[0])).name == "Rocket model"


async def test_a_change_to_earlier_work_stays_in_its_project(deps, owner, storage):
    user, _ = owner
    deps.storage = storage
    _, first_run = await team_run(deps, owner, "Rocket model team")
    stl = await save_generated(deps, user.id, "rocket.stl", b"v1", run_id=first_run)
    # A new team, started from the rocket's files (based_on), makes a new file.
    _, fix_run = await team_run(deps, owner, "Rocket fins fix", file_ids=[stl.id])
    fins = await save_generated(deps, user.id, "fins.stl", b"x", run_id=fix_run)
    # And a new version of the rocket, from an unrelated team, follows its first version.
    _, other = await team_run(deps, owner, "Minion team")
    again = await save_generated(deps, user.id, "rocket.stl", b"v2", run_id=other)
    home = (await folder_of(deps, stl)).id
    assert (await folder_of(deps, fins)).id == home
    assert again.version == 2 and (await folder_of(deps, again)).id == home
    # A new piece of work is a new project.
    _, new_run = await team_run(deps, owner, "Minion team")
    minion = await save_generated(deps, user.id, "minion.stl", b"x", run_id=new_run)
    assert (await folder_of(deps, minion)).name == "Minion"


async def test_a_dev_teams_files_go_with_its_software_project(deps, owner, storage):
    user, chat_id = owner
    deps.storage = storage
    async with deps.sessionmaker() as db:
        project = Project(user_id=user.id, workbench_id="solitaire-1", name="Solitaire")
        db.add(project)
        await db.commit()
    _, run_id = await team_run(
        deps, owner, "Solitaire dev team", topology="dev_project", spec={"project": "solitaire-1"}
    )
    shot = await save_generated(deps, user.id, "solitaire-screenshot.png", b"x", run_id=run_id)
    later = await save_generated(
        deps, user.id, "solitaire-screenshot.png", b"y", project_id=project.id, new_version=False
    )
    folder = await folder_of(deps, shot)
    assert folder.project_id == project.id and folder.name == "Solitaire"
    assert (await folder_of(deps, later)).id == folder.id


async def test_nexus_files_in_a_chat_go_together_and_uploads_stay_loose(deps, owner, storage):
    user, chat_id = owner
    deps.storage = storage
    async with deps.sessionmaker() as db:
        (await db.get(Chat, chat_id)).title = "Weekly meal plan"
        await db.commit()
    a = await save_generated(deps, user.id, "monday.md", b"x", chat_id=chat_id)
    b = await save_generated(deps, user.id, "shopping.csv", b"x", chat_id=chat_id)
    assert (await folder_of(deps, a)).id == (await folder_of(deps, b)).id
    assert (await folder_of(deps, a)).name == "Weekly meal plan"
    loose = await save_generated(deps, user.id, "scratch.md", b"x")
    assert await folder_of(deps, loose) is None


async def test_a_kept_teams_runs_share_a_folder(deps, owner, storage):
    user, _ = owner
    deps.storage = storage
    system_id, first = await team_run(deps, owner, "Weekly report team")
    _, second = await team_run(deps, owner, "", system_id=system_id)
    a = await save_generated(deps, user.id, "week-1.md", b"x", run_id=first)
    b = await save_generated(deps, user.id, "week-2.md", b"x", run_id=second)
    assert (await folder_of(deps, a)).id == (await folder_of(deps, b)).id
