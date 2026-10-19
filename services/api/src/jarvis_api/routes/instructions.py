"""Standing instructions for agents, in layers: the shipped safety rules (read only), global
and per-role instructions (admins), and your own house style (every user)."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_api.deps import AdminUser, CurrentUser, DbDep, MemberUser, SettingsDep
from jarvis_api.schemas import InstructionIn
from jarvis_common.models import AuditLog, Instruction, InstructionLayer, User, utcnow
from jarvis_common.skills import shipped_instructions
from jarvis_common.spec import AGENT_ID

router = APIRouter(prefix="/api/instructions", tags=["instructions"])


async def _row(db: AsyncSession, layer: InstructionLayer, key: str, user_id) -> Instruction | None:
    cond = Instruction.user_id.is_(None) if user_id is None else Instruction.user_id == user_id
    return await db.scalar(
        select(Instruction).where(Instruction.layer == layer, Instruction.key == key, cond)
    )


async def _save(
    db: AsyncSession, actor: User, layer: InstructionLayer, key: str, user_id, content: str
) -> None:
    row = await _row(db, layer, key, user_id)
    if row is None:
        row = Instruction(layer=layer, key=key, user_id=user_id)
        db.add(row)
    row.content = content.strip()
    row.updated_by = actor.id
    row.updated_at = utcnow()
    if user_id is None:
        db.add(
            AuditLog(actor_id=actor.id, action=f"instructions.{layer.value}", target=key or None)
        )
    await db.commit()


@router.get("")
async def get_instructions(user: CurrentUser, db: DbDep, settings: SettingsDep):
    safety, shipped = shipped_instructions(Path(settings.library_path))
    rows = (
        await db.scalars(
            select(Instruction).where(
                (Instruction.user_id.is_(None)) | (Instruction.user_id == user.id)
            )
        )
    ).all()
    extra = {r.key: r.content for r in rows if r.layer == InstructionLayer.role}
    return {
        "safety": safety,
        "global": next((r.content for r in rows if r.layer == InstructionLayer.global_), ""),
        "roles": [
            {"role": k, "shipped": shipped.get(k, ""), "extra": extra.get(k, "")}
            for k in sorted(set(shipped) | set(extra))
        ],
        "user": next(
            (r.content for r in rows if r.layer == InstructionLayer.user and r.user_id == user.id),
            "",
        ),
    }


@router.put("/user", status_code=status.HTTP_204_NO_CONTENT)
async def set_user(body: InstructionIn, user: MemberUser, db: DbDep):
    await _save(db, user, InstructionLayer.user, "", user.id, body.content)


@router.put("/global", status_code=status.HTTP_204_NO_CONTENT)
async def set_global(body: InstructionIn, user: AdminUser, db: DbDep):
    await _save(db, user, InstructionLayer.global_, "", None, body.content)


@router.put("/roles/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def set_role(role: str, body: InstructionIn, user: AdminUser, db: DbDep):
    if not AGENT_ID.match(role):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Roles are lowercase words")
    await _save(db, user, InstructionLayer.role, role, None, body.content)
