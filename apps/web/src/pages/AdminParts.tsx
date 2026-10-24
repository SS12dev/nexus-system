import { ChevronDown, Users, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { User } from "../lib/types";

/** Pieces every Admin tab uses: the people filter, figure tiles, sections and bar charts. */

export const personName = (u: Pick<User, "name" | "email">) => u.name || u.email;

/** Pick one person (tap their name) or several (tick the boxes). None picked means everyone. */
export function PeopleFilter({
  people,
  picked,
  onChange,
}: {
  people: User[];
  picked: string[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent) {
        // Escape closes and hands focus back to the button, like a native dropdown.
        if (e.key !== "Escape") return;
        setOpen(false);
        trigger.current?.focus();
      } else if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const chosen = people.filter((p) => picked.includes(p.id));
  const summary =
    picked.length === 0
      ? "Everyone"
      : picked.length === 1
        ? (chosen[0] && personName(chosen[0])) || "1 person"
        : `${picked.length} people`;
  const q = query.trim().toLowerCase();
  const shown = q
    ? people.filter((p) => `${p.name ?? ""} ${p.email}`.toLowerCase().includes(q))
    : people;
  const toggle = (id: string) =>
    onChange(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);

  return (
    <div ref={box} className="relative">
      <div className="flex items-center rounded-md border border-edge bg-panel">
        <button
          ref={trigger}
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-label={`Show data for: ${summary}`}
          className="flex max-w-56 items-center gap-1.5 rounded-md py-1 pr-2 pl-2.5 hover:bg-edge/40"
        >
          <Users size={14} className="shrink-0 text-slate-400" />
          <span className="truncate">{summary}</span>
          <ChevronDown size={14} className="shrink-0 text-slate-400" />
        </button>
        {picked.length > 0 && (
          <button
            onClick={() => onChange([])}
            className="border-l border-edge px-1.5 py-1 text-slate-400 hover:text-white"
            aria-label="Show everyone"
            title="Show everyone"
          >
            <X size={14} />
          </button>
        )}
      </div>
      {open && (
        <div
          role="dialog"
          aria-label="Choose people"
          className="absolute right-0 z-20 mt-1 w-72 max-w-[calc(100vw-2rem)] rounded-lg border border-edge bg-panel p-2 shadow-xl"
        >
          {people.length > 6 && (
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a person"
              className="mb-2 w-full rounded-md border border-edge bg-ink px-2 py-1"
            />
          )}
          <button
            onClick={() => {
              onChange([]);
              setOpen(false);
            }}
            className={`mb-1 w-full rounded-md px-2 py-1.5 text-left hover:bg-edge/60 ${picked.length === 0 ? "text-sky-300" : ""}`}
          >
            Everyone
          </button>
          <ul aria-label="People" className="max-h-72 overflow-y-auto">
            {shown.map((p) => (
              <li key={p.id} className="flex items-center gap-2 rounded-md px-2 hover:bg-edge/60">
                <input
                  type="checkbox"
                  checked={picked.includes(p.id)}
                  onChange={() => toggle(p.id)}
                  aria-label={`Include ${personName(p)}`}
                />
                <button
                  onClick={() => {
                    onChange([p.id]);
                    setOpen(false);
                  }}
                  className="min-w-0 flex-1 py-1.5 text-left"
                  title={`Only ${personName(p)}`}
                >
                  <div className="truncate">{personName(p)}</div>
                  {p.name && <div className="truncate text-xs text-slate-500">{p.email}</div>}
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-2 border-t border-edge px-2 pt-2 text-xs text-slate-500">
            Tap a name to see only them, or tick several.
          </p>
        </div>
      )}
    </div>
  );
}

export function usd(n: number): string {
  return `$${n < 10 ? n.toFixed(2) : n.toFixed(0)}`;
}

export function Tile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-lg border border-edge bg-panel px-4 py-3">
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="mb-6">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="font-medium">{title}</h2>
        <div className="ml-auto">{action}</div>
      </div>
      {children}
    </section>
  );
}

const TONES = {
  sky: ["fill-sky-500", "fill-sky-300"],
  violet: ["fill-violet-500", "fill-violet-300"],
  emerald: ["fill-emerald-500", "fill-emerald-300"],
  amber: ["fill-amber-500", "fill-amber-300"],
} as const;

/** One series of bars over time, with a tooltip per bar. Values are drawn from a zero baseline. */
export function Bars({
  points,
  label,
  format = String,
  tone = "sky",
}: {
  points: { key: string; value: number; tip: string }[];
  label: string;
  format?: (n: number) => string;
  tone?: keyof typeof TONES;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const peak = Math.max(0, ...points.map((p) => p.value));
  const max = peak || 1;
  const w = 600;
  const h = 80;
  const step = w / Math.max(1, points.length);
  const bar = Math.max(1, step - 2);
  const shown = hover != null ? points[hover] : null;
  return (
    <figure className="rounded-lg border border-edge bg-panel px-4 py-3">
      <figcaption className="mb-1 flex text-xs text-slate-400">
        <span>{label}</span>
        <span className="ml-auto tabular-nums text-slate-300">
          {shown ? shown.tip : peak ? `peak ${format(peak)}` : "none yet"}
        </span>
      </figcaption>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        className="h-20 w-full"
        role="img"
        aria-label={label}
        onMouseLeave={() => setHover(null)}
      >
        <line x1={0} x2={w} y1={h - 0.5} y2={h - 0.5} className="stroke-edge" strokeWidth={1} />
        {points.map((p, i) => {
          const bh = p.value ? Math.max(2, (p.value / max) * (h - 4)) : 0;
          return (
            <g key={p.key} onMouseEnter={() => setHover(i)} onTouchStart={() => setHover(i)}>
              {/* Hit target: the whole column, bigger than the bar. */}
              <rect x={i * step} y={0} width={step} height={h} fill="transparent" />
              {bh > 0 && (
                <rect
                  x={i * step + 1}
                  y={h - bh}
                  width={bar}
                  height={bh}
                  rx={Math.min(2, bar / 2)}
                  className={hover === i ? TONES[tone][1] : TONES[tone][0]}
                />
              )}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

