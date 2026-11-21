import {
  AppWindow,
  ArrowRight,
  Bot,
  Brain,
  CalendarClock,
  CheckSquare,
  CornerDownLeft,
  FolderKanban,
  Inbox,
  FileText,
  Link2,
  Loader2,
  MessageSquare,
  NotebookPen,
  Search,
  Sparkles,
  User as UserIcon,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useNavigate } from "react-router";
import { create } from "zustand";
import { api } from "../lib/api";
import { toast } from "./Feedback";
import { useAssistantName } from "../lib/assistant";
import { COMMANDS, KIND_LABELS, groupResults, matchCommands, snippetParts } from "../lib/find";
import { hasRole, isOwner, type FindKind, type FindResponse } from "../lib/types";
import { useSend } from "../lib/useSend";
import { useStore } from "../store/chat";

/** Open or close the bar from anywhere (the sidebar button, the phone header, Cmd-K). */
export const useCommandBar = create<{ open: boolean; setOpen: (open: boolean) => void }>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));

export const KIND_ICONS: Record<FindKind, LucideIcon> = {
  chat: MessageSquare,
  task: CheckSquare,
  note: NotebookPen,
  project: FolderKanban,
  file: FileText,
  memory: Brain,
  person: UserIcon,
  thing: Sparkles,
  link: Link2,
  routine: CalendarClock,
  app: AppWindow,
  system: Bot,
  skill: Zap,
};

/** Matched words in bold, the way the server marked them. */
export function Snippet({ text }: { text: string }) {
  return (
    <>
      {snippetParts(text).map((p, i) =>
        p.hit ? (
          <mark key={i} className="rounded-sm bg-sky-400/20 px-0.5 text-sky-100">
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
export const SHORTCUT = isMac ? "⌘K" : "Ctrl K";

/** Is this key press Cmd-K (Ctrl-K)? By the key's place too, so it works on any layout. */
export function isSearchKey(e: Pick<KeyboardEvent, "metaKey" | "ctrlKey" | "altKey" | "key" | "code">): boolean {
  return (e.metaKey || e.ctrlKey) && !e.altKey && (e.code === "KeyK" || (e.key ?? "").toLowerCase() === "k");
}

/** Cmd-K (Ctrl-K) anywhere opens the bar; "/" does too when not typing in a field. Listened for
 * in the capture phase, so no page, editor or chart that handles keys itself can swallow it. */
export function useCommandBarShortcut() {
  const setOpen = useCommandBar((s) => s.setOpen);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isSearchKey(e)) {
        e.preventDefault();
        e.stopPropagation();
        if (!e.repeat) setOpen(!useCommandBar.getState().open);
        return;
      }
      const t = e.target as HTMLElement | null;
      const typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
      if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey && !e.defaultPrevented) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [setOpen]);
}

interface Row {
  key: string;
  section: string;
  icon: LucideIcon;
  label: string;
  detail?: string;
  snippet?: string;
  run: () => void;
}

const DEBOUNCE_MS = 160;

export function CommandBar() {
  const { open, setOpen } = useCommandBar();
  if (!open) return null;
  return <Bar onClose={() => setOpen(false)} />;
}

function Bar({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const user = useStore((s) => s.user)!;
  const chats = useStore((s) => s.chats);
  const assistant = useAssistantName();
  const { send } = useSend();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<FindResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const q = query.trim();

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => before?.focus?.();
  }, []);

  // Ask the server after a short pause; a newer query cancels the one in flight.
  useEffect(() => {
    if (!q) {
      setFound(null);
      setLoading(false);
      setError("");
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      api
        .find(q, { limit: 4, signal: ctrl.signal })
        .then((r) => {
          setFound(r);
          setError("");
        })
        .catch((e) => {
          if ((e as Error).name !== "AbortError") setError((e as Error).message);
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, [q]);

  const go = useCallback(
    (to: string) => {
      onClose();
      navigate(to);
    },
    [navigate, onClose],
  );

  const rows = useMemo<Row[]>(() => {
    const allowed = COMMANDS.filter(
      (c) => (!c.owner || isOwner(user)) && (!c.admin || hasRole(user, "admin")),
    );
    const out: Row[] = [];
    const commands = matchCommands(q, allowed).slice(0, q ? 4 : allowed.length);
    if (!q) {
      for (const c of commands.filter((c) => c.section === "Actions"))
        out.push({ key: c.id, section: "Actions", icon: ArrowRight, label: c.label, run: () => go(c.to!) });
      for (const c of chats.slice(0, 5))
        out.push({
          key: `recent-${c.id}`,
          section: "Recent chats",
          icon: MessageSquare,
          label: c.title || "New chat",
          run: () => go(`/c/${c.id}`),
        });
      for (const c of commands.filter((c) => c.section === "Go to"))
        out.push({ key: c.id, section: "Go to", icon: ArrowRight, label: c.label, run: () => go(c.to!) });
      return out;
    }
    for (const g of groupResults(found?.results ?? [])) {
      for (const r of g.items)
        out.push({
          key: `${r.kind}-${r.id}`,
          section: KIND_LABELS[g.kind],
          icon: KIND_ICONS[g.kind],
          label: r.title,
          detail: r.detail,
          snippet: r.snippet,
          run: () => go(r.url),
        });
    }
    for (const c of commands)
      out.push({ key: c.id, section: "Pages and actions", icon: ArrowRight, label: c.label, run: () => go(c.to!) });
    if (hasRole(user, "member"))
      // A to-do typed straight in ("[ ] call the bank") goes first, so Enter captures it.
      out[/^\s*(\[\s?\]|todo:?\s|task:)/i.test(q) ? "unshift" : "push"]({
        key: "capture",
        section: "Organiser",
        icon: Inbox,
        label: `Capture to Inbox: “${q}”`,
        detail: "Saved in the Organiser to file later",
        run: () => {
          onClose();
          api
            .orgCapture(q)
            .then((item) =>
              toast(`In your Inbox: ${item.type} “${item.title}”${item.due_on ? `, due ${item.due_on}` : ""}`),
            )
            .catch((e: Error) => toast(`Not captured: ${e.message}`, "error"));
        },
      });
    if (hasRole(user, "member"))
      out.push({
        key: "ask",
        section: `Ask ${assistant}`,
        icon: Sparkles,
        label: `Ask ${assistant}: “${q}”`,
        detail: "Starts a new chat with this as the first message",
        run: () => {
          onClose();
          void send(q, []);
        },
      });
    out.push({
      key: "all",
      section: "More",
      icon: Search,
      label: `See all results for “${q}”`,
      run: () => go(`/search?q=${encodeURIComponent(q)}`),
    });
    return out;
  }, [q, found, chats, user, assistant, go, send, onClose]);

  useEffect(() => setActive(0), [q, found]);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      rows[active]?.run();
    }
  };

  let lastSection = "";
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm sm:px-4 sm:pt-[12vh]"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search everything"
        className="flex h-full w-full flex-col overflow-hidden border-edge bg-panel shadow-2xl sm:h-auto sm:max-h-[70vh] sm:max-w-2xl sm:rounded-xl sm:border"
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-3 border-b border-edge px-4 py-3">
          {loading ? (
            <Loader2 size={18} className="shrink-0 animate-spin text-slate-400" />
          ) : (
            <Search size={18} className="shrink-0 text-slate-400" />
          )}
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chats, files, memories, people… or jump to a page"
            className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-slate-500"
            style={{ outline: "none" }}
            role="combobox"
            aria-expanded="true"
            aria-controls="command-bar-list"
            aria-activedescendant={rows[active] ? `cmd-${rows[active].key}` : undefined}
            maxLength={200}
            enterKeyHint="search"
          />
          <button
            onClick={onClose}
            className="rounded-md border border-edge px-2 py-0.5 text-xs text-slate-400 hover:text-white"
          >
            <span className="hidden sm:inline">Esc</span>
            <span className="sm:hidden">Close</span>
          </button>
        </div>
        <div ref={list} id="command-bar-list" role="listbox" className="flex-1 overflow-y-auto py-2">
          {error && <p className="px-4 py-2 text-sm text-rose-400">{error}</p>}
          {q && found && !found.results.length && !loading && (
            <p className="px-4 py-3 text-sm text-slate-400">
              Nothing in your chats, files, memories or people matches “{q}”.
              {hasRole(user, "member") && ` Press Enter on “Ask ${assistant}” below to ask instead.`}
            </p>
          )}
          {rows.map((row, i) => {
            const heading = row.section !== lastSection ? row.section : null;
            lastSection = row.section;
            const Icon = row.icon;
            return (
              <div key={row.key}>
                {heading && (
                  <div className="px-4 pb-1 pt-3 text-xs font-medium uppercase tracking-wide text-slate-500">
                    {heading}
                  </div>
                )}
                <div
                  id={`cmd-${row.key}`}
                  role="option"
                  aria-selected={i === active}
                  data-index={i}
                  onMouseMove={() => i !== active && setActive(i)}
                  onClick={row.run}
                  className={`mx-2 flex cursor-pointer items-start gap-3 rounded-lg px-3 py-2 ${
                    i === active ? "bg-edge text-white" : "text-slate-300"
                  }`}
                >
                  <Icon size={16} className="mt-0.5 shrink-0 text-slate-400" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="truncate">
                        <Snippet text={row.label} />
                      </span>
                      {row.detail && (
                        <span className="hidden shrink-0 truncate text-xs text-slate-500 sm:inline">
                          {row.detail}
                        </span>
                      )}
                    </div>
                    {row.snippet && (
                      <div className="line-clamp-2 text-sm text-slate-400">
                        <Snippet text={row.snippet} />
                      </div>
                    )}
                  </div>
                  {i === active && <CornerDownLeft size={14} className="mt-1 shrink-0 text-slate-500" />}
                </div>
              </div>
            );
          })}
        </div>
        <div className="hidden items-center gap-4 border-t border-edge px-4 py-2 text-xs text-slate-500 sm:flex">
          <span>↑↓ to move</span>
          <span>Enter to open</span>
          <span>Esc to close</span>
          <span className="ml-auto">
            {found && q
              ? found.semantic
                ? "Matching words and meaning"
                : "Matching words only (meaning search is offline)"
              : `${SHORTCUT} or / opens this anywhere`}
          </span>
        </div>
      </div>
    </div>
  );
}
