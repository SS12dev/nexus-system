"""The life organiser: notes, tasks, projects, areas, resources and a daily note, organised the
PARA way (Projects end, Areas don't, Resources are topics, anything archived is the Archive),
with one Inbox for quick capture and a guided weekly review.

Everything here is shared by the API (the Organiser pages) and the orchestrator (the
assistant's organiser tools, filing suggestions, the Sunday reminder, the nightly export), so
the rules live in one place:

- `create` / `update` validate type and status, bump the version (a save naming an older
  version is refused, so the laptop and the phone can't silently overwrite each other), keep a
  revision of the old text (coalesced while typing), re-index [[wikilinks]] for backlinks and
  tell every open tab.
- `daily` gets or makes the daily note for a date, from a short template.
- `review_state` gathers what the weekly review walks through.
- `vault` writes everything as Markdown files with YAML front matter and [[wikilinks]], laid
  out so Obsidian opens it as a normal vault.

Titles and text are personal: logs carry ids, types and counts only.
"""

import io
import logging
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.events import Event, publish
from nexus_common.models import OrgItem, OrgLink, OrgReview, OrgRevision, User, utcnow
from nexus_common.trash import add as add_to_trash
from nexus_common.trash import visible

log = logging.getLogger(__name__)

TYPES = ("note", "task", "project", "area", "resource", "daily")
STATUSES: dict[str, tuple[str, ...]] = {
    "note": ("inbox", "active", "archived"),
    "task": ("inbox", "todo", "doing", "done", "dropped"),
    "project": ("active", "paused", "done", "archived"),
    "area": ("active", "archived"),
    "resource": ("active", "archived"),
    "daily": ("active",),
}
DEFAULT_STATUS = {
    "note": "active",
    "task": "todo",
    "project": "active",
    "area": "active",
    "resource": "active",
    "daily": "active",
}
# Containers a note or task can sit in; a project can sit in an area.
CONTAINERS = ("project", "area", "resource")
OPEN_TASK = ("inbox", "todo", "doing")
TITLE_CHARS = 300
BODY_CHARS = 200_000
MAX_TAGS = 20
# Autosave writes often; keep one revision per this many minutes of editing.
REVISION_EVERY = timedelta(minutes=5)
MAX_REVISIONS = 50
# A project with nothing done or changed in this long shows as stalled in the review.
STALLED_AFTER = timedelta(days=14)
TRASH_KIND = "org_item"

DAILY_TEMPLATE = "## Top three\n\n- [ ] \n- [ ] \n- [ ] \n\n## Notes\n\n"
_WIKILINK = re.compile(r"\[\[([^\[\]|#\n]{1,300})(?:#[^\[\]|\n]*)?(?:\|[^\[\]\n]*)?\]\]")
_CHECKBOX = re.compile(r"^(\s*[-*+]\s+\[ \]\s+)(?!\[\[)(\S.*?)\s*$", re.M)


class OrganiserError(ValueError):
    """Something the person asked for that doesn't fit (a bad status, a parent that isn't a
    container). The message is safe to show."""


class VersionConflict(Exception):
    def __init__(self, item: OrgItem):
        super().__init__("This was changed somewhere else. Reload to see the latest.")
        self.item = item


