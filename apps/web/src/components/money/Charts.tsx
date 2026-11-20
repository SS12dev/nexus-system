import { AlertTriangle, CircleCheck, OctagonAlert, Table2 } from "lucide-react";
import { useState } from "react";
import { budgetState, compact, monthLabel, rupees, type BudgetLine, type MoneyOverview } from "../../lib/money";

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
