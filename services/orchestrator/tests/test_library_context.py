"""What the shared library puts into prompts: house conventions for every agent and Nexus,
a shelf index instead of the full catalogue once the library is big, and roles for the
designer."""

from pathlib import Path

from nexus_api.users import create_user
from nexus_common.models import RoleName, UserStatus
from nexus_common.skills import (
    catalogue,
    instruction_layers,
    role_names,
    sync_library,
    usable_skills,
)
from nexus_orchestrator.systems.prompts import design_request
from nexus_orchestrator.turn import CATALOGUE_LIMIT, skills_context


async def make_user(db):
    return await create_user(
        db, email="shivam@example.com", role=RoleName.owner, status=UserStatus.active
    )


async def test_big_library_lists_relevant_skills_and_shelves(sessionmaker, settings, embedder):
    library = Path(settings.library_path)
    async with sessionmaker() as db:
        await sync_library(db, library)
        user = await make_user(db)
        await db.commit()
        skills = await usable_skills(db, user.id)
        assert len(skills) > CATALOGUE_LIMIT
        block = await skills_context(
            db, embedder, user.id, "Find research gaps in recent papers on LLM fine-tuning"
        )
    assert "Likely relevant to this message:" in block
    assert "core/research-gaps:" in block
    # Every skill is still named, by shelf, so Nexus knows the rest exists.
    assert "- research: core/" in block and "core/pdf" in block
    assert len(block) < len(catalogue(skills)) / 2


async def test_conventions_reach_agents_and_roles_reach_the_designer(sessionmaker, settings):
    library = Path(settings.library_path)
    async with sessionmaker() as db:
        user = await make_user(db)
        layers = await instruction_layers(db, library, user, "scientist")
    rendered = layers.render()
    assert "<global_instructions>\nHouse conventions" in rendered
    assert "careful scientist" in layers.role
    prompt = design_request(
        "Write a paper", [], [], [], [], [], "", False, roles=role_names(library)
    )
    roles_line = next(line for line in prompt.splitlines() if line.startswith("ROLES: "))
    assert "scientist" in roles_line and "fact_checker" in roles_line and "assistant" in roles_line
