import { AlertTriangle, CircleCheck, OctagonAlert, Table2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { budgetState, compact, monthLabel, rupees, shortMonth, type BudgetLine, type ForecastPoint, type MoneyOverview } from "../../lib/money";

/** One series, one hue (the dataviz palette's blue for dark surfaces). Status colours are only
 * for a budget's state, and always come with an icon and words. */
export const SERIES = "#3987e5";
const STATUS = { good: "#0ca30c", warning: "#fab219", critical: "#d03b3b" } as const;

/** Spending per month for the last six months; the chosen month in full colour. A column is a
 * button: it opens that month. Hover or focus shows the month's numbers. */
export function TrendBars({
  trend,
  month,
  onPick,
}: {
  trend: MoneyOverview["trend"];
  month: string;
  onPick: (m: string) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const max = Math.max(...trend.map((t) => t.spent_paise), 1);
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium text-slate-300">Spending by month</span>
        <button
          onClick={() => setTable((t) => !t)}
          aria-pressed={table}
          className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-edge hover:text-slate-200"
        >
          <Table2 size={12} /> {table ? "Chart" : "Table"}
        </button>
      </figcaption>
      {table ? (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="py-1 font-normal">Month</th>
              <th className="py-1 text-right font-normal">Spent</th>
              <th className="py-1 text-right font-normal">Came in</th>
            </tr>
          </thead>
          <tbody>
            {trend.map((t) => (
              <tr key={t.month} className={`border-t border-edge/60 ${t.month === month ? "text-white" : "text-slate-300"}`}>
                <td className="py-1">{monthLabel(t.month)}</td>
                <td className="py-1 text-right tabular-nums">{rupees(t.spent_paise, { whole: true })}</td>
                <td className="py-1 text-right tabular-nums">{rupees(t.income_paise, { whole: true })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="relative">
          {/* A recessive grid line at the top value, then the columns on a baseline. */}
          <div className="pointer-events-none absolute inset-x-0 top-5 border-t border-dashed border-edge/70" />
          <span className="pointer-events-none absolute right-0 top-0 text-[11px] text-slate-500">{compact(max)}</span>
          <div className="flex h-40 items-end gap-2 border-b border-slate-600 pt-5" role="list">
            {trend.map((t, i) => {
              const h = (t.spent_paise / max) * 100;
              const active = t.month === month;
              return (
                <button
                  key={t.month}
                  role="listitem"
                  onClick={() => onPick(t.month)}
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                  aria-label={`${monthLabel(t.month)}: spent ${rupees(t.spent_paise, { whole: true })}`}
                  aria-current={active ? "true" : undefined}
                  className="group relative flex h-full flex-1 items-end justify-center rounded-t-md outline-none hover:bg-white/[0.03] focus-visible:ring-1 focus-visible:ring-sky-400"
                >
                  {active && t.spent_paise > 0 && (
                    <span className="absolute left-1/2 -translate-x-1/2 text-[11px] font-medium text-slate-200" style={{ bottom: `calc(${h}% + 4px)` }}>
                      {compact(t.spent_paise)}
                    </span>
                  )}
                  <span
                    className="block w-full max-w-9 rounded-t transition-opacity"
                    style={{
                      height: `${Math.max(h, t.spent_paise ? 1.5 : 0)}%`,
                      background: SERIES,
                      opacity: active ? 1 : hover === i ? 0.75 : 0.45,
                    }}
                  />
                  {hover === i && (
                    <span className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 w-max -translate-x-1/2 rounded-md border border-edge bg-ink px-2 py-1 text-left text-xs shadow-lg">
                      <span className="block font-medium text-slate-200">{monthLabel(t.month)}</span>
                      <span className="block text-slate-300">Spent {rupees(t.spent_paise, { whole: true })}</span>
                      <span className="block text-slate-400">Came in {rupees(t.income_paise, { whole: true })}</span>
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="mt-1 flex gap-2">
            {trend.map((t) => (
              <span key={t.month} className={`flex-1 text-center text-[11px] ${t.month === month ? "text-slate-200" : "text-slate-500"}`}>
                {monthLabel(t.month, true)}
              </span>
            ))}
          </div>
        </div>
      )}
    </figure>
  );
}

/** Where the month's money went, biggest first. Names and amounts are written beside each bar,
 * so the bars only show proportion. Each row opens that category's transactions. */
export function CategoryBars({
  categories,
  total,
  onPick,
}: {
  categories: MoneyOverview["categories"];
  total: number;
  onPick: (category: string) => void;
}) {
  const max = Math.max(...categories.map((c) => c.spent_paise), 1);
  return (
    <ul className="space-y-0.5">
      {categories.map((c) => {
        const share = total ? Math.round((c.spent_paise / total) * 100) : 0;
        return (
          <li key={c.category}>
            <button
              onClick={() => onPick(c.category)}
              className="block w-full rounded-md px-1.5 py-1.5 text-left text-sm hover:bg-edge/50 focus-visible:outline focus-visible:outline-1 focus-visible:outline-sky-400"
              title={`${c.category}: ${rupees(c.spent_paise)}, ${share}% of spending. Show these transactions.`}
            >
              <span className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-slate-300">{c.category}</span>
                <span className="tabular-nums text-slate-200">{rupees(c.spent_paise, { whole: true })}</span>
                <span className="w-9 text-right text-xs text-slate-500">{share}%</span>
              </span>
              <span className="mt-1 block h-1.5">
                <span
                  className="block h-full rounded-full"
                  style={{ width: `${Math.max((c.spent_paise / max) * 100, 1)}%`, background: SERIES }}
                />
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

const STATE_COPY = {
  over: { icon: OctagonAlert, colour: STATUS.critical, words: (left: number) => `Over by ${rupees(-left, { whole: true })}` },
  ahead: { icon: AlertTriangle, colour: STATUS.warning, words: (left: number) => `Ahead of pace · ${rupees(left, { whole: true })} left` },
  ok: { icon: CircleCheck, colour: STATUS.good, words: (left: number) => `${rupees(left, { whole: true })} left` },
} as const;

/** A budget: how much of the month's limit is spent, a tick for how far through the month we
 * are, and its state in words. */
export function BudgetMeter({ budget, progress, compactRow = false }: { budget: BudgetLine; progress: number; compactRow?: boolean }) {
  const state = budgetState(budget, progress);
  const s = STATE_COPY[state];
  const used = Math.min(budget.spent_paise / budget.monthly_paise, 1);
  const left = budget.monthly_paise - budget.spent_paise;
  return (
    <div className={compactRow ? "" : "rounded-lg border border-edge bg-panel p-3"}>
      <div className="mb-1.5 flex items-baseline gap-2 text-sm">
        <span className="min-w-0 flex-1 truncate text-slate-200">{budget.category}</span>
        <span className="tabular-nums text-slate-300">
          {rupees(budget.spent_paise, { whole: true })}
          <span className="text-slate-500"> of {rupees(budget.monthly_paise, { whole: true })}</span>
        </span>
      </div>
      <div className="relative h-2 rounded-full bg-edge" role="meter" aria-valuemin={0} aria-valuemax={budget.monthly_paise} aria-valuenow={budget.spent_paise} aria-label={`${budget.category} budget`}>
        <div className="h-full rounded-full" style={{ width: `${used * 100}%`, background: state === "ok" ? SERIES : s.colour }} />
        <div
          className="absolute -top-1 h-4 w-0.5 rounded bg-slate-300"
          style={{ left: `calc(${Math.min(progress, 1) * 100}% - 1px)` }}
          title="How far through the month we are"
        />
      </div>
      <p className="mt-1.5 flex items-center gap-1 text-xs" style={{ color: state === "ok" ? undefined : s.colour }}>
        <s.icon size={12} className={state === "ok" ? "text-slate-500" : ""} />
        <span className={state === "ok" ? "text-slate-400" : ""}>{s.words(left)}</span>
      </p>
    </div>
  );
}


/** Net worth month by month: one line (the expected path) inside a lighter band (the likely
 * range). A dashed line marks zero when the range crosses it. Hover or arrow keys move a
 * crosshair with the month's numbers; the table shows every month. */
export function ForecastChart({ start, points, title = "Net worth forecast" }: { start: number; points: ForecastPoint[]; title?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [table]);

  const H = 200;
  const pad = { l: 8, r: 52, t: 12, b: 22 };
  const all = [start, ...points.flatMap((p) => [p.low_paise, p.high_paise])];
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  if (hi === lo) hi = lo + 100;
  const span = hi - lo;
  lo -= span * 0.06;
  hi += span * 0.06;
  const n = points.length;
  const x = (i: number) => pad.l + (i / n) * (width - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const line = [`M${x(0)},${y(start)}`, ...points.map((p, i) => `L${x(i + 1)},${y(p.net_worth_paise)}`)].join(" ");
  const band =
    `M${x(0)},${y(start)} ` +
    points.map((p, i) => `L${x(i + 1)},${y(p.high_paise)}`).join(" ") +
    " " +
    [...points].reverse().map((p, i) => `L${x(n - i)},${y(p.low_paise)}`).join(" ") +
    " Z";
  const ticks = [lo + (hi - lo) * 0.15, lo + (hi - lo) * 0.5, lo + (hi - lo) * 0.85];
  const every = n <= 12 ? 3 : n <= 36 ? 6 : 12;
  const pick = (clientX: number) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect) return;
    const i = Math.round(((clientX - rect.left - pad.l) / (width - pad.l - pad.r)) * n);
    setHover(Math.min(Math.max(i, 1), n) - 1);
  };
  const h = hover != null ? points[hover] : null;
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium text-slate-300">{title}</span>
        <span className="flex items-center gap-1.5 text-[11px] text-slate-500">
          <span className="inline-block h-0.5 w-4 rounded" style={{ background: SERIES }} /> expected
          <span className="ml-1 inline-block h-2.5 w-4 rounded-sm" style={{ background: SERIES, opacity: 0.22 }} /> likely range
        </span>
        <button
          onClick={() => setTable((t) => !t)}
          aria-pressed={table}
          className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-edge hover:text-slate-200"
        >
          <Table2 size={12} /> {table ? "Chart" : "Table"}
        </button>
      </figcaption>
      {table ? (
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-panel text-left text-xs text-slate-500">
              <tr>
                <th className="py-1 font-normal">Month</th>
                <th className="py-1 text-right font-normal">Expected</th>
                <th className="hidden py-1 text-right font-normal sm:table-cell">Likely range</th>
                <th className="py-1 text-right font-normal">Loans left</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.month} className="border-t border-edge/60 text-slate-300">
                  <td className="py-1">{shortMonth(p.month)}</td>
                  <td className="py-1 text-right tabular-nums text-slate-100">{rupees(p.net_worth_paise, { whole: true })}</td>
                  <td className="hidden py-1 text-right tabular-nums sm:table-cell">
                    {compact(p.low_paise)} to {compact(p.high_paise)}
                  </td>
                  <td className="py-1 text-right tabular-nums">{p.debt_paise ? compact(p.debt_paise) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div
          ref={box}
          className="relative touch-pan-y outline-none focus-visible:ring-1 focus-visible:ring-sky-400"
          tabIndex={0}
          role="img"
          aria-label={`${title}: from ${rupees(start, { whole: true })} now to about ${rupees(points[n - 1]?.net_worth_paise ?? start, { whole: true })} in ${shortMonth(points[n - 1]?.month ?? "")}`}
          onPointerMove={(e) => pick(e.clientX)}
          onPointerDown={(e) => pick(e.clientX)}
          onPointerLeave={() => setHover(null)}
          onBlur={() => setHover(null)}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight") setHover((v) => Math.min((v ?? -1) + 1, n - 1));
            if (e.key === "ArrowLeft") setHover((v) => Math.max((v ?? n) - 1, 0));
          }}
        >
          <svg width={width} height={H} className="block overflow-visible">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="#334155" strokeDasharray="2 4" strokeWidth={1} />
                <text x={width - pad.r + 6} y={y(t) + 4} fontSize={11} fill="#64748b">
                  {compact(t)}
                </text>
              </g>
            ))}
            {lo < 0 && hi > 0 && <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} stroke="#94a3b8" strokeWidth={1} strokeDasharray="4 3" />}
            <path d={band} fill={SERIES} opacity={0.18} />
            <path d={line} fill="none" stroke={SERIES} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            <circle cx={x(0)} cy={y(start)} r={4} fill={SERIES} stroke="#0f172a" strokeWidth={2} />
            {points.map((p, i) =>
              (i + 1) % every === 0 ? (
                <text key={p.month} x={x(i + 1)} y={H - 4} fontSize={11} fill="#64748b" textAnchor="middle">
                  {shortMonth(p.month)}
                </text>
              ) : null,
            )}
            <text x={x(0)} y={H - 4} fontSize={11} fill="#94a3b8" textAnchor="start">
              Now
            </text>
            {h && hover != null && (
              <g>
                <line x1={x(hover + 1)} x2={x(hover + 1)} y1={pad.t} y2={H - pad.b} stroke="#94a3b8" strokeWidth={1} />
                <circle cx={x(hover + 1)} cy={y(h.net_worth_paise)} r={4.5} fill={SERIES} stroke="#0f172a" strokeWidth={2} />
              </g>
            )}
          </svg>
          {h && hover != null && (
            <div
              className="pointer-events-none absolute top-1 z-10 w-max rounded-md border border-edge bg-ink px-2 py-1.5 text-xs shadow-lg"
              style={x(hover + 1) > width / 2 ? { right: width - x(hover + 1) + 8 } : { left: x(hover + 1) + 8 }}
            >
              <span className="block font-medium text-slate-200">{shortMonth(h.month)}</span>
              <span className="block text-slate-100">About {rupees(h.net_worth_paise, { whole: true })}</span>
              <span className="block text-slate-400">
                Likely {compact(h.low_paise)} to {compact(h.high_paise)}
              </span>
              <span className="block text-slate-400">
                Bank {compact(h.cash_paise)} · funds {compact(h.funds_paise)} · savings {compact(h.savings_paise)}
              </span>
              {h.debt_paise > 0 && <span className="block text-slate-400">Loans left {compact(h.debt_paise)}</span>}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}
