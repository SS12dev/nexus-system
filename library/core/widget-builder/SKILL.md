---
name: widget-builder
description: Build a custom widget for the user's main screen (a countdown, tracker, weather card, calendar glance, pixel pet, or buttons that fork the open chat, start a chat, ask the assistant, or search a few chosen sites and read the results) as one self-contained HTML file saved with save_widget. Use for any request for a widget or a change to one.
metadata:
  category: productivity
---

# Building a custom widget

A widget is one small HTML file shown in a card on the user's main screen, beside the orb.
It runs in a locked-down frame: no network, no outside scripts or fonts, no cookies, no
browser storage, its own opaque origin. Everything it needs comes through `window.nexus`.
You save it with `save_widget`, which checks the HTML, checks that the page and its manifest
agree, loads it in headless Chromium at a narrow and a wide card size, and puts it on the dock.

The user can drag the card anywhere, **resize it by its corner** (from about 160 px to 720 px
wide and 64 px to 800 px tall), remove it (it goes to their widget library, to put back
later) and undo your last change. Build for all of that.

Before writing, load `references/examples.md` (skills_read_file): seven complete widgets that
pass every check, including a search box kept to one field's sites. Start from the nearest one.

## 1. Pin down what it is

Answer these before writing a line. If the request leaves one open, pick the sensible default
and say which in your reply; don't stop to ask.

- **What's on the card**, in one sentence, and the one thing the eye should land on first
  (a big number, a short list, a picture, a row of buttons).
- **How it changes**: by the clock (a countdown ticks each second), from data (weather every
  30 minutes, a calendar every 10), from taps (a tracker), or not at all.
- **Size class**: `s` (about 120 px tall: one number or one line), `m` (200 px, the
  default), `l` (320 px: a small chart or list). Width starts at about 220 px (180 px on a
  phone) and grows on the widget board or when resized.
- **Where data comes from**: nothing, a web search (`web.search` in `data`), a public https
  API (`hosts`), the user's Nexus data (`data`), or the user through taps saved in
  `nexus.state`.
- **What its buttons do in the app** (`actions`), if anything: read a page inside Nexus,
  open a link, fork the chat, start a new chat, ask the assistant.
