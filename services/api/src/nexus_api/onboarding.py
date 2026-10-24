"""The setup interview's answers, turned into what Nexus knows and does for someone: memories
(source "onboarding", editable on the Memory page like any other), routines from their profile
packs, and a welcome chat. Running it again replaces the earlier onboarding memories (unless
the person edited them, which makes them theirs) and adds only routines they don't have."""

import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.schemas import OnboardingIn
from nexus_common.memory import mark_profile_stale, record
from nexus_common.models import (
    AuditLog,
    Chat,
    Memory,
    MemoryKind,
    Message,
    MessageRole,
    MessageStatus,
    RoleName,
    Routine,
    User,
    utcnow,
)
from nexus_common.profiles import (
    INTERESTS,
    Pack,
    find_routine,
    packs_for,
    suggested_prompts,
)
from nexus_common.routines import (
    RoutineLimit,
    ScheduleError,
    add_routine,
    check_timezone,
    describe,
    lower_first,
    rule_for,
)
from nexus_common.settings import Settings

SOURCE = "onboarding"
LENGTH = {
    "short": "Prefers short, to-the-point answers.",
    "detailed": "Prefers detailed, thorough answers.",
}
TONE = {
    "casual": "Prefers a casual, friendly tone.",
    "professional": "Prefers a professional tone.",
}


def memories_from(body: OnboardingIn, packs: list[Pack]) -> list[tuple[MemoryKind, str, bool]]:
    """(kind, content, pinned) for each thing the answers say. Direct answers are pinned."""
    out: list[tuple[MemoryKind, str, bool]] = []
    if body.role_note.strip():
        out.append((MemoryKind.fact, f"Work: {' '.join(body.role_note.split())}", True))
    for p in packs:
        if p.kind == "role" and p.id in body.roles:
            out += [(kind, text, True) for kind, text in p.memories]
    wanted = [INTERESTS[i] for i in body.interests if i in INTERESTS]
    if wanted:
        out.append((MemoryKind.goal, f"Wants help with: {', '.join(wanted)}.", True))
    if body.about.strip():
        out.append((MemoryKind.fact, " ".join(body.about.split()), True))
    if body.day_start:
        out.append((MemoryKind.fact, f"Usually starts the day around {body.day_start}.", False))
    if body.style:
        if text := LENGTH.get(body.style.length):
            out.append((MemoryKind.preference, text, True))
        out.append((MemoryKind.preference, TONE[body.style.tone], True))
        if body.style.language.strip():
            out.append((MemoryKind.instruction, f"Reply in {body.style.language.strip()}.", True))
    return out


def welcome_text(user: User, routines: list[Routine], prompts: list[str]) -> str:
    first = (user.name or "").split(" ")[0]
    lines = [f"Hi{' ' + first if first else ''}, I'm {user.assistant}. You're all set."]
    if routines:
        lines += ["", "I'll run these for you:"]
        lines += [f"- **{r.name}**: {lower_first(describe(r.schedule))}" for r in routines]
        lines += [
            "",
            "Each one posts in a chat of its own. Change, pause or add routines on the "
            '**Routines** page, or just ask me ("every Monday at 9, send me …").',
        ]
    lines += [
        "",
        "What I remember about you is on the **Memory** page, where you can edit or delete "
        "any of it. Some things to try:",
    ]
    lines += [f"- {p}" for p in prompts]
    return "\n".join(lines)


async def apply(
    db: AsyncSession, settings: Settings, user: User, body: OnboardingIn, packs: list[Pack]
) -> tuple[uuid.UUID, int]:
    """Store the answers. Returns the welcome chat's id and how many routines were added."""
    now = utcnow()
    if body.assistant_name:
        user.assistant_name = body.assistant_name
    if body.timezone:
        try:
            user.timezone = check_timezone(body.timezone)
        except ScheduleError:
            user.timezone = user.timezone or "UTC"
    user.timezone = user.timezone or "UTC"
    if body.phone is not None:
        user.phone = body.phone or None
    profile = {
        "roles": body.roles,
        "role_note": body.role_note.strip(),
        "interests": body.interests,
        "day_start": body.day_start,
        "style": body.style.model_dump() if body.style else None,
    }
    mine = packs_for(packs, profile)
    profile["packs"] = [p.id for p in mine]
    user.profile = profile

    # Earlier onboarding memories go; ones the person edited have source "user" and stay.
    await db.execute(delete(Memory).where(Memory.user_id == user.id, Memory.source == SOURCE))
    for kind, content, pinned in memories_from(body, mine):
        m = Memory(
            id=uuid.uuid4(),
            user_id=user.id,
            kind=kind,
            content=content,
            confidence=1.0,
            pinned=pinned,
            source=SOURCE,
        )
        db.add(m)
        await db.flush()
        record(db, m, "add", "user", content)
    await mark_profile_stale(db, user.id)

    added: list[Routine] = []
    if user.has_role(RoleName.member) and settings.routines_enabled:
        rows = await db.execute(
            select(Routine.source, Routine.name).where(Routine.user_id == user.id)
        )
        have = {(source, name) for source, name in rows}
        for choice in body.routines:
            found = find_routine(packs, choice.key)
            if found is None:
                continue
            pack, routine = found
            source = f"pack:{pack.id}"
            if (source, routine.name) in have:
                continue
            days = list(routine.days) if isinstance(routine.days, tuple) else routine.days
            schedule = (
                rule_for(days, choice.time) if choice.time else routine.schedule(body.day_start)
            )
            try:
                r = await add_routine(
                    db,
                    settings,
                    user,
                    name=routine.name,
                    prompt=routine.prompt,
                    schedule=schedule,
                    delivery=list(body.delivery),
                    only_if_new=routine.only_if_new,
                    source=source,
                )
            except RoutineLimit:
                break
            added.append(r)

    first_time = user.onboarded_at is None
    user.onboarded_at = now
    chat = Chat(user_id=user.id, title="Welcome" if first_time else "Setup updated")
    db.add(chat)
    await db.flush()
    db.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content=welcome_text(user, added, suggested_prompts(packs, profile, 4)),
            status=MessageStatus.complete,
            created_at=now + timedelta(microseconds=1),
        )
    )
    db.add(
        AuditLog(
            actor_id=user.id,
            action="user.onboarded",
            target=str(user.id),
            data={"packs": profile["packs"], "routines": len(added)},
        )
    )
    return chat.id, len(added)
