import {
  AlarmClock,
  ArrowLeft,
  BellRing,
  Clock,
  ArrowUpToLine,
  Library,
  Music,
  PanelRightClose,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Undo2,
  StickyNote,
  Send,
  Timer,
  Trash2,
  Watch,
  X,
  type LucideIcon,
} from "lucide-react";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { create } from "zustand";
import { api } from "../lib/api";
import type {
  AppAction,
  BridgeCall,
  SearchEngine,
  Widget,
  WidgetAction,
  WidgetInput,
  WidgetPos,
  WidgetSize,
} from "../lib/types";
import {
  ACTION_LABELS,
  ago,
  APP_ACTIONS,
  clampSize,
  clock,
  DATA_LABELS,
  ENGINES,
  libraryLine,
  mayAct,
  safeLink,
  searchUrl,
  daysFor,
  elapsed,
  frameHeight,
  isRinging,
  nextRing,
  pendingAsks,
  placeIn,
  repeatText,
  ruleDays,
  timeIn,
  timerLeft,
  WEEK,
  zoneCity,
} from "../lib/widgets";
import { useStore } from "../store/chat";
import { useWidgetPointedAt } from "./Screen";
import { useAssistantName } from "../lib/assistant";
import { useWide } from "../lib/media";
import { useSend } from "../lib/useSend";
import { useChatActions } from "../lib/chatActions";
import { lastTune, playTune, previewTune, rememberTune, swell, tuneOf, TUNES, type TuneId } from "../lib/tunes";

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

/** What each kind is, in the add menu and on its card: a name, a line on what it does, an
 * icon and a colour of its own, so a glance tells a timer from an alarm or a note. */
const KINDS: Record<Widget["kind"], { name: string; about: string; icon: LucideIcon; tone: string }> = {
  timer: { name: "Timer", about: "Counts down", icon: Timer, tone: "text-sky-300 bg-sky-400/15" },
  alarm: { name: "Alarm", about: "Rings at a set time", icon: AlarmClock, tone: "text-amber-300 bg-amber-400/15" },
  reminder: { name: "Reminder", about: "Nudges you later", icon: BellRing, tone: "text-rose-300 bg-rose-400/15" },
  stopwatch: { name: "Stopwatch", about: "Counts up", icon: Watch, tone: "text-emerald-300 bg-emerald-400/15" },
  clock: { name: "World clock", about: "Other time zones", icon: Clock, tone: "text-violet-300 bg-violet-400/15" },
  note: { name: "Note", about: "A blank note", icon: StickyNote, tone: "text-yellow-300 bg-yellow-400/15" },
  search: { name: "Search", about: "Google, or ask", icon: Search, tone: "text-teal-300 bg-teal-400/15" },
  custom: { name: "Your own", about: "Built for you", icon: Sparkles, tone: "text-purple-300 bg-purple-400/15" },
};

function KindIcon({ kind, size = 13 }: { kind: Widget["kind"]; size?: number }) {
  const Icon = KINDS[kind].icon;
  return <Icon size={size} className={KINDS[kind].tone.split(" ")[0]} />;
}

const btn =
  "rounded-md border border-edge bg-ink/40 p-1 text-slate-300 hover:border-sky-400/60 hover:text-white disabled:opacity-40";

/** Where a card sits: the widget board fills the main area with a grid of full cards; on a
 * laptop's main screen a card can be dragged out of the dock (movable) and left anywhere
 * (floating). */
const Dock = createContext<{ board: boolean; firstId: string | null; movable: boolean; floating: boolean }>({
  board: false,
  firstId: null,
  movable: false,
  floating: false,
});

/** The card being dragged, and where it would land (placeIn). */
const useDrag = create<{ drag: { id: string; x: number; y: number } | null }>(() => ({ drag: null }));

/** The part of the main screen widgets can be left in (FloatingWidgets), while it's shown.
 * There is one main screen at a time. */
let room: HTMLElement | null = null;

/** Drag a card from where it is to anywhere in the room. Saved on release; a press that
 * doesn't move, or one on a button or a note's text, is left to the card. */
function startDrag(e: ReactPointerEvent, w: Widget, card: HTMLElement | null) {
  const target = e.target as HTMLElement;
  // A panel it opened (portalled, but its events still bubble here) isn't the card.
  if (e.button !== 0 || !room || !card || target.closest("button, a, input, textarea, select, [data-popover]")) return;
  const r = room.getBoundingClientRect();
  const c = card.getBoundingClientRect();
  const size = { width: c.width, height: c.height };
  const grab = { x: e.clientX - c.left, y: e.clientY - c.top };
  const from = { x: e.clientX, y: e.clientY };
  let moved = false;
  const move = (ev: PointerEvent) => {
    if (!moved && Math.hypot(ev.clientX - from.x, ev.clientY - from.y) < 4) return;
    if (!moved) {
      moved = true;
      window.getSelection()?.removeAllRanges();
      // Frames would swallow the pointer as it passes over them.
      document.body.classList.add("widget-dragging");
    }
    const at = placeIn(ev.clientX - grab.x - r.left, ev.clientY - grab.y - r.top, r, size);
    useDrag.setState({ drag: { id: w.id, ...at } });
  };
  const done = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", done);
    window.removeEventListener("pointercancel", done);
    document.body.classList.remove("widget-dragging");
    const drag = useDrag.getState().drag;
    useDrag.setState({ drag: null });
    if (moved && drag?.id === w.id) place(w, { x: drag.x, y: drag.y });
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", done);
  window.addEventListener("pointercancel", done);
}

/** Leave a widget where it was dropped, or (null) put it back in the dock. Shown at once; done
 * by hand, so the assistant leaves its place alone. */
async function place(w: Widget, pos: WidgetPos | null) {
  const { upsertWidget } = useStore.getState();
  upsertWidget({ ...w, pos });
  try {
    upsertWidget(await api.editWidget(w.id, pos ? { pos } : { dock: true }));
  } catch {
    upsertWidget(w);
  }
}

/** The card being resized by its corner: its size so far and, when it was left on the screen,
 * where it sits so the corner opposite the handle stays put. */
const useResize = create<{ live: { id: string; size: WidgetSize; pos: WidgetPos | null } | null }>(() => ({
  live: null,
}));

/** Which corner a card's resize handle is on: the one away from the screen edge it's held to,
 * so the card grows into the room. The dock is on the right, so its cards grow leftwards. */
function handleOnLeft(w: Widget, floating: boolean): boolean {
  return floating ? (w.pos?.x ?? 0) > 0.5 : true;
}

/** Drag a card's corner to make it bigger or smaller. Saved on release; a double-click on the
 * corner gives it back its own size. */
