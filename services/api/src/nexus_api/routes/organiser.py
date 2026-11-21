"""The life organiser (nexus_common/organiser.py): notes, tasks, projects, areas and resources,
one Inbox for quick capture, a daily note, the weekly review and an Obsidian-ready export.

Everything is the signed-in person's own; another person's ids answer 404. Saves carry the
version they were based on, and an outdated one answers 409 with the current item, so the
laptop and the phone can't silently overwrite each other.
"""

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_api.deps import CurrentUser, DbDep, MemberUser, QueueDep, RedisDep
from nexus_api.ratelimit import hit, too_many
from nexus_common import organiser as org
from nexus_common.models import OrgItem, OrgLink, OrgRevision, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/organiser", tags=["organiser"])

ItemType = Literal["note", "task", "project", "area", "resource"]
CAPTURES_PER_MINUTE = 60
EXPORTS_PER_HOUR = 30


class ItemOut(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    status: str
    parent_id: uuid.UUID | None
    due_on: date | None
    priority: int
    tags: list[str]
    pinned: bool
    day: date | None
    source: str
    suggestion: dict | None
    version: int
    done_at: datetime | None
    created_at: datetime
    updated_at: datetime
    body: str | None = None
    excerpt: str | None = None


class RefOut(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    status: str


class ItemDetailOut(ItemOut):
    parent: RefOut | None
    children: list[ItemOut]
    backlinks: list[RefOut]
    # Items this one's [[links]] point at (the editor's preview turns them into links).
    links: list[RefOut]
    # [[Links]] in the text with no item of that title yet: the page offers to make them.
    unresolved: list[str]


class ItemIn(BaseModel):
    type: ItemType = "note"
    title: str = Field(default="", max_length=org.TITLE_CHARS)
    body: str = Field(default="", max_length=org.BODY_CHARS)
    status: str | None = None
    parent_id: uuid.UUID | None = None
    due_on: date | None = None
    priority: int = Field(default=0, ge=0, le=3)
    tags: list[str] = Field(default_factory=list, max_length=org.MAX_TAGS)
    # Capture unfiled: it waits in the Inbox with a filing suggestion.
    inbox: bool = False


class ItemPatch(BaseModel):
    # The version this edit started from; leave out to save regardless.
    version: int | None = None
    # Always keep the text being replaced as a revision (saving over a conflicting edit).
    keep_revision: bool = False
    type: ItemType | None = None
    title: str | None = Field(default=None, max_length=org.TITLE_CHARS)
    body: str | None = Field(default=None, max_length=org.BODY_CHARS)
    status: str | None = None
    parent_id: uuid.UUID | None = None
    due_on: date | None = None
    priority: int | None = Field(default=None, ge=0, le=3)
    tags: list[str] | None = Field(default=None, max_length=org.MAX_TAGS)
    pinned: bool | None = None


class CaptureIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    # False files it straight away (a task typed into a project's list, say).
    inbox: bool = True
    # Make it a task even without "[ ]" in front.
    task: bool = False
    parent_id: uuid.UUID | None = None


class RevisionOut(BaseModel):
    id: uuid.UUID
    version: int
    title: str
    actor: str
    created_at: datetime
    body: str | None = None


class TodayOut(BaseModel):
    day: date
    daily: ItemOut
    overdue: list[ItemOut]
    due_today: list[ItemOut]
    doing: list[ItemOut]
    focus: list[ItemOut]
    upcoming: list[ItemOut]


class ReviewStatusOut(BaseModel):
    week_of: date
    completed_at: datetime | None
    # Sunday (or later in the week) with this week's review not done yet.
    due: bool


class OverviewOut(BaseModel):
    today: date
    inbox: int
    overdue: int
    due_today: int
    open_tasks: int
    projects: int
    areas: int
    resources: int
    notes: int
    archived: int
    review: ReviewStatusOut


class ReviewOut(BaseModel):
    week_of: date
    completed_at: datetime | None
    focus: list[uuid.UUID]
    notes: str
    inbox: list[ItemOut]
    overdue: list[ItemOut]
    stalled: list[ItemOut]
    done_this_week: list[ItemOut]
    upcoming: list[ItemOut]
    candidates: list[ItemOut]


class ReviewIn(BaseModel):
    focus: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    notes: str = Field(default="", max_length=20_000)


class TasksOut(BaseModel):
    item: ItemOut
    made: list[ItemOut]


def out(item: OrgItem, *, body: bool = True) -> ItemOut:
    return ItemOut.model_validate(org.payload(item, body=body))


def ref(item: OrgItem) -> RefOut:
    return RefOut(id=item.id, type=item.type, title=item.title, status=item.status)


async def own_item(db: AsyncSession, user: User, item_id: uuid.UUID) -> OrgItem:
    item = await db.scalar(select(OrgItem).where(OrgItem.id == item_id, org.live(user.id)))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return item


def unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def conflict(exc: org.VersionConflict) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc), "current": out(exc.item).model_dump(mode="json")},
    )


