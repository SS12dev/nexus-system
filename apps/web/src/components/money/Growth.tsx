import { Table2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { compact, dayLabel, fullDay, rupees, type Growth, type GrowthGroup, type GrowthRange, type GrowthSeries } from "../../lib/money";
import { Select } from "../Select";
import { useMoney } from "./parts";

/** The groups in their fixed colour slots: a group keeps its colour whatever is filtered. */
export const GROWTH_GROUPS: {
  key: GrowthGroup;
  label: string;
  color: string;
}[] = [
  { key: "shares", label: "Shares", color: "var(--viz-1)" },
  { key: "funds", label: "Mutual funds", color: "var(--viz-2)" },
  { key: "deposits", label: "FDs and RDs", color: "var(--viz-3)" },
  { key: "retirement", label: "PPF, EPF and NPS", color: "var(--viz-4)" },
  { key: "other", label: "Gold and other", color: "var(--viz-5)" },
];
const RANGES: { key: GrowthRange; label: string; long: string }[] = [
  { key: "1m", label: "1M", long: "a month" },
  { key: "3m", label: "3M", long: "three months" },
  { key: "6m", label: "6M", long: "six months" },
  { key: "1y", label: "1Y", long: "a year" },
  { key: "3y", label: "3Y", long: "three years" },
  { key: "all", label: "All", long: "since the start" },
];
/** The line for everything together: neutral ink, since it isn't any one group. */
const TOTAL = "var(--color-slate-300)";

/** One band of a stacked chart: a kind (everything shown) or a holding (one kind shown). */
type Layer = {
  key: string;
  label: string;
  color: string;
  opacity: number;
  series: GrowthSeries[];
};

function sumAt(series: GrowthSeries[], i: number, pick: "values" | "costs" = "values"): number | null {
  let sum = 0;
  let any = false;
  for (const s of series) {
    const v = s[pick][i];
    if (v == null) continue;
    sum += v;
    any = true;
  }
  return any ? sum : null;
}

/** What was paid for everything held on a day, or null when any of it has no price paid
 * (deposits, or a share added without one): a part-paid line would mislead. */
function paidAt(series: GrowthSeries[], i: number): number | null {
  let sum = 0;
  let any = false;
  for (const s of series) {
    if (s.values[i] == null) continue;
    const c = s.costs[i];
    if (c == null) return null;
    sum += c;
    any = true;
  }
  return any ? sum : null;
}

function signed(paise: number): string {
  return rupees(paise, { whole: true, sign: true });
}

/** Investments over time on Savings: one line for what's chosen (all, a kind, or one holding),
 * a dashed line for what was paid where that's known, and today's split by kind below. Filters
 * sit in one row above; hover, touch or arrow keys show a day; the table shows every point. */
export function GrowthCard() {
  const [range, setRange] = useState<GrowthRange>("1y");
  const [group, setGroup] = useState<GrowthGroup | "all">("all");
  const [item, setItem] = useState("all");
  const [table, setTable] = useState(false);
  const { data, error } = useMoney<Growth>(() => api.moneyGrowth(range), [range]);

  const present = useMemo(() => GROWTH_GROUPS.filter((g) => data?.series.some((s) => s.group === g.key)), [data]);
  if (error) return <p className="mb-6 text-sm text-rose-400">{error}</p>;
  if (!data || !data.series.length) return null;

  const inGroup = group === "all" ? data.series : data.series.filter((s) => s.group === group);
  const picked = item === "all" ? inGroup : inGroup.filter((s) => s.id === item);
  const chosen = picked.length ? picked : inGroup;
  const n = data.days.length;
  // Holdings with only today's value (no history yet) would show as a jump on the last day:
  // they're counted in "now" and named under the graph instead of being drawn.
  const fresh = n > 1 ? chosen.filter((s) => s.values.findIndex((v) => v != null) === n - 1) : [];
  const drawn = chosen.filter((s) => !fresh.includes(s));
  const now = sumAt(chosen, n - 1) ?? 0;
  const paidNow = paidAt(chosen, n - 1);
  const values = data.days.map((_, i) => sumAt(drawn, i));
  const paid = data.days.map((_, i) => paidAt(drawn, i));
  const showPaid = paid[n - 1] != null;
  // The change on what was held at both ends, so money added on the way isn't counted as growth.
  const first = values.findIndex((v) => v != null);
  const both = drawn.filter((s) => s.values[first] != null && s.values[n - 1] != null);
  const change = first >= 0 && first < n - 1 && both.length ? (sumAt(both, n - 1) ?? 0) - (sumAt(both, first) ?? 0) : null;
  const base = both.length && first >= 0 ? (sumAt(both, first) ?? 0) : 0;
  const color = item !== "all" || group !== "all" ? (GROWTH_GROUPS.find((g) => g.key === (chosen[0]?.group ?? group))?.color ?? TOTAL) : TOTAL;
  const name =
    item !== "all" && chosen.length === 1 ? chosen[0].label : group === "all" ? "All investments" : (GROWTH_GROUPS.find((g) => g.key === group)?.label ?? "");
  // More than one thing shown: stacked bands, so something that starts being tracked shows as a
  // new band rather than as growth. By kind for everything, by holding within one kind (shades
  // of the kind's colour).
  const layers: Layer[] | null =
    drawn.length < 2
      ? null
      : group === "all" && item === "all"
        ? GROWTH_GROUPS.map((g) => ({
            key: g.key,
            label: g.label,
            color: g.color,
            opacity: 0.3,
            series: drawn.filter((s) => s.group === g.key),
          })).filter((l) => l.series.length)
        : drawn.map((s, k) => ({
            key: s.id,
            label: s.label,
            color,
            opacity: k % 2 ? 0.16 : 0.34,
            series: [s],
          }));
  const rangeLong = RANGES.find((r) => r.key === data.range)?.long ?? "";
  const loading = data.range !== range;

  return (
    <section className="mb-6" aria-label="Investments over time">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-sm font-medium text-slate-300">Investments over time</h2>
        <div className="flex rounded-md border border-edge p-0.5" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button
              key={r.key}
              onClick={() => setRange(r.key)}
              aria-pressed={range === r.key}
              className={`rounded px-2 py-0.5 text-xs tabular-nums ${range === r.key ? "bg-edge text-white" : "text-slate-400 hover:text-slate-200"}`}
            >
              {r.label}
            </button>
          ))}
        </div>
        <div className="flex w-full gap-2 sm:w-auto">
          <Select
            className="flex-1 sm:flex-none"
            aria-label="Kind of investment"
            value={group}
            onChange={(e) => {
              setGroup(e.target.value as GrowthGroup | "all");
              setItem("all");
            }}
          >
            <option value="all">All kinds</option>
            {present.map((g) => (
              <option key={g.key} value={g.key}>
                {g.label}
              </option>
            ))}
          </Select>
          {inGroup.length > 1 && (
            <Select aria-label="Holding" value={item} onChange={(e) => setItem(e.target.value)} className="flex-1 sm:max-w-[12rem] sm:flex-none">
              <option value="all">Every holding</option>
              {inGroup.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </Select>
          )}
        </div>
      </div>

      <div className={`rounded-xl border border-edge bg-panel p-3 transition-opacity ${loading ? "opacity-60" : ""}`}>
        <div className="mb-3 flex flex-wrap items-end gap-x-6 gap-y-1">
          <div>
            <span className="block text-xs text-slate-500">{name} now</span>
            <span className="block text-lg font-semibold tabular-nums text-slate-100">{rupees(now, { whole: true })}</span>
          </div>
          {change != null && (
            <div>
              <span className="block text-xs text-slate-500">
                Since {fullDay(data.days[first])}
                {both.length < chosen.length && ", on what you held then"}
              </span>
              <span className="block text-sm tabular-nums text-slate-200">
                {signed(change)}
                {base > 0 && ` (${change >= 0 ? "+" : "−"}${Math.abs((change / base) * 100).toFixed(1)}%)`}
              </span>
            </div>
          )}
          {showPaid && (
            <div>
              <span className="block text-xs text-slate-500">On what you paid</span>
              <span className="block text-sm tabular-nums text-slate-200">{signed(now - (paidNow ?? 0))}</span>
            </div>
          )}
          <button
            onClick={() => setTable((t) => !t)}
            aria-pressed={table}
            className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-edge hover:text-slate-200"
          >
            <Table2 size={12} /> {table ? "Chart" : "Table"}
          </button>
        </div>

        {n < 2 || !drawn.length ? (
          <p className="py-6 text-center text-sm text-slate-500">The line starts tomorrow, once tonight's prices are stored.</p>
        ) : table ? (
          <GrowthTable days={data.days} values={values} paid={showPaid ? paid : null} />
        ) : (
          <GrowthLine days={data.days} values={values} paid={showPaid ? paid : null} color={color} name={name} layers={layers} />
        )}

        {fresh.length > 0 && drawn.length > 0 && (
          <p className="mt-2 text-xs text-slate-400">
            Not drawn yet, as there's no history for {fresh.length > 2 ? `${fresh.length} holdings` : fresh.map((s) => s.label).join(" and ")}: counted in the
            total above, and in the graph from tomorrow.
            {fresh.some((s) => s.kind === "share") && " For a share, add the day you bought it and its past prices fill in."}
          </p>
        )}
        {group === "all" && item === "all" && present.length > 1 && <Split series={data.series} n={n} onPick={(g) => setGroup(g)} />}
      </div>
      <p className="mt-1.5 text-xs text-slate-500">
        {data.filling && "Earlier share prices are on their way from NSE and fill in within a few minutes. "}
        Shares before you added them use NSE's closing prices with the quantity you hold now. Funds start from the first night Nexus valued them, and deposits
        and schemes are worked out from their terms. The change {rangeLong === "since the start" ? "since the start" : `over ${rangeLong}`} counts only what you
        held at both ends, so money you added isn't shown as growth.
      </p>
    </section>
  );
}

