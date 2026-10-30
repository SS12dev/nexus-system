"""The assistant's screen tool: open a result over the orb, switch the main screen's layout,
order the widget dock, light people and projects in the orb, or put a notice up. The rules
(the person's own changes win, every change is audited) live in nexus_common/screen.py.

Only the assistant has it. A team's run names its result with present_result and the notice it
raises opens that; ten agents each repainting the screen would be chaos."""

import uuid
from collections.abc import Callable
from typing import Any, Literal

from sqlalchemy import select

from nexus_common import screen as sc
from nexus_common.library import file_payload
from nexus_common.models import Entity, File, NotificationKind, Widget, utcnow
from nexus_common.notices import notify
from nexus_common.trash import visible_file
from nexus_common.widgets import on_screen

Op = Literal["pin", "move", "focus", "mode", "highlight", "notice", "clear"]
MAX_HIGHLIGHT = 8

LEFT_ALONE = (
    "Left as it is: the user {did} themselves in the last {hours} hours. Only pass "
    "user_asked=true when they asked for this change in the conversation."
)


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value).strip())
    except ValueError:
        return None


def build_screen_tools(ctx: Any, note: Callable) -> dict[str, Callable]:
    deps, user_id = ctx.deps, ctx.user_id
    left_alone = LEFT_ALONE.format(did="changed this", hours=sc.OVERRIDE_HOURS)

    async def screen(
        op: Op,
        target: str = "",
        position: int = 1,
        mode: Literal["orb", "list", "dashboard"] | None = None,
        text: str = "",
        user_asked: bool = False,
    ) -> str:
        """Change the user's main screen.
        - focus: open something in the orb's place, target = a file id or exact file name
          (the user's library) or a widget id (list_widgets) to bring to their attention.
        - mode: switch the layout, mode = "orb" (the 3D orb), "list" (plain lists) or
          "dashboard" (a board of their widgets, for a timer-and-notes kind of day).
        - pin / move: put a widget (target = its id) first on the dock, or at position (1 =
          first).
        - highlight: light people, projects or topics the user has mentioned in the orb,
          target = their names, comma-separated.
        - notice: put a short notice (text) on the main screen and in the bell's tray.
        - clear: close what's open and go back to the orb.
        The user's own recent changes win: set user_asked=true only when they asked for this
        change in the conversation ("show me the dashboard"), never on your own initiative."""
        await note("screen", op=op, target=target, mode=mode)
        if ctx.run_id is not None:
            return "Only the assistant changes the screen; name the result with present_result."
        now = utcnow()
        async with deps.sessionmaker() as db:
            state = await sc.load(db, user_id, lock=True)
            done = ""
            extra: dict = {}

            if op in ("pin", "move"):
                wid = _uuid(target)
                w = wid and await db.scalar(
                    select(Widget).where(Widget.id == wid, Widget.user_id == user_id, on_screen())
                )
                if not w:
                    return "No such widget; call list_widgets for the ids."
                if not user_asked and sc.overridden(state, sc.override_key("move", str(w.id)), now):
                    return left_alone
                sc.audit(db, user_id, op, str(w.id), by="assistant", chat_id=ctx.chat_id)
                at = await sc.reorder(db, deps.redis, user_id, w, 1 if op == "pin" else position)
                return f"Moved {w.title} to place {at} on the dock."

            if op == "focus":
                if not target.strip():
                    return "Say what to open: a file id or name, or a widget id."
                found = await _find_file(db, user_id, target)
                if found is not None:
                    key = sc.override_key("focus", "file", str(found.id))
                    if not user_asked and sc.overridden(state, key, now):
                        return LEFT_ALONE.format(did="closed that file", hours=sc.OVERRIDE_HOURS)
                    state.focus = {
                        "kind": "file",
                        "id": str(found.id),
                        "chat_id": str(ctx.chat_id) if ctx.chat_id else None,
                        "at": now.isoformat(),
                    }
                    extra["file"] = file_payload(found)
                    done = (
                        f"Opening {found.name} on the user's screen (or as a notice they can "
                        "open, if they're typing or away)."
                    )
                else:
                    wid = _uuid(target)
                    w = wid and await db.scalar(
                        select(Widget).where(
                            Widget.id == wid, Widget.user_id == user_id, on_screen()
                        )
                    )
                    if not w:
                        return f"No file or widget called {target}; list_documents or list_widgets."
                    state.focus = {"kind": "widget", "id": str(w.id), "at": now.isoformat()}
                    done = f"Pointed the user to {w.title} on the dock."

            elif op == "mode":
                if mode not in sc.MODES:
                    return "Give mode: orb, list or dashboard."
                if state.mode == mode:
                    return f"The screen already shows the {mode}."
                if not user_asked and sc.overridden(state, "mode", now):
                    return left_alone
                state.mode = mode
                done = f"Switched the main screen to the {mode}."

            elif op == "highlight":
                names = [n.strip().lower() for n in target.split(",") if n.strip()][:MAX_HIGHLIGHT]
                if not names:
                    return "Name who or what to light, comma-separated."
                lit = await _find_entities(db, user_id, names)
                if not lit:
                    return "None of those are in what the user has told you yet."
                extra["recall"] = {"memories": [], "entities": lit}
                done = "Lit " + ", ".join(e["name"] for e in lit) + " in the orb."

            elif op == "notice":
                if not text.strip():
                    return "Give the notice's text."
                sc.audit(db, user_id, op, text[:80], by="assistant", chat_id=ctx.chat_id)
                await notify(
                    db,
                    deps.redis,
                    deps.settings,
                    user_id,
                    NotificationKind.info,
                    text,
                    url=f"/c/{ctx.chat_id}" if ctx.chat_id else "/",
                    chat_id=ctx.chat_id,
                    send_push=False,
                )
                return "The notice is on the user's screen."

            elif op == "clear":
                if state.mode != "orb" and not sc.overridden(state, "mode", now):
                    state.mode = "orb"
                state.focus = None
                done = "Cleared the screen."

            else:
                return f"Unknown op {op}."

            state.updated_at = now
            sc.audit(db, user_id, op, target or (mode or ""), by="assistant", chat_id=ctx.chat_id)
            await db.commit()
        await sc.broadcast(
            deps.redis, user_id, op, state, by="assistant", chat_id=ctx.chat_id, **extra
        )
        return done

    return {"screen": screen}


async def _find_file(db, user_id: uuid.UUID, target: str) -> File | None:
    fid = _uuid(target)
    if fid is not None:
        return await db.scalar(
            select(File).where(
                File.id == fid, File.user_id == user_id, visible_file(File.id, File.folder_id)
            )
        )
    # By name: the newest version of that name.
    return await db.scalar(
        select(File)
        .where(
            File.user_id == user_id,
            File.name == target.strip(),
            visible_file(File.id, File.folder_id),
        )
        .order_by(File.created_at.desc())
        .limit(1)
    )


async def _find_entities(db, user_id: uuid.UUID, names: list[str]) -> list[dict]:
    rows = (
        await db.execute(
            select(Entity.id, Entity.name, Entity.type, Entity.aliases).where(
                Entity.user_id == user_id, Entity.is_self.is_(False)
            )
        )
    ).all()
    wanted = set(names)
    return [
        {"id": str(r.id), "name": r.name, "type": r.type}
        for r in rows
        if r.name.lower() in wanted or wanted & {a.lower() for a in r.aliases or []}
    ]