async def suggest(queue, item: OrgItem) -> None:
    if item.status != "inbox":
        return
    try:
        await queue.enqueue_organiser_suggest(item.id)
    except Exception:
        # The item is saved; it just waits in the Inbox without a suggestion.
        log.warning("organiser.suggest_not_queued", extra={"item_id": str(item.id)})


# --- Overview and today ---------------------------------------------------------------------------


@router.get("/overview", response_model=OverviewOut)
async def overview(user: CurrentUser, db: DbDep):
    """Counts for the sidebar and the Organiser home."""
    today = org.local_today(user)
    open_task = (OrgItem.type == "task") & OrgItem.status.in_(("todo", "doing"))
    row = (
        await db.execute(
            select(
                func.count().filter(OrgItem.status == "inbox"),
                func.count().filter(open_task & (OrgItem.due_on < today)),
                func.count().filter(open_task & (OrgItem.due_on == today)),
                func.count().filter(open_task),
                func.count().filter((OrgItem.type == "project") & (OrgItem.status != "archived")),
                func.count().filter((OrgItem.type == "area") & (OrgItem.status == "active")),
                func.count().filter((OrgItem.type == "resource") & (OrgItem.status == "active")),
                func.count().filter((OrgItem.type == "note") & (OrgItem.status == "active")),
                func.count().filter(OrgItem.status.in_(("archived", "dropped"))),
            ).where(org.live(user.id))
        )
    ).one()
    state = await org.review_state(db, user, today)
    return OverviewOut(
        today=today,
        inbox=row[0],
        overdue=row[1],
        due_today=row[2],
        open_tasks=row[3],
        projects=row[4],
        areas=row[5],
        resources=row[6],
        notes=row[7],
        archived=row[8],
        review=ReviewStatusOut(
            week_of=state.week_of,
            completed_at=state.review.completed_at if state.review else None,
            due=today.weekday() == 6 and not (state.review and state.review.completed_at),
        ),
    )


@router.get("/today", response_model=TodayOut)
async def today_view(user: CurrentUser, db: DbDep, redis: RedisDep):
    """Today's daily note with the tasks that matter today and the week's focus."""
    today = org.local_today(user)
    note = await org.daily(db, user, today, redis)
    base = (
        org.live(user.id),
        OrgItem.type == "task",
        OrgItem.status.in_(("todo", "doing")),
    )

    async def tasks(*where, order=(OrgItem.due_on, OrgItem.priority.desc())) -> list[ItemOut]:
        rows = await db.scalars(select(OrgItem).where(*base, *where).order_by(*order).limit(100))
        return [out(t, body=False) for t in rows]

    focus: list[ItemOut] = []
    state = await org.review_state(db, user, today)
    if state.review and state.review.focus:
        ids = [uuid.UUID(i) for i in state.review.focus]
        rows = await db.scalars(select(OrgItem).where(org.live(user.id), OrgItem.id.in_(ids)))
        by_id = {r.id: r for r in rows}
        focus = [out(by_id[i], body=False) for i in ids if i in by_id]
    return TodayOut(
        day=today,
        daily=out(note),
        overdue=await tasks(OrgItem.due_on < today),
        due_today=await tasks(OrgItem.due_on == today),
        doing=await tasks(
            OrgItem.status == "doing",
            or_(OrgItem.due_on.is_(None), OrgItem.due_on > today),
            order=(OrgItem.updated_at.desc(),),
        ),
        focus=focus,
        # What's already under "In progress" isn't repeated here.
        upcoming=await tasks(
            OrgItem.status == "todo",
            OrgItem.due_on > today,
            OrgItem.due_on <= today + timedelta(days=7),
        ),
    )


