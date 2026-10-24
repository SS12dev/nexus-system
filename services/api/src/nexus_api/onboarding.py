"""The setup interview's answers, turned into what Nexus knows and does for someone: memories
(source "onboarding", editable on the Memory page like any other), routines from their profile
packs, and a welcome chat. Running it again replaces the earlier onboarding memories (unless
the person edited them, which makes them theirs) and adds only routines they don't have."""

import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.schemas import OnboardingIn
from nexus_common import graph
from nexus_common.embeddings import CachedEmbedder
from nexus_common.graph import SELF, Extracted
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


# (kind, content, pinned, names of the graph entities it is about)
Seed = tuple[MemoryKind, str, bool, list[str]]


def memories_from(body: OnboardingIn, packs: list[Pack]) -> list[Seed]:
    """What the answers say, one memory each. Direct answers are pinned, and each names the
    profession or interest it is about, so it hangs off that topic in the orb."""
    roles = [p for p in packs if p.kind == "role" and p.id in body.roles]
    role_names = [p.label for p in roles]
    out: list[Seed] = []
    if body.role_note.strip():
        out.append((MemoryKind.fact, f"Work: {' '.join(body.role_note.split())}", True, role_names))
    for p in roles:
        out += [(kind, text, True, [p.label]) for kind, text in p.memories]
    for i in body.interests:
        if label := INTERESTS.get(i):
            out.append((MemoryKind.preference, f"Wants help with {label.lower()}.", True, [label]))
    if body.about.strip():
        out.append((MemoryKind.fact, " ".join(body.about.split()), True, []))
    if body.day_start:
        out.append((MemoryKind.fact, f"Usually starts the day around {body.day_start}.", False, []))
    if body.style:
        if text := LENGTH.get(body.style.length):
            out.append((MemoryKind.preference, text, True, []))
        out.append((MemoryKind.preference, TONE[body.style.tone], True, []))
        if body.style.language.strip():
            out.append(
                (MemoryKind.instruction, f"Reply in {body.style.language.strip()}.", True, [])
            )
    return out


def graph_from(body: OnboardingIn, packs: list[Pack]) -> Extracted:
    """The professions and interests as topics around the user, so the orb shows them from the
    first minute rather than after the graph job has read the memories."""
    found = Extracted()
    for p in packs:
        if p.kind == "role" and p.id in body.roles:
            found.entities.append({"name": p.label, "type": "topic", "summary": p.blurb})
            found.relations.append(
                {"from": SELF, "to": p.label, "predicate": "works in", "fact": "", "ended": False}
            )
    for i in body.interests:
        if label := INTERESTS.get(i):
            found.entities.append({"name": label, "type": "topic", "summary": ""})
            found.relations.append(
                {
                    "from": SELF,
                    "to": label,
                    "predicate": "wants help with",
                    "fact": "",
                    "ended": False,
                }
            )
    return found


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
    db: AsyncSession,
    settings: Settings,
    user: User,
    body: OnboardingIn,
    packs: list[Pack],
    embedder: CachedEmbedder | None = None,
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
    if (user.profile or {}).get("tour_done"):
        profile["tour_done"] = True
    user.profile = profile

    # Earlier onboarding memories go; ones the person edited have source "user" and stay.
    await db.execute(delete(Memory).where(Memory.user_id == user.id, Memory.source == SOURCE))
    links: list[tuple[dict, list[str]]] = []
    for kind, content, pinned, about in memories_from(body, mine):
        m = Memory(
            id=uuid.uuid4(),
            user_id=user.id,
            kind=kind,
            content=content,
            confidence=1.0,
            pinned=pinned,
            source=SOURCE,
            # Memories tied to a topic here are linked now; the rest (the free-text answer,
            # say) go to the graph job, which can find a city or a tool in them.
            graph_at=now if about else None,
        )
        db.add(m)
        await db.flush()
        record(db, m, "add", "user", content)
        if about:
            links.append(({"memory_id": m.id}, about))
    await mark_profile_stale(db, user.id)
    if settings.graph_enabled and embedder is not None:
        await graph.ingest(db, embedder, user, graph_from(body, mine), links=links)

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
