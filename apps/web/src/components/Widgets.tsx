import {
  AlarmClock,
  BellRing,
  Clock,
  ArrowUpToLine,
  Pause,
  Play,
  Plus,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Undo2,
  StickyNote,
  Timer,
  Watch,
  X,
} from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { api } from "../lib/api";
import type { BridgeCall, Widget, WidgetAction, WidgetInput } from "../lib/types";
import {
  clock,
  DATA_LABELS,
  elapsed,
  frameHeight,
  isRinging,
  nextRing,
  pendingAsks,
  repeatText,
  timeIn,
  timerLeft,
  zoneCity,
} from "../lib/widgets";
import { useStore } from "../store/chat";
import { useWidgetPointedAt } from "./Screen";
import { useAssistantName } from "../lib/assistant";

/** The clock every widget reads from, ticking once a second (or faster for a stopwatch). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(t);
  }, [ms]);
  return now;
}

/** Load the widgets when signed in, and again after a reconnect. */
export function useWidgetsLoader() {
  const connected = useStore((s) => s.connected);
  const setWidgets = useStore((s) => s.setWidgets);
  useEffect(() => {
    if (!connected) return;
    api
      .widgets()
      .then(setWidgets)
      .catch(() => {});
  }, [connected, setWidgets]);
}

function useWidgetList(): Widget[] {
  const widgets = useStore((s) => s.widgets);
  return useMemo(() => Object.values(widgets).sort((a, b) => a.slot - b.slot), [widgets]);
}

async function press(w: Widget, action: WidgetAction, minutes?: number) {
  const { upsertWidget, removeWidget } = useStore.getState();
  try {
    const next = await api.widgetAction(w.id, action, minutes);
    if (next) upsertWidget(next);
    else removeWidget(w.id);
  } catch {
    /* the widget changed meanwhile; its event brings the truth */
  }
}

const ICONS: Record<Widget["kind"], ReactNode> = {
  timer: <Timer size={13} />,
  alarm: <AlarmClock size={13} />,
  stopwatch: <Watch size={13} />,
  reminder: <BellRing size={13} />,
  clock: <Clock size={13} />,
  note: <StickyNote size={13} />,
  custom: <Sparkles size={13} />,
};

const btn =
  "rounded-md border border-edge bg-ink/40 p-1 text-slate-300 hover:border-sky-400/60 hover:text-white disabled:opacity-40";

/** Where a card sits: the widget board fills the main area with a grid of full cards. */
const Dock = createContext<{ board: boolean; firstId: string | null }>({ board: false, firstId: null });

/** How long a widget the assistant pointed at stays lit. */
const POINTED_MS = 5000;

/** The widgets beside the orb: a column on laptops, a row you swipe on phones; or, as the
 * widget board, a grid over the whole main area. Orion adds them when asked ("set a timer for
 * 5 minutes"); the + button adds one by hand. */
export function WidgetDock({ compact = false, board = false }: { compact?: boolean; board?: boolean }) {
  const list = useWidgetList();
  const now = useNow(list.some((w) => w.kind === "stopwatch" && w.state.status === "running") ? 100 : 1000);
  const ctx = useMemo(() => ({ board, firstId: list[0]?.id ?? null }), [board, list]);
  const assistant = useAssistantName();
  if (board) {
    return (
      <Dock.Provider value={ctx}>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <AddWidget empty={list.length === 0} compact={false} board />
          <p className="text-xs text-slate-400">
            Your widgets, big. Ask {assistant} for a timer, alarm, reminder, clock or note, or switch back to the orb above.
          </p>
        </div>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(10.5rem,1fr))] items-start gap-3" aria-label="Widgets">
          {list.map((w) => (
            <WidgetCard key={w.id} widget={w} now={now} compact={false} />
          ))}
        </div>
      </Dock.Provider>
    );
  }
  if (list.length === 0 && compact) return null;
  // The + sits outside the scrolling list, so its menu isn't clipped by it.
  return (
    <Dock.Provider value={ctx}>
      <div className={`pointer-events-auto flex gap-2 lg:max-h-full lg:flex-col ${compact ? "lg:w-44" : "lg:w-56"}`}>
        {list.length > 0 && (
          <div
            className="flex min-w-0 items-start gap-2 overflow-x-auto pb-1 lg:min-h-0 lg:flex-col lg:items-stretch lg:overflow-y-auto lg:overflow-x-visible lg:pb-0"
            aria-label="Widgets"
          >
            {list.map((w) => (
              <WidgetCard key={w.id} widget={w} now={now} compact={compact} />
            ))}
          </div>
        )}
        <AddWidget empty={list.length === 0} compact={compact} />
      </div>
    </Dock.Provider>
  );
}