- **Is it a built-in?** Timers, alarms, reminders, stopwatches, world clocks, notes and a
  plain web search box (results in the card, or Google, DuckDuckGo, Bing, "ask the
  assistant") are built-in widgets the assistant adds directly. Build a custom one only when
  it does something they don't: a search kept to one field's sites (cinematography, arXiv
  papers, a framework's docs), a habit tracker, a pet.

## 2. The bridge: window.nexus

Every call returns a promise. Calls the page makes that the manifest doesn't name fail with
"isn't approved" or "isn't in this widget's manifest"; `save_widget` refuses a page that
calls something its manifest leaves out.

| Call | Gives | Needs |
| --- | --- | --- |
| `nexus.state.get()` | The widget's own saved JSON (null at first) | Nothing |
| `nexus.state.set(value)` | Saves JSON up to 64 KB, the same on every device | Nothing |
| `nexus.fetch(url)` | `{status, content_type, text}` from a server-side GET (cached 1 minute, 256 KB max) | The host in `hosts`, https only |
| `nexus.data(source, options)` | Read-only Nexus data, as JSON | The source in `data` |
| `nexus.notify(title, body)` | A notice on the main screen (10 an hour at most) | Nothing |
| `nexus.resize(height)` | Asks for a frame height in px, kept within half to 1.5x its size class (ignored once the user resized the card) | Nothing |
| `nexus.context()` | `{chat: {id, title} or null, theme, inApp}`: the chat open beside it | Nothing |
| `nexus.on("context", fn)` | Calls `fn(context)` whenever the open chat or theme changes; returns an unsubscribe function | Nothing |
| `nexus.read(url)` | Opens a web page as text in Nexus's reading view, with an "Open in browser" link | `read` in `actions` |
| `nexus.open(url)` | Opens an https link in a new tab | `open` in `actions` |
| `nexus.chat.fork()` | Copies the open chat into a new chat and opens it in a second chat window beside it; resolves `{id, title}` | `chat.fork` in `actions` |
| `nexus.chat.new(text)` | A new chat in a second window, asking `text` there first if given; resolves `{id, title}` | `chat.new` in `actions` |
| `nexus.ask(text)` | Sends `text` to the assistant in the open chat (a new chat from the home screen) | `ask` in `actions` |

Data sources: `web.search` with `{query, sites}` (up to 10 results
`[{title, url, site, snippet}]` from the web search Nexus's agents use, cached 10 minutes;
`sites` is an optional list of up to 8 domains to keep it to), `calendar.today` (today's events: title, start, end, location),
`tasks.open` (Google Tasks: title, due), `files.recent` (newest file names),
`memory.search` with `{query}` (matching memories as text), `widgets` (the user's other
widgets on screen: kind, title, status, rings_at).

**What needs the user's Allow.** Only what reaches their private data or speaks for them:
hosts for `nexus.fetch`, the personal data sources (`calendar.today`, `tasks.open`,
`files.recent`, `memory.search`, `widgets`) and `ask`. The user allows those once, with an
Allow button on the card that names them in plain words; until then those calls fail, so
show a quiet "Waiting for access" line, never an error box. `web.search`, `read`, `open`,
`chat.fork` and `chat.new` only need naming in the manifest, no Allow. Ask only for what the
widget needs.

**App actions need a tap.** `read`, `open`, `chat.fork`, `chat.new` and `ask` only work straight
after the user taps or clicks inside the widget. Call them directly in the click or submit
handler, never from a timer, on load, or after a long `await`. `chat.fork` fails with
"Open a chat first" when no chat is open: use `nexus.context()` / `nexus.on("context")` to
disable the button and say why.

Opened on its own (the save check, or the file in a browser) `window.nexus` answers with
empty data (`[]`, `""`, `null`, a context with no chat), so the page must load cleanly with
nothing.

## 3. Write the file

**Shape.** One file: `<!doctype html><html><head><meta charset="utf-8">`, one `<style>`, the
markup, one `<script>` at the end. Inline everything: icons as inline SVG, images as
`data:` URLs. Under 200 KB; most widgets are 2 to 6 KB.

**Never** use `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `localStorage`,
`sessionStorage`, `indexedDB`, `document.cookie`, `<script src>`, `<link href>` to a URL, CSS
`@import`, `url(https://...)`, `<img src="https://...">`, `<iframe>`, `<object>` or `<embed>`.
The frame blocks them and `save_widget` refuses them. Use `nexus.fetch` and `nexus.state`.
`nexus.fetch` returns text, so a picture from the web can't be shown; draw with SVG or canvas.

**Look like the app.**
- Transparent `body` background, `margin: 0`, system font
  (`font: 13px/1.4 system-ui, sans-serif`). Text `#e2e8f0`, muted `#94a3b8`, accent
  `#38bdf8`, borders `#334155`, danger `#fb7185`, good `#34d399`.
- The page gets the card's theme as `<html data-theme="dark|light">` (dark over the orb,
  the app's theme elsewhere). Style light with `[data-theme="light"]` selectors (text
  `#0f172a`, muted `#475569`, borders `#cbd5e1`), never `prefers-color-scheme`.
- No title inside the widget unless it adds something: the card already shows the widget's
  name above the frame. Big numbers in `font-variant-numeric: tabular-nums`.

**Fill any size (it will be resized).**
- Lay out with flex or grid and relative units; never fixed pixel widths on containers.
  Text that can be long gets `min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap`, or wraps. The save check fails a page wider than the card at 200 px.
- Make the page a size container and scale the hero element with it:
  `body{container-type:inline-size}` then `font-size: clamp(20px, 14cqw, 56px)`. Show more
  when there's room with `@container (min-width: 320px) { ... }`, hide extras below 220 px.
- If it fills the height (a chart, a canvas, a pet), set `html,body{height:100%}` and size
  from `innerWidth` / `innerHeight` with a `ResizeObserver` on `document.body`. Redraw a
  canvas at the new size; for pixel art keep a whole-number scale.

**Behave.**
- Refresh on a sensible timer, never faster than the data changes, and skip work while
  `document.hidden`. Stop timers that have nothing left to do.
- Handle every failed call with a quiet fallback line ("Couldn't load the weather",
  "Waiting for access"); keep the last good data on screen when a refresh fails.
- Animation: CSS keyframes, or `requestAnimationFrame` on a `<canvas>` throttled to what it
  needs (10 to 15 fps for pixel art, `image-rendering: pixelated`). Gentle, slow loops; taps
  can make it react. Respect `@media (prefers-reduced-motion: reduce)` by calming it down.
- Save what the user creates (ticks, counts, choices, a list) with `nexus.state.set` as it
  changes, load it with `nexus.state.get()` on start, and draw a sensible empty state before
  it arrives. Keep it small: trim history you don't show.
- Put anything the user typed or that came from an API into the page with `textContent`, not
  `innerHTML`.
- Accessible: real `<button>`s with labels (`aria-label` for icon-only ones), visible
  `:focus-visible` outlines, `aria-pressed` on toggles, `aria-live="polite"` on a value that
  changes by itself.

**Parsing an API.** Read the API's documentation (web search) before using it; pick one that
needs no key (open-meteo, ll.thespacedevs.com, api.coingecko.com, en.wikipedia.org REST,
hacker-news.firebaseio.com). `nexus.fetch` returns text: `JSON.parse(r.text)` inside the
`try`, check `r.status === 200`, and read fields defensively (`d?.current?.temperature_2m`).
The fetch runs on the server, so CORS doesn't matter, but redirects must stay on approved
hosts.

## 4. Save, check, then report

Call `save_widget(name, html, size, hosts, data, actions, about)`:

- `name`: the card's title, two to four words ("Artemis countdown").
- `about`: one line on what it does; it shows in the user's widget library.
- `hosts`, `data`, `actions`: exactly what the page uses, no more.

If it says "Not saved", fix **every** point it lists and call it again. Typical ones:
"calls nexus.fetch but hosts is empty", "fetches from X, which isn't in hosts",
"calls nexus.chat.fork, which isn't in actions", "reads nexus.data('web.search'), which isn't in
data", "content is 40 px wider than the card (at
200 px wide)", "error: ... is not defined". After three failed tries, simplify the widget
rather than patching around the same error.

To change an existing widget, pass its `widget_id` (from `list_widgets`; with
`library=true` for one the user removed, which comes back onto the screen). That saves a new
version; the user can undo back to the previous one. Change only what was asked and keep the
rest of the page as it was.

Reply in a sentence or two: what the widget shows, anything waiting for the user's Allow
(and what Allow lets it do), and how to change it ("drag its corner to make it bigger, or
ask me to change the colours or undo the last change").