@router.get("/daily/{day}", response_model=ItemOut)
async def daily_note(day: date, user: CurrentUser, db: DbDep, redis: RedisDep):
    """The daily note for a date, made from the template the first time it's opened."""
    today = org.local_today(user)
    if abs((day - today).days) > 3660:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That date is too far away.")
    return out(await org.daily(db, user, day, redis))


# --- Items ----------------------------------------------------------------------------------------


@router.get("/items", response_model=list[ItemOut])
async def list_items(
    user: CurrentUser,
    db: DbDep,
    type: list[str] = Query(default_factory=list),
    item_status: list[str] = Query(default_factory=list, alias="status"),
    parent_id: uuid.UUID | None = None,
    unfiled: bool = False,
    pinned: bool | None = None,
    tag: str | None = Query(default=None, max_length=40),
    due_before: date | None = None,
    due_after: date | None = None,
    q: str | None = Query(default=None, max_length=200),
    order: Literal["updated", "created", "due", "title", "priority"] = "updated",
    limit: int = Query(default=200, ge=1, le=1000),
):
    """Items without their full text (an excerpt instead), newest change first by default."""
    query = select(OrgItem).where(org.live(user.id))
    if type:
        query = query.where(OrgItem.type.in_([t for t in type if t in org.TYPES]))
    else:
        # Daily notes have their own view; they'd crowd every other list.
        query = query.where(OrgItem.type != "daily")
    if item_status:
        query = query.where(OrgItem.status.in_(item_status))
    if parent_id is not None:
        query = query.where(OrgItem.parent_id == parent_id)
    if unfiled:
        query = query.where(OrgItem.parent_id.is_(None))
    if pinned is not None:
        query = query.where(OrgItem.pinned.is_(pinned))
    if tag:
        tags = org.clean_tags([tag])
        if tags:
            query = query.where(cast(OrgItem.tags, JSONB).contains([tags[0]]))
    if due_before is not None:
        query = query.where(OrgItem.due_on < due_before)
    if due_after is not None:
        query = query.where(OrgItem.due_on >= due_after)
    for word in (q or "").split()[:6]:
        pattern = f"%{word.replace('%', '').replace('_', '')}%"
        query = query.where(or_(OrgItem.title.ilike(pattern), OrgItem.body.ilike(pattern)))
    orders = {
        "updated": (OrgItem.pinned.desc(), OrgItem.updated_at.desc()),
        "created": (OrgItem.created_at.desc(),),
        "due": (OrgItem.due_on.asc().nulls_last(), OrgItem.priority.desc()),
        "title": (func.lower(OrgItem.title),),
        "priority": (OrgItem.priority.desc(), OrgItem.due_on.asc().nulls_last()),
    }
    rows = await db.scalars(query.order_by(*orders[order]).limit(limit))
    return [out(r, body=False) for r in rows]