function startResize(e: ReactPointerEvent, w: Widget, card: HTMLElement | null, floating: boolean) {
  if (e.button !== 0 || !card) return;
  e.preventDefault();
  e.stopPropagation();
  const c = card.getBoundingClientRect();
  const r = floating ? room?.getBoundingClientRect() : null;
  const left = handleOnLeft(w, floating);
  const from = { x: e.clientX, y: e.clientY };
  document.body.classList.add("widget-dragging");
  const move = (ev: PointerEvent) => {
    const dx = ev.clientX - from.x;
    const size = clampSize(left ? c.width - dx : c.width + dx, c.height + ev.clientY - from.y);
    let pos: WidgetPos | null = null;
    if (r) {
      // Keep the top corner opposite the handle where it was.
      const at = left ? c.right - size.w : c.left;
      pos = placeIn(at - r.left, c.top - r.top, r, { width: size.w, height: size.h });
    }
    useResize.setState({ live: { id: w.id, size, pos } });
  };
  const done = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", done);
    window.removeEventListener("pointercancel", done);
    document.body.classList.remove("widget-dragging");
    const live = useResize.getState().live;
    useResize.setState({ live: null });
    if (live?.id === w.id) resize(w, live.size, live.pos);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", done);
  window.addEventListener("pointercancel", done);
}

/** Keep a widget at the size it was dragged to (and, left on the screen, where that put it),
 * or (null) give it back its own size. */
async function resize(w: Widget, size: WidgetSize | null, pos: WidgetPos | null = null) {
  const { upsertWidget } = useStore.getState();
  upsertWidget({ ...w, size, ...(pos ? { pos } : {}) });
  try {
    upsertWidget(await api.editWidget(w.id, size ? { size, ...(pos ? { pos } : {}) } : { reset_size: true }));
  } catch {
    upsertWidget(w);
  }
}

/** The note made with a note's + button, which takes the cursor when it shows up. */
let focusNote: string | null = null;

/** A new blank note next to this one: just below it when it was left on the screen, else in
 * the dock. */
async function newNote(from: Widget, floating: boolean, card: HTMLElement | null) {
  let pos: WidgetPos | undefined;
  if (floating && from.pos && room && card) {
    const free = room.getBoundingClientRect().height - card.offsetHeight;
    const step = free > 0 ? (card.offsetHeight + 8) / free : 1;
    // Below it, or above when it's already near the bottom.
    const y = from.pos.y + step <= 1 ? from.pos.y + step : Math.max(0, from.pos.y - step);
    pos = { x: from.pos.x, y: Math.round(y * 1e4) / 1e4 };
  }
  try {
    const w = await api.addWidget({ kind: "note", ...(pos ? { pos } : {}) });
    focusNote = w.id;
    useStore.getState().upsertWidget(w);
  } catch {
    /* the dock is full; the menu says so when adding there */
  }
}

/** How long a widget the assistant pointed at stays lit. */
const POINTED_MS = 5000;

/** The widgets beside the orb: a column on laptops, a row you swipe on phones; or, as the
 * widget board, a grid over the whole main area. Orion adds them when asked ("set a timer for
 * 5 minutes"); the + button adds one by hand. */
