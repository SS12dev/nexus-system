---
name: widget-builder
description: Build a custom widget for the user's main screen - a countdown, a habit tracker, a ticker, a weather card, a calendar glance, a mini dashboard - as one self-contained HTML file saved with save_widget. Use whenever the user asks for a widget, a live card on their screen, or a change to one.
metadata:
  category: productivity
---

# Building a custom widget

A widget is one small HTML file shown in a card on the user's main screen, next to the orb.
It runs in a locked-down frame: no network, no outside scripts or fonts, no cookies, no
browser storage. Everything it needs comes through `window.nexus`. You save it with the
`save_widget` tool, which checks it, loads it in headless Chromium and puts it on the dock.

## 1. Pin down what it shows

- What's on the card, and how often it changes (a countdown ticks every second; a weather
  card refreshes every 15 to 30 minutes; a habit tracker changes when the user taps).
- Size: `s` (about 140 px tall, one number or line), `m` (220 px, the default), `l`
  (340 px, a small chart or list). The width is a dock card: about 220 px on a laptop, 180 px
  on a phone; on the widget board it grows to fill a grid cell, so design for 180 to 320 px.
- Where data comes from: nothing (a countdown, a note-like tracker), a public API (weather,
  prices, launches), or the user's own Nexus data.

## 2. The bridge: window.nexus

Every call returns a promise.

| Call | Gives | Needs |
| --- | --- | --- |
| `nexus.state.get()` | The widget's own saved JSON (null at first) | Nothing |
| `nexus.state.set(value)` | Saves JSON up to 64 KB, the same on every device | Nothing |
| `nexus.fetch(url)` | `{status, content_type, text}` from a server-side GET (cached 1 minute) | The host in `hosts`, https only |
| `nexus.data(source, options)` | Read-only Nexus data, as JSON | The source in `data` |
| `nexus.notify(title, body)` | A notice on the main screen (10 an hour at most) | Nothing |
| `nexus.resize(height)` | Asks for a height in px; the card keeps it within its size | Nothing |

Data sources: `calendar.today` (today's events: title, start, end, location),
`tasks.open` (Google Tasks: title, due), `files.recent` (newest file names),
`memory.search` with `{query}` (matching memories as text), `widgets` (the user's other
widgets: kind, title, status, rings_at).

The user approves each host and data source once, with an Allow button on the card. Until
then the call fails with "isn't approved"; show a short "Waiting for access" line instead of
an error. Ask only for what the widget needs.

## 3. Write the file

- One file: `<!doctype html><html><head><meta charset="utf-8">`, a `<style>`, the markup and
  a `<script>` at the end. Inline everything. Icons as inline SVG; images as data: URLs.
- Never `fetch`, `XMLHttpRequest`, `WebSocket`, `localStorage`, `document.cookie`,
  `<script src>`, `<link href>` to a URL, CSS `@import` or `url(https://...)`: the frame
  blocks them and save_widget refuses them. Use `nexus.fetch` and `nexus.state`.
- Look like the app: transparent `body` background, `margin: 0`, system font
  (`font: 13px/1.4 system-ui, sans-serif`), text `#e2e8f0`, muted `#94a3b8`, accent
  `#38bdf8`. The page gets the card's theme as `<html data-theme="dark|light">` (dark over
  the orb, the app's theme elsewhere): style light with `[data-theme="light"]` selectors
  (text `#0f172a`, muted `#475569`), not `prefers-color-scheme`. Big numbers in
  `font-variant-numeric: tabular-nums`.
- Keep it calm: refresh on a sensible timer, never faster than the data changes, and stop
  work when nothing changes. Handle every failed call with a quiet fallback.
- Animation (a pixel pet, a ticker, a spinner): CSS keyframes, or `requestAnimationFrame`
  on a `<canvas>` (`image-rendering: pixelated` for pixel art, drawn small and scaled up).
  Keep it gentle and loop slowly; skip frames while `document.hidden` so it costs nothing
  in a background tab. Taps on the card can make it react (a hop, a purr).
- Save state the user creates (ticks, counts, choices) with `nexus.state.set` and load it on
  start, so it survives reloads and shows on the phone too.

A minimal countdown:

```html
<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font:13px/1.4 system-ui,sans-serif;color:#e2e8f0;background:transparent}
.n{font-size:28px;font-variant-numeric:tabular-nums}.m{color:#94a3b8}
[data-theme="light"] body{color:#0f172a}[data-theme="light"] .m{color:#475569}
</style></head><body><div class="m">Artemis III launch</div><div class="n" id="n"></div>
<script>
const at = Date.parse("2027-09-01T12:00:00Z");
function tick(){const s=Math.max(0,(at-Date.now())/1000|0);
n.textContent=`${s/86400|0}d ${(s%86400)/3600|0}h ${(s%3600)/60|0}m ${s%60}s`;}
tick(); setInterval(tick, 1000);
</script></body></html>
```

## 4. Save, then report

Call `save_widget(name, html, size, hosts, data, about)`. If it says "Not saved", fix every
point it lists and call it again. To change an existing widget, pass its `widget_id` (from
`list_widgets`): that saves a new version, and the user can undo back to the previous one.

Reply with what the widget shows, anything waiting for the user's Allow, and how to change
it ("ask me to make it bigger, change the colours, or undo the last change").