def zone(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def local_today(user: User, now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(zone(user)).date()


def week_of(day: date) -> date:
    """The Monday of the week a review plans: on Sunday that is tomorrow, otherwise this one."""
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day - timedelta(days=day.weekday())


def wikilinks(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in _WIKILINK.finditer(text or ""):
        seen.setdefault(m.group(1).strip(), None)
    return [t for t in seen if t]


def clean_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        t = re.sub(r"[^\w/-]+", "-", str(t).strip().lstrip("#").lower()).strip("-")[:40]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _check_status(type_: str, status: str) -> None:
    if status not in STATUSES[type_]:
        raise OrganiserError(f"A {type_} can't be {status}.")


async def _check_parent(
    db: AsyncSession, user_id: uuid.UUID, item_type: str, parent_id: uuid.UUID | None,
    self_id: uuid.UUID | None = None,
) -> None:  # fmt: skip
    if parent_id is None:
        return
    if parent_id == self_id:
        raise OrganiserError("Something can't be inside itself.")
    parent = await db.scalar(
        select(OrgItem).where(OrgItem.id == parent_id, OrgItem.user_id == user_id)
    )
    if parent is None:
        raise OrganiserError("That project, area or resource doesn't exist.")
    allowed = ("area",) if item_type == "project" else CONTAINERS
    if item_type in ("area", "resource", "daily") or parent.type not in allowed:
        raise OrganiserError(f"A {item_type} can't go inside a {parent.type}.")


_MD_NOISE = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+\.\s+)|[*_`~]+|\|", re.M
)


def plain(text: str) -> str:
    """Markdown as one line of plain words, for list excerpts: [[links]] keep their text,
    headings, bullets, checkboxes, emphasis and table bars go."""
    text = _WIKILINK.sub(lambda m: m.group(0)[2:-2].split("|")[-1].split("#")[0], text or "")
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*\|?\s*:?-{2,}.*$", " ", text, flags=re.M)
    return " ".join(_MD_NOISE.sub(" ", text).split())


def payload(item: OrgItem, *, body: bool = True) -> dict:
    out = {
        "id": str(item.id),
        "type": item.type,
        "title": item.title,
        "status": item.status,
        "parent_id": str(item.parent_id) if item.parent_id else None,
        "due_on": item.due_on.isoformat() if item.due_on else None,
        "priority": item.priority,
        "tags": list(item.tags or []),
        "pinned": item.pinned,
        "day": item.day.isoformat() if item.day else None,
        "source": item.source,
        "suggestion": item.suggestion,
        "version": item.version,
        "done_at": item.done_at.isoformat() if item.done_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
    if body:
        out["body"] = item.body
    else:
        # Lists show a line of the text, not all of it.
        out["excerpt"] = plain(item.body)[:160]
    return out


async def changed(redis: Redis | None, user_id: uuid.UUID, item: OrgItem, op: str) -> None:
    if redis is None:
        return
    await publish(
        redis,
        user_id,
        Event(type="organiser.changed", data={"id": str(item.id), "op": op, "type": item.type}),
    )


async def _relink(db: AsyncSession, item: OrgItem) -> None:
    """Point this item's [[links]] at the items with those titles (case-insensitive)."""
    await db.execute(delete(OrgLink).where(OrgLink.src_id == item.id))
    titles = [t.lower() for t in wikilinks(item.body)][:200]
    if not titles:
        return
    targets = await db.scalars(
        select(OrgItem.id).where(
            OrgItem.user_id == item.user_id,
            func.lower(OrgItem.title).in_(titles),
            OrgItem.id != item.id,
        )
    )
    for dst in set(targets.all()):
        db.add(OrgLink(src_id=item.id, dst_id=dst))


async def create(
    db: AsyncSession,
    user: User,
    *,
    type: str = "note",
    title: str = "",
    body: str = "",
    status: str | None = None,
    parent_id: uuid.UUID | None = None,
    due_on: date | None = None,
    priority: int = 0,
    tags: list[str] | None = None,
    source: str = "user",
    inbox: bool = False,
    redis: Redis | None = None,
) -> OrgItem:
    """Add an item. inbox=True captures it unfiled (a note or task only). Commits."""
    if type not in TYPES or type == "daily":
        raise OrganiserError("Pick note, task, project, area or resource.")
    title, body = title.strip()[:TITLE_CHARS], body[:BODY_CHARS]
    if not title and not body.strip():
        raise OrganiserError("Write something first.")
    if not title:
        first = body.strip().splitlines()[0]
        title = re.sub(r"^[#>\-*+\s\[\]x]+", "", first).strip()[:120] or "Untitled"
    status = "inbox" if inbox else (status or DEFAULT_STATUS[type])
    _check_status(type, status)
    await _check_parent(db, user.id, type, parent_id)
    now = utcnow()
    item = OrgItem(
        user_id=user.id,
        type=type,
        title=title,
        body=body,
        status=status,
        parent_id=parent_id,
        due_on=due_on,
        priority=max(0, min(3, priority)),
        tags=clean_tags(tags),
        source=source,
        done_at=now if status == "done" else None,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    await db.flush()
    await _relink(db, item)
    await db.commit()
    log.info("organiser.created", extra={"item_id": str(item.id), "type": type, "src": source})
    await changed(redis, user.id, item, "created")
    return item


FIELDS = ("title", "body", "status", "parent_id", "due_on", "priority", "tags", "pinned", "type")


async def update(
    db: AsyncSession,
    user: User,
    item: OrgItem,
    changes: dict,
    *,
    version: int | None = None,
    actor: str = "user",
    redis: Redis | None = None,
) -> OrgItem:
    """Apply `changes` (only FIELDS). With `version`, refuse when the item moved on. Commits."""
    if version is not None and version != item.version:
        raise VersionConflict(item)
    changes = {k: v for k, v in changes.items() if k in FIELDS}
    new_type = changes.get("type", item.type)
    if new_type != item.type:
        if item.type == "daily" or new_type not in TYPES or new_type == "daily":
            raise OrganiserError("That can't change type.")
        if "status" not in changes:
            changes["status"] = (
                item.status if item.status in STATUSES[new_type] else DEFAULT_STATUS[new_type]
            )
    if "status" in changes:
        _check_status(new_type, changes["status"])
    if "parent_id" in changes or new_type != item.type:
        await _check_parent(
            db, user.id, new_type, changes.get("parent_id", item.parent_id), item.id
        )
    if "title" in changes:
        changes["title"] = (changes["title"] or "").strip()[:TITLE_CHARS] or item.title
    if "body" in changes:
        changes["body"] = (changes["body"] or "")[:BODY_CHARS]
    if "tags" in changes:
        changes["tags"] = clean_tags(changes["tags"])
    if "priority" in changes:
        changes["priority"] = max(0, min(3, int(changes["priority"] or 0)))
    text_changed = any(k in changes and changes[k] != getattr(item, k) for k in ("title", "body"))
    if text_changed:
        last = await db.scalar(
            select(OrgRevision.created_at)
            .where(OrgRevision.item_id == item.id)
            .order_by(OrgRevision.created_at.desc())
            .limit(1)
        )
        if last is None or utcnow() - last > REVISION_EVERY or actor != "user":
            db.add(
                OrgRevision(
                    item_id=item.id,
                    version=item.version,
                    title=item.title,
                    body=item.body,
                    actor=actor,
                )
            )
            await _prune_revisions(db, item.id)
    old_status = item.status
    for k, v in changes.items():
        setattr(item, k, v)
    if item.status == "done" and old_status != "done":
        item.done_at = utcnow()
    elif item.status != "done":
        item.done_at = None
    # Filed out of the inbox: the suggestion has done its job.
    if old_status == "inbox" and item.status != "inbox":
        item.suggestion = None
    item.version += 1
    item.updated_at = utcnow()
    if "body" in changes:
        # Embedding is redone by the background job.
        item.embedding = None
        await _relink(db, item)
    if "title" in changes and text_changed:
        # Other items' [[old title]] links point by title: re-index the ones naming either.
        await _relink_mentions(db, item)
    await db.commit()
    log.info(
        "organiser.updated",
        extra={"item_id": str(item.id), "fields": ",".join(sorted(changes)), "actor": actor},
    )
    await changed(redis, user.id, item, "updated")
    return item


async def _relink_mentions(db: AsyncSession, item: OrgItem) -> None:
    pattern = f"%[[{item.title.lower()}%"
    rows = await db.scalars(
        select(OrgItem).where(
            OrgItem.user_id == item.user_id,
            OrgItem.id != item.id,
            or_(
                func.lower(OrgItem.body).like(pattern),
                OrgItem.id.in_(select(OrgLink.src_id).where(OrgLink.dst_id == item.id)),
            ),
        )
    )
    for other in rows.all():
        await _relink(db, other)


async def _prune_revisions(db: AsyncSession, item_id: uuid.UUID) -> None:
    keep = (
        select(OrgRevision.id)
        .where(OrgRevision.item_id == item_id)
        .order_by(OrgRevision.created_at.desc())
        .limit(MAX_REVISIONS - 1)
    )
    await db.execute(
        delete(OrgRevision).where(OrgRevision.item_id == item_id, OrgRevision.id.not_in(keep))
    )


def live(user_id: uuid.UUID):
    """Items of this user that aren't in Trash."""
    return and_(OrgItem.user_id == user_id, visible(TRASH_KIND, OrgItem.id))


async def daily(db: AsyncSession, user: User, day: date, redis: Redis | None = None) -> OrgItem:
    """The daily note for `day`, made from the template the first time it is opened."""
    item = await db.scalar(
        select(OrgItem).where(
            OrgItem.user_id == user.id, OrgItem.type == "daily", OrgItem.day == day
        )
    )
    if item is not None:
        return item
    now = utcnow()
    item = OrgItem(
        user_id=user.id,
        type="daily",
        title=f"{day:%A} {day.day} {day:%B %Y}",
        body=DAILY_TEMPLATE,
        status="active",
        day=day,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    try:
        await db.commit()
    except Exception:
        # Opened on two devices at once: the other one made it.
        await db.rollback()
        return await db.scalar(
            select(OrgItem).where(
                OrgItem.user_id == user.id, OrgItem.type == "daily", OrgItem.day == day
            )
        )
    await changed(redis, user.id, item, "created")
    return item


async def tasks_from_checkboxes(
    db: AsyncSession, user: User, item: OrgItem, redis: Redis | None = None
) -> list[OrgItem]:
    """Every unticked "- [ ] something" line becomes a task (filed where the note is), and the
    line becomes a [[link]] to it. Lines already linking somewhere are left alone."""
    found = [(m.group(1), m.group(2)) for m in _CHECKBOX.finditer(item.body)]
    if not found:
        return []
    parent = item.parent_id
    made: list[OrgItem] = []
    for _prefix, text in found[:50]:
        task = OrgItem(
            user_id=user.id,
            type="task",
            title=text[:TITLE_CHARS],
            status="todo",
            parent_id=parent,
            due_on=item.day if item.type == "daily" else None,
            source="user",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(task)
        made.append(task)
    titles = iter(t.title for t in made)
    body = _CHECKBOX.sub(lambda m: f"{m.group(1)}[[{next(titles, m.group(2))}]]", item.body, 50)
    await db.flush()
    await update(db, user, item, {"body": body}, redis=redis)
    for t in made:
        await changed(redis, user.id, t, "created")
    log.info("organiser.tasks_from_note", extra={"item_id": str(item.id), "count": len(made)})
    return made


async def to_trash(db: AsyncSession, user: User, item: OrgItem, redis: Redis | None = None) -> int:
    """Move an item to Trash, with everything inside it (so a deleted project takes its notes
    and tasks along, and Restore brings them all back). Returns how many went. Commits."""
    if item.type == "daily":
        raise OrganiserError("A daily note can't be deleted. Clear its text instead.")
    root = await add_to_trash(
        db, user.id, TRASH_KIND, item.id, item.title or item.type, data={"type": item.type}
    )
    await db.flush()
    count, frontier, seen = 1, [item.id], {item.id}
    while frontier and count < 5000:
        rows = (
            await db.scalars(
                select(OrgItem).where(
                    OrgItem.user_id == user.id,
                    OrgItem.parent_id.in_(frontier),
                    visible(TRASH_KIND, OrgItem.id),
                )
            )
        ).all()
        frontier = []
        for child in rows:
            if child.id in seen:
                continue
            seen.add(child.id)
            frontier.append(child.id)
            await add_to_trash(
                db, user.id, TRASH_KIND, child.id, child.title or child.type, parent_id=root.id
            )
            count += 1
    await db.commit()
    log.info("organiser.trashed", extra={"item_id": str(item.id), "count": count})
    await changed(redis, user.id, item, "deleted")
    return count


# --- Quick capture -----------------------------------------------------------------------------

_TASK_PREFIX = re.compile(r"^\s*(?:[-*+]\s*)?(?:\[\s?\]|todo:?|task:|t:)\s*", re.I)
_TAG = re.compile(r"(?<![\w&])#([A-Za-z][\w/-]{0,39})")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DUE = re.compile(
    r"\s*\b(?:(?:due|by|on)\s+)?(today|tonight|tomorrow|tmrw|next week|"
    + "|".join(_WEEKDAYS)
    + r"|\d{4}-\d{2}-\d{2})\b\s*$",
    re.I,
)


@dataclass
class Capture:
    type: str
    title: str
    body: str
    tags: list[str]
    due_on: date | None


def parse_capture(text: str, today: date) -> Capture:
    """Read a quick capture: "[ ] call the bank tomorrow #money" is a task due tomorrow tagged
    money; anything else is a note. The first line is the title, the rest the text. Dates are
    understood only at the end of a task's title (today, tomorrow, a weekday, next week, or
    2026-12-01), so a note that mentions Friday stays as written."""
    text = (text or "").strip()
    first, _, rest = text.partition("\n")
    is_task = bool(_TASK_PREFIX.match(first))
    title = _TASK_PREFIX.sub("", first, count=1) if is_task else first
    tags = clean_tags(_TAG.findall(text))
    title = _TAG.sub("", title)
    due: date | None = None
    if is_task and (m := _DUE.search(title)):
        due = _due_word(m.group(1).lower(), today)
        if due is not None:
            title = title[: m.start()]
    title = " ".join(title.split())[:TITLE_CHARS]
    return Capture("task" if is_task else "note", title, rest.strip(), tags, due)


def _due_word(word: str, today: date) -> date | None:
    if word in ("today", "tonight"):
        return today
    if word in ("tomorrow", "tmrw"):
        return today + timedelta(days=1)
    if word == "next week":
        return today + timedelta(days=7 - today.weekday())
    if word in _WEEKDAYS:
        ahead = (_WEEKDAYS.index(word) - today.weekday()) % 7
        return today + timedelta(days=ahead or 7)
    try:
        return date.fromisoformat(word)
    except ValueError:
        return None


async def capture(
    db: AsyncSession,
    user: User,
    text: str,
    *,
    source: str = "user",
    inbox: bool = True,
    task: bool = False,
    parent_id: uuid.UUID | None = None,
    redis: Redis | None = None,
) -> OrgItem:
    """Save a quick capture: to the Inbox by default, or filed straight away (inbox=False,
    e.g. typed into a project's task list). task=True makes it a task without the "[ ]".
    Commits."""
    c = parse_capture(
        f"[ ] {text}" if task and not _TASK_PREFIX.match(text) else text, local_today(user)
    )
    return await create(
        db,
        user,
        type=c.type,
        title=c.title,
        body=c.body,
        due_on=c.due_on,
        tags=c.tags,
        parent_id=parent_id,
        source=source,
        inbox=inbox,
        redis=redis,
    )


# --- Weekly review -----------------------------------------------------------------------------


@dataclass
class ReviewState:
    week_of: date
    review: OrgReview | None
    inbox: list[OrgItem]
    overdue: list[OrgItem]
    stalled: list[OrgItem]
    done_this_week: list[OrgItem]
    upcoming: list[OrgItem]
    # Open tasks that could be next week's focus, best first.
    candidates: list[OrgItem]


async def review_state(db: AsyncSession, user: User, today: date | None = None) -> ReviewState:
    today = today or local_today(user)
    monday = week_of(today)
    base = live(user.id)

    async def items(*where, order=None, limit=100) -> list[OrgItem]:
        q = select(OrgItem).where(base, *where)
        q = q.order_by(*(order or (OrgItem.updated_at.desc(),))).limit(limit)
        return list((await db.scalars(q)).all())

    inbox = await items(OrgItem.status == "inbox", order=(OrgItem.created_at,))
    overdue = await items(
        OrgItem.type == "task",
        OrgItem.status.in_(("todo", "doing")),
        OrgItem.due_on < today,
        order=(OrgItem.due_on,),
    )
    week_ago = utcnow() - timedelta(days=7)
    done = await items(OrgItem.type == "task", OrgItem.done_at >= week_ago)
    upcoming = await items(
        OrgItem.type == "task",
        OrgItem.status.in_(("todo", "doing")),
        OrgItem.due_on >= today,
        OrgItem.due_on < monday + timedelta(days=7),
        order=(OrgItem.due_on,),
    )
    # Active projects with no open task, or untouched (themselves and their tasks) for a while.
    projects = await items(OrgItem.type == "project", OrgItem.status == "active")
    stalled: list[OrgItem] = []
    cutoff = utcnow() - STALLED_AFTER
    for p in projects:
        open_tasks, last_touch = (
            await db.execute(
                select(
                    func.count().filter(OrgItem.status.in_(("todo", "doing"))),
                    func.max(OrgItem.updated_at),
                ).where(base, OrgItem.parent_id == p.id)
            )
        ).one()
        latest = max(x for x in (p.updated_at, last_touch) if x is not None)
        if not open_tasks or latest < cutoff:
            stalled.append(p)
    candidates = await items(
        OrgItem.type == "task",
        OrgItem.status.in_(("todo", "doing")),
        order=(
            OrgItem.priority.desc(),
            OrgItem.due_on.asc().nulls_last(),
            OrgItem.updated_at.desc(),
        ),
        limit=30,
    )
    review = await db.scalar(
        select(OrgReview).where(OrgReview.user_id == user.id, OrgReview.week_of == monday)
    )
    return ReviewState(monday, review, inbox, overdue, stalled, done, upcoming, candidates)


async def complete_review(
    db: AsyncSession, user: User, focus: list[uuid.UUID], notes: str, stats: dict
) -> OrgReview:
    monday = week_of(local_today(user))
    review = await db.scalar(
        select(OrgReview).where(OrgReview.user_id == user.id, OrgReview.week_of == monday)
    )
    if review is None:
        review = OrgReview(user_id=user.id, week_of=monday)
        db.add(review)
    owned = set(
        (
            await db.scalars(
                select(OrgItem.id).where(OrgItem.user_id == user.id, OrgItem.id.in_(focus))
            )
        ).all()
    )
    review.focus = [str(i) for i in focus if i in owned][:10]
    review.notes = notes[:20_000]
    review.stats = {k: int(v) for k, v in stats.items() if isinstance(v, int)}
    review.completed_at = utcnow()
    await db.commit()
    log.info("organiser.review_done", extra={"focus": len(review.focus)})
    return review


# --- Obsidian-compatible export ----------------------------------------------------------------

_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]+')
FOLDERS = {
    "project": "1 Projects",
    "area": "2 Areas",
    "resource": "3 Resources",
    "note": "Notes",
    "task": "Tasks",
    "daily": "Daily",
}


def file_name(title: str) -> str:
    name = _UNSAFE.sub(" ", title).strip(" .")
    return (" ".join(name.split()) or "Untitled")[:120]


def _yaml(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_yaml(v) for v in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


async def vault(db: AsyncSession, user: User) -> dict[str, str]:
    """Every live item as a Markdown file: {path: text}. Archived things go under 4 Archive;
    a project's or area's open tasks are listed as a checklist in its own file, so the vault
    reads well without plugins."""
    rows = list(await db.scalars(select(OrgItem).where(live(user.id)).order_by(OrgItem.created_at)))
    names: dict[uuid.UUID, str] = {}
    used: set[str] = set()
    for item in rows:
        base = file_name(item.day.isoformat() if item.type == "daily" else item.title)
        name, n = base, 2
        while name.lower() in used:
            name, n = f"{base} {n}", n + 1
        used.add(name.lower())
        names[item.id] = name
    by_parent: dict[uuid.UUID, list[OrgItem]] = {}
    for item in rows:
        if item.parent_id:
            by_parent.setdefault(item.parent_id, []).append(item)

    files: dict[str, str] = {}
    for item in rows:
        archived = item.status in ("archived", "dropped") or (
            item.type == "project" and item.status == "done"
        )
        folder = "4 Archive" if archived else FOLDERS[item.type]
        if item.status == "inbox":
            folder = "0 Inbox"
        meta = {
            "id": str(item.id),
            "type": item.type,
            "status": item.status,
            "parent": f"[[{names[item.parent_id]}]]" if item.parent_id in names else None,
            "due": item.due_on.isoformat() if item.due_on else None,
            "priority": item.priority or None,
            "tags": item.tags or None,
            "created": item.created_at.isoformat(timespec="seconds"),
            "updated": item.updated_at.isoformat(timespec="seconds"),
        }
        front = "\n".join(f"{k}: {_yaml(v)}" for k, v in meta.items() if v is not None)
        body = item.body.rstrip()
        if item.type == "task":
            tick = "x" if item.status == "done" else " "
            body = f"- [{tick}] {item.title}" + (f"\n\n{body}" if body else "")
        children = by_parent.get(item.id, [])
        tasks = [c for c in children if c.type == "task"]
        others = [c for c in children if c.type != "task"]
        if tasks:
            lines = [
                f"- [{'x' if t.status == 'done' else ' '}] [[{names[t.id]}]]"
                + (f" 📅 {t.due_on.isoformat()}" if t.due_on else "")
                for t in tasks
            ]
            body += "\n\n## Tasks\n\n" + "\n".join(lines)
        if others:
            body += "\n\n## Inside\n\n" + "\n".join(f"- [[{names[c.id]}]]" for c in others)
        files[f"{folder}/{names[item.id]}.md"] = f"---\n{front}\n---\n\n{body.strip()}\n"
    files["README.md"] = (
        "# Nexus organiser export\n\nExported "
        f"{utcnow():%d %B %Y %H:%M} UTC. Open this folder as a vault in Obsidian.\n\n"
        "Folders follow PARA: 0 Inbox, 1 Projects, 2 Areas, 3 Resources, 4 Archive, plus "
        "Notes, Tasks and Daily. Each file's front matter keeps its Nexus id, type and status.\n"
    )
    return files


def zip_vault(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in sorted(files.items()):
            z.writestr(f"Nexus/{path}", text)
    return buf.getvalue()


def export_key(user_id: uuid.UUID) -> str:
    """Where the nightly export of a person's vault is kept in object storage."""
    return f"exports/{user_id}/organiser-vault.zip"