function GrowthTable({ days, values, paid }: { days: string[]; values: (number | null)[]; paid: (number | null)[] | null }) {
  return (
    <div className="max-h-72 overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-panel text-left text-xs text-slate-500">
          <tr>
            <th className="py-1 font-normal">Day</th>
            <th className="py-1 text-right font-normal">Worth</th>
            {paid && <th className="py-1 text-right font-normal">Paid</th>}
          </tr>
        </thead>
        <tbody>
          {days
            .map((d, i) => ({ d, i }))
            .reverse()
            .map(({ d, i }) => (
              <tr key={d} className="border-t border-edge/60 text-slate-300">
                <td className="py-1">{fullDay(d)}</td>
                <td className="py-1 text-right tabular-nums text-slate-100">{values[i] != null ? rupees(values[i], { whole: true }) : "—"}</td>
                {paid && <td className="py-1 text-right tabular-nums">{paid[i] != null ? rupees(paid[i], { whole: true }) : "—"}</td>}
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}

function GrowthLine({
  days,
  values,
  paid,
  color,
  name,
  layers,
}: {
  days: string[];
  values: (number | null)[];
  paid: (number | null)[] | null;
  color: string;
  name: string;
  /** Stacked bands, when more than one thing is shown. */
  layers: Layer[] | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const H = 200;
  const pad = { l: 4, r: 52, t: 10, b: 22 };
  const n = days.length;
  const t = days.map((d) => Date.parse(`${d}T00:00:00Z`));
  const t0 = t[0];
  const t1 = t[n - 1] === t0 ? t0 + 1 : t[n - 1];
  const nums = [...values, ...(paid ?? [])].filter((v): v is number => v != null);
  let lo = Math.min(...nums);
  let hi = Math.max(...nums);
  if (hi === lo) {
    hi += Math.max(Math.abs(hi) * 0.05, 100);
    lo -= Math.max(Math.abs(lo) * 0.05, 100);
  }
  const span = hi - lo;
  // Stacked bands are read from zero; a single line is read for its shape.
  lo = layers ? 0 : Math.max(0, lo - span * 0.08);
  hi += span * 0.08;
  const x = (i: number) => pad.l + ((t[i] - t0) / (t1 - t0)) * (width - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const path = (vs: (number | null)[]) => {
    let d = "";
    let open = false;
    vs.forEach((v, i) => {
      if (v == null) {
        open = false;
        return;
      }
      d += `${open ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      open = true;
    });
    return d;
  };
  const ticks = [lo + (hi - lo) * 0.1, lo + (hi - lo) * 0.5, lo + (hi - lo) * 0.9];
  // Everything together: one band per kind, stacked in the fixed order, so a kind that starts
  // being tracked shows as a new band rather than as growth.
  const bands: {
    key: string;
    color: string;
    opacity: number;
    d: string;
    top: string;
  }[] = [];
  if (layers) {
    const held = values.map((v) => v != null);
    const below = values.map(() => 0);
    for (const l of layers) {
      const add = values.map((_, i) => sumAt(l.series, i) ?? 0);
      if (!add.some((v) => v > 0)) continue;
      const upper = below.map((b, i) => b + add[i]);
      let d = "";
      let top = "";
      let run: number[] = [];
      const flush = () => {
        if (!run.length) return;
        const up = run.map((i, k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(upper[i]).toFixed(1)}`).join(" ");
        const down = [...run]
          .reverse()
          .map((i) => `L${x(i).toFixed(1)},${y(below[i]).toFixed(1)}`)
          .join(" ");
        d += `${up} ${down} Z `;
        top += `${up} `;
        run = [];
      };
      values.forEach((_, i) => (held[i] && add[i] > 0 ? run.push(i) : flush()));
      flush();
      bands.push({ key: l.key, color: l.color, opacity: l.opacity, d, top });
      upper.forEach((u, i) => (below[i] = u));
    }
  }
  const spanDays = (t1 - t0) / 86400000;
  const label = (d: string) => (spanDays <= 200 ? dayLabel(d) : `${dayLabel(d).split(" ")[1]} ${d.slice(2, 4)}`);
  const count = width < 480 ? 3 : 5;
  const xTicks = Array.from(new Set(Array.from({ length: count }, (_, k) => Math.round((k * (n - 1)) / (count - 1)))));

  const pick = (clientX: number) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect) return;
    const at = t0 + ((clientX - rect.left - pad.l) / (width - pad.l - pad.r)) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < n; i++) if (Math.abs(t[i] - at) < Math.abs(t[best] - at)) best = i;
    setHover(best);
  };
  const h = hover;
  const last = values[n - 1];
  // The tooltip lists every band at that day, biggest first, up to six.
  const breakdown =
    h != null && layers
      ? layers
          .map((l) => ({ l, v: sumAt(l.series, h) }))
          .filter((r): r is { l: Layer; v: number } => r.v != null)
          .sort((a, b) => b.v - a.v)
          .slice(0, 6)
      : [];

  return (
    <figure className="min-w-0">
      {paid && (
        <figcaption className="mb-1 flex items-center gap-3 text-[11px] text-slate-500">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4 rounded" style={{ background: color }} /> worth
          </span>
          <span className="flex items-center gap-1.5">
            <svg width="16" height="2" aria-hidden>
              <line x1="0" x2="16" y1="1" y2="1" stroke="var(--color-slate-500)" strokeWidth="2" strokeDasharray="3 3" />
            </svg>
            paid
          </span>
        </figcaption>
      )}
      <div
        ref={box}
        className="relative touch-pan-y outline-none focus-visible:ring-1 focus-visible:ring-sky-400"
        tabIndex={0}
        role="img"
        aria-label={`${name}: ${values[0] != null ? `${rupees(values[0], { whole: true })} on ${fullDay(days[0])}, ` : ""}${rupees(last ?? 0, { whole: true })} today`}
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
          {ticks.map((v) => (
            <g key={v}>
              <line x1={pad.l} x2={width - pad.r} y1={y(v)} y2={y(v)} stroke="var(--color-edge)" strokeWidth={1} />
              <text x={width - pad.r + 6} y={y(v) + 4} fontSize={11} fill="var(--color-slate-500)">
                {compact(v)}
              </text>
            </g>
          ))}
          {bands.map((b) => (
            <g key={b.key}>
              <path d={b.d} fill={b.color} opacity={b.opacity} />
              <path d={b.top} fill="none" stroke={b.color} strokeWidth={1.5} strokeLinejoin="round" />
            </g>
          ))}
          {paid && <path d={path(paid)} fill="none" stroke="var(--color-slate-500)" strokeWidth={2} strokeDasharray="3 3" strokeLinejoin="round" />}
          {!layers && <path d={path(values)} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
          {last != null && <circle cx={x(n - 1)} cy={y(last)} r={4} fill={color} stroke="var(--color-panel)" strokeWidth={2} />}
          {xTicks.map((i) => (
            <text key={i} x={x(i)} y={H - 4} fontSize={11} fill="var(--color-slate-500)" textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}>
              {i === n - 1 ? "Today" : label(days[i])}
            </text>
          ))}
          {h != null && (
            <g>
              <line x1={x(h)} x2={x(h)} y1={pad.t} y2={H - pad.b} stroke="var(--color-slate-500)" strokeWidth={1} />
              {values[h] != null && <circle cx={x(h)} cy={y(values[h]!)} r={4.5} fill={color} stroke="var(--color-panel)" strokeWidth={2} />}
            </g>
          )}
        </svg>
        {h != null && (
          <div
            className="pointer-events-none absolute top-1 z-10 w-max max-w-[16rem] rounded-md border border-edge bg-ink px-2 py-1.5 text-xs shadow-lg"
            style={x(h) > width / 2 ? { right: width - x(h) + 8 } : { left: x(h) + 8 }}
          >
            <span className="block text-slate-400">{h === n - 1 ? "Today" : fullDay(days[h])}</span>
            <span className="block font-medium tabular-nums text-slate-100">{values[h] != null ? rupees(values[h]!, { whole: true }) : "Not held"}</span>
            {paid && paid[h] != null && values[h] != null && (
              <span className="block tabular-nums text-slate-400">
                Paid {rupees(paid[h]!, { whole: true })} · {signed(values[h]! - paid[h]!)}
              </span>
            )}
            {breakdown.length > 1 &&
              breakdown.map(({ l, v }) => (
                <span key={l.key} className="flex items-center gap-1.5 tabular-nums text-slate-300">
                  <span className="inline-block h-0.5 w-3 shrink-0 rounded" style={{ background: l.color }} />
                  {compact(v)} <span className="truncate text-slate-500">{l.label}</span>
                </span>
              ))}
          </div>
        )}
      </div>
    </figure>
  );
}

/** Today's split by kind: a thin segmented bar with a 2px gap between parts, and a legend of
 * buttons that filter to that kind. */
function Split({ series, n, onPick }: { series: GrowthSeries[]; n: number; onPick: (g: GrowthGroup) => void }) {
  const parts = GROWTH_GROUPS.map((g) => ({
    g,
    v:
      sumAt(
        series.filter((s) => s.group === g.key),
        n - 1,
      ) ?? 0,
  })).filter((p) => p.v > 0);
  const total = parts.reduce((a, p) => a + p.v, 0);
  if (!total) return null;
  return (
    <div className="mt-3 border-t border-edge/60 pt-3">
      <div className="flex h-2 gap-[2px] overflow-hidden rounded-full" aria-hidden>
        {parts.map((p) => (
          <span key={p.g.key} style={{ width: `${(p.v / total) * 100}%`, background: p.g.color }} />
        ))}
      </div>
      <ul className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2" aria-label="Today, by kind">
        {parts.map((p) => (
          <li key={p.g.key}>
            <button onClick={() => onPick(p.g.key)} className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left text-xs hover:bg-edge/50">
              <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: p.g.color }} />
              <span className="flex-1 text-slate-300">{p.g.label}</span>
              <span className="tabular-nums text-slate-100">{rupees(p.v, { whole: true })}</span>
              <span className="w-10 text-right tabular-nums text-slate-500">{Math.round((p.v / total) * 100)}%</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
