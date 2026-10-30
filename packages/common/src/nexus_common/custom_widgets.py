"""Custom widgets: small HTML apps on the main screen, written by a widget-builder team ("make me
a widget that counts down to the Artemis launch").

A widget is one self-contained HTML file plus a manifest. It's served by the API with a
Content-Security-Policy whose sandbox directive gives it an opaque origin (no cookies, no
session, no reading the app), and shown in an <iframe sandbox="allow-scripts">. Its CSP allows
inline script and style and nothing else: no network, no outside scripts, no form posts. The
only way out is the postMessage bridge (the injected window.nexus), which the app forwards to
POST /api/widgets/{id}/bridge and which is checked here against the manifest:

    nexus.state.get() / .set(value)   the widget's own JSON state (64 KB)
    nexus.fetch(url)                  a server-side GET, only to hosts the person approved
    nexus.data(source, options)       read-only Nexus data the person approved (DATA_SOURCES)
    nexus.notify(title, body)         a notice on the main screen, rate-limited
    nexus.resize(height)              the app sizes the frame, within its size class
    nexus.context() / nexus.on(...)   the chat that's open, the theme and the card's size

and the app actions, which the app itself carries out (ACTIONS), each only when the manifest
names it, the person allowed it, and the frame was just tapped or clicked:

    nexus.open(url)                   an https link in a new tab
    nexus.chat.fork()                 a copy of the open chat, in a second chat window
    nexus.chat.new(text)              a new chat in a second window, optionally asking text
    nexus.ask(text)                   send text to the assistant in the open chat

Each save is a new version (CustomWidget row, same root_id), so a change can be rolled back.
The manifest's hosts, data and actions are approved once per widget by the person; a new
version that asks for more waits for approval of the new ones only.
"""

import asyncio
import ipaddress
import json
import re
import socket
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common import google
from nexus_common.models import CustomWidget, File, Memory, User, Widget, utcnow
from nexus_common.widgets import (
    TITLE_CHARS,
    WidgetError,
    check_room,
    next_slot,
    on_screen,
    restore,
    zone_of,
)

MAX_HTML = 200_000
MAX_STATE = 64_000
MAX_HOSTS = 5
SIZES = ("s", "m", "l")
DATA_SOURCES = {
    "calendar.today": "today's events on your Google Calendar",
    "tasks.open": "your open Google Tasks",
    "files.recent": "the names of your newest files",
    "memory.search": "searching what Nexus remembers about you",
    "widgets": "your other widgets and when they ring",
}
# What a widget can ask the app to do, in the words its Allow button uses. The app carries these
# out (apps/web lib/widgets.ts APP_ACTIONS has the same names), only after a tap in the frame.
ACTIONS = {
    "open": "open web links in a new tab",
    "chat.fork": "copy the chat you have open into a second chat window",
    "chat.new": "start a new chat in a second window",
    "ask": "send messages to your assistant for you",
}
FETCH_MAX_BYTES = 256_000
FETCH_CACHE_S = 60
FETCH_REDIRECTS = 3
NOTIFY_PER_HOUR = 10
HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

# Served with every widget: no network, no outside code, no forms, no navigating the app.
FRAME_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; "
    "connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
)

# window.nexus inside the frame. Opened on its own (no parent, e.g. the builder's smoke test)
# it answers with empty data, so a widget can be tried outside the app. The app also sends it
# {nexus: 1, event, data} messages (the "context" event: the open chat, theme and card size),
# which nexus.on(event, fn) listens for.
SHIM = (
    "<script>(function(){var seq=0,waiting={},subs={},alone=parent===window;"
    'var ctx={chat:null,theme:document.documentElement.dataset.theme||"dark",'
    "width:innerWidth,height:innerHeight,inApp:!alone};function stub(name){"
    'return name==="data"?[]:name==="fetch"?{'
    'status:200,content_type:"text/plain",text:""}:name==="context"?ctx:null;}'
    "function call(name,args){"
    "return new Promise(function(res,rej){if(alone){res(stub(name));return;}var id=++seq;"
    "waiting[id]={res:res,rej:rej};parent.postMessage({"
    'nexus:1,id:id,call:name,args:args||{}},"*");setTimeout(function(){if(waiting[id]){'
    'delete waiting[id];rej(new Error("Nexus didn\'t answer "+name));}},20000);});}'
    'window.addEventListener("message",function(e){if(e.source!==parent)return;'
    "var m=e.data;if(!m||m.nexus!==1)return;if(m.event){"
    'if(m.event==="context")ctx=m.data;(subs[m.event]||[]).forEach(function(f){'
    "try{f(m.data);}catch(err){setTimeout(function(){throw err;});}});return;}"
    "if(!waiting[m.id])return;var w=waiting[m.id];"
    "delete waiting[m.id];if(m.ok)w.res(m.result);"
    'else w.rej(new Error(m.error||"Nexus refused"));});window.nexus={state:{'
    'get:function(){return call("state.get");},set:function(v){return call("state.set",{'
    'value:v});}},fetch:function(u){return call("fetch",{url:u});},data:function(s,o){'
    'return call("data",{source:s,options:o||{}});},notify:function(t,b){'
    'return call("notify",{title:t,body:b||""});},resize:function(h){'
    'return call("resize",{height:h});},context:function(){return call("context");},'
    "on:function(n,f){(subs[n]=subs[n]||[]).push(f);return function(){"
    "subs[n]=(subs[n]||[]).filter(function(g){return g!==f;});};},"
    'open:function(u){return call("open",{url:u});},'
    'ask:function(t){return call("ask",{text:t});},chat:{'
    'fork:function(){return call("chat.fork");},'
    'new:function(t){return call("chat.new",{text:t||""});}}};})();</script>'
)

_CHECKS = [
    (re.compile(r"<script\b[^>]*\bsrc\s*=", re.I), "loads a script from a URL; inline it instead"),
    (
        re.compile(r"<link\b[^>]*\bhref\s*=\s*[\"']?(?:https?:)?//", re.I),
        "links a stylesheet or font from the internet; inline it",
    ),
    (re.compile(r"@import\b", re.I), "uses CSS @import; inline the styles"),
    (
        re.compile(r"url\(\s*[\"']?(?:https?:)?//", re.I),
        "points CSS url() at the internet; use a data: URL",
    ),
    (
        re.compile(r"<(?:img|video|audio|source)\b[^>]*\bsrc\s*=\s*[\"']?(?:https?:)?//", re.I),
        "loads media from the internet, which the frame blocks; use data: URLs or nexus.fetch",
    ),
    (re.compile(r"<(?:iframe|object|embed)\b", re.I), "embeds another frame or plugin"),
    (
        re.compile(r"(?<![.\w$])(?:fetch\s*\(|XMLHttpRequest|WebSocket\s*\(|EventSource\s*\()"),
        "uses the network directly, which the frame blocks; use nexus.fetch(url)",
    ),
    (
        re.compile(r"\b(?:localStorage|sessionStorage|indexedDB|document\.cookie)\b"),
        "uses browser storage, which the frame doesn't have; use nexus.state",
    ),
]


class CustomWidgetError(WidgetError):
    """A widget that won't be saved, or a bridge call that isn't allowed; safe to show."""


def check_manifest(
    size: str = "m",
    hosts: list[str] | None = None,
    data: list[str] | None = None,
    about="",
    actions: list[str] | None = None,
) -> dict:
    """The manifest as stored: a size class, the web hosts it may fetch from, the Nexus data it
    may read and the app actions it may take (each approved once by the person), and a line
    about what it does."""
    if size not in SIZES:
        raise CustomWidgetError("size is s, m or l")
    clean_hosts = []
    for h in hosts or []:
        h = h.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
        if not HOST.match(h):
            raise CustomWidgetError(f"{h!r} isn't a host name like api.example.com")
        if h not in clean_hosts:
            clean_hosts.append(h)
    if len(clean_hosts) > MAX_HOSTS:
        raise CustomWidgetError(f"A widget can fetch from up to {MAX_HOSTS} hosts")
    unknown = [d for d in data or [] if d not in DATA_SOURCES]
    if unknown:
        raise CustomWidgetError(
            f"No data source {', '.join(unknown)}; there are {', '.join(DATA_SOURCES)}"
        )
    strange = [a for a in actions or [] if a not in ACTIONS]
    if strange:
        raise CustomWidgetError(f"No action {', '.join(strange)}; there are {', '.join(ACTIONS)}")
    return {
        "size": size,
        "hosts": clean_hosts,
        "data": list(dict.fromkeys(data or [])),
        "actions": list(dict.fromkeys(actions or [])),
        "about": (about or "").strip()[:300],
    }


