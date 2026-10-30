# Widget examples

Complete widgets that pass `save_widget`'s checks (static checks, manifest agreement, and
the headless Chromium load at 200 px and 480 px wide). Start from the nearest one and change
it; keep the parts that make it robust (fallback text, refresh timers, theme selectors).

## Countdown (size s, nothing to approve)

Grows with the card: `container-type: inline-size` on body and `clamp(…, 14cqw, …)` for the number.

```html
<!doctype html><html><head><meta charset="utf-8"><style>
html,body{height:100%}
body{margin:0;font:13px/1.4 system-ui,sans-serif;color:#e2e8f0;background:transparent;
  display:flex;flex-direction:column;justify-content:center;container-type:inline-size}
.m{color:#94a3b8}
.n{font-size:clamp(20px,14cqw,56px);font-weight:600;font-variant-numeric:tabular-nums;line-height:1.1}
.u{display:flex;gap:4cqw;flex-wrap:wrap}.u span{display:flex;flex-direction:column}
.u small{font-size:11px;color:#94a3b8}
[data-theme="light"] body{color:#0f172a}[data-theme="light"] .m,[data-theme="light"] .u small{color:#475569}
</style></head><body>
<div class="m">Artemis III launch</div>
<div class="u n" id="n" aria-live="polite"></div>
<script>
const at = Date.parse("2027-09-01T12:00:00Z");
const n = document.getElementById("n");
function part(v, label) { return `<span>${v}<small>${label}</small></span>`; }
function tick() {
  const s = Math.max(0, Math.floor((at - Date.now()) / 1000));
  if (s === 0) { n.textContent = "Lift-off!"; return; }
  n.innerHTML = part(Math.floor(s / 86400), "days") + part(Math.floor(s % 86400 / 3600), "hours") +
    part(Math.floor(s % 3600 / 60), "min") + part(s % 60, "sec");
}
tick(); setInterval(tick, 1000);
</script></body></html>
```

## Habit tracker (size m, state)

Saves taps with `nexus.state`, so it's the same on the phone; builds the list with textContent, never innerHTML of user text.

```html
<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font:13px/1.4 system-ui,sans-serif;color:#e2e8f0;background:transparent}
.m{color:#94a3b8;font-size:11px}
.row{display:flex;align-items:center;gap:6px;margin:4px 0}
.name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.days{display:flex;gap:3px}
button.d{width:16px;height:16px;border-radius:4px;border:1px solid #334155;background:transparent;padding:0;cursor:pointer}
button.d[aria-pressed="true"]{background:#38bdf8;border-color:#38bdf8}
button.d:focus-visible{outline:2px solid #38bdf8;outline-offset:1px}
[data-theme="light"] body{color:#0f172a}[data-theme="light"] .m{color:#475569}
[data-theme="light"] button.d{border-color:#cbd5e1}
</style></head><body>
<div class="m" id="hint">Tap a day when you've done it</div>
<div id="list"></div>
<script>
const HABITS = ["Read 20 pages", "Stretch", "No sugar"];
const list = document.getElementById("list");
// The last 7 days as YYYY-MM-DD in the viewer's own time zone, oldest first.
function lastWeek() {
  const out = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    out.push(d.toLocaleDateString("en-CA"));
  }
  return out;
}
let done = {}; // {"Stretch": ["2026-10-29", ...]}
function draw() {
  const days = lastWeek();
  list.innerHTML = "";
  for (const h of HABITS) {
    const row = document.createElement("div"); row.className = "row";
    const name = document.createElement("span"); name.className = "name"; name.textContent = h;
    const box = document.createElement("span"); box.className = "days";
    for (const day of days) {
      const b = document.createElement("button"); b.className = "d";
      const on = (done[h] || []).includes(day);
      b.setAttribute("aria-pressed", on); b.title = `${h}, ${day}`;
      b.setAttribute("aria-label", `${h} on ${day}`);
      b.onclick = () => {
        const set = new Set(done[h] || []);
        on ? set.delete(day) : set.add(day);
        // Keep a fortnight, so the state stays small.
        done[h] = [...set].sort().slice(-14);
        draw();
        nexus.state.set(done).catch(() => {});
      };
      box.append(b);
    }
    row.append(name, box); list.append(row);
  }
}
nexus.state.get().then((s) => { done = s || {}; draw(); }).catch(draw);
draw();
</script></body></html>
```

## Weather card (size m, hosts: api.open-meteo.com)

