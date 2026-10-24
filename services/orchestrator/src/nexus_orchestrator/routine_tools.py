"""The assistant's routine tools: "every weekday at 8, brief me" becomes a routine without a
trip to the Routines page. Same rules as the page (nexus_common/routines.py): validated
schedules, at most ROUTINES_MAX_PER_USER, at most hourly."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from nexus_common.events import Event, publish
from nexus_common.models import AuditLog, Routine, User, utcnow
from nexus_common.routines import (
    RoutineLimit,
    ScheduleError,
    add_routine,
    clean_delivery,
    describe,
    lower_first,
    reschedule,
    routine_payload,
    upcoming,
    validate,
)


def _line(r: Routine) -> str:
    state = "on" if r.enabled else "paused"
    when = describe(r.schedule)
    nxt = f", next {r.next_run_at:%a %d %b %H:%M} UTC" if r.next_run_at else ""
    return f"- {r.name} (id {r.id}, {state}): {when} ({r.timezone}){nxt}"


def build_routine_tools(ctx: Any, note: Callable) -> dict[str, Callable]:
    deps, user_id = ctx.deps, ctx.user_id

    async def changed(r: Routine) -> None:
        await publish(
            deps.redis,
            user_id,
            Event(type="routine.updated", chat_id=r.chat_id, data={"routine": routine_payload(r)}),
        )

    async def create_routine(
        name: str,
        prompt: str,
        schedule: str,
        only_if_new: bool = False,
        delivery: list[str] | None = None,
    ) -> str:
        """Save a routine: a prompt that runs on a schedule as the user, posting into a chat of
        its own, e.g. a morning brief or a weekly digest. schedule is an RFC 5545 RRULE without
        DTSTART, in the user's local time: "FREQ=DAILY;BYHOUR=8;BYMINUTE=0",
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=7;BYMINUTE=30",
        "FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9;BYMINUTE=0". At most hourly. Write prompt as the
        user would ask it, self-contained (it runs later with no chat around it). Set
        only_if_new for digests and watches so a run with nothing new stays silent. delivery
        may list "email" and "whatsapp" to also notify the user there. Only create one when
        the user asks for something recurring."""
        await note("create_routine", routine=name, schedule=schedule)
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            try:
                r = await add_routine(
                    db,
                    deps.settings,
                    user,
                    name=name,
                    prompt=prompt,
                    schedule=schedule,
                    delivery=delivery,
                    only_if_new=only_if_new,
                    source="chat",
                )
            except (ScheduleError, RoutineLimit) as exc:
                return f"Not saved: {exc}"
            db.add(AuditLog(actor_id=user_id, action="routine.created", target=str(r.id)))
            await db.commit()
            times = upcoming(r.schedule, r.timezone, datetime.now(UTC), 3)
        await changed(r)
        zone = ZoneInfo(r.timezone)
        listed = ", ".join(f"{t.astimezone(zone):%a %d %b %H:%M}" for t in times)
        return (
            f"Saved routine {r.name} (id {r.id}): {lower_first(describe(r.schedule))} in "
            f"{r.timezone}. Next runs: {listed}. Its results post in a chat called "
            f"{r.name}; the user can change or pause it on the Routines page."
        )

    async def list_routines() -> str:
        """List the user's routines with their ids, schedules and whether they're on."""
        await note("list_routines")
        async with deps.sessionmaker() as db:
            rows = (
                await db.scalars(
                    select(Routine).where(Routine.user_id == user_id).order_by(Routine.created_at)
                )
            ).all()
        return "\n".join(_line(r) for r in rows) or "The user has no routines."

    async def update_routine(
        routine_id: str,
        action: Literal["pause", "resume", "delete", "change"] = "change",
        name: str | None = None,
        prompt: str | None = None,
        schedule: str | None = None,
        only_if_new: bool | None = None,
        delivery: list[str] | None = None,
    ) -> str:
        """Pause, resume, delete or change one of the user's routines (id from list_routines).
        For "change", pass only the fields that change; schedule is an RRULE as in
        create_routine."""
        await note("update_routine", routine=routine_id, action=action)
        try:
            rid = uuid.UUID(routine_id)
        except ValueError:
            return "That isn't a routine id; call list_routines."
        async with deps.sessionmaker() as db:
            r = await db.scalar(
                select(Routine).where(Routine.id == rid, Routine.user_id == user_id)
            )
            if r is None:
                return "No such routine."
            now = datetime.now(UTC)
            if action == "delete":
                await db.delete(r)
                db.add(AuditLog(actor_id=user_id, action="routine.deleted", target=str(rid)))
                await db.commit()
                return f"Deleted the routine {r.name}. Its chat stays."
            if action in ("pause", "resume"):
                r.enabled = action == "resume"
                if r.enabled:
                    r.failures = 0
            else:
                try:
                    if schedule:
                        r.schedule = validate(
                            schedule, r.timezone, deps.settings.routine_min_interval_minutes, now
                        )
                except ScheduleError as exc:
                    return f"Not changed: {exc}"
                if name:
                    r.name = " ".join(name.split())[:80]
                if prompt:
                    r.prompt = prompt.strip()
                if only_if_new is not None:
                    r.only_if_new = only_if_new
                if delivery is not None:
                    r.delivery = clean_delivery(delivery)
            reschedule(r, now)
            r.updated_at = utcnow()
            await db.commit()
        await changed(r)
        return f"Done: {_line(r)[2:]}"

    return {
        "create_routine": create_routine,
        "list_routines": list_routines,
        "update_routine": update_routine,
    }