/** Put a widget first on the dock. Done by hand, so the assistant leaves its place alone. */
async function moveFirst(w: Widget) {
  try {
    useStore.getState().upsertWidget(await api.editWidget(w.id, { slot: 1 }));
  } catch {
    /* its event brings the truth */
  }
}

function Card({ widget, children, compact }: { widget: Widget; children: ReactNode; compact: boolean }) {
  const { board, firstId } = useContext(Dock);
  const pointed = useWidgetPointedAt(widget.id);
  const [lit, setLit] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!pointed) return;
    setLit(true);
    box.current?.scrollIntoView?.({ block: "nearest", inline: "nearest", behavior: "smooth" });
    const t = window.setTimeout(() => setLit(false), POINTED_MS);
    return () => window.clearTimeout(t);
  }, [pointed]);
  const size = board ? "w-full px-4 py-3" : compact ? "w-32 px-2.5 py-1.5 lg:px-3 lg:py-2" : "w-44 px-3 py-2";
  const hover = "lg:opacity-0 lg:group-hover:opacity-100 lg:focus:opacity-100";
  return (
    <div
      ref={box}
      className={`group relative shrink-0 rounded-xl border bg-panel/85 backdrop-blur transition-shadow lg:w-full ${size} ${
        lit ? "border-sky-400 shadow-[0_0_0_3px_rgba(56,189,248,0.35)]" : "border-edge"
      }`}
    >
      <div className="flex items-center gap-1.5 pr-10 text-[11px] text-slate-400">
        {ICONS[widget.kind]}
        <span className="truncate">{widget.title}</span>
      </div>
      <div className="absolute right-1.5 top-1.5 flex gap-0.5">
        {firstId !== widget.id && !compact && (
          <button
            onClick={() => moveFirst(widget)}
            aria-label={`Move ${widget.title} first`}
            title="Move first"
            className={`rounded p-0.5 text-slate-500 hover:text-white ${hover}`}
          >
            <ArrowUpToLine size={12} />
          </button>
        )}
        <button
          onClick={() => press(widget, "remove")}
          aria-label={`Remove ${widget.title}`}
          title="Remove"
          className={`rounded p-0.5 text-slate-500 hover:text-white ${hover}`}
        >
          <X size={12} />
        </button>
      </div>
      {children}
    </div>
  );
}

