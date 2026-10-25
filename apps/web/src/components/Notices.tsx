import { AlarmClock, AlertTriangle, Bell, BellRing, CheckCircle2, Hand, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../lib/api";
import { formatAgo } from "../lib/format";
import type { Notice } from "../lib/types";
import { useStore } from "../store/chat";

/** How long a toast stays on screen unless the pointer is on it. */
const TOAST_MS = 8000;

const ICON = {
  result: <CheckCircle2 size={16} className="shrink-0 text-emerald-400" />,
  failed: <AlertTriangle size={16} className="shrink-0 text-rose-400" />,
  approval: <Hand size={16} className="shrink-0 text-amber-300" />,
  timer: <AlarmClock size={16} className="shrink-0 text-sky-300" />,
  reminder: <BellRing size={16} className="shrink-0 text-violet-300" />,
};

/** Load the bell's tray when signed in, and again after a reconnect in case notices were
 * missed while offline. */
export function useNoticeTray() {
  const connected = useStore((s) => s.connected);
  const setNotices = useStore((s) => s.setNotices);
  useEffect(() => {
    if (!connected) return;
    api
      .notifications()
      .then((t) => setNotices(t.items, t.unread))
      .catch(() => {});
  }, [connected, setNotices]);
}

/** Open a notice: go where it points and, for a result file, open it on the main screen. */
export function useOpenNotice() {
  const navigate = useNavigate();
  const { markNoticesRead, openViewer } = useStore.getState();
  return (n: Notice) => {
    if (!n.read_at) {
      markNoticesRead([n.id]);
      api.readNotifications([n.id]).catch(() => {});
    }
    if (n.file) openViewer(n.file, n.chat_id);
    else if (n.link?.kind === "app") window.open(n.link.url, "_blank", "noopener");
    navigate(n.url);
  };
}

/** Toasts at the top of the screen for notices that arrive while the app is open. */
export function NoticeToasts() {
  const toasts = useStore((s) => s.toasts);
  const notices = useStore((s) => s.notices);
  const shown = toasts
    .map((id) => notices.find((n) => n.id === id))
    .filter((n): n is Notice => !!n)
    .slice(-3);
  if (shown.length === 0) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-3 z-50 flex flex-col items-center gap-2 px-4">
      {shown.map((n) => (
        <Toast key={n.id} notice={n} />
      ))}
    </div>
  );
}

function Toast({ notice }: { notice: Notice }) {
  const dismiss = useStore((s) => s.dismissToast);
  const open = useOpenNotice();
  const [hover, setHover] = useState(false);

  useEffect(() => {
    if (hover) return;
    const t = window.setTimeout(() => dismiss(notice.id), TOAST_MS);
    return () => window.clearTimeout(t);
  }, [hover, notice.id, dismiss]);

  const action = notice.file ? "Open" : notice.link?.kind === "app" ? "Open app" : "View";
  return (
    <div
      role="status"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-xl border border-edge bg-panel/95 px-3 py-2.5 shadow-lg backdrop-blur"
    >
      <span className="mt-0.5">{ICON[notice.kind]}</span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-slate-100">{notice.title}</p>
        {(notice.file || notice.body) && (
          <p className="truncate text-xs text-slate-400">{notice.file?.name ?? notice.body}</p>
        )}
      </div>
      <button
        onClick={() => {
          dismiss(notice.id);
          open(notice);
        }}
        className="shrink-0 rounded-md bg-sky-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-sky-500"
      >
        {action}
      </button>
      <button
        onClick={() => dismiss(notice.id)}
        aria-label="Dismiss"
        className="shrink-0 p-0.5 text-slate-500 hover:text-white"
      >
        <X size={14} />
      </button>
    </div>
  );
}

/** The bell: how many notices are unread, and the latest ones in a tray. */
export function NoticeBell({ className = "" }: { className?: string }) {
  const notices = useStore((s) => s.notices);
  const unread = useStore((s) => s.unread);
  const markNoticesRead = useStore((s) => s.markNoticesRead);
  const open = useOpenNotice();
  const [show, setShow] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!show) return;
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setShow(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setShow(false);
    document.addEventListener("mousedown", away);
    window.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      window.removeEventListener("keydown", esc);
    };
  }, [show]);

  const readAll = () => {
    markNoticesRead(null);
    api.readNotifications().catch(() => {});
  };

  return (
    <div ref={box} className={`relative ${className}`}>
      <button
        onClick={() => setShow((v) => !v)}
        aria-label={unread ? `Notices, ${unread} unread` : "Notices"}
        title="Notices"
        aria-expanded={show}
        className="relative rounded-lg border border-edge bg-panel/80 p-1.5 text-slate-400 backdrop-blur hover:text-white"
      >
        <Bell size={14} />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-sky-500 px-1 text-center text-[10px] font-semibold leading-4 text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {show && (
        <div className="absolute right-0 top-full z-30 mt-1 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-edge bg-panel shadow-xl">
          <div className="flex items-center justify-between border-b border-edge px-3 py-2">
            <span className="text-sm font-medium">Notices</span>
            {unread > 0 && (
              <button onClick={readAll} className="text-xs text-sky-300 hover:text-sky-200">
                Mark all read
              </button>
            )}
          </div>
          {notices.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-slate-400">
              Nothing yet. When a team finishes, fails or needs your approval, it shows up here and
              its result opens on your screen.
            </p>
          ) : (
            <ul className="max-h-96 overflow-y-auto">
              {notices.map((n) => (
                <li key={n.id}>
                  <button
                    onClick={() => {
                      setShow(false);
                      open(n);
                    }}
                    className={`flex w-full items-start gap-2.5 px-3 py-2 text-left hover:bg-edge/60 ${n.read_at ? "opacity-70" : ""}`}
                  >
                    <span className="mt-0.5">{ICON[n.kind]}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-slate-100">{n.title}</span>
                      <span className="block truncate text-xs text-slate-400">
                        {n.file?.name ?? n.link?.title ?? n.body}
                      </span>
                    </span>
                    <span className="shrink-0 text-[11px] text-slate-500">{formatAgo(n.created_at)}</span>
                    {!n.read_at && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-sky-400" aria-label="Unread" />}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** When a team's result arrives for the chat that's open on screen, open it by itself. Only
 * then: a result for another chat, or one that lands while the tab is hidden, waits as a
 * toast and in the tray instead of taking over the screen. */
export function useAutoOpenResult(chatId: string | undefined) {
  const fresh = useStore((s) => s.freshNotice);
  const seen = useRef<string | null>(fresh?.id ?? null);
  useEffect(() => {
    if (!fresh || fresh.id === seen.current) return;
    seen.current = fresh.id;
    if (fresh.kind !== "result" || !fresh.file || !chatId || fresh.chat_id !== chatId) return;
    if (document.visibilityState !== "visible") return;
    const { openViewer, dismissToast, markNoticesRead } = useStore.getState();
    openViewer(fresh.file, chatId);
    dismissToast(fresh.id);
    markNoticesRead([fresh.id]);
    api.readNotifications([fresh.id]).catch(() => {});
  }, [fresh, chatId]);
}