A public JSON API through `nexus.fetch`, refreshed every 30 minutes, with a quiet line while it waits for Allow. A narrow card hides later days with a container query.

```html
<!doctype html><html><head><meta charset="utf-8"><style>
html,body{height:100%}
body{margin:0;font:13px/1.4 system-ui,sans-serif;color:#e2e8f0;background:transparent;container-type:inline-size}
.m{color:#94a3b8;font-size:11px}
.t{font-size:clamp(24px,16cqw,64px);font-weight:600;font-variant-numeric:tabular-nums}
.days{display:grid;grid-template-columns:repeat(auto-fit,minmax(44px,1fr));gap:4px;margin-top:6px}
.days div{text-align:center;font-size:11px}
@container (max-width: 220px){.days div:nth-child(n+4){display:none}}
[data-theme="light"] body{color:#0f172a}[data-theme="light"] .m{color:#475569}
</style></head><body>
<div class="m" id="where">Bengaluru</div>
<div class="t" id="t">--°</div>
<div class="m" id="what">Loading…</div>
<div class="days" id="days"></div>
<script>
const URL_ = "https://api.open-meteo.com/v1/forecast?latitude=12.97&longitude=77.59" +
  "&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min&timezone=auto";
const WORDS = {0:"Clear",1:"Mostly clear",2:"Partly cloudy",3:"Cloudy",45:"Fog",51:"Drizzle",61:"Rain",63:"Rain",65:"Heavy rain",80:"Showers",95:"Thunderstorm"};
const $ = (id) => document.getElementById(id);
async function load() {
  try {
    const r = await nexus.fetch(URL_);
    if (r.status !== 200) throw new Error("status " + r.status);
    const d = JSON.parse(r.text);
    $("t").textContent = Math.round(d.current.temperature_2m) + "°";
    $("what").textContent = WORDS[d.current.weather_code] || "—";
    $("days").innerHTML = d.daily.time.slice(1, 6).map((day, i) =>
      `<div><div class="m">${new Date(day).toLocaleDateString("en-GB", {weekday: "short"})}</div>` +
      `${Math.round(d.daily.temperature_2m_max[i + 1])}° / ${Math.round(d.daily.temperature_2m_min[i + 1])}°</div>`
    ).join("");
  } catch (e) {
    // Not allowed yet (the card shows Allow), offline, or the site changed: say so quietly.
    $("what").textContent = /approved/.test(e.message) ? "Waiting for access" : "Couldn't load the weather";
  }
}
load();
setInterval(() => { if (!document.hidden) load(); }, 30 * 60 * 1000);
</script></body></html>
```

## Today's calendar (size m, data: calendar.today)

Nexus data through `nexus.data`.

```html
<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font:13px/1.4 system-ui,sans-serif;color:#e2e8f0;background:transparent}
.m{color:#94a3b8;font-size:11px}
ul{list-style:none;margin:4px 0 0;padding:0}
li{display:flex;gap:8px;padding:3px 0;border-top:1px solid #1e293b}
li:first-child{border-top:0}
.at{font-variant-numeric:tabular-nums;color:#38bdf8;min-width:3.2em}
.what{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
[data-theme="light"] body{color:#0f172a}[data-theme="light"] .m{color:#475569}
[data-theme="light"] li{border-color:#e2e8f0}
</style></head><body>
<div class="m">Today</div><ul id="list"><li class="m">Loading…</li></ul>
<script>
const list = document.getElementById("list");
function time(iso) {
  return iso && iso.length > 10
    ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "All day";
}
function show(items, empty) {
  list.innerHTML = "";
  if (!items.length) { list.innerHTML = `<li class="m">${empty}</li>`; return; }
  for (const e of items) {
    const li = document.createElement("li");
    const at = document.createElement("span"); at.className = "at"; at.textContent = time(e.start);
    const what = document.createElement("span"); what.className = "what"; what.textContent = e.title;
    li.append(at, what); list.append(li);
  }
}
async function load() {
  try { show(await nexus.data("calendar.today"), "Nothing on today"); }
  catch (e) { show([], /approved/.test(e.message) ? "Waiting for access" : e.message); }
}
load();
setInterval(() => { if (!document.hidden) load(); }, 10 * 60 * 1000);
</script></body></html>
```

## Chat tools (size m, actions: chat.fork, chat.new, ask)

App actions from buttons, and `nexus.on("context")` to know which chat is open.