@router.post("/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(body: ItemIn, user: MemberUser, db: DbDep, redis: RedisDep, queue: QueueDep):
    if body.parent_id is not None:
        await own_item(db, user, body.parent_id)
    try:
        item = await org.create(
            db,
            user,
            type=body.type,
            title=body.title,
            body=body.body,
            status=body.status,
            parent_id=body.parent_id,
            due_on=body.due_on,
            priority=body.priority,
            tags=body.tags,
            inbox=body.inbox,
            redis=redis,
        )
    except org.OrganiserError as exc:
        raise unprocessable(exc) from exc
    await suggest(queue, item)
    return out(item)


@router.post("/capture", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def capture(body: CaptureIn, user: MemberUser, db: DbDep, redis: RedisDep, queue: QueueDep):
    """Quick capture: "[ ] call the bank tomorrow #money" makes a task due tomorrow tagged
    money; anything else is a note. It waits in the Inbox unless inbox is false."""
    if wait := await hit(redis, "org-capture", str(user.id), CAPTURES_PER_MINUTE, 60):
        raise too_many(wait, "captures")
    if body.parent_id is not None:
        await own_item(db, user, body.parent_id)
    try:
        item = await org.capture(
            db,
            user,
            body.text,
            inbox=body.inbox,
            task=body.task,
            parent_id=body.parent_id,
            redis=redis,
        )
    except org.OrganiserError as exc:
        raise unprocessable(exc) from exc
    await suggest(queue, item)
    return out(item)


@router.get("/items/{item_id}", response_model=ItemDetailOut)
async def get_item(item_id: uuid.UUID, user: CurrentUser, db: DbDep):
    """One item in full, with what it sits in, what's inside it and what links to it."""
    item = await own_item(db, user, item_id)
    parent = (
        await db.scalar(select(OrgItem).where(OrgItem.id == item.parent_id, org.live(user.id)))
        if item.parent_id
        else None
    )
    children = await db.scalars(
        select(OrgItem)
        .where(org.live(user.id), OrgItem.parent_id == item.id)
        .order_by(
            OrgItem.type,
            (OrgItem.status.in_(("done", "dropped", "archived"))).asc(),
            OrgItem.due_on.asc().nulls_last(),
            OrgItem.priority.desc(),
            OrgItem.updated_at.desc(),
        )
        .limit(500)
    )
    backlinks = await db.scalars(
        select(OrgItem)
        .join(OrgLink, OrgLink.src_id == OrgItem.id)
        .where(OrgLink.dst_id == item.id, org.live(user.id))
        .order_by(OrgItem.updated_at.desc())
        .limit(200)
    )
    named = org.wikilinks(item.body)
    linked: list[OrgItem] = []
    if named:
        linked = list(
            await db.scalars(
                select(OrgItem)
                .where(
                    org.live(user.id),
                    OrgItem.id != item.id,
                    func.lower(OrgItem.title).in_([n.lower() for n in named]),
                )
                .limit(200)
            )
        )
    found = {t.title.lower() for t in linked}
    return ItemDetailOut(
        **out(item).model_dump(),
        parent=ref(parent) if parent else None,
        children=[out(c, body=False) for c in children],
        backlinks=[ref(b) for b in backlinks],
        links=[ref(t) for t in linked],
        unresolved=[n for n in named if n.lower() not in found][:50],
    )


@router.patch("/items/{item_id}", response_model=ItemOut)
async def edit_item(
    item_id: uuid.UUID, body: ItemPatch, user: MemberUser, db: DbDep, redis: RedisDep
):
    item = await own_item(db, user, item_id)
    changes = body.model_dump(exclude_unset=True)
    version = changes.pop("version", None)
    actor = "conflict" if changes.pop("keep_revision", False) else "user"
    if changes.get("parent_id") is not None:
        await own_item(db, user, changes["parent_id"])
    try:
        item = await org.update(db, user, item, changes, version=version, actor=actor, redis=redis)
    except org.VersionConflict as exc:
        return conflict(exc)
    except org.OrganiserError as exc:
        raise unprocessable(exc) from exc
    return out(item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: uuid.UUID, user: MemberUser, db: DbDep, redis: RedisDep):
    """To Trash for seven days, with everything inside it."""
    item = await own_item(db, user, item_id)
    try:
        await org.to_trash(db, user, item, redis)
    except org.OrganiserError as exc:
        raise unprocessable(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/items/{item_id}/tasks", response_model=TasksOut)
async def tasks_from_note(item_id: uuid.UUID, user: MemberUser, db: DbDep, redis: RedisDep):
    """Turn every unticked "- [ ] ..." line into a task, linked from the line."""
    item = await own_item(db, user, item_id)
    if item.type == "task":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This is already a task.")
    made = await org.tasks_from_checkboxes(db, user, item, redis)
    await db.refresh(item)
    return TasksOut(item=out(item), made=[out(t, body=False) for t in made])


@router.get("/items/{item_id}/revisions", response_model=list[RevisionOut])
async def list_revisions(item_id: uuid.UUID, user: CurrentUser, db: DbDep):
    await own_item(db, user, item_id)
    rows = await db.scalars(
        select(OrgRevision)
        .where(OrgRevision.item_id == item_id)
        .order_by(OrgRevision.created_at.desc())
    )
    return [
        RevisionOut(
            id=r.id, version=r.version, title=r.title, actor=r.actor, created_at=r.created_at
        )
        for r in rows
    ]


async def own_revision(db, user, item_id, revision_id) -> tuple[OrgItem, OrgRevision]:
    item = await own_item(db, user, item_id)
    rev = await db.scalar(
        select(OrgRevision).where(OrgRevision.id == revision_id, OrgRevision.item_id == item.id)
    )
    if rev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return item, rev


@router.get("/items/{item_id}/revisions/{revision_id}", response_model=RevisionOut)
async def get_revision(item_id: uuid.UUID, revision_id: uuid.UUID, user: CurrentUser, db: DbDep):
    _, rev = await own_revision(db, user, item_id, revision_id)
    return RevisionOut(
        id=rev.id,
        version=rev.version,
        title=rev.title,
        actor=rev.actor,
        created_at=rev.created_at,
        body=rev.body,
    )


@router.post("/items/{item_id}/revisions/{revision_id}/restore", response_model=ItemOut)
async def restore_revision(
    item_id: uuid.UUID, revision_id: uuid.UUID, user: MemberUser, db: DbDep, redis: RedisDep
):
    """Put an earlier text back. The text being replaced is kept as a revision too."""
    item, rev = await own_revision(db, user, item_id, revision_id)
    item = await org.update(
        db, user, item, {"title": rev.title, "body": rev.body}, actor="restore", redis=redis
    )
    return out(item)


# --- Weekly review --------------------------------------------------------------------------------


@router.get("/review", response_model=ReviewOut)
async def get_review(user: CurrentUser, db: DbDep):
    """What the weekly review walks through, step by step."""
    s = await org.review_state(db, user)

    def many(items: list[OrgItem]) -> list[ItemOut]:
        return [out(i, body=False) for i in items]

    return ReviewOut(
        week_of=s.week_of,
        completed_at=s.review.completed_at if s.review else None,
        focus=[uuid.UUID(i) for i in (s.review.focus if s.review else [])],
        notes=s.review.notes if s.review else "",
        inbox=many(s.inbox),
        overdue=many(s.overdue),
        stalled=many(s.stalled),
        done_this_week=many(s.done_this_week),
        upcoming=many(s.upcoming),
        candidates=many(s.candidates),
    )


@router.post("/review", response_model=ReviewOut)
async def finish_review(body: ReviewIn, user: MemberUser, db: DbDep):
    """Save the week's focus and notes; the review is marked done for this week."""
    s = await org.review_state(db, user)
    await org.complete_review(
        db,
        user,
        body.focus,
        body.notes,
        {
            "inbox_left": len(s.inbox),
            "overdue": len(s.overdue),
            "stalled": len(s.stalled),
            "done": len(s.done_this_week),
        },
    )
    return await get_review(user, db)


# --- Export ---------------------------------------------------------------------------------------


@router.get("/export.zip")
async def export_vault(user: CurrentUser, db: DbDep, redis: RedisDep):
    """Everything as Markdown files in a zip, ready to open as an Obsidian vault."""
    if wait := await hit(redis, "org-export", str(user.id), EXPORTS_PER_HOUR, 3600):
        raise too_many(wait, "exports")
    files = await org.vault(db, user)
    data = org.zip_vault(files)
    log.info("organiser.exported", extra={"files": len(files), "bytes": len(data)})
    name = f"nexus-organiser-{org.local_today(user).isoformat()}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )
