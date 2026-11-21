import { X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { parsePercent, parseRupees } from "../../lib/money";
import { Select } from "../Select";

/** A field in a Money form. Values travel as the text typed; `read` turns them into numbers. */
export interface Field {
  key: string;
  label: string;
  kind: "text" | "money" | "percent" | "date" | "int" | "select" | "check" | "url";
  value: string;
  options?: [string, string][];
  hint?: ReactNode;
  placeholder?: string;
  optional?: boolean;
  /** Shown only when this returns true for the current values. */
  when?: (v: Record<string, string>) => boolean;
  /** Two short fields side by side on wider screens. */
  half?: boolean;
}

export type Values = Record<string, string>;

/** Paise from a money field ("" -> null), basis points from a percent field, an integer. */
export const read = {
  money: (v: string) => (v.trim() ? parseRupees(v) : null),
  percent: (v: string) => (v.trim() ? parsePercent(v) : null),
  int: (v: string) => (/^\d+$/.test(v.trim()) ? Number(v.trim()) : null),
};

function problem(f: Field, v: string): string | null {
  const empty = !v.trim();
  if (f.kind === "check") return null;
  if (empty) return f.optional ? null : `${f.label} is needed`;
  if (f.kind === "money" && read.money(v) == null) return `${f.label}: type rupees, like 1,500 or 249.50`;
  if (f.kind === "percent" && read.percent(v) == null) return `${f.label}: type a rate like 7.1`;
  if (f.kind === "int" && read.int(v) == null) return `${f.label}: type a whole number`;
  if (f.kind === "url" && !/^https?:\/\/\S+$/.test(v.trim())) return `${f.label}: a web address starting https://`;
  return null;
}

/** A small form in a dialog: centred on a laptop, a sheet from the bottom on a phone. Checks
 * each field as typed, shows an optional live preview, and keeps the dialog open with the
 * server's message when saving fails. */
export function FormDialog({
  title,
  description,
  fields,
  submitLabel,
  onSubmit,
  onClose,
  preview,
  extra,
}: {
  title: string;
  description?: ReactNode;
  fields: Field[];
  submitLabel: string;
  onSubmit: (v: Values) => Promise<void>;
  onClose: () => void;
  preview?: (v: Values) => ReactNode;
  extra?: ReactNode;
}) {
  const [values, setValues] = useState<Values>(() => Object.fromEntries(fields.map((f) => [f.key, f.value])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [touched, setTouched] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const shown = fields.filter((f) => !f.when || f.when(values));
  const problems = shown.map((f) => problem(f, values[f.key] ?? "")).filter(Boolean) as string[];

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>("input, select")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      before?.focus();
    };
  }, [busy, onClose]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (problems.length) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit(values);
      onClose();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };
  const set = (k: string, v: string) => setValues((cur) => ({ ...cur, [k]: v }));
  const input =
    "w-full rounded-md border border-edge bg-ink px-2.5 py-1.5 text-base text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none sm:text-sm";

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 sm:items-center sm:p-4"
      onPointerDown={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="max-h-[92dvh] w-full overflow-y-auto rounded-t-2xl border border-edge bg-panel p-5 shadow-2xl shadow-black/50 sm:max-w-lg sm:rounded-xl"
      >
        <div className="mb-1 flex items-start gap-2">
          <h2 className="flex-1 font-semibold text-slate-100">{title}</h2>
          <button onClick={onClose} className="-m-1 p-1 text-slate-400 hover:text-white" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {description && <div className="text-sm leading-relaxed text-slate-400">{description}</div>}
        <form onSubmit={submit} className="mt-4 grid grid-cols-2 gap-3" noValidate>
          {shown.map((f) => {
            const id = `money-field-${f.key}`;
            const v = values[f.key] ?? "";
            const bad = touched && problem(f, v);
            if (f.kind === "check")
              return (
                <label key={f.key} className="col-span-2 flex items-start gap-2 text-sm text-slate-300">
                  <input type="checkbox" checked={v === "1"} onChange={(e) => set(f.key, e.target.checked ? "1" : "")} className="mt-0.5 accent-sky-500" />
                  <span>
                    {f.label}
                    {f.hint && <span className="block text-xs text-slate-500">{f.hint}</span>}
                  </span>
                </label>
              );
            return (
              <label key={f.key} htmlFor={id} className={`block text-sm text-slate-300 ${f.half ? "col-span-2 sm:col-span-1" : "col-span-2"}`}>
                <span className="mb-1 block">
                  {f.label}
                  {f.optional && <span className="text-slate-500"> (optional)</span>}
                </span>
                {f.kind === "select" ? (
                  <Select id={id} value={v} onChange={(e) => set(f.key, e.target.value)} className="w-full">
                    {f.options?.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <span className="relative block">
                    {f.kind === "money" && <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-slate-500">₹</span>}
                    <input
                      id={id}
                      type={f.kind === "date" ? "date" : f.kind === "url" ? "url" : "text"}
                      inputMode={f.kind === "money" || f.kind === "percent" ? "decimal" : f.kind === "int" ? "numeric" : undefined}
                      value={v}
                      placeholder={f.placeholder}
                      onChange={(e) => set(f.key, e.target.value)}
                      aria-invalid={bad ? true : undefined}
                      className={`${input} ${f.kind === "money" ? "pl-6" : ""} ${f.kind === "percent" ? "pr-7" : ""} ${bad ? "border-rose-500/70" : ""}`}
                    />
                    {f.kind === "percent" && <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-slate-500">%</span>}
                  </span>
                )}
                {f.hint && <span className="mt-1 block text-xs text-slate-500">{f.hint}</span>}
              </label>
            );
          })}
          {preview && <div className="col-span-2">{preview(values)}</div>}
          {extra && <div className="col-span-2">{extra}</div>}
          {touched && problems.length > 0 && (
            <p role="alert" className="col-span-2 text-sm text-rose-300">
              {problems[0]}
            </p>
          )}
          {error && (
            <p role="alert" className="col-span-2 text-sm text-rose-300">
              {error}
            </p>
          )}
          <div className="col-span-2 flex justify-end gap-2 pt-1">
            <button type="button" disabled={busy} onClick={onClose} className="rounded-md border border-edge px-3.5 py-1.5 text-sm hover:bg-edge/60 disabled:opacity-50">
              Cancel
            </button>
            <button type="submit" disabled={busy} className="rounded-md bg-sky-500 px-3.5 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50">
              {busy ? "Saving…" : submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/** Rupees for a form field: 125050 -> "1250.5"; null -> "". */
export function moneyText(paise: number | null | undefined): string {
  if (paise == null) return "";
  return String(paise / 100);
}

export function percentText(bp: number | null | undefined): string {
  return bp == null ? "" : String(bp / 100);
}