```html
<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font:13px/1.4 system-ui,sans-serif;color:#e2e8f0;background:transparent}
.m{color:#94a3b8;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btns{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
button{flex:1 1 auto;border:1px solid #334155;background:rgba(15,23,42,.4);color:inherit;border-radius:8px;
  padding:5px 8px;font:inherit;cursor:pointer}
button:hover:not(:disabled){border-color:#38bdf8}
button:disabled{opacity:.4;cursor:default}
form{display:flex;gap:4px;margin-top:6px}
input{flex:1;min-width:0;border:1px solid #334155;background:transparent;color:inherit;border-radius:8px;padding:5px 8px;font:inherit}
.err{color:#fb7185;font-size:11px;min-height:1em}
[data-theme="light"] body{color:#0f172a}[data-theme="light"] .m{color:#475569}
[data-theme="light"] button,[data-theme="light"] input{border-color:#cbd5e1;background:transparent}
</style></head><body>
<div class="m" id="where">No chat open</div>
<div class="btns">
  <button id="fork" disabled>Fork this chat</button>
  <button id="fresh">New chat beside it</button>
</div>
<form id="ask"><input id="q" placeholder="Ask in this chat…" aria-label="Ask"><button>Ask</button></form>
<div class="err" id="err"></div>
<script>
const $ = (id) => document.getElementById(id);
// Which chat is open, kept up to date by the app.
function show(ctx) {
  $("where").textContent = ctx.chat ? `In “${ctx.chat.title || "Chat"}”` : "No chat open";
  $("fork").disabled = !ctx.chat;
}
nexus.context().then(show);
nexus.on("context", show);
// App actions must run straight from a click: call them in the handler, not after a delay.
function run(p) { $("err").textContent = ""; p.catch((e) => { $("err").textContent = e.message; }); }
$("fork").onclick = () => run(nexus.chat.fork());
$("fresh").onclick = () => run(nexus.chat.new());
$("ask").onsubmit = (e) => {
  e.preventDefault();
  const text = $("q").value.trim();
  if (text) run(nexus.ask(text).then(() => { $("q").value = ""; }));
};
</script></body></html>
```

## Pixel pet (size s)

Canvas animation at about 12 fps that stops in a hidden tab and keeps whole-pixel scaling when the card is resized.

```html
<!doctype html><html><head><meta charset="utf-8"><style>
html,body{height:100%;margin:0}
body{background:transparent;display:flex;align-items:center;justify-content:center}
canvas{image-rendering:pixelated;width:100%;height:100%;cursor:pointer}
</style></head><body>
<canvas id="c" width="32" height="24" aria-label="A pixel cat; tap it"></canvas>
<script>
const c = document.getElementById("c"), g = c.getContext("2d");
// Keep the pixels square however the card is resized: pick a whole scale that fits.
new ResizeObserver(() => {
  const scale = Math.max(1, Math.floor(Math.min(innerWidth / 32, innerHeight / 24)));
  c.style.width = 32 * scale + "px"; c.style.height = 24 * scale + "px";
}).observe(document.body);
let hop = 0, frame = 0;
c.onclick = () => { hop = 8; };
function draw() {
  const dark = document.documentElement.dataset.theme !== "light";
  g.clearRect(0, 0, 32, 24);
  const y = 12 - (hop > 0 ? Math.round(Math.sin(hop / 8 * Math.PI) * 4) : 0);
  g.fillStyle = dark ? "#e2e8f0" : "#334155";
  g.fillRect(10, y, 12, 8); g.fillRect(10, y - 3, 3, 3); g.fillRect(19, y - 3, 3, 3);   // body, ears
  g.fillRect(22, y + 2 + (frame % 40 < 20 ? 0 : 1), 5, 2);                                // tail sway
  g.fillStyle = "#38bdf8";
  if (frame % 90 > 3) { g.fillRect(12, y + 2, 2, 2); g.fillRect(18, y + 2, 2, 2); }       // eyes, blinking
  if (hop > 0) hop--;
}
// About 12 frames a second is plenty for pixel art, and nothing runs in a hidden tab.
let last = 0;
function loop(t) {
  if (!document.hidden && t - last > 80) { frame++; draw(); last = t; }
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);
</script></body></html>
```

## Domain search (size m, data: web.search, actions: read, open)

A search box kept to a few sites for one field (here cinematography). Results come back as
`[{title, url, site, snippet}]`; a tap reads the page inside Nexus, the arrow opens it in a
browser tab. None of it needs an Allow. Change `SITES` for another field.