export function WidgetDock({ compact = false, board = false }: { compact?: boolean; board?: boolean }) {
  const all = useWidgetList();
  const now = useWidgetClock(all);
  const drag = useDrag((s) => s.drag);
  // On a laptop's main screen the cards dragged out live in FloatingWidgets instead.
  const movable = useWide() && !board;
  const list = useMemo(
    () => (movable ? all.filter((w) => !w.pos && drag?.id !== w.id) : all),
    [all, movable, drag?.id],
  );
  const ctx = useMemo(
    () => ({ board, firstId: list[0]?.id ?? null, movable, floating: false }),
    [board, list, movable],
  );
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
  if (all.length === 0 && compact) return null;
  // The + sits outside the scrolling list, so its menu isn't clipped by it.
  return (
    <Dock.Provider value={ctx}>
      {/* As wide as its widest card: one made bigger by its corner widens the column leftwards. */}
      <div className={`pointer-events-auto flex gap-2 lg:max-h-full lg:flex-col ${compact ? "lg:w-44" : "lg:w-max lg:min-w-56"}`}>
        {list.length > 0 && (
          <div
            // On phones the row fades out before the + so a card that scrolls on is plainly
            // more to swipe to, not cut off.
            className="flex min-w-0 items-start gap-2 overflow-x-auto pb-1 pr-10 [mask-image:linear-gradient(to_right,black_calc(100%-3rem),transparent)] lg:min-h-0 lg:flex-col lg:items-end lg:overflow-y-auto lg:overflow-x-visible lg:pb-0 lg:pr-0 lg:[mask-image:none]"
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

/** Stopwatches tick tenths of a second; everything else once a second. */
function useWidgetClock(list: Widget[]): number {
  return useNow(list.some((w) => w.kind === "stopwatch" && w.state.status === "running") ? 100 : 1000);
}

/** The widgets dragged out of the dock on a laptop, each left where it was dropped over the
 * orb or the lists. Their places are saved, so they come back there on any laptop. */
export function FloatingWidgets({ compact = false }: { compact?: boolean }) {
  const wide = useWide();
  const all = useWidgetList();
  const now = useWidgetClock(all);
  const drag = useDrag((s) => s.drag);
  const live = useResize((s) => s.live);
  const ctx = useMemo(() => ({ board: false, firstId: null, movable: true, floating: true }), []);
  const shown = all.filter((w) => w.pos || drag?.id === w.id);
  if (!wide) return null;
  return (
    <div
      ref={(el) => {
        room = el;
      }}
      className={`pointer-events-none absolute inset-x-4 bottom-4 z-30 ${compact ? "top-12" : "top-16"}`}
      aria-label={shown.length ? "Widgets you placed" : undefined}
    >
      <Dock.Provider value={ctx}>
        {shown.map((w) => {
          const at = drag?.id === w.id ? drag : live?.id === w.id && live.pos ? live.pos : (w.pos as WidgetPos);
          const sized = !compact && (w.size || live?.id === w.id);
          return (
            <div
              key={w.id}
              className={`pointer-events-auto absolute ${sized ? "" : compact ? "w-44" : "w-56"}`}
              style={{
                left: `${at.x * 100}%`,
                top: `${at.y * 100}%`,
                transform: `translate(${-at.x * 100}%, ${-at.y * 100}%)`,
              }}
            >
              <WidgetCard widget={w} now={now} compact={compact} />
            </div>
          );
        })}
      </Dock.Provider>
    </div>
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

/** The size a card is shown at: dragged out by its corner on a laptop's main screen (not on the
 * board, in a chat's small orb strip, or on a phone), else null for the kind's own size. */
function useCardSize(widget: Widget, compact: boolean): WidgetSize | null {
  const { board, movable } = useContext(Dock);
  const live = useResize((s) => (s.live?.id === widget.id ? s.live.size : null));
  if (!movable || board || compact) return null;
  return live ?? widget.size ?? null;
}

function Card({ widget, children, compact }: { widget: Widget; children: ReactNode; compact: boolean }) {
  const { board, firstId, movable, floating } = useContext(Dock);
  const dragging = useDrag((s) => s.drag?.id === widget.id);
  const sized = useCardSize(widget, compact);
  const resizable = movable && !board && !compact;
  const onLeft = handleOnLeft(widget, floating);
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
  const size = board
    ? "w-full px-4 py-3"
    : compact
      ? "w-32 px-2.5 py-1.5 lg:w-full lg:px-3 lg:py-2"
      : sized
        ? "flex flex-col px-3 py-2"
        : "w-44 px-3 py-2 lg:w-56";
  const hover = "lg:opacity-0 lg:group-hover:opacity-100 lg:focus:opacity-100";
  // A clear sky edge and a lift off the background; brighter when pointed at or held.
  const edge = lit
    ? "border-sky-400 shadow-[0_0_0_3px_rgba(56,189,248,0.35)]"
    : dragging
      ? "border-sky-400 shadow-2xl shadow-sky-500/20 ring-2 ring-sky-400/30"
      : "border-sky-400/40 shadow-lg shadow-black/30 ring-1 ring-sky-400/10 hover:border-sky-400/70";
  return (
    <div
      ref={box}
      onPointerDown={movable ? (e) => startDrag(e, widget, box.current) : undefined}
      title={movable ? "Drag to put it anywhere" : undefined}
      data-sized={sized ? "true" : undefined}
      style={sized ? { width: sized.w, height: sized.h } : undefined}
      // A container, so big numbers grow with the card when it's made bigger.
      className={`group @container relative shrink-0 rounded-xl border bg-panel/90 backdrop-blur transition-[box-shadow,border-color] ${size} ${edge} ${
        movable ? (dragging ? "cursor-grabbing select-none" : "lg:cursor-grab") : ""
      }`}
    >
      <div className={`flex items-center gap-1.5 text-[11px] text-slate-400 ${widget.kind === "note" && !compact ? "pr-14" : "pr-10"}`}>
        <KindIcon kind={widget.kind} />
        <span className="truncate">{widget.title}</span>
      </div>
      <div className="absolute right-1.5 top-1.5 flex gap-0.5">
        {widget.kind === "note" && !compact && (
          <button
            onClick={() => newNote(widget, floating, box.current)}
            aria-label="New blank note"
            title="New note"
            className="rounded p-0.5 text-slate-500 hover:text-white"
          >
            <Plus size={12} />
          </button>
        )}
        {floating && (
          <button
            onClick={() => place(widget, null)}
            aria-label={`Put ${widget.title} back in the dock`}
            title="Back to the dock"
            className={`rounded p-0.5 text-slate-500 hover:text-white ${hover}`}
          >
            <PanelRightClose size={12} />
          </button>
        )}
        {!floating && firstId !== widget.id && !compact && (
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
      {sized ? <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div> : children}
      {resizable && (
        <button
          onPointerDown={(e) => startResize(e, widget, box.current, floating)}
          onDoubleClick={() => widget.size && resize(widget, null)}
          aria-label={`Resize ${widget.title}`}
          title={widget.size ? "Drag to resize · double-click for its own size" : "Drag to resize"}
          className={`absolute bottom-0 ${onLeft ? "left-0 cursor-nesw-resize" : "right-0 cursor-nwse-resize"} grid size-4 place-items-center text-slate-500 hover:text-sky-300 ${hover}`}
        >
          <svg viewBox="0 0 10 10" className={`size-2.5 ${onLeft ? "-scale-x-100" : ""}`} aria-hidden>
            <path d="M9 3 3 9M9 6 6 9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" fill="none" />
          </svg>
        </button>
      )}
    </div>
  );
}

function WidgetCard({ widget: w, now, compact }: { widget: Widget; now: number; compact: boolean }) {
  if (w.kind === "custom") return <CustomWidgetCard widget={w} compact={compact} />;
  if (w.kind === "search") return <SearchCard widget={w} compact={compact} />;
  const s = w.state;
  // In a chat on a phone the orb strip is short: values only, buttons on the ring screen. A card
  // made bigger shows its numbers bigger.
  const big = compact ? "text-lg lg:text-2xl" : "text-2xl @[15rem]:text-4xl @[24rem]:text-6xl";
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
  if (w.kind === "alarm") return <AlarmCard widget={w} compact={compact} big={big} />;
  if (w.kind === "reminder") {
    const done = s.status === "done";
    return (
      <Card widget={w} compact={compact}>
        <p
          className={`mt-0.5 line-clamp-2 text-sm group-data-[sized]:line-clamp-none @[15rem]:text-base ${done ? "text-slate-500 line-through" : "text-slate-100"}`}
        >
          {s.text}
        </p>
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
              <span className="truncate text-xs text-slate-400 @[15rem]:text-sm">{zoneCity(z)}</span>
              <span className="font-mono text-lg tabular-nums text-slate-100 @[15rem]:text-2xl @[24rem]:text-4xl">
                {timeIn(z, now)}
              </span>
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

/** A search box: what's typed goes to Google (or DuckDuckGo or Bing) in a new tab, or to the
 * assistant, in the open chat or a new one. The chips under it pick where. */
function SearchCard({ widget: w, compact }: { widget: Widget; compact: boolean }) {
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const { ask } = useChatActions();
  const assistant = useAssistantName();
  const engine: SearchEngine = w.state.engine ?? "google";
  const name = (id: SearchEngine) => (id === "assistant" ? `Ask ${assistant}` : ENGINES.find((e) => e.id === id)!.name);
  const go = async (e: FormEvent) => {
    e.preventDefault();
    const text = q.trim();
    if (!text) return;
    setError("");
    const url = searchUrl(engine, text);
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
      setQ("");
      return;
    }
    try {
      await ask(text);
      setQ("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't ask");
    }
  };
  const pick = (id: SearchEngine) => {
    if (id === engine) return;
    const { upsertWidget } = useStore.getState();
    upsertWidget({ ...w, state: { ...w.state, engine: id } });
    api
      .editWidget(w.id, { engine: id })
      .then(upsertWidget)
      .catch(() => upsertWidget(w));
  };
  return (
    <Card widget={w} compact={compact}>
      <form onSubmit={go} className="mt-1 flex items-center gap-1" role="search">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={engine === "assistant" ? `Ask ${assistant}…` : `Search ${name(engine)}…`}
          aria-label={engine === "assistant" ? `Ask ${assistant}` : `Search ${name(engine)}`}
          enterKeyHint="search"
          className="min-w-0 flex-1 rounded-md border border-edge bg-ink/60 px-2 py-1 text-base text-slate-100 placeholder:text-slate-500 focus:border-sky-400/70 focus:outline-none sm:text-sm @[24rem]:py-2 @[24rem]:text-base"
        />
        <button className={btn} disabled={!q.trim()} aria-label="Search" title={engine === "assistant" ? "Ask" : "Search in a new tab"}>
          {engine === "assistant" ? <Send size={13} /> : <Search size={13} />}
        </button>
      </form>
      {!compact && (
        <div className="mt-1.5 flex flex-wrap gap-1" role="radiogroup" aria-label="Search with">
          {ENGINES.map((e) => (
            <button
              key={e.id}
              type="button"
              role="radio"
              aria-checked={engine === e.id}
              onClick={() => pick(e.id)}
              className={`rounded-full border px-1.5 py-px text-[10px] ${
                engine === e.id ? "border-sky-400/70 bg-sky-400/15 text-sky-200" : "border-edge text-slate-400 hover:text-sky-200"
              }`}
            >
              {name(e.id)}
            </button>
          ))}
        </div>
      )}
      {error && <p className="mt-1 text-[11px] text-rose-400">{error}</p>}
    </Card>
  );
}

/** An alarm: its time (click to change the time, the days it repeats on and its tune), an
 * on/off switch, and how it repeats. */
function AlarmCard({ widget: w, compact, big }: { widget: Widget; compact: boolean; big: string }) {
  const s = w.state;
  const on = s.status !== "off";
  const [editing, setEditing] = useState<DOMRect | null>(null);
  const timeBtn = useRef<HTMLButtonElement>(null);
  return (
    <Card widget={w} compact={compact}>
      <div className="mt-0.5 flex items-center justify-between">
        <button
          ref={timeBtn}
          onClick={(e) => setEditing(editing ? null : e.currentTarget.getBoundingClientRect())}
          disabled={compact}
          aria-expanded={!!editing}
          title="Change the time, days or tune"
          className={`rounded font-mono ${big} tabular-nums enabled:hover:text-sky-200 ${on ? "text-slate-100" : "text-slate-500"}`}
        >
          {s.time}
        </button>
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
      <p className="mt-1 flex min-w-0 items-center gap-1 text-[11px] text-slate-400">
        <span className="truncate">{repeatText(s.repeat)}</span>
        {!compact && (
          <>
            <span aria-hidden>·</span>
            <Music size={10} className="shrink-0" />
            <span className="truncate">{tuneOf(s.tune).name}</span>
          </>
        )}
      </p>
      {editing && (
        <Popover anchor={editing} width={300} label={`Change ${w.title}`} onClose={() => setEditing(null)} inside={timeBtn}>
          <AlarmEditor widget={w} onDone={() => setEditing(null)} />
        </Popover>
      )}
    </Card>
  );
}

function AlarmEditor({ widget: w, onDone }: { widget: Widget; onDone: () => void }) {
  const [time, setTime] = useState(w.state.time ?? "07:00");
  const [days, setDays] = useState(() => ruleDays(w.state.repeat));
  const [tune, setTune] = useState<TuneId>(tuneOf(w.state.tune).id);
  const [error, setError] = useState("");
  const save = async () => {
    setError("");
    try {
      useStore.getState().upsertWidget(await api.editWidget(w.id, { time, days: daysFor(days), tune }));
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't change it");
    }
  };
  return (
    <div className="space-y-2.5">
      <p className="flex items-center gap-1.5 text-sm font-medium text-slate-100">
        <KindIcon kind="alarm" size={14} /> {w.title}
      </p>
      <input
        type="time"
        value={time}
        onChange={(e) => setTime(e.target.value)}
        aria-label="Alarm time"
        className="w-full rounded-md border border-edge bg-ink px-2 py-1 text-base sm:text-sm"
      />
      <DayPicker days={days} onChange={setDays} />
      <TunePicker tune={tune} onChange={setTune} />
      {error && <p className="text-xs text-rose-400">{error}</p>}
      <div className="flex justify-end gap-1.5">
        <button onClick={onDone} className="rounded-md px-2 py-1 text-xs text-slate-400 hover:text-white">
          Cancel
        </button>
        <button onClick={save} className="rounded-md bg-sky-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-sky-500">
          Save
        </button>
      </div>
    </div>
  );
}

/** Which days an alarm or reminder repeats on: none picked rings once. */
function DayPicker({ days, onChange }: { days: string[]; onChange: (days: string[]) => void }) {
  const toggle = (d: string) => onChange(days.includes(d) ? days.filter((x) => x !== d) : [...days, d]);
  const preset = (list: readonly string[]) => onChange([...list]);
  const chip = "rounded-full border px-2 py-0.5 text-[11px]";
  const off = "border-edge text-slate-400 hover:border-sky-400/60 hover:text-sky-200";
  const onC = "border-sky-400/70 bg-sky-400/15 text-sky-200";
  return (
    <div>
      <div className="flex justify-between gap-0.5" role="group" aria-label="Repeat on">
        {WEEK.map((d) => (
          <button
            key={d}
            type="button"
            onClick={() => toggle(d)}
            aria-pressed={days.includes(d)}
            title={DAY_LONG[d]}
            className={`grid size-6 place-items-center rounded-full border text-[10px] font-medium ${days.includes(d) ? onC : off}`}
          >
            {d[0]}
          </button>
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {(
          [
            ["Once", []],
            ["Every day", WEEK],
            ["Weekdays", WEEK.slice(0, 5)],
            ["Weekends", WEEK.slice(5)],
          ] as const
        ).map(([label, list]) => {
          const picked = list.length === days.length && list.every((d) => days.includes(d));
          return (
            <button key={label} type="button" onClick={() => preset(list)} className={`${chip} ${picked ? onC : off}`}>
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

const DAY_LONG: Record<string, string> = {
  MO: "Monday",
  TU: "Tuesday",
  WE: "Wednesday",
  TH: "Thursday",
  FR: "Friday",
  SA: "Saturday",
  SU: "Sunday",
};

/** The tunes, each played once when picked so you hear what you chose. */
function TunePicker({ tune, onChange }: { tune: TuneId; onChange: (tune: TuneId) => void }) {
  return (
    <div>
      <p className="mb-1 flex items-center gap-1 text-[11px] text-slate-400">
        <Music size={11} /> Tune <span className="text-slate-500">· tap to hear it</span>
      </p>
      <div className="grid grid-cols-3 gap-1" role="radiogroup" aria-label="Tune">
        {TUNES.map((t) => (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={tune === t.id}
            title={t.about}
            onClick={() => {
              onChange(t.id);
              previewTune(t.id);
            }}
            className={`truncate rounded-md border px-1.5 py-1 text-[11px] ${
              tune === t.id
                ? "border-sky-400/70 bg-sky-400/15 text-sky-200"
                : "border-edge text-slate-300 hover:border-sky-400/60"
            }`}
          >
            {t.name}
          </button>
        ))}
      </div>
    </div>
  );
}

function NoteText({ widget }: { widget: Widget }) {
  const [text, setText] = useState(widget.state.text ?? "");
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (focusNote !== widget.id) return;
    focusNote = null;
    area.current?.focus();
  }, [widget.id]);
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
      ref={area}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={save}
      rows={3}
      placeholder="Write something…"
      aria-label={`${widget.title} text`}
      // Fills a card made bigger.
      className="mt-1 w-full resize-none bg-transparent text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none group-data-[sized]:min-h-0 group-data-[sized]:flex-1"
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
 * origin), and the bridge its window.nexus talks through. Calls for data go to the server,
 * which checks them against what you allowed; resize and context are answered here, and so are
 * the app actions (open a link, fork the open chat, a new chat, ask the assistant), which also
 * need to be in its manifest, allowed by you, and pressed: a widget can't do them by itself on
 * a timer. The page hears about the open chat and the theme as a "context" event. */
function CustomWidgetCard({ widget: w, compact }: { widget: Widget; compact: boolean }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [asked, setAsked] = useState<number | null>(null);
  const theme = useCardTheme(frame);
  const sized = useCardSize(w, compact);
  const asks = pendingAsks(w);
  const waiting = asks.hosts.length + asks.data.length + asks.actions.length > 0;
  const size = w.state.manifest?.size ?? "m";
  const actions = useChatActions();
  const chatTitle = useStore((s) => s.chats.find((c) => c.id === actions.chatId)?.title ?? null);
  const context = useMemo(
    () => ({ chat: actions.chatId ? { id: actions.chatId, title: chatTitle } : null, theme, inApp: true }),
    [actions.chatId, chatTitle, theme],
  );
  // The message handler reads these as they are now, not as they were when it was set up.
  const live = useRef({ w, actions, context });
  live.current = { w, actions, context };

  const tell = () =>
    frame.current?.contentWindow?.postMessage({ nexus: 1, event: "context", data: live.current.context }, "*");
  useEffect(tell, [context]);

  useEffect(() => {
    const onMessage = async (e: MessageEvent) => {
      const win = frame.current?.contentWindow;
      const m = e.data;
      if (!win || e.source !== win || !m || m.nexus !== 1 || typeof m.id !== "number") return;
      // The frame's origin is opaque ("null"), so replies can only be addressed with "*"; they
      // go to this frame's window and nowhere else.
      const reply = (body: Record<string, unknown>) => win.postMessage({ nexus: 1, id: m.id, ...body }, "*");
      const now = live.current;
      if (m.call === "resize") {
        setAsked(Number(m.args?.height) || null);
        return reply({ ok: true, result: true });
      }
      if (m.call === "context") return reply({ ok: true, result: now.context });
      if (APP_ACTIONS.includes(m.call)) {
        const action = m.call as AppAction;
        if (!mayAct(now.w, action)) {
          const named = now.w.state.manifest?.actions?.includes(action);
          return reply({
            ok: false,
            error: named ? `${action} isn't allowed yet; tap Allow on the card` : `${action} isn't in this widget's manifest`,
          });
        }
        // A tap in the frame counts as one here too; anything else is the widget acting alone.
        if (navigator.userActivation && !navigator.userActivation.isActive)
          return reply({ ok: false, error: `${action} only works straight after a tap or click` });
        try {
          const text = String(m.args?.text ?? "");
          if (action === "open") {
            const url = safeLink(m.args?.url);
            if (!url) throw new Error("Only https links can be opened");
            window.open(url, "_blank", "noopener,noreferrer");
            return reply({ ok: true, result: true });
          }
          if (action === "ask") {
            await now.actions.ask(text);
            return reply({ ok: true, result: true });
          }
          const chat = action === "chat.fork" ? await now.actions.fork() : await now.actions.newChat(text);
          return reply({ ok: true, result: { id: chat.id, title: chat.title } });
        } catch (err) {
          return reply({ ok: false, error: err instanceof Error ? err.message : "Nexus refused" });
        }
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
      useStore.getState().upsertWidget(await api.approveWidget(w.id, asks.hosts, asks.data, asks.actions));
    } catch {
      /* its event brings the truth */
    }
  };

  const height = compact ? Math.min(frameHeight(size, asked), 140) : frameHeight(size, asked);
  return (
    <Card widget={w} compact={compact}>
      {waiting && !compact && (
        <div className="mt-1.5 shrink-0 rounded-lg border border-amber-400/40 bg-amber-400/10 p-2 text-[11px] text-amber-100">
          <p>
            Wants to{" "}
            {[
              ...asks.hosts.map((h) => `fetch from ${h}`),
              ...asks.data.map((d) => `read ${DATA_LABELS[d] ?? d}`),
              ...asks.actions.map((a) => ACTION_LABELS[a] ?? a),
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
        key={`${w.state.custom_id}:${(w.state.approved?.hosts ?? []).length}:${(w.state.approved?.data ?? []).length}:${(w.state.approved?.actions ?? []).length}`}
        src={theme ? `/api/widgets/${w.id}/frame?theme=${theme}` : undefined}
        title={w.title}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        onLoad={tell}
        // Made bigger by its corner, the page fills the card; else its size class sets it.
        className={`mt-1 block w-full rounded-md border-0 bg-transparent ${sized ? "min-h-0 flex-1" : ""}`}
        style={sized ? undefined : { height }}
      />
      {(w.state.version ?? 1) > 1 && !compact && (
        <button
          onClick={() => press(w, "undo")}
          className="mt-1 flex shrink-0 items-center gap-1 self-start text-[11px] text-slate-500 hover:text-white"
          title="Go back to the previous version"
        >
          <Undo2 size={11} /> Undo last change (v{w.state.version})
        </button>
      )}
    </Card>
  );
}

const QUICK_TIMERS = [1, 5, 10, 25];
// Roughly how tall the add menu is, to decide which way it opens.
const MENU_HEIGHT = 420;
const MENU_WIDTH = 384;

/** A panel drawn over everything beside what opened it (so nothing it sits in can cut it off),
 * closed by a click elsewhere, Escape, or the page moving under it. */
function Popover({
  anchor,
  width,
  alignLeft = false,
  label,
  onClose,
  inside,
  children,
}: {
  anchor: DOMRect;
  width: number;
  alignLeft?: boolean;
  label: string;
  onClose: () => void;
  /** Also counts as inside: the button that opened it. */
  inside?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const within = (t: EventTarget | null) =>
      panel.current?.contains(t as Node) || inside?.current?.contains(t as Node);
    const away = (e: MouseEvent) => !within(e.target) && close.current();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && close.current();
    const moved = (e: Event) => !within(e.target) && close.current();
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    window.addEventListener("resize", moved);
    document.addEventListener("scroll", moved, true);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("resize", moved);
      document.removeEventListener("scroll", moved, true);
    };
  }, [inside]);
  return createPortal(
    <div
      ref={panel}
      role="dialog"
      aria-label={label}
      data-popover
      style={menuPlace(anchor, alignLeft, width)}
      className="fixed z-40 overflow-y-auto rounded-xl border border-edge bg-panel p-3 shadow-2xl"
    >
      {children}
    </div>,
    document.body,
  );
}

/** Where the add menu sits on the page, beside its button: below it unless there's more room
 * above, on the button's right edge (left on the board), and always on screen. It's drawn over
 * everything, so nothing it sits in can cut it off. */
function menuPlace(r: DOMRect, alignLeft: boolean, wanted = MENU_WIDTH) {
  const gap = 6;
  const below = window.innerHeight - r.bottom - gap * 2;
  const above = r.top - gap * 2;
  const up = below < MENU_HEIGHT && above > below;
  const width = Math.min(wanted, window.innerWidth - 16);
  const left = Math.min(Math.max(8, alignLeft ? r.left : r.right - width), window.innerWidth - width - 8);
  return {
    left,
    width,
    maxHeight: Math.max(200, up ? above : below),
    ...(up ? { bottom: window.innerHeight - r.top + gap } : { top: r.bottom + gap }),
  };
}
// Time zones offered for a world clock, beside your own.
const ZONES = [
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Los_Angeles",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];
// The order of the tiles in the add menu.
const MENU: Exclude<Widget["kind"], "custom">[] = ["timer", "alarm", "reminder", "stopwatch", "clock", "note", "search"];
type MenuKind = (typeof MENU)[number] | "custom";

/** A tile being dragged out of the add menu, and where the pointer is. */
const useTileDrag = create<{ tile: { kind: MenuKind; x: number; y: number; over: boolean } | null }>(() => ({
  tile: null,
}));

/** The + on the dock: a menu of every kind of widget, each told apart by its colour and a line
 * on what it does. Click a tile to set it up (a stopwatch or note just appears); on a laptop's
 * main screen, drag a tile out and drop it where you want it. */
function AddWidget({ empty, compact, board = false }: { empty: boolean; compact: boolean; board?: boolean }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<DOMRect | null>(null);
  const [pick, setPick] = useState<MenuKind | null>(null);
  const [tab, setTab] = useState<"new" | "library">("new");
  const [minutes, setMinutes] = useState("");
  const [time, setTime] = useState("07:00");
  const [days, setDays] = useState<string[]>([]);
  const [tune, setTune] = useState<TuneId>(lastTune);
  const [text, setText] = useState("");
  const [remindAt, setRemindAt] = useState("18:00");
  const [zones, setZones] = useState<string[]>([]);
  const [error, setError] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const tile = useTileDrag((s) => s.tile);
  const assistant = useAssistantName();
  const { send } = useSend();
  const own = Intl.DateTimeFormat().resolvedOptions().timeZone;
  // Tiles can be dropped on the screen only where widgets can be left: a laptop's main screen.
  const droppable = useWide() && !board;

  useEffect(() => {
    if (!open) {
      setPick(null);
      setTab("new");
    }
  }, [open]);

  const pickTune = (t: TuneId) => {
    setTune(t);
    rememberTune(t);
  };

  const make = async (body: WidgetInput) => {
    setError("");
    try {
      const w = await api.addWidget(body);
      if (w.kind === "note") focusNote = w.id;
      useStore.getState().upsertWidget(w);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't add it");
    }
  };

  /** What a tile makes as it is set up now: dragging the alarm tile drops the alarm with the
   * time and tune picked in it. Null when it needs more first (a reminder's text). */
  const bodyFor = (kind: MenuKind): WidgetInput | null => {
    if (kind === "timer") return { kind, minutes: Number(minutes) > 0 ? Number(minutes) : 5, tune };
    if (kind === "alarm") return { kind, time, days: daysFor(days) ?? undefined, tune };
    if (kind === "reminder")
      return text.trim() ? { kind, text: text.trim(), time: remindAt, days: daysFor(days) ?? undefined } : null;
    if (kind === "clock") return { kind, zones: [own, ...zones.filter((z) => z !== own)].slice(0, 4) };
    if (kind === "custom") return null;
    return { kind };
  };

  const choose = (kind: MenuKind) => {
    setError("");
    if (kind === "stopwatch" || kind === "note" || kind === "search") make({ kind });
    else setPick(kind);
  };

  /** Drag a tile onto the main screen: the widget is made where it is dropped. A press that
   * doesn't move is a click. */
  const startTile = (e: ReactPointerEvent, kind: MenuKind) => {
    if (e.button !== 0 || !droppable || !room) return;
    const from = { x: e.clientX, y: e.clientY };
    let moved = false;
    const inRoom = (x: number, y: number) => {
      const r = room?.getBoundingClientRect();
      const m = document.querySelector("[data-popover]")?.getBoundingClientRect();
      const onMenu = m && x >= m.left && x <= m.right && y >= m.top && y <= m.bottom;
      return !!r && !onMenu && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
    };
    const move = (ev: PointerEvent) => {
      if (!moved && Math.hypot(ev.clientX - from.x, ev.clientY - from.y) < 6) return;
      moved = true;
      document.body.classList.add("widget-dragging");
      useTileDrag.setState({ tile: { kind, x: ev.clientX, y: ev.clientY, over: inRoom(ev.clientX, ev.clientY) } });
    };
    const done = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", done);
      window.removeEventListener("pointercancel", done);
      document.body.classList.remove("widget-dragging");
      useTileDrag.setState({ tile: null });
      if (!moved) return;
      const body = bodyFor(kind);
      if (!body) return setPick(kind);
      if (ev.type !== "pointerup" || !room || !inRoom(ev.clientX, ev.clientY)) return;
      const r = room.getBoundingClientRect();
      const card = { width: compact ? 176 : 224, height: 110 };
      const pos = placeIn(ev.clientX - card.width / 2 - r.left, ev.clientY - 18 - r.top, r, card);
      make({ ...body, pos });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", done);
    window.addEventListener("pointercancel", done);
  };

  const chip = "rounded-full border border-edge px-2 py-0.5 text-xs hover:border-sky-400/60 hover:text-sky-200";
  const field = "w-full rounded-md border border-edge bg-ink px-2 py-1 text-base sm:text-sm";
  const go = "rounded-md bg-sky-600 px-3 py-1 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-40";

  const pane = () => {
    if (pick === "timer")
      return (
        <div className="space-y-2.5">
          <div className="flex flex-wrap gap-1.5">
            {QUICK_TIMERS.map((m) => (
              <button key={m} className={chip} onClick={() => make({ kind: "timer", minutes: m, tune })}>
                {m} min
              </button>
            ))}
          </div>
          <form
            className="flex items-center gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (Number(minutes) > 0) make({ kind: "timer", minutes: Number(minutes), tune });
            }}
          >
            <input
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              inputMode="decimal"
              placeholder="Minutes"
              aria-label="Timer minutes"
              className={field}
            />
            <button className={go} disabled={!(Number(minutes) > 0)}>
              Start
            </button>
          </form>
          <TunePicker tune={tune} onChange={pickTune} />
        </div>
      );
    if (pick === "alarm")
      return (
        <form
          className="space-y-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            make({ kind: "alarm", time, days: daysFor(days) ?? undefined, tune });
          }}
        >
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Alarm time" className={field} />
          <DayPicker days={days} onChange={setDays} />
          <TunePicker tune={tune} onChange={pickTune} />
          <div className="flex justify-end">
            <button className={go}>Set alarm</button>
          </div>
        </form>
      );
    if (pick === "reminder")
      return (
        <form
          className="space-y-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            const body = bodyFor("reminder");
            if (body) make(body);
          }}
        >
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Remind me to…"
            aria-label="What to remind you of"
            maxLength={500}
            className={field}
            autoFocus
          />
          <label className="flex items-center gap-2 text-xs text-slate-400">
            At
            <input type="time" value={remindAt} onChange={(e) => setRemindAt(e.target.value)} aria-label="Reminder time" className={field} />
          </label>
          <DayPicker days={days} onChange={setDays} />
          <div className="flex justify-end">
            <button className={go} disabled={!text.trim()}>
              Add reminder
            </button>
          </div>
        </form>
      );
    if (pick === "clock")
      return (
        <div className="space-y-2.5">
          <p className="text-xs text-slate-400">
            Shows {zoneCity(own)} (your time) and up to three more. Pick them:
          </p>
          <div className="flex flex-wrap gap-1.5">
            {ZONES.filter((z) => z !== own).map((z) => {
              const on = zones.includes(z);
              return (
                <button
                  key={z}
                  type="button"
                  aria-pressed={on}
                  disabled={!on && zones.length >= 3}
                  onClick={() => setZones(on ? zones.filter((x) => x !== z) : [...zones, z])}
                  className={`${chip} disabled:opacity-40 ${on ? "border-sky-400/70 bg-sky-400/15 text-sky-200" : ""}`}
                >
                  {zoneCity(z)}
                </button>
              );
            })}
          </div>
          <div className="flex justify-end">
            <button className={go} onClick={() => make(bodyFor("clock")!)}>
              Add clock
            </button>
          </div>
        </div>
      );
    if (pick === "custom")
      return (
        <form
          className="space-y-2.5"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!text.trim()) return;
            if (await send(`Make me a custom widget: ${text.trim()}`, [])) {
              setText("");
              setOpen(false);
            }
          }}
        >
          <p className="text-xs text-slate-400">
            Describe it and {assistant} builds it, then puts it here: a countdown, a habit tracker, a weather card, a
            pixel-art pet…
          </p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            placeholder="A little pixel-art cat that naps and stretches"
            aria-label="Describe your widget"
            className={`${field} resize-none`}
            autoFocus
          />
          <div className="flex justify-end">
            <button className={`${go} flex items-center gap-1`} disabled={!text.trim()}>
              <Send size={12} /> Ask {assistant}
            </button>
          </div>
        </form>
      );
    return null;
  };

  return (
    <div ref={box} className="relative shrink-0">
      <button
        onClick={(e) => {
          setAt(e.currentTarget.getBoundingClientRect());
          setOpen((v) => !v);
        }}
        aria-expanded={open}
        title="Add a widget"
        className={`flex items-center gap-1.5 rounded-xl border border-dashed border-edge bg-panel/60 px-3 py-2 text-xs text-slate-400 backdrop-blur hover:border-sky-400/60 hover:text-sky-200 ${
          empty || board ? "" : "lg:w-full lg:justify-center"
        }`}
      >
        <Plus size={13} /> {empty ? "Add a widget" : <span className={compact ? "sr-only lg:not-sr-only" : ""}>Add</span>}
      </button>
      {open && at && (
        <Popover anchor={at} width={MENU_WIDTH} alignLeft={board} label="Add a widget" onClose={() => setOpen(false)} inside={box}>
          <div className="mb-2 flex items-center gap-2">
            {pick ? (
              <>
                <button
                  onClick={() => setPick(null)}
                  aria-label="Back to all widgets"
                  className="rounded p-0.5 text-slate-400 hover:text-white"
                >
                  <ArrowLeft size={15} />
                </button>
                <span className={`grid size-6 place-items-center rounded-md ${KINDS[pick].tone}`}>
                  <KindIcon kind={pick} size={14} />
                </span>
                <span className="text-sm font-medium text-slate-100">{KINDS[pick].name}</span>
              </>
            ) : (
              <div className="flex rounded-lg border border-edge p-0.5 text-xs" role="tablist" aria-label="Add a widget">
                {(
                  [
                    ["new", "New widget", Plus],
                    ["library", "Your library", Library],
                  ] as const
                ).map(([id, label, Icon]) => (
                  <button
                    key={id}
                    role="tab"
                    aria-selected={tab === id}
                    onClick={() => setTab(id)}
                    className={`flex items-center gap-1 rounded-md px-2 py-1 ${
                      tab === id ? "bg-edge text-slate-100" : "text-slate-400 hover:text-slate-100"
                    }`}
                  >
                    <Icon size={12} /> {label}
                  </button>
                ))}
              </div>
            )}
            <button
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="ml-auto rounded p-0.5 text-slate-500 hover:text-white"
            >
              <X size={14} />
            </button>
          </div>
          {pick ? (
            pane()
          ) : tab === "library" ? (
            <LibraryPane assistant={assistant} onAsk={() => setPick("custom")} />
          ) : (
            <>
              <p className="mb-2 text-[11px] text-slate-500">
                {droppable ? "Click one to set it up, or drag it onto the screen where you want it." : "Pick one to set it up."}
              </p>
              <div className="grid grid-cols-2 gap-1.5">
                {[...MENU, "custom" as const].map((kind) => (
                  <button
                    key={kind}
                    onClick={() => choose(kind)}
                    onPointerDown={kind === "custom" ? undefined : (e) => startTile(e, kind)}
                    title={KINDS[kind].about}
                    className={`flex min-w-0 items-center gap-2 rounded-lg border border-edge bg-ink/40 p-2 text-left hover:border-sky-400/60 hover:bg-edge/40 ${
                      droppable && kind !== "custom" ? "cursor-grab active:cursor-grabbing" : ""
                    } ${tile?.kind === kind ? "opacity-40" : ""}`}
                  >
                    <span className={`grid size-7 shrink-0 place-items-center rounded-md ${KINDS[kind].tone}`}>
                      <KindIcon kind={kind} size={15} />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-sm text-slate-100">{KINDS[kind].name}</span>
                      <span className="block truncate text-[11px] leading-snug text-slate-400">{KINDS[kind].about}</span>
                    </span>
                  </button>
                ))}
              </div>
              <p className="mt-2 text-[11px] text-slate-500">
                Or just ask {assistant}: "wake me at 7 on weekdays", "remind me to call mum at 6".
              </p>
            </>
          )}
          {error && <p className="pt-2 text-xs text-rose-400">{error}</p>}
        </Popover>
      )}
      {tile && <TileGhost kind={tile.kind} x={tile.x} y={tile.y} over={tile.over} />}
    </div>
  );
}

/** Your widget library, the other side of the add menu: every widget you removed, waiting with
 * everything on it (a note's text, a clock's cities, a custom widget's versions and what you
 * allowed it), newest first. Put one back on the screen, or delete it for good. Custom widgets
 * stay until you delete them; of the rest the newest 40 are kept. */
function LibraryPane({ assistant, onAsk }: { assistant: string; onAsk: () => void }) {
  const [items, setItems] = useState<Widget[] | null>(null);
  const [error, setError] = useState("");
  const [sure, setSure] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const now = useNow(60_000);
  useEffect(() => {
    let live = true;
    api
      .widgetLibrary()
      .then((list) => live && setItems(list))
      .catch(() => live && setError("Couldn't load your library"));
    return () => {
      live = false;
    };
  }, []);

  const act = async (w: Widget, action: "restore" | "forget") => {
    setError("");
    setBusy(w.id);
    try {
      const back = await api.widgetAction(w.id, action);
      if (back) useStore.getState().upsertWidget(back);
      setItems((list) => (list ?? []).filter((x) => x.id !== w.id));
      setSure(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't do that");
    } finally {
      setBusy(null);
    }
  };

  if (items === null)
    return <p className="py-6 text-center text-xs text-slate-500">{error || "Loading your library…"}</p>;
  const made = items.filter((w) => w.kind === "custom");
  const rest = items.filter((w) => w.kind !== "custom");
  const row = (w: Widget) => (
    <li key={w.id} className="flex items-center gap-2 rounded-lg border border-edge bg-ink/40 p-2">
      <span className={`grid size-7 shrink-0 place-items-center rounded-md ${KINDS[w.kind].tone}`}>
        <KindIcon kind={w.kind} size={15} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-slate-100">{w.title}</span>
        <span className="block truncate text-[11px] text-slate-400">
          {[`Removed ${ago(w.removed_at, now)}`, libraryLine(w)].filter(Boolean).join(" · ")}
        </span>
      </span>
      {sure === w.id ? (
        <span className="flex shrink-0 items-center gap-1">
          <button
            onClick={() => act(w, "forget")}
            disabled={busy === w.id}
            className="rounded-md bg-rose-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-rose-500 disabled:opacity-50"
          >
            Delete
          </button>
          <button onClick={() => setSure(null)} className="rounded-md px-1.5 py-1 text-[11px] text-slate-400 hover:text-white">
            Keep
          </button>
        </span>
      ) : (
        <span className="flex shrink-0 items-center gap-0.5">
          <button
            onClick={() => act(w, "restore")}
            disabled={busy === w.id}
            className="flex items-center gap-1 rounded-md bg-sky-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-sky-500 disabled:opacity-50"
            title="Put it back on the screen, as it was"
          >
            <Undo2 size={11} /> Put back
          </button>
          <button
            onClick={() => setSure(w.id)}
            aria-label={`Delete ${w.title} for good`}
            title="Delete for good"
            className="rounded-md p-1 text-slate-500 hover:text-rose-300"
          >
            <Trash2 size={13} />
          </button>
        </span>
      )}
    </li>
  );
  return (
    <div>
      {items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-edge p-4 text-center">
          <Library size={18} className="mx-auto text-slate-500" />
          <p className="mt-2 text-sm text-slate-300">Nothing put away yet</p>
          <p className="mt-1 text-[11px] text-slate-500">
            When you remove a widget with its ×, it waits here with everything on it, ready to put back. Widgets{" "}
            {assistant} makes for you live here too once you remove them.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {made.length > 0 && (
            <section>
              <p className="mb-1 flex items-center gap-1 text-[11px] font-medium text-slate-400">
                <Sparkles size={11} className="text-purple-300" /> Made for you
              </p>
              <ul className="space-y-1">{made.map(row)}</ul>
            </section>
          )}
          {rest.length > 0 && (
            <section>
              <p className="mb-1 text-[11px] font-medium text-slate-400">Removed</p>
              <ul className="space-y-1">{rest.map(row)}</ul>
            </section>
          )}
        </div>
      )}
      <p className="mt-2 text-[11px] text-slate-500">
        Custom widgets stay until you delete them; the 40 newest of the others are kept.{" "}
        <button onClick={onAsk} className="text-sky-300 hover:underline">
          Ask {assistant} for a new one
        </button>
        .
      </p>
      {error && <p className="pt-2 text-xs text-rose-400">{error}</p>}
    </div>
  );
}

/** The tile following the pointer while it is dragged out of the add menu. */
function TileGhost({ kind, x, y, over }: { kind: MenuKind; x: number; y: number; over: boolean }) {
  return (
    <div
      className={`pointer-events-none fixed z-50 flex w-44 items-center gap-2 rounded-xl border bg-panel/95 px-3 py-2 shadow-2xl backdrop-blur ${
        over ? "border-sky-400" : "border-edge opacity-70"
      }`}
      style={{ left: x, top: y, transform: "translate(-50%, -50%)" }}
    >
      <span className={`grid size-7 place-items-center rounded-md ${KINDS[kind].tone}`}>
        <KindIcon kind={kind} size={15} />
      </span>
      <span className="text-sm text-slate-100">{KINDS[kind].name}</span>
      <span className="ml-auto text-[10px] text-slate-400">{over ? "Drop here" : "Drag onto the screen"}</span>
    </div>
  );
}

/** The ringing widget's tune, played over and over until it's answered; an alarm starts softer
 * and grows. Browsers allow sound only after the person has used the page, so a silent first
 * ring is expected on a fresh tab. */
function useChime(tune: TuneId | null, grows: boolean) {
  useEffect(() => {
    if (!tune) return;
    let ctx: AudioContext | null = null;
    try {
      ctx = new AudioContext();
    } catch {
      return;
    }
    const t = tuneOf(tune);
    const began = Date.now();
    const ring = () => {
      if (!ctx) return;
      playTune(ctx, t, grows ? swell((Date.now() - began) / 1000) : 1);
      navigator.vibrate?.([200, 100, 200]);
    };
    ring();
    const timer = window.setInterval(ring, t.every * 1000);
    return () => {
      window.clearInterval(timer);
      ctx?.close().catch(() => {});
    };
  }, [tune, grows]);
}

/** What's ringing, over everything, until it's snoozed or dismissed. */
export function RingOverlay() {
  const list = useWidgetList();
  const now = useNow();
  const ringing = list.filter((w) => isRinging(w, now));
  const w = ringing[0];
  useChime(w ? tuneOf(w.state.tune).id : null, w?.kind === "alarm");
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
