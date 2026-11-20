"""The assistant's organiser tools: "add buy soil to the garden project", "what's due this
week?", "note this in today's daily note", "mark the passport task done". Same rules as the
Organiser pages (nexus_common/organiser.py); the assistant can't delete, only archive or drop.
"""

import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import func, or_, select

from nexus_common import organiser as org
from nexus_common.models import OrgItem, User

LIST_LIMIT = 40
READ_CHARS = 12_000

Due = Literal["any", "overdue", "today", "week", "none"]


def line(item: OrgItem, parents: dict[uuid.UUID, OrgItem] | None = None) -> str:
    bits = [f"{item.type}", item.status]
    if item.due_on:
        bits.append(f"due {item.due_on:%a %d %b %Y}")
    if item.priority:
        bits.append(("", "low", "medium", "high")[item.priority] + " priority")
    if parents and item.parent_id in parents:
        p = parents[item.parent_id]
        bits.append(f"in {p.type} {p.title}")
    if item.tags:
        bits.append(" ".join(f"#{t}" for t in item.tags))
    return f"- {item.title} (id {item.id}; {', '.join(bits)})"


def build_organiser_tools(ctx: Any, note: Callable) -> dict[str, Callable]:
    deps, user_id = ctx.deps, ctx.user_id

    async def container(db, where: str | None) -> OrgItem | None | str:
        """The project, area or resource named `where` (id or title); a message if unknown."""
        if not where:
            return None
        where = where.strip()
        try:
            wid = uuid.UUID(where)
        except ValueError:
            wid = None
        q = select(OrgItem).where(org.live(user_id), OrgItem.type.in_(org.CONTAINERS))
        found = await db.scalar(
            q.where(OrgItem.id == wid)
            if wid
            else q.where(func.lower(OrgItem.title) == where.lower())
        )
        if found is None and not wid:
            pattern = f"%{where.replace('%', '').replace('_', '')}%"
            close = (await db.scalars(q.where(OrgItem.title.ilike(pattern)).limit(5))).all()
            if len(close) == 1:
                return close[0]
            names = ", ".join(f"{c.type} {c.title}" for c in close)
            return (
                f'No project, area or resource is called "{where}".'
                + (f" Close: {names}." if names else "")
                + " Create it first with organiser_add, or leave where out to file it later."
            )
        return found if found is not None else "No such project, area or resource."

    async def parents_of(db, items) -> dict[uuid.UUID, OrgItem]:
        ids = {i.parent_id for i in items if i.parent_id}
        if not ids:
            return {}
        rows = await db.scalars(select(OrgItem).where(OrgItem.id.in_(ids)))
        return {r.id: r for r in rows}

    async def organiser_add(
        title: str,
        type: Literal["task", "note", "project", "area", "resource"] = "task",
        body: str = "",
        where: str | None = None,
        due_on: str | None = None,
        priority: int = 0,
        tags: list[str] | None = None,
        inbox: bool = False,
    ) -> str:
        """Add to the user's organiser (their PARA notes and tasks). type: task (something to
        do), note (information), project (a goal with an end), area (an ongoing
        responsibility like Health or Money), resource (a topic). where is the title or id of
        the project, area or resource it belongs in (a project can only go in an area). due_on
        is YYYY-MM-DD. priority 0 none to 3 high. inbox=true puts it in the Inbox to file later,
        for quick captures where the place isn't clear. Body is Markdown and may [[link]] other
        items by title."""
        await note("organiser_add", type=type)
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            parent = await container(db, where)
            if isinstance(parent, str):
                return parent
            try:
                due = date.fromisoformat(due_on) if due_on else None
                item = await org.create(
                    db,
                    user,
                    type=type,
                    title=title,
                    body=body,
                    parent_id=parent.id if parent else None,
                    due_on=due,
                    priority=priority,
                    tags=tags,
                    source="assistant",
                    inbox=inbox and type in ("note", "task"),
                    redis=deps.redis,
                )
            except (org.OrganiserError, ValueError) as exc:
                return f"Not added: {exc}"
        place = (
            f" in {parent.type} {parent.title}" if parent else (" to the Inbox" if inbox else "")
        )
        if inbox and deps.enqueue is not None:
            await deps.enqueue(
                "organiser_suggest", str(item.id), _job_id=f"organiser-suggest:{item.id}"
            )
        return f"Added {item.type} {item.title}{place} (id {item.id})."

    async def organiser_find(
        query: str = "",
        type: Literal["any", "task", "note", "project", "area", "resource", "daily"] = "any",
        status: Literal["open", "any", "inbox", "done", "archived"] = "open",
        where: str | None = None,
        due: Due = "any",
        limit: int = 20,
    ) -> str:
        """List items in the user's organiser. query matches words in titles and text. status
        open means not done, dropped or archived. where limits to one project, area or
        resource (title or id). due: overdue, today, week (the next seven days) or none."""
        await note("organiser_find", type=type, due=due)
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            today = org.local_today(user)
            q = select(OrgItem).where(org.live(user_id))
            q = q.where(OrgItem.type == type) if type != "any" else q.where(OrgItem.type != "daily")
            if status == "open":
                q = q.where(OrgItem.status.not_in(("done", "dropped", "archived")))
            elif status == "archived":
                q = q.where(OrgItem.status.in_(("archived", "dropped")))
            elif status != "any":
                q = q.where(OrgItem.status == status)
            if where:
                parent = await container(db, where)
                if isinstance(parent, str):
                    return parent
                q = q.where(OrgItem.parent_id == parent.id)
            if due == "overdue":
                q = q.where(OrgItem.due_on < today)
            elif due == "today":
                q = q.where(OrgItem.due_on == today)
            elif due == "week":
                q = q.where(OrgItem.due_on >= today, OrgItem.due_on <= today + timedelta(days=7))
            elif due == "none":
                q = q.where(OrgItem.due_on.is_(None))
            for word in query.split()[:6]:
                pattern = f"%{word.replace('%', '').replace('_', '')}%"
                q = q.where(or_(OrgItem.title.ilike(pattern), OrgItem.body.ilike(pattern)))
            rows = (
                await db.scalars(
                    q.order_by(
                        OrgItem.due_on.asc().nulls_last(),
                        OrgItem.priority.desc(),
                        OrgItem.updated_at.desc(),
                    ).limit(max(1, min(limit, LIST_LIMIT)))
                )
            ).all()
            parents = await parents_of(db, rows)
        if not rows:
            return "Nothing in the organiser matches."
        return f"Today is {today:%A %d %B %Y}.\n" + "\n".join(line(r, parents) for r in rows)

    async def organiser_read(item_id: str) -> str:
        """Read one organiser item in full (id from organiser_find), with what's inside it."""
        await note("organiser_read")
        try:
            iid = uuid.UUID(item_id)
        except ValueError:
            return "That isn't an organiser id; use organiser_find."
        async with deps.sessionmaker() as db:
            item = await db.scalar(select(OrgItem).where(OrgItem.id == iid, org.live(user_id)))
            if item is None:
                return "No such item."
            children = (
                await db.scalars(
                    select(OrgItem)
                    .where(org.live(user_id), OrgItem.parent_id == item.id)
                    .order_by(OrgItem.type, OrgItem.due_on.asc().nulls_last())
                    .limit(LIST_LIMIT)
                )
            ).all()
            parents = await parents_of(db, [item])
        out = [line(item, parents)[2:], "", item.body[:READ_CHARS] or "(no text)"]
        if children:
            out += ["", "Inside:", *(line(c) for c in children)]
        return "\n".join(out)

    async def organiser_update(
        item_id: str,
        title: str | None = None,
        body: str | None = None,
        append: str | None = None,
        status: str | None = None,
        where: str | None = None,
        due_on: str | None = None,
        priority: int | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Change an organiser item (id from organiser_find). Pass only what changes. status:
        tasks todo, doing, done or dropped; notes active or archived; projects active, paused,
        done or archived. append adds a line to the end of the text without rewriting it
        (best for adding to a note); body replaces the whole text. where moves it into a
        project, area or resource. due_on YYYY-MM-DD, or "" to clear it."""
        await note("organiser_update", status=status)
        try:
            iid = uuid.UUID(item_id)
        except ValueError:
            return "That isn't an organiser id; use organiser_find."
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            item = await db.scalar(select(OrgItem).where(OrgItem.id == iid, org.live(user_id)))
            if item is None:
                return "No such item."
            changes: dict = {}
            if title is not None:
                changes["title"] = title
            if body is not None:
                changes["body"] = body
            if append:
                base = changes.get("body", item.body).rstrip("\n")
                changes["body"] = f"{base}\n{append.strip()}\n" if base else f"{append.strip()}\n"
            if status is not None:
                changes["status"] = status
                if item.status == "inbox" and status == "inbox":
                    changes.pop("status")
            if where is not None:
                parent = await container(db, where)
                if isinstance(parent, str):
                    return parent
                changes["parent_id"] = parent.id if parent else None
                if item.status == "inbox" and "status" not in changes:
                    changes["status"] = org.DEFAULT_STATUS[item.type]
            if due_on is not None:
                try:
                    changes["due_on"] = date.fromisoformat(due_on) if due_on else None
                except ValueError:
                    return "due_on must be YYYY-MM-DD."
            if priority is not None:
                changes["priority"] = priority
            if tags is not None:
                changes["tags"] = tags
            if not changes:
                return "Nothing to change."
            try:
                item = await org.update(
                    db, user, item, changes, actor="assistant", redis=deps.redis
                )
            except org.OrganiserError as exc:
                return f"Not changed: {exc}"
        return f"Done: {line(item)[2:]}"

    async def organiser_today(add_to_daily_note: str | None = None) -> str:
        """The user's day from their organiser: overdue tasks, tasks due today, what they're
        doing, this week's focus from their weekly review, and today's daily note. Pass
        add_to_daily_note to add a line to today's daily note first (a thought, a log
        entry)."""
        await note("organiser_today")
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            today = org.local_today(user)
            daily = await org.daily(db, user, today, deps.redis)
            if add_to_daily_note:
                body = daily.body.rstrip("\n")
                daily = await org.update(
                    db,
                    user,
                    daily,
                    {"body": f"{body}\n- {add_to_daily_note.strip()}\n"},
                    actor="assistant",
                    redis=deps.redis,
                )
            open_tasks = (
                await db.scalars(
                    select(OrgItem)
                    .where(
                        org.live(user_id),
                        OrgItem.type == "task",
                        OrgItem.status.in_(("todo", "doing")),
                        or_(OrgItem.due_on <= today, OrgItem.status == "doing"),
                    )
                    .order_by(OrgItem.due_on.asc().nulls_last(), OrgItem.priority.desc())
                    .limit(LIST_LIMIT)
                )
            ).all()
            state = await org.review_state(db, user, today)
            focus: list[OrgItem] = []
            if state.review and state.review.focus:
                ids = [uuid.UUID(i) for i in state.review.focus]
                focus = list(
                    (
                        await db.scalars(
                            select(OrgItem).where(org.live(user_id), OrgItem.id.in_(ids))
                        )
                    ).all()
                )
            inbox = len(state.inbox)
        overdue = [t for t in open_tasks if t.due_on and t.due_on < today]
        due = [t for t in open_tasks if t.due_on == today]
        doing = [t for t in open_tasks if t not in overdue and t not in due]
        parts = [f"Today is {today:%A %d %B %Y}."]
        for label, items in (
            ("Overdue", overdue),
            ("Due today", due),
            ("In progress", doing),
            ("This week's focus", focus),
        ):
            if items:
                parts += [f"{label}:", *(line(i) for i in items)]
        if inbox:
            parts.append(f"{inbox} item(s) waiting in the Inbox.")
        if not state.review or not state.review.completed_at:
            parts.append("This week's review isn't done yet.")
        parts += ["", f"Daily note (id {daily.id}):", daily.body[:4000].strip() or "(empty)"]
        return "\n".join(parts)

    return {
        "organiser_add": organiser_add,
        "organiser_find": organiser_find,
        "organiser_read": organiser_read,
        "organiser_update": organiser_update,
        "organiser_today": organiser_today,
    }
