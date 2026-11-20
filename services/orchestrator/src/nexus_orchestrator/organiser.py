"""Background work for the life organiser (nexus_common/organiser.py):

- `suggest_filing`: when something lands in the Inbox, the small model suggests where it
  belongs (which project, area or resource, note or task, a due date). It is only a
  suggestion: the item stays in the Inbox until the person files it, with one tap.
- `review_reminders`: on Sunday evening, local time, a nudge to do the weekly review, once.
- `nightly_exports`: a fresh Obsidian-ready zip of each person's organiser in storage, so a
  copy outside the database always exists.

Titles and text never reach the logs; ids and counts do.
"""

import logging
import uuid
from datetime import date, timedelta

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from nexus_common import organiser as org
from nexus_common.litellm import ensure_user_key
from nexus_common.models import NotificationKind, OrgItem, User, UserStatus, utcnow
from nexus_common.notices import notify
from nexus_orchestrator.llm import parse_json, private
from nexus_orchestrator.turn import TurnDeps

log = logging.getLogger(__name__)

MAX_CONTAINERS = 150
# A suggested due date further out than this is more likely a misreading than a plan.
LATEST_DUE = timedelta(days=730)
SUGGEST_SYSTEM = """You help someone keep a PARA organiser tidy. Projects have an end goal,
areas are ongoing responsibilities, resources are topics of interest.

Given one new item from their inbox and the list of their projects, areas and resources, say
where it belongs. Reply with JSON only:
{"type": "note" | "task", "container": <number from the list, or null if none fits>,
 "due_on": "YYYY-MM-DD" | null, "why": "<one short sentence>"}

- "task" if it is something to do, "note" if it is information or an idea.
- Pick a container only when it clearly fits. null is a good answer.
- due_on only when the item states or clearly implies a date; today is {today}.
- Write "why" in British English, in under 15 words, addressed to the person ("Fits your ...").
"""


async def suggest_filing(deps: TurnDeps, item_id: uuid.UUID) -> bool:
    """Store a filing suggestion on an Inbox item. Returns whether one was made."""
    async with deps.sessionmaker() as db:
        item = await db.scalar(
            select(OrgItem).where(OrgItem.id == item_id, OrgItem.status == "inbox")
        )
        if item is None or item.suggestion:
            return False
        user = await db.get(User, item.user_id)
        if user is None or user.status != UserStatus.active:
            return False
        containers = list(
            (
                await db.scalars(
                    select(OrgItem)
                    .where(
                        org.live(user.id),
                        OrgItem.type.in_(org.CONTAINERS),
                        OrgItem.status.in_(("active", "paused")),
                    )
                    .order_by(OrgItem.updated_at.desc())
                    .limit(MAX_CONTAINERS)
                )
            ).all()
        )
        today = org.local_today(user)
        listing = "\n".join(f"{n}. {c.type}: {c.title}" for n, c in enumerate(containers, 1))
        prompt = (
            f"Their projects, areas and resources:\n{listing or '(none yet)'}\n\n"
            f"Inbox item ({item.type}):\n{item.title}\n{item.body[:2000]}"
        )
        api_key = await ensure_user_key(db, deps.settings, deps.gateway, user)
        model = deps.model_factory(
            model=deps.settings.organiser_model,
            api_key=api_key,
            metadata={
                "trace_id": uuid.uuid4().hex,
                "trace_name": "nexus.organiser_suggest",
                "generation_name": "nexus.organiser_suggest",
                "trace_user_id": str(user.id),
                **private(deps.settings, user),
                "tags": ["nexus", "organiser", deps.settings.env],
            },
        )
        try:
            reply = await model.bind(response_format={"type": "json_object"}).ainvoke(
                [
                    SystemMessage(SUGGEST_SYSTEM.replace("{today}", today.isoformat())),
                    HumanMessage(prompt),
                ]
            )
        except Exception:
            log.warning("organiser.suggest_failed", extra={"item_id": str(item.id)})
            return False
        text = reply.text if isinstance(reply.text, str) else reply.text()
        suggestion = read_suggestion(parse_json(text), containers, today)
        # The person may have filed it while the model was thinking.
        await db.refresh(item)
        if item.status != "inbox":
            return False
        item.suggestion = suggestion
        await db.commit()
        log.info(
            "organiser.suggested",
            extra={"item_id": str(item.id), "filed": bool(suggestion.get("parent_id"))},
        )
        await org.changed(deps.redis, user.id, item, "updated")
        return True


