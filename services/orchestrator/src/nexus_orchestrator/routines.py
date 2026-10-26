"""Running routines: prompts people scheduled (nexus_common/routines.py has the rules).

Every minute `due` claims the routines whose time has come (FOR UPDATE SKIP LOCKED, so two
workers never take the same one), moves each to its next time in the same transaction, and
queues `run_routine` with the arq job id routine:<id>:<slot>. A routine_runs row per routine and
slot makes a second job for the same slot a no-op too.

A run is an ordinary chat turn in the routine's own chat, so it has the person's memory, tools,
Google services and teams. That chat is hidden (kind "routine"): routines aren't conversations,
so their runs are read on the Routines page, which shows each run's reply. The prompt goes in
as their message with the local date and time; the reply streams like any other. Then:

- with only_if_new, a reply of NOTHING_NEW is a quiet run: both messages are removed and nobody
  is told;
- otherwise the routine's delivery channels (push, email, WhatsApp) get a short notice;
- a failed run counts toward MAX_FAILURES, after which the routine pauses itself and says why
  in a notice (bell, toast and phone).

Runs are skipped when the person has less than ROUTINE_MIN_BUDGET_USD left this period, and
don't feed memory extraction: a news digest says nothing new about the person.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from nexus_common import mail, push, whatsapp
from nexus_common.events import Event, publish
from nexus_common.litellm import active_key, get_budget
from nexus_common.models import (
    Chat,
    Message,
    MessageRole,
    MessageStatus,
    NotificationKind,
    Routine,
    RoutineRun,
    RoutineRunStatus,
    TrashItem,
    User,
    UserStatus,
    utcnow,
)
from nexus_common.notices import notify
from nexus_common.routines import (
    LATE_LIMIT,
    MAX_FAILURES,
    NOTHING_NEW,
    ROUTINE_CHAT,
    ScheduleError,
    next_run,
    routine_payload,
    routine_url,
)
from nexus_orchestrator.turn import ROUTINE_PREFIX, run_turn

log = logging.getLogger(__name__)

CLAIM_BATCH = 50


async def due(deps) -> int:
    """Queue every routine whose time has come. Returns how many were queued."""
    now = utcnow()
    jobs: list[tuple[uuid.UUID, datetime]] = []
    async with deps.sessionmaker() as db:
        rows = (
            await db.scalars(
                select(Routine)
                .join(User, User.id == Routine.user_id)
                .where(Routine.enabled.is_(True), Routine.next_run_at <= now)
                .where(User.status == UserStatus.active, User.account_deleted_at.is_(None))
                .order_by(Routine.next_run_at)
                .limit(CLAIM_BATCH)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for r in rows:
            slot = r.next_run_at
            try:
                r.next_run_at = next_run(r.schedule, r.timezone, now)
            except ScheduleError:
                log.warning("Routine %s has a schedule that no longer parses; paused", r.id)
                r.enabled, r.next_run_at = False, None
                continue
            if now - slot > LATE_LIMIT:
                log.info("Routine %s missed its %s run; next at %s", r.id, slot, r.next_run_at)
                continue
            jobs.append((r.id, slot))
        await db.commit()
    queued = 0
    for routine_id, slot in jobs:
        when = slot.isoformat()
        try:
            await deps.enqueue(
                "run_routine", str(routine_id), when, _job_id=f"routine:{routine_id}:{when}"
            )
            queued += 1
        except Exception:
            log.exception("Could not queue routine %s", routine_id)
    return queued


def turn_prompt(r: Routine, now: datetime, last_run_at: datetime | None) -> str:
    """The routine's prompt as it is sent: what it is and when, then the prompt itself."""
    zone = ZoneInfo(r.timezone)
    local = now.astimezone(zone)
    head = f'{ROUTINE_PREFIX}{r.name}", {local:%A %d %B %Y, %H:%M} ({r.timezone})'
    if last_run_at:
        head += f"; last ran {last_run_at.astimezone(zone):%A %d %B, %H:%M}"
    head += "]"
    tail = ""
    if r.only_if_new:
        tail = (
            f"\n\nIf there is nothing new or worth telling me since the last run, reply with "
            f"exactly {NOTHING_NEW} and nothing else."
        )
    return f"{head}\n\n{r.prompt}{tail}"


async def _budget_left(deps, db, user: User) -> float | None:
    budget = await get_budget(db, deps.settings, user)
    key = await active_key(db, user.id)
    if key is None:
        return float(budget.max_usd)
    try:
        spend = await deps.gateway.spend(key.secret)
    except Exception:
        log.warning("Could not read spend before a routine; running it anyway", exc_info=True)
        return None
    return None if spend is None else float(budget.max_usd) - spend


async def _event(deps, r: Routine, status: str) -> None:
    try:
        await publish(
            deps.redis,
            r.user_id,
            Event(
                type="routine.updated",
                chat_id=r.chat_id,
                data={"routine": routine_payload(r), "status": status},
            ),
        )
    except Exception:
        log.warning("Could not publish routine.updated", exc_info=True)