function WidgetCard({ widget: w, now, compact }: { widget: Widget; now: number; compact: boolean }) {
  if (w.kind === "custom") return <CustomWidgetCard widget={w} compact={compact} />;
  const s = w.state;
  // In a chat on a phone the orb strip is short: values only, buttons on the ring screen.
  const big = compact ? "text-lg lg:text-2xl" : "text-2xl";
  const row = compact ? "mt-2 hidden gap-1 lg:flex" : "mt-2 flex gap-1";
  if (w.kind === "timer") {
    const left = timerLeft(w, now);
    const total = s.duration_s || 1;
    const running = s.status === "running";
    return (
      <Card widget={w} compact={compact}>
        <div className={`mt-0.5 font-mono ${big} tabular-nums text-slate-100`}>
          {s.status === "done" ? "Done" : clock(left)}
        </div>
        <div className="mt-1 h-1 overflow-hidden rounded bg-edge">
          <div className="h-full bg-sky-400 transition-[width]" style={{ width: `${(100 * left) / total}%` }} />
        </div>
        <div className={row}>
          {running ? (
            <button className={btn} onClick={() => press(w, "pause")} aria-label="Pause" title="Pause">
              <Pause size={13} />
            </button>
          ) : (
            s.status === "paused" && (
              <button className={btn} onClick={() => press(w, "resume")} aria-label="Resume" title="Resume">
                <Play size={13} />
              </button>
            )
          )}
          <button className={`${btn} px-1.5 text-[11px]`} onClick={() => press(w, "add_time", 1)} title="Add a minute">
            +1 min
          </button>
          <button className={btn} onClick={() => press(w, "reset")} aria-label="Reset" title="Reset">
            <RotateCcw size={13} />
          </button>
        </div>
      </Card>
    );
  }
  if (w.kind === "stopwatch") {
    const t = elapsed(w, now);
    return (
      <Card widget={w} compact={compact}>
        <div className={`mt-0.5 font-mono ${big} tabular-nums text-slate-100`}>
          {clock(Math.floor(t))}
          <span className="text-base text-slate-400">.{Math.floor((t % 1) * 10)}</span>
        </div>
        <div className={row}>
          {s.status === "running" ? (
            <button className={btn} onClick={() => press(w, "pause")} aria-label="Pause" title="Pause">
              <Pause size={13} />
            </button>
          ) : (
            <button className={btn} onClick={() => press(w, "resume")} aria-label="Start" title="Start">
              <Play size={13} />
            </button>
          )}
          <button className={btn} onClick={() => press(w, "reset")} aria-label="Reset" title="Reset">
            <RotateCcw size={13} />
          </button>
        </div>
      </Card>
    );
  }
  if (w.kind === "alarm") {
    const on = s.status !== "off";
    return (
      <Card widget={w} compact={compact}>
        <div className="mt-0.5 flex items-center justify-between">
          <span className={`font-mono ${big} tabular-nums ${on ? "text-slate-100" : "text-slate-500"}`}>{s.time}</span>
          <button
            role="switch"
            aria-checked={on}
            aria-label={on ? "Turn alarm off" : "Turn alarm on"}
            onClick={() => press(w, on ? "pause" : "resume")}
            className={`relative h-5 w-9 rounded-full transition ${on ? "bg-sky-500" : "bg-edge"}`}
          >
            <span className={`absolute top-0.5 size-4 rounded-full bg-white transition-all ${on ? "left-4.5" : "left-0.5"}`} />
          </button>
        </div>
        <p className="mt-1 text-[11px] text-slate-400">{repeatText(s.repeat)}</p>
      </Card>
    );
  }
  if (w.kind === "reminder") {
    const done = s.status === "done";
    return (
      <Card widget={w} compact={compact}>
        <p className={`mt-0.5 line-clamp-2 text-sm ${done ? "text-slate-500 line-through" : "text-slate-100"}`}>{s.text}</p>
        <p className="mt-1 truncate text-[11px] text-slate-400">
          {done ? "Done" : `${nextRing(w.fires_at, now)}${s.repeat ? ` · ${repeatText(s.repeat)}` : ""}`}
        </p>
      </Card>
    );
  }
  if (w.kind === "clock") {
    return (
      <Card widget={w} compact={compact}>
        <ul className="mt-0.5 space-y-0.5">
          {(s.zones ?? []).map((z) => (
            <li key={z} className="flex items-baseline justify-between gap-2">
              <span className="truncate text-xs text-slate-400">{zoneCity(z)}</span>
              <span className="font-mono text-lg tabular-nums text-slate-100">{timeIn(z, now)}</span>
            </li>
          ))}
        </ul>
      </Card>
    );
  }
  return (
    <Card widget={w} compact={compact}>
      <NoteText widget={w} />
    </Card>
  );
}

function NoteText({ widget }: { widget: Widget }) {
  const [text, setText] = useState(widget.state.text ?? "");
  useEffect(() => setText(widget.state.text ?? ""), [widget.state.text]);
  const save = () => {
    if (text === (widget.state.text ?? "")) return;
    api
      .editWidget(widget.id, { text })
      .then(useStore.getState().upsertWidget)
      .catch(() => {});
  };
  return (
    <textarea
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={save}
      rows={3}
      placeholder="Write something…"
      aria-label={`${widget.title} text`}
      className="mt-1 w-full resize-none bg-transparent text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none"
    />
  );
}

/** The theme where a card sits: dark over the orb's night sky, the app's own elsewhere. The
 * widget's page is served in it, and reloads when it changes. */
function useCardTheme(ref: RefObject<HTMLElement | null>): "dark" | "light" | null {
  const [theme, setTheme] = useState<"dark" | "light" | null>(null);
  useEffect(() => {
    const read = () =>
      setTheme(ref.current?.closest("[data-theme]")?.getAttribute("data-theme") === "light" ? "light" : "dark");
    read();
    const watch = new MutationObserver(read);
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"], subtree: true });
    return () => watch.disconnect();
  }, [ref]);
  return theme;
}

const BRIDGE_CALLS: BridgeCall[] = ["state.get", "state.set", "fetch", "data", "notify"];

/** A custom widget: its page in a locked-down frame (no network, no cookies, its own opaque
 * origin), and the bridge its window.nexus talks through. Calls go to the server, which checks
 * them against what you allowed; resize is handled here. */
