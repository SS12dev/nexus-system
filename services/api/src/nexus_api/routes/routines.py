"""Routines: the person's prompts that run on a schedule (nexus_common/routines.py). The
orchestrator runs them; here they are listed, made, changed, run on demand and deleted."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.deps import CurrentUser, DbDep, MemberUser, QueueDep, SettingsDep
from nexus_api.schemas import (
    RoutineIn,
    RoutineOut,
    RoutineRunOut,
    RoutineUpdateIn,
    SchedulePreviewIn,
    SchedulePreviewOut,
)
from nexus_common.models import AuditLog, Message, Routine, RoutineRun, User, utcnow
from nexus_common.routines import (
    RoutineLimit,
    ScheduleError,
    add_routine,
    check_timezone,
    clean_delivery,
    describe,
    remove_routine,
    reschedule,
    routine_payload,
    upcoming,
    validate,
)

router = APIRouter(prefix="/api/routines", tags=["routines"])


async def own_routine(db: AsyncSession, user: User, routine_id: uuid.UUID) -> Routine:
    r = await db.scalar(select(Routine).where(Routine.id == routine_id, Routine.user_id == user.id))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine not found")
    return r


async def routine_out(db: AsyncSession, r: Routine) -> RoutineOut:
    last = await db.scalar(
        select(RoutineRun.status)
        .where(RoutineRun.routine_id == r.id)
        .order_by(RoutineRun.started_at.desc())
        .limit(1)
    )
    return RoutineOut(**routine_payload(r), last_status=last)


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("", response_model=list[RoutineOut])
async def list_routines(user: CurrentUser, db: DbDep):
    rows = (
        await db.scalars(
            select(Routine).where(Routine.user_id == user.id).order_by(Routine.created_at)
        )
    ).all()
    return [await routine_out(db, r) for r in rows]


@router.post("", response_model=RoutineOut, status_code=status.HTTP_201_CREATED)
async def create_routine(body: RoutineIn, user: MemberUser, db: DbDep, settings: SettingsDep):
    try:
        r = await add_routine(
            db,
            settings,
            user,
            name=body.name,
            prompt=body.prompt,
            schedule=body.schedule,
            timezone=body.timezone,
            delivery=list(body.delivery),
            only_if_new=body.only_if_new,
            enabled=body.enabled,
            source="user",
        )
    except (ScheduleError, RoutineLimit) as exc:
        raise _bad(exc) from exc
    db.add(AuditLog(actor_id=user.id, action="routine.created", target=str(r.id)))
    await db.commit()
    return await routine_out(db, r)


@router.post("/preview", response_model=SchedulePreviewOut)
async def preview(body: SchedulePreviewIn, user: CurrentUser, settings: SettingsDep):
    """The schedule in words and its next three runs, or why it won't do."""
    tz = body.timezone or user.timezone or "UTC"
    now = datetime.now(UTC)
    try:
        schedule = validate(body.schedule, tz, settings.routine_min_interval_minutes, now)
    except ScheduleError as exc:
        return SchedulePreviewOut(ok=False, error=str(exc))
    return SchedulePreviewOut(ok=True, text=describe(schedule), next=upcoming(schedule, tz, now, 3))


@router.patch("/{routine_id}", response_model=RoutineOut)
async def update_routine(
    routine_id: uuid.UUID,
    body: RoutineUpdateIn,
    user: MemberUser,
    db: DbDep,
    settings: SettingsDep,
):
    r = await own_routine(db, user, routine_id)
    now = datetime.now(UTC)
    try:
        if body.timezone is not None:
            r.timezone = check_timezone(body.timezone)
        if body.schedule is not None or body.timezone is not None:
            r.schedule = validate(
                body.schedule or r.schedule,
                r.timezone,
                settings.routine_min_interval_minutes,
                now,
            )
    except ScheduleError as exc:
        raise _bad(exc) from exc
    if body.name is not None:
        r.name = " ".join(body.name.split())[:80]
    if body.prompt is not None:
        r.prompt = body.prompt.strip()
    if body.delivery is not None:
        r.delivery = clean_delivery(list(body.delivery))
    if body.only_if_new is not None:
        r.only_if_new = body.only_if_new
    if body.enabled is not None:
        if body.enabled and not r.enabled:
            r.failures = 0
        r.enabled = body.enabled
    reschedule(r, now)
    r.updated_at = utcnow()
    await db.commit()
    return await routine_out(db, r)


@router.delete("/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routine(routine_id: uuid.UUID, user: MemberUser, db: DbDep):
    """Delete the routine, its run history and the hidden chat it ran in."""
    r = await own_routine(db, user, routine_id)
    await remove_routine(db, r)
    db.add(AuditLog(actor_id=user.id, action="routine.deleted", target=str(routine_id)))
    await db.commit()


@router.post("/{routine_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_now(routine_id: uuid.UUID, user: MemberUser, db: DbDep, queue: QueueDep) -> dict:
    """Run it now, outside its schedule. Its next scheduled run doesn't change."""
    r = await own_routine(db, user, routine_id)
    await queue.enqueue_routine(r.id, datetime.now(UTC).replace(microsecond=0))
    return {"queued": True, "chat_id": str(r.chat_id) if r.chat_id else None}


@router.get("/{routine_id}/runs", response_model=list[RoutineRunOut])
async def runs(routine_id: uuid.UUID, user: CurrentUser, db: DbDep):
    """The latest runs, each with what it said: routines aren't in the chat list, so this is
    where their results are read."""
    r = await own_routine(db, user, routine_id)
    rows = await db.execute(
        select(RoutineRun, Message.content)
        .outerjoin(Message, Message.id == RoutineRun.message_id)
        .where(RoutineRun.routine_id == r.id)
        .order_by(RoutineRun.started_at.desc())
        .limit(30)
    )
    out = []
    for run, content in rows.all():
        item = RoutineRunOut.model_validate(run)
        item.content = content or None
        out.append(item)
    return out
