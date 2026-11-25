import { Lightbulb, ShoppingBasket, Table2, UtensilsCrossed } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../../lib/api";
import { compact, dayLabel, parseRupees, rupees, SMOKING, WEEKDAYS, type BasketSummary, type Daily, type DailyDay } from "../../lib/money";
import { toast } from "../Feedback";
import { SERIES } from "./Charts";
import { OrderShot } from "./OrderShot";
import { Card, useMoney } from "./parts";

/** Sequential, one hue: a day's everyday spend as the strength of the series blue over the
 * surface (so near-nothing recedes in both themes). The amount is always written in the cell. */
function fill(value: number, max: number): string | undefined {
  if (value <= 0 || max <= 0) return undefined;
  const steps = [0.14, 0.24, 0.36, 0.48, 0.6];
  const i = Math.min(steps.length - 1, Math.floor((value / max) * steps.length));
  const a = Math.round(steps[i] * 255)
    .toString(16)
    .padStart(2, "0");
  return `${SERIES}${a}`;
}

function weekdayOf(day: string): number {
  const [y, m, d] = day.split("-").map(Number);
  return (new Date(y, m - 1, d).getDay() + 6) % 7;
}

/** Overview › Day by day: a calendar of the month's everyday spending, a day's spends on a
 * tap, and the habits under it. */
export function DayByDay({ month }: { month: string }) {
  const { data } = useMoney(() => api.moneyDaily(month), [month]);
  if (!data) return null;
  return (
    <div className="mb-4 grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Calendar data={data} />
      <div className="grid min-w-0 content-start gap-4">
        <Habits data={data} />
        <Smoking data={data} />
      </div>
    </div>
  );
}