function CustomWidgetCard({ widget: w, compact }: { widget: Widget; compact: boolean }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [asked, setAsked] = useState<number | null>(null);
  const theme = useCardTheme(frame);
  const asks = pendingAsks(w);
  const waiting = asks.hosts.length + asks.data.length > 0;
  const size = w.state.manifest?.size ?? "m";

  useEffect(() => {
    const onMessage = async (e: MessageEvent) => {
      const win = frame.current?.contentWindow;
      const m = e.data;
      if (!win || e.source !== win || !m || m.nexus !== 1 || typeof m.id !== "number") return;
      // The frame's origin is opaque ("null"), so replies can only be addressed with "*"; they
      // go to this frame's window and nowhere else.
      const reply = (body: Record<string, unknown>) => win.postMessage({ nexus: 1, id: m.id, ...body }, "*");
      if (m.call === "resize") {
        setAsked(Number(m.args?.height) || null);
        return reply({ ok: true, result: true });
      }
      if (!BRIDGE_CALLS.includes(m.call)) return reply({ ok: false, error: `Unknown call ${m.call}` });
      try {
        const { result } = await api.widgetBridge(w.id, m.call, m.args ?? {});
        reply({ ok: true, result });
      } catch (err) {
        reply({ ok: false, error: err instanceof Error ? err.message : "Nexus refused" });
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [w.id]);

  const allow = async () => {
    try {
      useStore.getState().upsertWidget(await api.approveWidget(w.id, asks.hosts, asks.data));
    } catch {
      /* its event brings the truth */
    }
  };

  const height = compact ? Math.min(frameHeight(size, asked), 140) : frameHeight(size, asked);
  return (
    <Card widget={w} compact={compact}>
      {waiting && !compact && (
        <div className="mt-1.5 rounded-lg border border-amber-400/40 bg-amber-400/10 p-2 text-[11px] text-amber-100">
          <p>
            Wants to{" "}
            {[
              ...asks.hosts.map((h) => `fetch from ${h}`),
              ...asks.data.map((d) => `read ${DATA_LABELS[d] ?? d}`),
            ].join(", ")}
            .
          </p>
          <button
            onClick={allow}
            className="mt-1.5 flex items-center gap-1 rounded-md bg-amber-500 px-2 py-0.5 font-medium text-black hover:bg-amber-400"
          >
            <ShieldCheck size={12} /> Allow
          </button>
        </div>
      )}
      <iframe
        ref={frame}
        // A new version or an approval reloads the page, so it starts with what it may use.
        key={`${w.state.custom_id}:${(w.state.approved?.hosts ?? []).length}:${(w.state.approved?.data ?? []).length}`}
        src={theme ? `/api/widgets/${w.id}/frame?theme=${theme}` : undefined}
        title={w.title}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        className="mt-1 block w-full rounded-md border-0 bg-transparent"
        style={{ height }}
      />
      {(w.state.version ?? 1) > 1 && !compact && (
        <button
          onClick={() => press(w, "undo")}
          className="mt-1 flex items-center gap-1 text-[11px] text-slate-500 hover:text-white"
          title="Go back to the previous version"
        >
          <Undo2 size={11} /> Undo last change (v{w.state.version})
        </button>
      )}
    </Card>
  );
}

const QUICK_TIMERS = [1, 5, 10, 25];

/** The + on the dock: a timer, stopwatch, alarm, clock or note by hand. */
function AddWidget({ empty, compact, board = false }: { empty: boolean; compact: boolean; board?: boolean }) {
  const [open, setOpen] = useState(false);
  const [minutes, setMinutes] = useState("");
  const [time, setTime] = useState("07:00");
  const [error, setError] = useState("");
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const make = async (body: WidgetInput) => {
    setError("");
    try {
      useStore.getState().upsertWidget(await api.addWidget(body));
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't add it");
    }
  };
  const item = "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-edge/60";
  const chip = "rounded-full border border-edge px-2 py-0.5 text-xs hover:border-sky-400/60 hover:text-sky-200";

  return (
    <div ref={box} className="relative shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="Add a widget"
        className={`flex items-center gap-1.5 rounded-xl border border-dashed border-edge bg-panel/60 px-3 py-2 text-xs text-slate-400 backdrop-blur hover:border-sky-400/60 hover:text-sky-200 ${
          empty || board ? "" : "lg:w-full lg:justify-center"
        }`}
      >
        <Plus size={13} /> {empty ? "Add a widget" : <span className={compact ? "sr-only lg:not-sr-only" : ""}>Add</span>}
      </button>
      {open && (
        <div
          className={`absolute top-full z-30 mt-1 w-64 rounded-xl border border-edge bg-panel p-2 shadow-xl ${
            board ? "left-0" : "right-0 lg:bottom-full lg:top-auto lg:mb-1 lg:mt-0"
          }`}
        >
          <p className="px-2 pb-1 text-[11px] text-slate-500">
            Or just ask: "set a timer for 10 minutes", "wake me at 7 on weekdays", "remind me to call mum at 6".
          </p>
          <div className="px-2 py-1">
            <div className="flex items-center gap-2 text-sm">
              <Timer size={14} /> Timer
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {QUICK_TIMERS.map((m) => (
                <button key={m} className={chip} onClick={() => make({ kind: "timer", minutes: m })}>
                  {m} min
                </button>
              ))}
              <form
                className="flex items-center gap-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (Number(minutes) > 0) make({ kind: "timer", minutes: Number(minutes) });
                }}
              >
                <input
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.value)}
                  inputMode="decimal"
                  placeholder="min"
                  aria-label="Timer minutes"
                  className="w-12 rounded-md border border-edge bg-ink px-1.5 py-0.5 text-base sm:text-xs"
                />
                <button className={chip}>Start</button>
              </form>
            </div>
          </div>
          <form
            className="flex items-center gap-2 px-2 py-1.5 text-sm"
            onSubmit={(e) => {
              e.preventDefault();
              make({ kind: "alarm", time });
            }}
          >
            <AlarmClock size={14} /> Alarm
            <input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              aria-label="Alarm time"
              className="ml-auto rounded-md border border-edge bg-ink px-1.5 py-0.5 text-base sm:text-xs"
            />
            <button className={chip}>Set</button>
          </form>
          <button className={item} onClick={() => make({ kind: "stopwatch" })}>
            <Watch size={14} /> Stopwatch
          </button>
          <button
            className={item}
            onClick={() => make({ kind: "clock", zones: [Intl.DateTimeFormat().resolvedOptions().timeZone] })}
          >
            <Clock size={14} /> Clock
          </button>
          <button className={item} onClick={() => make({ kind: "note" })}>
            <StickyNote size={14} /> Note
          </button>
          {error && <p className="px-2 pt-1 text-xs text-rose-400">{error}</p>}
        </div>
      )}
    </div>
  );
}

