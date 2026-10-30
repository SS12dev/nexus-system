"""The assistant's widget tools: "set a timer for 5 minutes" or "wake me at 7" puts a widget on
the main screen that rings by itself (nexus_common/widgets.py), with no team and no model call
when it goes off. Removing one puts it in the user's widget library, where restore finds it
again. save_widget is the widget-builder team's: it checks a custom widget's HTML and manifest
(nexus_common/custom_widgets.py), loads it in headless Chromium on the workbench at a narrow
and a wide card size, and puts it on the dock."""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any, Literal

from sqlalchemy import select

from nexus_common import custom_widgets as cw
from nexus_common.models import User, Widget
from nexus_common.widgets import (
    ENGINES,
    TUNES,
    WidgetError,
    act,
    add,
    announce,
    describe_widget,
    on_screen,
    prune_library,
    put_away,
    restore,
    zone_of,
)
from nexus_orchestrator.workbench import WorkbenchError

log = logging.getLogger(__name__)

# The workbench project the smoke test runs in, one per user.
CHECK_PROJECT = "widget-check"
# Loads the widget alone (window.nexus answers with empty data) in a narrow dock card and in a
# card resized wide, waits for it to settle, and prints what went wrong: uncaught errors,
# console errors, nothing on screen, or content spilling out sideways.
CHECK_SCRIPT = """
const { chromium } = require(require("child_process").execSync("npm root -g").toString().trim()
  + "/@playwright/test");
const fs = require("fs");
(async () => {
  const browser = await chromium.launch();
  const html = fs.readFileSync("widget.html", "utf8");
  const errors = [];
  for (const [width, height] of [[200, SIZE_H], [480, 360]]) {
    const page = await browser.newPage({ viewport: { width, height } });
    const at = " (at " + width + " px wide)";
    page.on("pageerror", (e) => errors.push("error: " + e.message + at));
    page.on("console", (m) => m.type() === "error" && errors.push("console: " + m.text() + at));
    await page.setContent(html, { waitUntil: "load" });
    await page.waitForTimeout(2000);
    const box = await page.evaluate(() => {
      const r = document.body.getBoundingClientRect();
      const root = document.documentElement;
      const h = Math.max(r.height, document.body.scrollHeight);
      return { h, spill: root.scrollWidth - root.clientWidth };
    });
    if (box.h < 10) errors.push("nothing on screen: the page is empty after loading" + at);
    if (box.spill > 4)
      errors.push("content is " + box.spill + " px wider than the card" + at +
        "; let it wrap or shrink (flex-wrap, min-width: 0, %, clamp())");
    await page.close();
  }
  console.log(JSON.stringify([...new Set(errors)].slice(0, 8)));
  await browser.close();
})().catch((e) => { console.log(JSON.stringify(["the check couldn't run: " + e.message])); });
"""
# The frame height the narrow check uses, by size class (apps/web lib/widgets.ts FRAME_HEIGHT).
CHECK_HEIGHT = {"s": 120, "m": 200, "l": 320}

Days = Literal["daily", "weekdays", "weekends"] | list[str] | None
Tune = Literal[TUNES] | None


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value).strip())
    except ValueError:
        return None


def _days(days):
    return ["SA", "SU"] if days == "weekends" else days