def check_html(html: str) -> list[str]:
    """What's wrong with a widget's HTML, in words the builder can act on; empty when fine."""
    problems = []
    size = len(html.encode())
    if size > MAX_HTML:
        problems.append(f"It's {size // 1000} KB; the limit is {MAX_HTML // 1000} KB")
    if not re.search(r"<(?:body|div|main|canvas|svg|section)\b", html, re.I):
        problems.append("It has no visible content (no body, div, canvas or svg)")
    for pattern, why in _CHECKS:
        if pattern.search(html):
            problems.append(f"It {why}")
    return problems


# What the page calls on window.nexus, to check against what the manifest asks for.
_USES = [
    (re.compile(r"\bnexus\.open\s*\("), "open"),
    (re.compile(r"\bnexus\.chat\.fork\s*\("), "chat.fork"),
    (re.compile(r"\bnexus\.chat\.new\s*\("), "chat.new"),
    (re.compile(r"\bnexus\.ask\s*\("), "ask"),
]
_FETCH_URL = re.compile(r"\bnexus\.fetch\s*\(\s*[\"'`]https?://([^/\"'`?#:]+)", re.I)
_DATA_NAME = re.compile(r"\bnexus\.data\s*\(\s*[\"'`]([\w.]+)[\"'`]")


def check_uses(html: str, manifest: dict) -> list[str]:
    """Where the page and its manifest disagree: a call to something the manifest doesn't ask
    for fails in the app with "isn't approved", so it's caught before saving."""
    problems = []
    hosts = set(manifest.get("hosts", []))
    if re.search(r"\bnexus\.fetch\s*\(", html) and not hosts:
        problems.append("It calls nexus.fetch but hosts is empty; list the hosts it fetches from")
    for host in sorted({h.lower() for h in _FETCH_URL.findall(html)} - hosts):
        problems.append(f"It fetches from {host}, which isn't in hosts; add it")
    for source in sorted(set(_DATA_NAME.findall(html)) - set(manifest.get("data", []))):
        problems.append(f"It reads nexus.data({source!r}), which isn't in data; add it")
    for pattern, action in _USES:
        if pattern.search(html) and action not in manifest.get("actions", []):
            problems.append(f"It calls nexus.{action}, which isn't in actions; add {action!r}")
    return problems


THEMES = ("dark", "light")


def frame_html(html: str, theme: str = "dark") -> str:
    """The widget's page as served: the app's theme (as <html data-theme> and a matching
    color-scheme, which keeps the frame see-through) and window.nexus first, then the widget."""
    theme = theme if theme in THEMES else "dark"
    lead = (
        f'<meta name="color-scheme" content="{theme}">'
        f'<script>document.documentElement.dataset.theme="{theme}"</script>' + SHIM
    )
    head = re.search(r"<head\b[^>]*>", html, re.I)
    if head:
        return html[: head.end()] + lead + html[head.end() :]
    return lead + html


def pending(state: dict) -> dict:
    """What the widget's manifest asks for that the person hasn't approved yet."""
    manifest = state.get("manifest") or {}
    approved = state.get("approved") or {}
    return {
        key: [x for x in manifest.get(key, []) if x not in approved.get(key, [])]
        for key in ("hosts", "data", "actions")
    }


def _state_for(cw: CustomWidget, versions: int, old: dict | None = None) -> dict:
    old = old or {}
    return {
        "custom_id": str(cw.id),
        "root_id": str(cw.root_id),
        "version": cw.version,
        "versions": versions,
        "manifest": cw.manifest,
        "approved": {"hosts": [], "data": [], "actions": [], **(old.get("approved") or {})},
        "data": old.get("data"),
    }