async def run_routine(deps, routine_id: uuid.UUID, scheduled_for: datetime) -> str | None:
    """Run one routine for one slot. Returns the run's status, or None if it didn't start."""
    async with deps.sessionmaker() as db:
        r = await db.get(Routine, routine_id)
        if r is None:
            return None
        if await db.scalar(
            select(TrashItem.id).where(
                TrashItem.kind == "routine", TrashItem.entity_id == routine_id
            )
        ):
            return None
        user = await db.get(User, r.user_id)
        if user is None or user.status != UserStatus.active or user.account_deleted_at is not None:
            return None
        run = RoutineRun(
            routine_id=r.id, scheduled_for=scheduled_for, status=RoutineRunStatus.running
        )
        run.delivered = []
        db.add(run)
        try:
            await db.flush()
        except IntegrityError:
            log.info("Routine %s already ran for %s", routine_id, scheduled_for)
            return None

        left = await _budget_left(deps, db, user)
        if left is not None and left < deps.settings.routine_min_budget_usd:
            run.status, run.finished_at = RoutineRunStatus.skipped, utcnow()
            run.error = "Not enough budget left this period"
            await db.commit()
            await _event(deps, r, run.status)
            return run.status

        chat = await db.get(Chat, r.chat_id) if r.chat_id else None
        if chat is None:
            chat = Chat(user_id=user.id, title=r.name, kind=ROUTINE_CHAT)
            db.add(chat)
            await db.flush()
            r.chat_id = chat.id
        now = utcnow()
        user_msg = Message(
            chat_id=chat.id,
            role=MessageRole.user,
            content=turn_prompt(r, now, r.last_run_at),
            status=MessageStatus.complete,
            created_at=now,
        )
        reply = Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="",
            status=MessageStatus.streaming,
            model=deps.settings.chat_model,
            trace_id=uuid.uuid4().hex,
            created_at=now + timedelta(microseconds=1),
        )
        db.add_all([user_msg, reply])
        chat.updated_at = now
        r.last_run_at = now
        await db.flush()
        run.message_id = reply.id
        run_id, chat_id, reply_id, user_msg_id = run.id, chat.id, reply.id, user_msg.id
        await db.commit()

    await run_turn(deps, chat_id, reply_id, learn=False)

    async with deps.sessionmaker() as db:
        r = await db.get(Routine, routine_id)
        run = await db.get(RoutineRun, run_id)
        reply = await db.get(Message, reply_id)
        user = await db.get(User, r.user_id) if r else None
        if r is None or run is None or reply is None or user is None:
            return None
        run.finished_at = utcnow()
        run.input_tokens, run.output_tokens = reply.input_tokens, reply.output_tokens
        paused = False
        if reply.status != MessageStatus.complete:
            run.status = RoutineRunStatus.failed
            run.error = reply.error or "The run didn't finish"
            r.failures += 1
            if r.failures >= MAX_FAILURES:
                r.enabled, r.next_run_at = False, None
                paused = True
        elif r.only_if_new and reply.content.strip().strip(".").upper() == NOTHING_NEW:
            run.status = RoutineRunStatus.quiet
            run.message_id = None
            r.failures = 0
            await db.execute(delete(Message).where(Message.id.in_([user_msg_id, reply_id])))
        else:
            run.status = RoutineRunStatus.ok
            r.failures = 0
        status = run.status
        content = reply.content if status == RoutineRunStatus.ok else ""
        await db.commit()
        if status == RoutineRunStatus.ok:
            run.delivered = await deliver(deps, r, user, content)
            await db.commit()
        if paused:
            try:
                await notify(
                    db,
                    deps.redis,
                    deps.settings,
                    r.user_id,
                    NotificationKind.failed,
                    f"Paused {r.name}",
                    f"{r.failures} failed runs in a row. The last error: "
                    f"{run.error.rstrip('.')}. Turn it back on from the Routines page once "
                    "that's sorted.",
                    url=routine_url(r),
                    tag=f"routine:{r.id}",
                )
            except Exception:
                log.warning("Could not post the pause notice for routine %s", r.id, exc_info=True)
        await _event(deps, r, status)
    return status


def notice_text(deps, r: Routine, content: str) -> str:
    base = deps.settings.nexus_url.rstrip("/")
    return (
        f"{content.strip()}\n\n---\nSee it in Nexus, and change or pause this routine: "
        f"{base}{routine_url(r)}\n"
    )


async def deliver(deps, r: Routine, user: User, content: str) -> list[str]:
    """Tell the person on the routine's channels. Returns the channels that worked."""
    done: list[str] = []
    channels = set(r.delivery or [])
    if "push" in channels and deps.settings.push_enabled:
        notice = push.Notice(title=r.name, body=content, url=routine_url(r), tag=f"routine:{r.id}")
        try:
            async with deps.sessionmaker() as db:
                if await push.send(db, deps.settings, user.id, notice):
                    done.append("push")
        except Exception:
            log.warning("Routine %s: push failed", r.id, exc_info=True)
    if "email" in channels and deps.settings.smtp_enabled:
        msg = mail.message(deps.settings, [user.email], r.name, notice_text(deps, r, content))
        try:
            await mail.send(deps.settings, msg)
            done.append("email")
        except mail.MailError as exc:
            log.warning("Routine %s: email failed: %s", r.id, exc)
    if "whatsapp" in channels and deps.settings.whatsapp_enabled and user.phone:
        try:
            await whatsapp.send_template(
                deps.settings, user.phone, [r.name, content], http=deps.google_http
            )
            done.append("whatsapp")
        except whatsapp.WhatsAppError as exc:
            log.warning("Routine %s: WhatsApp failed: %s", r.id, exc)
    return done


def parse_slot(value: str) -> datetime:
    slot = datetime.fromisoformat(value)
    return slot if slot.tzinfo else slot.replace(tzinfo=UTC)