function Calendar({ data }: { data: Daily }) {
  const [picked, setPicked] = useState<string | null>(null);
  const [table, setTable] = useState(false);
  const shown = data.days.filter((d) => !d.future);
  const max = Math.max(...shown.map((d) => d.everyday_paise), 0);
  const lead = data.days.length ? weekdayOf(data.days[0].on) : 0;
  const day = data.days.find((d) => d.on === picked) ?? null;
  return (
    <Card
      title="Day by day"
      action={
        <button
          onClick={() => setTable((t) => !t)}
          aria-pressed={table}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-edge hover:text-slate-200"
        >
          <Table2 size={12} /> {table ? "Calendar" : "Table"}
        </button>
      }
    >
      <p className="-mt-1 mb-3 text-xs text-slate-500">
        Each day's everyday spending: what you chose to spend that day, without rent, EMIs, bills and subscriptions (a dot marks a day
        one of those was paid). Darker means more. Tap a day to see where it went.
      </p>
      <div className="mb-3 grid grid-cols-3 gap-2 text-center">
        <Fact label="Daily average" value={rupees(data.everyday_average_paise, { whole: true })} />
        <Fact label="No-spend days" value={`${data.no_spend_days} of ${data.counted_days}`} />
        <Fact
          label="Biggest day"
          value={data.biggest ? compact(data.biggest.everyday_paise) : "—"}
          sub={data.biggest ? dayLabel(data.biggest.on) : undefined}
        />
      </div>
      {table ? (
        <div className="max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="py-1 font-normal">Day</th>
                <th className="py-1 text-right font-normal">Everyday</th>
                <th className="py-1 text-right font-normal">Bills and rent</th>
                <th className="py-1 text-right font-normal">Spends</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((d) => (
                <tr key={d.on} className="border-t border-edge/60 text-slate-300">
                  <td className="py-1">
                    {WEEKDAYS[weekdayOf(d.on)]} {dayLabel(d.on)}
                  </td>
                  <td className="py-1 text-right tabular-nums">{rupees(d.everyday_paise, { whole: true })}</td>
                  <td className="py-1 text-right tabular-nums text-slate-400">{d.fixed_paise ? rupees(d.fixed_paise, { whole: true }) : "—"}</td>
                  <td className="py-1 text-right tabular-nums text-slate-400">{d.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div role="grid" aria-label="Spending by day">
          <div className="mb-1 grid grid-cols-7 gap-1 text-center text-[11px] text-slate-500" role="row">
            {WEEKDAYS.map((w) => (
              <span key={w} role="columnheader">
                {w}
              </span>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-1" role="row">
            {Array.from({ length: lead }, (_, i) => (
              <span key={`lead${i}`} aria-hidden />
            ))}
            {data.days.map((d) => {
              const n = Number(d.on.slice(8));
              const on = d.on === picked;
              return (
                <button
                  key={d.on}
                  role="gridcell"
                  disabled={d.future}
                  onClick={() => setPicked(on ? null : d.on)}
                  aria-pressed={on}
                  aria-label={`${dayLabel(d.on)}: ${rupees(d.everyday_paise, { whole: true })} everyday spending${d.fixed_paise ? `, plus ${rupees(d.fixed_paise, { whole: true })} in bills or rent` : ""}`}
                  title={`${dayLabel(d.on)}: ${rupees(d.everyday_paise, { whole: true })}${d.fixed_paise ? ` + ${rupees(d.fixed_paise, { whole: true })} bills` : ""}`}
                  style={{ backgroundColor: d.future ? undefined : fill(d.everyday_paise, max) }}
                  className={`relative flex h-11 flex-col items-start justify-between rounded-md border px-1 py-0.5 text-left sm:h-14 sm:px-1.5 ${
                    on ? "border-sky-300" : "border-edge/60"
                  } ${d.future ? "opacity-30" : "hover:border-slate-400"} focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sky-300`}
                >
                  <span className="text-[10px] leading-none text-slate-400 sm:text-[11px]">{n}</span>
                  {!d.future && d.everyday_paise > 0 && (
                    <span className="w-full truncate text-[10px] font-medium tabular-nums leading-tight text-slate-100 sm:text-xs">{compact(d.everyday_paise)}</span>
                  )}
                  {d.fixed_paise > 0 && <span className="absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-slate-400" aria-hidden />}
                </button>
              );
            })}
          </div>
        </div>
      )}
      {day && <DayList day={day} />}
      <Weekdays data={data} />
    </Card>
  );
}

function Fact({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 rounded-lg bg-ink/40 px-2 py-1.5">
      <p className="truncate text-[11px] text-slate-500">{label}</p>
      <p className="truncate text-sm font-semibold tabular-nums text-slate-100">{value}</p>
      {sub && <p className="truncate text-[11px] text-slate-500">{sub}</p>}
    </div>
  );
}

function DayList({ day }: { day: DailyDay }) {
  const navigate = useNavigate();
  return (
    <div className="mt-3 rounded-lg border border-edge bg-ink/40 p-2.5">
      <p className="mb-1.5 flex items-baseline gap-2 text-sm">
        <span className="font-medium text-slate-100">
          {WEEKDAYS[weekdayOf(day.on)]} {dayLabel(day.on)}
        </span>
        <span className="ml-auto tabular-nums text-slate-300">{rupees(day.spent_paise, { whole: true })}</span>
      </p>
      {day.items.length ? (
        <ul className="space-y-0.5 text-sm">
          {day.items.map((t) => (
            <li key={t.id}>
              <button
                onClick={() => navigate(`/money/transactions?${new URLSearchParams({ month: day.on.slice(0, 7), q: t.merchant })}`)}
                className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-edge/50"
              >
                <span className="min-w-0 flex-1 truncate text-slate-200">
                  {t.merchant}
                  {t.place && <span className="text-slate-400"> · {t.place}</span>}
                  <span className="ml-1.5 text-xs text-slate-500">
                    {t.category}
                    {t.fixed && " · fixed"}
                    {t.basket && (t.kinds.length ? "" : " · contents not known yet")}
                  </span>
                </span>
                <span className="w-20 text-right tabular-nums text-slate-200">{rupees(t.amount_paise, { whole: true, sign: t.amount_paise < 0 })}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-slate-500">Nothing spent this day.</p>
      )}
    </div>
  );
}

/** Average everyday spending per weekday over the last eight weeks: one hue, the dearest day
 * labelled. */
function Weekdays({ data }: { data: Daily }) {
  const max = Math.max(...data.weekdays.map((w) => w.average_paise), 1);
  const top = data.weekdays.reduce((a, b) => (b.average_paise > a.average_paise ? b : a), data.weekdays[0]);
  if (!data.weekdays.some((w) => w.average_paise > 0)) return null;
  return (
    <figure className="mt-4">
      <figcaption className="mb-1.5 text-xs text-slate-400">Average by weekday, last eight weeks</figcaption>
      <div className="flex h-16 items-end gap-1.5 border-b border-slate-600" role="list">
        {data.weekdays.map((w) => {
          const h = (w.average_paise / max) * 100;
          return (
            <div
              key={w.weekday}
              role="listitem"
              className="relative flex h-full flex-1 items-end justify-center"
              title={`${WEEKDAYS[w.weekday]}: ${rupees(w.average_paise, { whole: true })} on average`}
              aria-label={`${WEEKDAYS[w.weekday]}: ${rupees(w.average_paise, { whole: true })} on average`}
            >
              {w === top && w.average_paise > 0 && (
                <span className="absolute text-[10px] font-medium text-slate-200" style={{ bottom: `calc(${h}% + 2px)` }}>
                  {compact(w.average_paise)}
                </span>
              )}
              <span className="w-full max-w-7 rounded-t-[4px]" style={{ height: `${Math.max(h, w.average_paise ? 3 : 0)}%`, backgroundColor: SERIES }} />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-1.5 text-center text-[10px] text-slate-500">
        {data.weekdays.map((w) => (
          <span key={w.weekday} className="flex-1">
            {WEEKDAYS[w.weekday]}
          </span>
        ))}
      </div>
    </figure>
  );
}

/** Where everyday money goes in habits: quick-commerce baskets and food deliveries and what
 * was in them, small spends and the shops visited most, with tips only where the numbers hold. */
function Habits({ data }: { data: Daily }) {
  const navigate = useNavigate();
  const txns = (q: string) => navigate(`/money/transactions?${new URLSearchParams({ month: data.month, q })}`);
  return (
    <Card title="Habits and leaks">
      <p className="-mt-1 mb-3 text-xs text-slate-500">
        Where everyday money slips away: food deliveries, quick-commerce orders and what was in them, small spends and the shops you go
        back to. Tracking comes first; a tip only shows once the numbers behind it are in.
      </p>
      <Orders
        title="Food deliveries"
        icon={UtensilsCrossed}
        summary={data.delivery}
        last={data.delivery_last}
        empty="No Zomato or Swiggy food orders this month."
        untold={(n) => `${n} order${n === 1 ? " has" : "s have"} no item list (only Zomato's emails list items).`}
        onPlace={txns}
      />
      <Orders
        title="Quick commerce"
        icon={ShoppingBasket}
        summary={data.baskets}
        last={data.baskets_last}
        empty="No Zepto, Blinkit, Instamart or similar orders this month."
        untold={(n) => `${n} order${n === 1 ? " is" : "s are"} waiting on "What did you buy?" in Money suggests: a tap each.`}
        onPlace={txns}
      />
      <OrderShot label="Add order screenshots" className="-mt-1 mb-3" />
      <div className="rounded-lg bg-ink/40 px-2.5 py-1.5 text-xs" title="Everyday spends under ₹300 each">
        <p className="flex items-baseline gap-2">
          <span className="text-slate-300">Small spends</span>
          <span className="text-slate-500">{data.small.count} under ₹300</span>
          <span className="ml-auto text-sm font-semibold tabular-nums text-slate-100">{rupees(data.small.spent_paise, { whole: true })}</span>
        </p>
      </div>
      {data.frequent.length > 0 && (
        <section aria-labelledby="frequent-h" className="mt-3">
          <h3 id="frequent-h" className="mb-1 text-xs text-slate-400">
            Shops you went back to most
          </h3>
          <ol className="space-y-0.5 text-sm">
            {data.frequent.map((f) => (
              <li key={f.merchant}>
                <button onClick={() => txns(f.merchant)} className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-edge/50">
                  <span className="min-w-0 flex-1 truncate text-slate-200">{f.merchant}</span>
                  <span className="text-xs text-slate-500">{f.count}×</span>
                  <span className="w-20 text-right tabular-nums text-slate-200">{rupees(f.spent_paise, { whole: true })}</span>
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
      {data.tips.length > 0 && (
        <section aria-labelledby="tips-h" className="mt-3 border-t border-edge/60 pt-3">
          <h3 id="tips-h" className="mb-1.5 flex items-center gap-1.5 text-xs text-slate-400">
            <Lightbulb size={13} /> What stands out
          </h3>
          <ul className="space-y-1.5 text-sm text-slate-300">
            {data.tips.map((t) => (
              <li key={t.kind}>{t.text}</li>
            ))}
          </ul>
        </section>
      )}
    </Card>
  );
}

/** One sort of order (food deliveries, quick commerce): count and cost against last month,
 * what was in them as bars (one hue; treats and fees named in words), and the places ordered
 * from most. */
function Orders({
  title,
  icon: Icon,
  summary: b,
  last,
  empty,
  untold,
  onPlace,
}: {
  title: string;
  icon: typeof ShoppingBasket;
  summary: BasketSummary;
  last: { orders: number; spent_paise: number };
  empty: string;
  untold: (n: number) => string;
  onPlace: (q: string) => void;
}) {
  const known = b.spent_paise ? Math.round((b.told_paise / b.spent_paise) * 100) : 0;
  const maxKind = Math.max(...b.by_kind.map((k) => k.spent_paise), 1);
  return (
    <section aria-label={title} className="mb-3">
      <h3 className="mb-0.5 flex items-center gap-1.5 text-sm text-slate-200">
        <Icon size={14} className="text-slate-400" /> {title}
        <span className="ml-auto tabular-nums">{rupees(b.spent_paise, { whole: true })}</span>
      </h3>
      {b.orders ? (
        <>
          <p className="text-xs text-slate-500">
            {b.orders} order{b.orders === 1 ? "" : "s"}, about {rupees(b.average_paise, { whole: true })} each
            {last.orders > 0 && ` · last month ${last.orders} for ${rupees(last.spent_paise, { whole: true })}`}
          </p>
          {b.by_kind.length > 0 && (
            <ul className="mt-2 space-y-1.5">
              {b.by_kind.map((k) => (
                <li key={k.kind} className="text-xs">
                  <div className="flex items-baseline gap-2">
                    <span className="min-w-0 flex-1 truncate text-slate-300">
                      {k.label}
                      {!k.need && !["meals", "tobacco", "gadgets"].includes(k.kind) && (
                        <span className="ml-1.5 text-slate-500">{k.kind === "fees" ? "· avoidable" : "· treat"}</span>
                      )}
                    </span>
                    <span className="tabular-nums text-slate-200">{rupees(k.spent_paise, { whole: true })}</span>
                  </div>
                  <div className="mt-0.5 h-1.5 rounded-full bg-edge/60">
                    <div className="h-1.5 rounded-full" style={{ width: `${(k.spent_paise / maxKind) * 100}%`, backgroundColor: SERIES }} />
                  </div>
                </li>
              ))}
            </ul>
          )}
          {b.places.length > 0 && (
            <p className="mt-1.5 text-xs text-slate-500">
              Most from:{" "}
              {b.places.slice(0, 3).map((p, i) => (
                <span key={p.place}>
                  {i > 0 && ", "}
                  <button onClick={() => onPlace(p.place)} className="text-slate-300 hover:text-white">
                    {p.place}
                  </button>{" "}
                  {p.count}×
                </span>
              ))}
            </p>
          )}
          <p className="mt-1 text-xs text-slate-500">
            {b.untold ? `Money knows what was in ${known}% of it. ${untold(b.untold)}` : "Money knows what was in every order."}
          </p>
        </>
      ) : (
        <p className="text-xs text-slate-500">{empty}</p>
      )}
    </section>
  );
}

/** Cigarettes and tobacco: what they cost this month against last, on how many days, how long
 * since the last purchase, what the recent pace comes to in a year, and a monthly limit to keep
 * to. Plain facts to manage it by; no lectures. */
function Smoking({ data }: { data: Daily }) {
  const s = data.smoking;
  const navigate = useNavigate();
  const [limit, setLimit] = useState<number | null>(s.budget_paise);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  useEffect(() => setLimit(s.budget_paise), [s.budget_paise]);
  const tracked = s.spent_paise > 0 || s.last_month_paise > 0 || s.last_on !== null;
  const typed = text.trim() ? parseRupees(text) : null;
  const saveLimit = async () => {
    if (!typed) return;
    try {
      await api.moneySetBudget(SMOKING, typed);
      setLimit(typed);
      setEditing(false);
      setText("");
      toast(`Limit set: ${rupees(typed, { whole: true })} a month`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const shown = data.days.filter((d) => !d.future);
  const bought = shown.filter((d) => d.tobacco_paise > 0);
  const left = limit ? limit - s.spent_paise : 0;
  const since = s.days_since == null ? "Not lately" : s.days_since === 0 ? "Today" : `${s.days_since} day${s.days_since === 1 ? "" : "s"}`;
  return (
    <Card title="Cigarettes and tobacco">
      <p className="-mt-1 mb-3 text-xs text-slate-500">
        Payments at paan shops and tobacconists, and cigarettes in your Zepto or Blinkit orders. A shop paid by UPI under a person's name
        shows up in Money suggests once: answer "Cigarettes or a paan shop" and every payment there counts from then on.
      </p>
      {tracked ? (
        <>
          <div className="grid grid-cols-3 gap-2">
            <Fact
              label="This month"
              value={rupees(s.spent_paise, { whole: true })}
              sub={s.last_month_paise ? `Last month ${rupees(s.last_month_paise, { whole: true })}` : undefined}
            />
            <Fact
              label="Days bought"
              value={`${s.days} of ${data.counted_days}`}
              sub={s.days ? `About ${rupees(s.average_paise, { whole: true })} a time` : undefined}
            />
            <Fact label="Since the last" value={since} sub={s.last_on ? dayLabel(s.last_on) : undefined} />
          </div>
          {shown.length > 0 && (
            <div className="mt-3">
              <ol aria-label="Days with a purchase this month" className="flex flex-wrap gap-0.5">
                {shown.map((d) => (
                  <li
                    key={d.on}
                    title={`${dayLabel(d.on)}: ${d.tobacco_paise ? rupees(d.tobacco_paise, { whole: true }) : "none"}`}
                    className="h-3 w-3 rounded-sm border border-edge/70"
                    style={d.tobacco_paise ? { backgroundColor: SERIES, borderColor: SERIES } : undefined}
                  />
                ))}
              </ol>
              <p className="mt-1 text-xs text-slate-500">
                {bought.length
                  ? `Bought on ${bought.map((d) => dayLabel(d.on).split(" ")[0]).join(", ")}; ${s.free_days} day${s.free_days === 1 ? "" : "s"} without.`
                  : "Nothing bought so far this month."}
              </p>
            </div>
          )}
          {s.year_paise != null && s.year_paise > 0 && (
            <p className="mt-2 text-sm text-slate-300">
              At the pace of the last few weeks that's about{" "}
              <span className="font-semibold text-slate-100">{rupees(s.year_paise, { whole: true })}</span> a year.
            </p>
          )}
          {s.shops.length > 0 && (
            <p className="mt-1.5 text-xs text-slate-500">
              Mostly at:{" "}
              {s.shops.map((p, i) => (
                <span key={p.merchant}>
                  {i > 0 && ", "}
                  <button
                    onClick={() => navigate(`/money/transactions?${new URLSearchParams({ month: data.month, q: p.merchant })}`)}
                    className="text-slate-300 hover:text-white"
                  >
                    {p.merchant}
                  </button>{" "}
                  {p.count}×
                </span>
              ))}
            </p>
          )}
        </>
      ) : (
        <p className="text-sm text-slate-400">Nothing tracked yet this month or last.</p>
      )}
      <div className="mt-3 border-t border-edge/60 pt-3 text-sm">
        {limit && !editing ? (
          <>
            <p className="flex items-baseline gap-2">
              <span className="text-slate-300">Monthly limit {rupees(limit, { whole: true })}</span>
              <span className="ml-auto tabular-nums text-slate-100">
                {left >= 0 ? `${rupees(left, { whole: true })} left` : `${rupees(-left, { whole: true })} over`}
              </span>
            </p>
            <div
              className="mt-1 h-1.5 rounded-full bg-edge/60"
              role="img"
              aria-label={`${Math.round((s.spent_paise / limit) * 100)}% of the limit used`}
            >
              <div
                className="h-1.5 rounded-full"
                style={{ width: `${Math.min(100, (s.spent_paise / limit) * 100)}%`, backgroundColor: left >= 0 ? SERIES : "#d03b3b" }}
              />
            </div>
            <button onClick={() => setEditing(true)} className="mt-1 text-xs text-sky-300 hover:text-sky-200">
              Change the limit
            </button>
          </>
        ) : (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void saveLimit();
            }}
          >
            <label htmlFor="smoke-limit" className="text-xs text-slate-400">
              {limit ? "New monthly limit" : "Set a monthly limit to cut down to"}
            </label>
            <input
              id="smoke-limit"
              value={text}
              onChange={(e) => setText(e.target.value)}
              inputMode="decimal"
              placeholder={s.last_month_paise ? String(Math.round((s.last_month_paise * 0.75) / 100)) : "1500"}
              className="w-24 rounded-md border border-edge bg-ink px-2 py-1 text-base sm:text-sm"
            />
            <button
              type="submit"
              disabled={!typed}
              className="rounded-md border border-edge px-2.5 py-1 text-sm text-slate-200 hover:bg-edge disabled:opacity-50"
            >
              Save
            </button>
            {limit && (
              <button type="button" onClick={() => setEditing(false)} className="text-xs text-slate-400 hover:text-slate-200">
                Cancel
              </button>
            )}
          </form>
        )}
        <p className="mt-2 text-xs text-slate-500">Free help to quit or cut down: the national quitline, 1800-11-2356.</p>
      </div>
    </Card>
  );
}