async def save(
    db: AsyncSession,
    user: User,
    name: str,
    html: str,
    manifest: dict,
    *,
    widget: Widget | None = None,
    chat_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> Widget:
    """Save a new widget, or a new version of `widget`, and show it on the dock (a widget in
    the library comes back onto the screen with its new version). Doesn't commit. The HTML must
    already pass check_html."""
    now = utcnow()
    name = (name or "").strip()[:TITLE_CHARS] or "Widget"
    if widget is not None and widget.removed_at is not None:
        await restore(db, user, widget, now)
    if widget is None:
        await check_room(db, user.id)
        cid = uuid.uuid4()
        cw = CustomWidget(id=cid, root_id=cid, version=1)
    else:
        root = uuid.UUID(widget.state["root_id"])
        last = await db.scalar(
            select(func.max(CustomWidget.version)).where(CustomWidget.root_id == root)
        )
        cw = CustomWidget(root_id=root, version=(last or 0) + 1)
    cw.user_id = user.id
    cw.name = name
    cw.html = html
    cw.manifest = manifest
    cw.chat_id = chat_id
    cw.run_id = run_id
    cw.created_at = now
    db.add(cw)
    await db.flush()
    if widget is None:
        widget = Widget(
            user_id=user.id,
            kind="custom",
            title=name,
            state=_state_for(cw, 1),
            slot=await next_slot(db, user.id),
            chat_id=chat_id,
            created_at=now,
            updated_at=now,
        )
        db.add(widget)
    else:
        widget.title = name
        widget.state = _state_for(cw, cw.version, widget.state)
        widget.updated_at = now
    await db.flush()
    return widget


async def versions(db: AsyncSession, widget: Widget) -> list[CustomWidget]:
    root = uuid.UUID(widget.state["root_id"])
    return list(
        (
            await db.scalars(
                select(CustomWidget)
                .where(CustomWidget.root_id == root, CustomWidget.user_id == widget.user_id)
                .order_by(CustomWidget.version.desc())
            )
        ).all()
    )


async def show_version(db: AsyncSession, widget: Widget, version: int | None = None) -> int:
    """Show another version (by default the one before the current): undo a change. Doesn't
    commit; returns the version now shown."""
    rows = await versions(db, widget)
    current = widget.state.get("version")
    if version is None:
        older = [r for r in rows if r.version < current]
        if not older:
            raise CustomWidgetError("This is the first version; there's nothing to go back to")
        pick = older[0]
    else:
        pick = next((r for r in rows if r.version == version), None)
        if pick is None:
            raise CustomWidgetError(f"There's no version {version}")
    widget.state = _state_for(pick, len(rows), widget.state)
    widget.title = pick.name
    widget.updated_at = utcnow()
    return pick.version


async def current(db: AsyncSession, widget: Widget) -> CustomWidget:
    cw = await db.get(CustomWidget, uuid.UUID(widget.state["custom_id"]))
    if cw is None or cw.user_id != widget.user_id:
        raise CustomWidgetError("This widget's page is gone")
    return cw


def approve(
    widget: Widget, hosts: list[str], data: list[str], actions: list[str] | None = None
) -> None:
    """The person allowed some of what the manifest asks for. Only what it asks for counts."""
    s = dict(widget.state)
    manifest = s.get("manifest") or {}
    approved = s.get("approved") or {}
    given = {"hosts": hosts, "data": data, "actions": actions or []}
    s["approved"] = {
        key: sorted(
            {*approved.get(key, []), *(x for x in given[key] if x in manifest.get(key, []))}
        )
        for key in given
    }
    widget.state = s


def set_state(widget: Widget, value) -> None:
    if len(json.dumps(value)) > MAX_STATE:
        raise CustomWidgetError(f"A widget's state is limited to {MAX_STATE // 1000} KB")
    widget.state = {**widget.state, "data": value}


# --- The bridge's outside calls -----------------------------------------------------------------


async def _public(host: str, port: int) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port)
    except socket.gaierror as exc:
        raise CustomWidgetError(f"Can't resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise CustomWidgetError(f"{host} is not a public address")


async def fetch(
    widget: Widget, url: str, redis: Redis, http: httpx.AsyncClient | None = None
) -> dict:
    """A GET for the widget, from the server, to a host the person approved (redirects too).
    Cached for a minute, so a widget that refreshes often doesn't hammer the site."""
    allowed = set((widget.state.get("approved") or {}).get("hosts", []))
    key = f"nexus:widget-fetch:{widget.id}:{url}"
    if cached := await redis.get(key):
        return json.loads(cached)
    client = http or httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5))
    target = url
    try:
        for _ in range(FETCH_REDIRECTS + 1):
            parts = urlsplit(target)
            host = (parts.hostname or "").lower()
            if parts.scheme != "https" or host not in allowed:
                raise CustomWidgetError(
                    f"{host or url} isn't approved for this widget"
                    if parts.scheme == "https"
                    else "Only https URLs can be fetched"
                )
            await _public(host, parts.port or 443)
            async with client.stream(
                "GET", target, headers={"User-Agent": "Nexus widget"}, follow_redirects=False
            ) as resp:
                if resp.is_redirect:
                    target = urljoin(target, resp.headers.get("location", ""))
                    continue
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > FETCH_MAX_BYTES:
                        raise CustomWidgetError(f"{host} sent more than 256 KB")
                result = {
                    "status": resp.status_code,
                    "content_type": resp.headers.get("content-type", ""),
                    "text": body.decode(resp.encoding or "utf-8", "replace"),
                }
                break
        else:
            raise CustomWidgetError("Too many redirects")
    except httpx.HTTPError as exc:
        raise CustomWidgetError(f"Couldn't reach {urlsplit(target).hostname}") from exc
    finally:
        if http is None:
            await client.aclose()
    await redis.set(key, json.dumps(result), ex=FETCH_CACHE_S)
    return result