def build_widget_tools(ctx: Any, note: Callable) -> dict[str, Callable]:
    deps, user_id = ctx.deps, ctx.user_id

    async def make(kind: str, **opts) -> str:
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            try:
                w = await add(db, user, kind, chat_id=ctx.chat_id, **opts)
            except WidgetError as exc:
                return f"Not added: {exc}"
            await db.commit()
            line = describe_widget(w, zone_of(user))
        await announce(deps.redis, user_id, w)
        return f"Added to the main screen: {line[2:]}"

    async def start_timer(minutes: float, label: str = "", tune: Tune = None) -> str:
        """Start a countdown timer on the user's main screen; it rings (in the app and as a
        phone notification) when it ends, even with the app closed. minutes may be fractional
        (0.5 = 30 seconds), up to 24 hours. label names it ("Pasta"). tune: the sound it rings
        with, only when the user named one (default chime)."""
        await note("start_timer", minutes=minutes, label=label)
        return await make("timer", minutes=minutes, title=label, tune=tune)

    async def set_alarm(time: str, label: str = "", days: Days = None, tune: Tune = None) -> str:
        """Set an alarm at a clock time in the user's time zone, 24-hour "HH:MM". Without days
        it rings once, at the next time the clock shows it; days repeats it: "daily",
        "weekdays", "weekends" or day codes like ["MO", "WE"]. tune: the sound, only when the
        user asked for one: chime, bell, marimba, sunrise (gentle, rising), birdsong, harp,
        retro, digital (classic beeps) or pulse (loud, urgent)."""
        await note("set_alarm", time=time, days=days)
        return await make("alarm", time=time, days=_days(days), title=label, tune=tune)

    async def remind_me(
        text: str,
        when: str = "",
        minutes: float = 0,
        time: str = "",
        days: Days = None,
    ) -> str:
        """Remind the user of something: a notice on screen and on their phone when it's due,
        with no model run. Give exactly one of: when (a local date and time,
        "2026-10-26T21:00"), minutes from now, time ("21:00", the next time the clock shows
        it), or time plus days to repeat ("daily", "weekdays", "weekends", ["MO", "TH"]).
        Use this, not a routine, for a plain reminder; a routine is for work that runs on a
        schedule (a brief, a digest)."""
        await note("remind_me", text=text, when=when or time, days=days)
        return await make(
            "reminder",
            text=text,
            when=when or None,
            minutes=minutes or None,
            time=time or None,
            days=_days(days),
        )

    async def add_widget(
        kind: Literal["stopwatch", "clock", "note", "search"],
        title: str = "",
        text: str = "",
        zones: list[str] | None = None,
        engine: Literal[ENGINES] | None = None,
    ) -> str:
        """Put a stopwatch (starts at once), a clock (zones: IANA names like "Asia/Kolkata",
        "Europe/London", up to 4; the user's own zone when left out), a note (text) or a search
        box (engine: nexus to list results in the card and read them inside Nexus, the
        default; google, duckduckgo or bing to open a new tab; assistant to ask you in chat)
        on the user's main screen. To bring back one the user removed, use
        list_widgets(library=true) and control_widget restore instead."""
        await note("add_widget", kind=kind)
        return await make(kind, title=title, text=text or None, zones=zones, engine=engine)

    async def list_widgets(library: bool = False) -> str:
        """List the widgets on the user's main screen with their ids and when they ring.
        library=true lists the ones the user put away too (their widget library: removed
        widgets and custom widgets they made), which control_widget restore puts back."""
        await note("list_widgets", library=library)
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            query = select(Widget).where(Widget.user_id == user_id)
            if not library:
                query = query.where(on_screen())
            rows = (
                await db.scalars(
                    query.order_by(Widget.removed_at.desc().nulls_first(), Widget.slot)
                )
            ).all()
            tz = zone_of(user)
        empty = (
            "No widgets on the screen or in the library."
            if library
            else "No widgets on the screen."
        )
        return "\n".join(describe_widget(w, tz) for w in rows) or empty

    async def control_widget(
        widget_id: str,
        action: Literal[
            "pause",
            "resume",
            "reset",
            "add_time",
            "snooze",
            "dismiss",
            "remove",
            "undo",
            "restore",
        ],
        minutes: float = 0,
    ) -> str:
        """Change a widget (id from list_widgets): pause, resume or reset a timer or
        stopwatch; add_time (minutes) to a timer; snooze (minutes, default 5) or dismiss one
        that's ringing; pause or resume an alarm; undo a custom widget's last change (back to
        its previous version); remove any widget from the screen (it goes to the user's widget
        library); restore one from the library (list_widgets library=true) back onto the
        screen as it was."""
        await note("control_widget", widget=widget_id, action=action)
        try:
            wid = uuid.UUID(widget_id)
        except ValueError:
            return "That isn't a widget id; call list_widgets."
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            w = await db.scalar(select(Widget).where(Widget.id == wid, Widget.user_id == user_id))
            if w is None:
                return "No such widget."
            if action == "remove":
                if w.removed_at is not None:
                    return f"{w.title} is already in the library."
                put_away(w)
                await db.flush()
                await prune_library(db, user_id)
                await db.commit()
                await announce(deps.redis, user_id, w, deleted=True)
                return f"Removed {w.title} from the screen; it's in the widget library to restore."
            try:
                if action == "restore":
                    await restore(db, user, w)
                elif action == "undo":
                    if w.kind != "custom":
                        return "Not changed: only a custom widget has versions to go back to."
                    await cw.show_version(db, w)
                else:
                    act(w, action, minutes=minutes or None)
            except WidgetError as exc:
                return f"Not changed: {exc}"
            await db.commit()
            line = describe_widget(w, zone_of(user))
        await announce(deps.redis, user_id, w)
        return f"Done: {line[2:]}"

    async def smoke_test(html: str, size: str = "m") -> list[str]:
        """Problems headless Chromium found loading the widget; empty when it ran cleanly or
        the workbench isn't there to try (the static checks still ran)."""
        wb = deps.workbench
        if wb is None:
            return []
        user = str(user_id)
        script = CHECK_SCRIPT.replace("SIZE_H", str(CHECK_HEIGHT.get(size, 200)))
        try:
            await wb.write(user, CHECK_PROJECT, "widget.html", cw.frame_html(html))
            await wb.write(user, CHECK_PROJECT, "check.cjs", script)
            result = await wb.run(user, CHECK_PROJECT, "node check.cjs", 60)
        except WorkbenchError:
            log.warning("Couldn't smoke-test a widget", exc_info=True)
            return []
        lines = [x for x in result.stdout.strip().splitlines() if x.startswith("[")]
        try:
            return json.loads(lines[-1]) if lines else []
        except ValueError:
            return []

    async def save_widget(
        name: str,
        html: str,
        size: Literal["s", "m", "l"] = "m",
        hosts: list[str] | None = None,
        data: list[str] | None = None,
        about: str = "",
        widget_id: str = "",
        actions: list[str] | None = None,
    ) -> str:
        """Save a custom widget and put it on the user's main screen: one self-contained HTML
        file (inline CSS and JS, no outside URLs, under 200 KB) that talks to Nexus only
        through window.nexus (see the widget-builder skill). size: s, m or l (the user can
        resize the card, so the layout must stretch). hosts: the web hosts it fetches from
        with nexus.fetch (https only, e.g. api.open-meteo.com); data: the Nexus data it reads
        with nexus.data (calendar.today, tasks.open, files.recent, memory.search, widgets,
        web.search for web results listed in the card); actions: the app actions its buttons
        take (open: a link in a new tab; read: a page in Nexus's reading view; chat.fork: copy
        the open chat into a second chat window; chat.new: a new chat in a second window;
        ask: send a message to the assistant). The user approves hosts, data (except
        web.search) and ask once; the other actions only need naming. about: one line on
        what it does. widget_id: an existing custom widget's id (from list_widgets,
        library=true for one the user put away) to save a new version of it. The HTML and
        manifest are checked and loaded in a headless browser first; fix what it reports."""
        await note("save_widget", title=name, widget=widget_id or None)
        try:
            manifest = cw.check_manifest(size, hosts, data, about, actions)
        except WidgetError as exc:
            return f"Not saved: {exc}"
        problems = cw.check_html(html) + cw.check_uses(html, manifest)
        if not problems:
            problems = await smoke_test(html, size)
        if problems:
            return "Not saved. Fix these and call save_widget again:\n- " + "\n- ".join(problems)
        async with deps.sessionmaker() as db:
            user = await db.get(User, user_id)
            old = None
            if widget_id:
                wid = _uuid(widget_id)
                old = wid and await db.scalar(
                    select(Widget).where(
                        Widget.id == wid, Widget.user_id == user_id, Widget.kind == "custom"
                    )
                )
                if not old:
                    return "No such custom widget; call list_widgets for the ids."
            try:
                w = await cw.save(
                    db,
                    user,
                    name,
                    html,
                    manifest,
                    widget=old,
                    chat_id=ctx.chat_id,
                    run_id=ctx.run_id,
                )
            except WidgetError as exc:
                return f"Not saved: {exc}"
            await db.commit()
        await announce(deps.redis, user_id, w)
        asks = cw.pending(w.state)
        wait = ""
        if asks["hosts"] or asks["data"] or asks["actions"]:
            wait = (
                " It shows an Allow button for "
                + ", ".join(asks["hosts"] + asks["data"] + asks["actions"])
                + " until the user approves."
            )
        version = w.state["version"]
        return f"Saved {w.title} (id {w.id}, version {version}) on the user's main screen.{wait}"

    return {
        "save_widget": save_widget,
        "start_timer": start_timer,
        "set_alarm": set_alarm,
        "remind_me": remind_me,
        "add_widget": add_widget,
        "list_widgets": list_widgets,
        "control_widget": control_widget,
    }