def read_suggestion(data: dict, containers: list[OrgItem], today: date) -> dict:
    """The model's answer, checked: a known container, a sensible date, a short reason."""
    kind = data.get("type") if data.get("type") in ("note", "task") else "note"
    out: dict = {"type": kind, "parent_id": None, "parent_title": None, "parent_type": None}
    pick = data.get("container")
    if isinstance(pick, str) and pick.strip().isdigit():
        pick = int(pick)
    if isinstance(pick, int) and not isinstance(pick, bool) and 1 <= pick <= len(containers):
        c = containers[pick - 1]
        out.update(parent_id=str(c.id), parent_title=c.title, parent_type=c.type)
    due = None
    if kind == "task" and isinstance(data.get("due_on"), str):
        try:
            due = date.fromisoformat(data["due_on"][:10])
        except ValueError:
            due = None
        if due is not None and not today - timedelta(days=1) <= due <= today + LATEST_DUE:
            due = None
    out["due_on"] = due.isoformat() if due else None
    why = data.get("why")
    out["why"] = " ".join(why.split())[:200] if isinstance(why, str) else ""
    return out


async def review_reminders(deps: TurnDeps) -> int:
    """Sunday at the set local hour: remind each person with an organiser to do the weekly
    review, unless it's done. Runs every hour; a Redis key makes it once a week per person."""
    sent = 0
    async with deps.sessionmaker() as db:
        users = (
            await db.scalars(
                select(User).where(
                    User.status == UserStatus.active,
                    select(OrgItem.id).where(OrgItem.user_id == User.id).exists(),
                )
            )
        ).all()
        now = utcnow()
        for user in users:
            today = org.local_today(user, now)
            local_hour = now.astimezone(org.zone(user)).hour
            if today.weekday() != 6 or local_hour != deps.settings.organiser_review_hour:
                continue
            state = await org.review_state(db, user, today)
            if state.review and state.review.completed_at:
                continue
            key = f"nexus:organiser:review-reminded:{user.id}:{state.week_of.isoformat()}"
            if not await deps.redis.set(key, "1", nx=True, ex=8 * 86400):
                continue
            body = (
                f"{len(state.inbox)} in your Inbox, {len(state.overdue)} overdue. "
                "About ten minutes."
            )
            await notify(
                db,
                deps.redis,
                deps.settings,
                user.id,
                NotificationKind.reminder,
                "Time for your weekly review",
                body,
                url="/organiser/review",
            )
            sent += 1
    if sent:
        log.info("organiser.review_reminders", extra={"count": sent})
    return sent


async def nightly_exports(deps: TurnDeps) -> int:
    """Write each person's organiser as a vault zip to storage (organiser.export_key)."""
    if deps.storage is None or not deps.settings.organiser_nightly_export:
        return 0
    done = 0
    async with deps.sessionmaker() as db:
        users = (
            await db.scalars(
                select(User).where(
                    User.status == UserStatus.active,
                    select(OrgItem.id).where(OrgItem.user_id == User.id).exists(),
                )
            )
        ).all()
        for user in users:
            try:
                files = await org.vault(db, user)
                await deps.storage.put(
                    org.export_key(user.id), org.zip_vault(files), "application/zip"
                )
                done += 1
            except Exception:
                log.exception("organiser.export_failed", extra={"user_id": str(user.id)})
    if done:
        log.info("organiser.exported_nightly", extra={"count": done})
    return done