async def read_data(db: AsyncSession, settings, user: User, widget: Widget, source: str, options):
    """Read-only Nexus data for the widget, if the person approved that source for it."""
    if source not in DATA_SOURCES:
        raise CustomWidgetError(f"No data source {source}")
    if source not in (widget.state.get("approved") or {}).get("data", []):
        raise CustomWidgetError(f"{source} isn't approved for this widget")
    options = options if isinstance(options, dict) else {}
    if source == "files.recent":
        rows = await db.scalars(
            select(File).where(File.user_id == user.id).order_by(File.created_at.desc()).limit(10)
        )
        return [
            {"name": f.name, "type": f.content_type, "created_at": f.created_at.isoformat()}
            for f in rows
        ]
    if source == "memory.search":
        query = str(options.get("query", "")).strip()[:100]
        if not query:
            raise CustomWidgetError("memory.search needs options.query")
        rows = await db.scalars(
            select(Memory)
            .where(Memory.user_id == user.id, Memory.content.ilike(f"%{query}%"))
            .order_by(Memory.created_at.desc())
            .limit(10)
        )
        return [m.content for m in rows]
    if source == "widgets":
        rows = await db.scalars(
            select(Widget)
            .where(Widget.user_id == user.id, Widget.kind != "custom", on_screen())
            .order_by(Widget.slot)
        )
        return [
            {
                "kind": w.kind,
                "title": w.title,
                "status": (w.state or {}).get("status"),
                "rings_at": w.fires_at.astimezone(UTC).isoformat() if w.fires_at else None,
            }
            for w in rows
        ]
    try:
        return await _google(db, settings, user, source)
    except httpx.HTTPError as exc:
        raise CustomWidgetError(f"Google didn't answer for {source}") from exc


async def _google(db: AsyncSession, settings, user: User, source: str):
    service = "calendar" if source == "calendar.today" else "tasks"
    try:
        token = await google.access_token(db, settings, user.id, service)
    except google.GoogleNotConnected as exc:
        raise CustomWidgetError(f"Google {service.title()} isn't connected") from exc
    await db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15, headers=headers) as http:
        if service == "calendar":
            tz = ZoneInfo(zone_of(user))
            start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            resp = await http.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                params={
                    "timeMin": start.isoformat(),
                    "timeMax": (start + timedelta(days=1)).isoformat(),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 25,
                },
            )
            resp.raise_for_status()
            return [
                {
                    "title": e.get("summary", ""),
                    "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
                    "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
                    "location": e.get("location", ""),
                }
                for e in resp.json().get("items", [])
            ]
        resp = await http.get(
            "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks",
            params={"showCompleted": "false", "maxResults": 50},
        )
        resp.raise_for_status()
        return [
            {"title": t.get("title", ""), "due": t.get("due")} for t in resp.json().get("items", [])
        ]


async def may_notify(redis: Redis, widget: Widget) -> bool:
    key = f"nexus:widget-notify:{widget.id}:{utcnow():%Y%m%d%H}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)
    return count <= NOTIFY_PER_HOUR
