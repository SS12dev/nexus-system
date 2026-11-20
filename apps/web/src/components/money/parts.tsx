import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { monthLabel, shiftMonth, type MoneyAccount, type MoneySettings } from "../../lib/money";
import { useStore } from "../../store/chat";

export function useMoneyVersion() {
  return useStore((s) => s.moneyVersion);
}

/** Load something from the Money API, again whenever Money changes or `deps` do. */
export function useMoney<T>(load: () => Promise<T>, deps: unknown[] = []): { data: T | null; error: string; reload: () => void } {
  const version = useMoneyVersion();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    load()
      .then((d) => {
        if (!live) return;
        setData(d);
        setError("");
      })
      .catch((e) => live && setError((e as Error).message));
    return () => {
      live = false;
    };
  }, [version, tick, ...deps]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}

export function useAccounts(): MoneyAccount[] {
  return useMoney(api.moneyAccounts).data ?? [];
}

export function useMoneySettings() {
  return useMoney<MoneySettings>(api.moneySettings);
}

export function MonthPicker({ month, onChange, latest }: { month: string; onChange: (m: string) => void; latest: string }) {
  return (
    <div className="flex items-center gap-1">
      <button
        onClick={() => onChange(shiftMonth(month, -1))}
        className="rounded-md p-1.5 text-slate-400 hover:bg-edge hover:text-white"
        aria-label="Month before"
      >
        <ChevronLeft size={18} />
      </button>
      <span className="min-w-36 text-center font-medium">{monthLabel(month)}</span>
      <button
        onClick={() => onChange(shiftMonth(month, 1))}
        disabled={month >= latest}
        className="rounded-md p-1.5 text-slate-400 hover:bg-edge hover:text-white disabled:opacity-30 disabled:hover:bg-transparent"
        aria-label="Month after"
      >
        <ChevronRight size={18} />
      </button>
    </div>
  );
}

export function Stat({ label, value, sub, title }: { label: string; value: ReactNode; sub?: ReactNode; title?: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-edge bg-panel px-3.5 py-3" title={title}>
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-0.5 truncate text-xl font-semibold tabular-nums tracking-tight sm:text-2xl">{value}</p>
      {sub && <p className="mt-0.5 truncate text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

export function Card({ title, action, children, className = "" }: { title?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-edge bg-panel p-4 ${className}`}>
      {(title || action) && (
        <h2 className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-300">
          {title}
          {action && <span className="ml-auto text-xs font-normal">{action}</span>}
        </h2>
      )}
      {children}
    </section>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className={`flex shrink-0 items-center gap-2 text-xs text-slate-400 ${disabled ? "opacity-50" : "cursor-pointer"}`}>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="peer sr-only"
        aria-label={label}
      />
      <span className="relative h-5 w-9 rounded-full bg-edge transition peer-checked:bg-sky-500 peer-focus-visible:ring-1 peer-focus-visible:ring-sky-300 after:absolute after:top-0.5 after:left-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:transition peer-checked:after:translate-x-4" />
      {checked ? "On" : "Off"}
    </label>
  );
}

export function Spinner() {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-slate-400">
      <Loader2 size={16} className="animate-spin" /> Loading…
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-edge px-4 py-6 text-center text-sm text-slate-400">{children}</div>;
}