```html
<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font:13px/1.4 system-ui,sans-serif;color:#e2e8f0;background:transparent;container-type:inline-size}
form{display:flex;gap:4px}
input{flex:1;min-width:0;border:1px solid #334155;background:transparent;color:inherit;border-radius:8px;padding:5px 8px;font:inherit}
button{border:1px solid #334155;background:rgba(15,23,42,.4);color:inherit;border-radius:8px;padding:4px 8px;font:inherit;cursor:pointer}
button:hover{border-color:#38bdf8}
button:focus-visible,input:focus-visible,a:focus-visible{outline:2px solid #38bdf8;outline-offset:1px}
.chips{display:flex;flex-wrap:wrap;gap:3px;margin:6px 0}
.chips button{font-size:11px;padding:2px 7px;border-radius:999px}
.chips button[aria-pressed="true"]{border-color:#38bdf8;color:#38bdf8}
ul{list-style:none;margin:0;padding:0}
li{display:flex;gap:6px;align-items:flex-start;padding:5px 0;border-top:1px solid #334155}
li .t{flex:1;min-width:0;text-align:left;border:0;background:none;padding:0;cursor:pointer}
li .t b{display:block;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.m{color:#94a3b8;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.s{display:none;color:#94a3b8;font-size:11px}
li .o{flex:none;font-size:11px;padding:1px 6px}
@container (min-width: 320px){.s{display:block}}
[data-theme="light"] body{color:#0f172a}[data-theme="light"] .m,[data-theme="light"] .s{color:#475569}
[data-theme="light"] input,[data-theme="light"] button,[data-theme="light"] li{border-color:#cbd5e1;background:transparent}
</style></head><body>
<form id="f"><input id="q" placeholder="Lens, lighting, a shot…" aria-label="Search cinematography sites"><button>Search</button></form>
<div class="chips" id="chips" aria-label="Sites to search"></div>
<div class="m" id="msg" aria-live="polite">Search a few cinematography sites.</div>
<ul id="list"></ul>
<script>
// The sites this widget keeps its search to. Tap a chip to leave one out.
const SITES = [
  ["ASC", "theasc.com"], ["No Film School", "nofilmschool.com"],
  ["StudioBinder", "studiobinder.com"], ["CineD", "cined.com"], ["Shot Deck", "shotdeck.com"],
];
const $ = (id) => document.getElementById(id);
let off = [];
function chips() {
  $("chips").textContent = "";
  for (const [name, site] of SITES) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = name;
    b.setAttribute("aria-pressed", String(!off.includes(site)));
    b.onclick = () => {
      off = off.includes(site) ? off.filter((s) => s !== site) : [...off, site];
      chips(); nexus.state.set({ off });
    };
    $("chips").append(b);
  }
}
nexus.state.get().then((s) => { off = (s && s.off) || []; chips(); });
chips();

function show(results) {
  $("list").textContent = "";
  $("msg").textContent = results.length ? "" : "Nothing found on these sites.";
  for (const r of results) {
    const li = document.createElement("li");
    const t = document.createElement("button");
    t.className = "t"; t.title = "Read it here";
    const b = document.createElement("b"); b.textContent = r.title;
    const m = document.createElement("div"); m.className = "m"; m.textContent = r.site;
    const s = document.createElement("div"); s.className = "s"; s.textContent = r.snippet;
    t.append(b, m, s);
    // Opens the page as text in Nexus's reading view, straight from the tap.
    t.onclick = () => nexus.read(r.url).catch((e) => { $("msg").textContent = e.message; });
    const o = document.createElement("button");
    o.className = "o"; o.textContent = "↗"; o.setAttribute("aria-label", "Open " + r.title + " in the browser");
    o.onclick = () => nexus.open(r.url).catch((e) => { $("msg").textContent = e.message; });
    li.append(t, o);
    $("list").append(li);
  }
  nexus.resize(Math.min(document.body.scrollHeight, 400));
}
$("f").onsubmit = async (e) => {
  e.preventDefault();
  const query = $("q").value.trim();
  if (!query) return;
  const sites = SITES.map((s) => s[1]).filter((s) => !off.includes(s));
  if (!sites.length) { $("msg").textContent = "Turn on at least one site."; return; }
  $("msg").textContent = "Searching…";
  try { show(await nexus.data("web.search", { query, sites })); }
  catch (err) { $("msg").textContent = "Couldn't search just now."; }
};
</script></body></html>
```