/** A soft two-tone chime, repeated while something rings. Browsers allow sound only after the
 * person has used the page, so a silent first ring is expected on a fresh tab. */
function useChime(on: boolean) {
  useEffect(() => {
    if (!on) return;
    let ctx: AudioContext | null = null;
    try {
      ctx = new AudioContext();
    } catch {
      return;
    }
    const ring = () => {
      if (!ctx) return;
      [880, 660].forEach((f, i) => {
        const o = ctx!.createOscillator();
        const g = ctx!.createGain();
        const t = ctx!.currentTime + i * 0.25;
        o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.2, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
        o.connect(g).connect(ctx!.destination);
        o.start(t);
        o.stop(t + 0.25);
      });
      navigator.vibrate?.([200, 100, 200]);
    };
    ring();
    const t = window.setInterval(ring, 1800);
    return () => {
      window.clearInterval(t);
      ctx?.close().catch(() => {});
    };
  }, [on]);
}

/** What's ringing, over everything, until it's snoozed or dismissed. */
export function RingOverlay() {
  const list = useWidgetList();
  const now = useNow();
  const ringing = list.filter((w) => isRinging(w, now));
  useChime(ringing.length > 0);
  const w = ringing[0];
  if (!w) return null;
  const text = w.kind === "reminder" ? w.state.text : w.kind === "timer" ? "Time's up" : w.state.time;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm" role="alertdialog" aria-label={w.title}>
      <div className="w-full max-w-sm rounded-2xl border border-edge bg-panel p-6 text-center shadow-2xl">
        <div className="mx-auto grid size-14 animate-pulse place-items-center rounded-full bg-sky-500/20 text-sky-300">
          {w.kind === "reminder" ? <BellRing size={26} /> : <AlarmClock size={26} />}
        </div>
        <p className="mt-3 text-sm text-slate-400">{w.title}</p>
        <p className="mt-1 text-2xl font-semibold text-slate-100">{text}</p>
        <div className="mt-5 flex justify-center gap-2">
          <button
            onClick={() => press(w, "snooze", 5)}
            className="rounded-lg border border-edge px-4 py-2 text-sm text-slate-200 hover:bg-edge/60"
          >
            Snooze 5 min
          </button>
          {w.kind === "timer" && (
            <button
              onClick={() => press(w, "add_time", 1)}
              className="rounded-lg border border-edge px-4 py-2 text-sm text-slate-200 hover:bg-edge/60"
            >
              +1 min
            </button>
          )}
          <button
            onClick={() => press(w, "dismiss")}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500"
          >
            Dismiss
          </button>
        </div>
        {ringing.length > 1 && <p className="mt-3 text-xs text-slate-500">{ringing.length - 1} more ringing</p>}
      </div>
    </div>
  );
}
