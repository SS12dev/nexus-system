import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useEffect, useRef, useSyncExternalStore } from "react";

/** App-wide confirmations and toasts, drawn like the rest of the app instead of the browser's
 * own confirm() box (which installed PWAs show as a bare system alert, and which can't mark a
 * destructive action). Call `confirmAction` or `toast` from anywhere; <FeedbackHost /> draws
 * them. */

type Confirm = {
  title: string;
  body?: string;
  confirmLabel?: string;
  /** Red button, for actions that delete or can't be undone. */
  danger?: boolean;
  resolve: (ok: boolean) => void;
};
type ToastKind = "success" | "error" | "info";
type Toast = { id: number; kind: ToastKind; message: string };
type State = { confirm: Confirm | null; toasts: Toast[] };

let state: State = { confirm: null, toasts: [] };
const listeners = new Set<() => void>();
const emit = (next: Partial<State>) => {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
};
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export function confirmAction(opts: Omit<Confirm, "resolve">): Promise<boolean> {
  // A second request replaces an open one, which counts as cancelled.
  state.confirm?.resolve(false);
  return new Promise((resolve) => emit({ confirm: { ...opts, resolve } }));
}

let nextId = 1;
export function toast(message: string, kind: ToastKind = "success") {
  const id = nextId++;
  emit({ toasts: [...state.toasts.slice(-3), { id, kind, message }] });
  // Errors stay longer: they're more likely to need reading twice.
  window.setTimeout(() => dismiss(id), kind === "error" ? 8000 : 4000);
}

function dismiss(id: number) {
  emit({ toasts: state.toasts.filter((t) => t.id !== id) });
}

export function FeedbackHost() {
  const { confirm, toasts } = useSyncExternalStore(subscribe, () => state);
  return (
    <>
      {confirm && <ConfirmDialog confirm={confirm} />}
      <div
        className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4 sm:bottom-6"
        aria-live="polite"
      >
        {toasts.map((t) => (
          <ToastView key={t.id} toast={t} />
        ))}
      </div>
    </>
  );
}

const TOAST_STYLE: Record<ToastKind, { icon: typeof Info; cls: string }> = {
  success: { icon: CheckCircle2, cls: "border-emerald-700/60 text-emerald-200" },
  error: { icon: XCircle, cls: "border-rose-800/60 text-rose-200" },
  info: { icon: Info, cls: "border-sky-800/60 text-sky-200" },
};

function ToastView({ toast: t }: { toast: Toast }) {
  const { icon: Icon, cls } = TOAST_STYLE[t.kind];
  return (
    <div
      role={t.kind === "error" ? "alert" : "status"}
      className={`toast-in pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-lg border bg-panel/95 px-3.5 py-2.5 text-sm shadow-xl shadow-black/40 backdrop-blur ${cls}`}
    >
      <Icon size={16} className="mt-0.5 shrink-0" aria-hidden />
      <span className="flex-1 text-slate-100">{t.message}</span>
      <button onClick={() => dismiss(t.id)} className="text-slate-500 hover:text-slate-200" aria-label="Dismiss">
        <X size={14} />
      </button>
    </div>
  );
}

function ConfirmDialog({ confirm }: { confirm: Confirm }) {
  const okRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  const close = (ok: boolean) => {
    confirm.resolve(ok);
    if (state.confirm === confirm) emit({ confirm: null });
  };

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    // Destructive actions start on Cancel, so a stray Enter doesn't delete anything.
    (confirm.danger ? cancelRef : okRef).current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(false);
      if (e.key === "Tab") {
        // Keep focus inside the dialog: with two buttons, Tab and Shift+Tab both swap.
        const order = [cancelRef.current, okRef.current];
        const i = order.indexOf(document.activeElement as HTMLButtonElement);
        e.preventDefault();
        order[(i + 1) % 2]?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      before?.focus?.();
    };
  }, [confirm]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onPointerDown={(e) => e.target === e.currentTarget && close(false)}>
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby={confirm.body ? "confirm-body" : undefined}
        className="w-full max-w-sm rounded-xl border border-edge bg-panel p-5 shadow-2xl shadow-black/50"
      >
        <div className="flex gap-3">
          {confirm.danger && (
            <span className="grid size-9 shrink-0 place-items-center rounded-full bg-rose-500/15 text-rose-400">
              <AlertTriangle size={18} aria-hidden />
            </span>
          )}
          <div className="min-w-0">
            <h2 id="confirm-title" className="font-semibold text-slate-100">
              {confirm.title}
            </h2>
            {confirm.body && (
              <p id="confirm-body" className="mt-1.5 text-sm leading-relaxed text-slate-400">
                {confirm.body}
              </p>
            )}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button ref={cancelRef} onClick={() => close(false)} className="rounded-md border border-edge px-3.5 py-1.5 text-sm hover:bg-edge/60">
            Cancel
          </button>
          <button
            ref={okRef}
            onClick={() => close(true)}
            className={`rounded-md px-3.5 py-1.5 text-sm font-medium ${
              confirm.danger ? "bg-rose-600 text-white hover:bg-rose-500" : "bg-sky-500 text-ink hover:bg-sky-400"
            }`}
          >
            {confirm.confirmLabel ?? "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
