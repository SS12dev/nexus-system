import { AlertTriangle, CheckCircle2, Info, Pencil, Plus, Target, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import { addMonths, compact, fullDay, isoToday, percent, rupees, shortMonth, type Assumptions, type Goal, type Insight, type Plan } from "../../lib/money";
import { confirmAction, toast } from "../Feedback";
import { Guide, GuideHeading } from "../Guide";
import { ForecastChart, SERIES } from "./Charts";
import { FormDialog, moneyText, percentText, read, type Field } from "./FormDialog";
import { Card, Spinner, Stat, useMoney } from "./parts";

const HORIZONS = [
  { months: 12, label: "1 year" },
  { months: 24, label: "2 years" },
  { months: 60, label: "5 years" },
  { months: 120, label: "10 years" },
];

/** Net worth now and where it's heading, what a monthly investment would change, goals and
 * the assumptions behind it all. */
export function PlanView() {
  const [months, setMonths] = useState(24);
  const [sip, setSip] = useState<number | null>(null);
  const [debounced, setDebounced] = useState<number | null>(null);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(sip), 250);
    return () => clearTimeout(t);
  }, [sip]);
  const { data, error } = useMoney(() => api.moneyPlan({ months, new_sip_paise: debounced ?? undefined }), [months, debounced]);
  const [dialog, setDialog] = useState<"assumptions" | "goal" | Goal | null>(null);
  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <Spinner />;
  const end = data.points[data.points.length - 1];
  const s = data.start;
  const i = data.inputs;
  const sipNow = sip ?? data.assumptions.new_sip_paise;
  const sliderMax = Math.max(Math.ceil(Math.max(i.surplus_paise, 2000000) / 100000) * 100000, sipNow);
  return (
    <>
      <Guide title="How the forecast works">
        <div>
          <GuideHeading>Starting point</GuideHeading>
          Bank balances less what's owed on cards, plus mutual funds and the savings you track, less loans still owed.
          Accounts without a known balance count as zero, so add balances on the Accounts tab for a truer number.
        </div>
        <div>
          <GuideHeading>Each month after</GuideHeading>
          Income and spending start from your last three full months (or your own figures) and grow with the raise and
          inflation you set. EMIs drop off as loans end. Deposits, PPF and EPF follow their own rates; funds grow at the
          expected return.
        </div>
        <div>
          <GuideHeading>The likely range</GuideHeading>
          The shaded band widens with how much your monthly surplus has swung about, and uses the low and high fund
          returns. If the coming months look like recent ones, about two in three should land inside it.
        </div>
        <div>
          <GuideHeading>Not advice</GuideHeading>
          It's arithmetic on your own numbers, to see the effect of choices: a SIP, ending an EMI early, a goal. Fund
          returns aren't guaranteed and can be negative for a year or more.
        </div>
      </Guide>

      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Net worth now" value={rupees(s.net_worth_paise, { whole: true })} sub="everything less loans" />
        <Stat label="Bank, less card dues" value={rupees(s.cash_paise, { whole: true })} sub={data.unknown_balances ? `${data.unknown_balances} without a balance` : undefined} />
        <Stat label="Funds and savings" value={rupees(s.funds_paise + s.savings_paise, { whole: true })} sub={`${compact(s.funds_paise)} funds · ${compact(s.savings_paise)} savings`} />
        <Stat label="Loans" value={rupees(-s.debt_paise, { whole: true })} sub="principal still owed" />
      </div>
      {data.unknown_balances > 0 && (
        <p className="mb-4 flex items-center gap-2 text-xs text-slate-400">
          <Info size={13} className="shrink-0 text-slate-500" />
          {data.unknown_balances} bank account{data.unknown_balances > 1 ? "s have" : " has"} no balance yet, so the starting point is low.{" "}
          <Link to="/money/accounts" className="text-sky-300 hover:text-sky-200">
            Add balances
          </Link>
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="grid min-w-0 content-start gap-4">
          <Card>
            <div className="mb-3 flex flex-wrap gap-1" role="group" aria-label="How far ahead">
              {HORIZONS.map((h) => (
                <button
                  key={h.months}
                  onClick={() => setMonths(h.months)}
                  aria-pressed={months === h.months}
                  className={`rounded-full border px-2.5 py-1 text-xs ${months === h.months ? "border-sky-500/60 bg-sky-500/15 text-sky-100" : "border-edge text-slate-300 hover:bg-edge/60"}`}
                >
                  {h.label}
                </button>
              ))}
            </div>
            <ForecastChart start={s.net_worth_paise} points={data.points} />
            {end && (
              <p className="mt-3 text-sm text-slate-300">
                By {shortMonth(end.month)}, about <b className="text-slate-100">{rupees(end.net_worth_paise, { whole: true })}</b>; likely between{" "}
                {rupees(end.low_paise, { whole: true })} and {rupees(end.high_paise, { whole: true })}.
              </p>
            )}
          </Card>
          <Card title="Try investing each month">
            <p className="mb-3 text-xs text-slate-500">
              Move the slider to see the forecast with a monthly SIP at {percent(data.assumptions.fund_return_bp)} a year. Nothing is saved until you
              choose to.
            </p>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={sliderMax}
                step={50000}
                value={sipNow}
                onChange={(e) => setSip(Number(e.target.value))}
                className="flex-1 accent-sky-500"
                aria-label="Monthly investment"
              />
              <span className="w-24 text-right tabular-nums text-sm text-slate-100">{rupees(sipNow, { whole: true })}</span>
            </div>
            <p className="mt-2 text-sm text-slate-300">
              {sipNow > 0 ? (
                <>
                  Over {months / 12 >= 1 ? `${months / 12} year${months > 12 ? "s" : ""}` : `${months} months`} that puts in {rupees(sipNow * months, { whole: true })}.
                  {i.surplus_paise > 0 && sipNow > i.surplus_paise && (
                    <span className="mt-1 flex items-center gap-1 text-xs text-amber-200">
                      <AlertTriangle size={12} /> More than your usual surplus of {rupees(i.surplus_paise, { whole: true })}; the bank balance falls to pay for it.
                    </span>
                  )}
                </>
              ) : (
                "At ₹0, the forecast is your money as it goes today."
              )}
            </p>
            {sip != null && sip !== data.saved_assumptions.new_sip_paise && (
              <button
                onClick={async () => {
                  await api.moneySaveAssumptions({ ...data.assumptions, new_sip_paise: sip });
                  toast("Saved in your plan");
                }}
                className="mt-2 rounded-md border border-edge px-2.5 py-1 text-xs text-slate-200 hover:bg-edge/60"
              >
                Keep this in my plan
              </button>
            )}
          </Card>
          <Goals goals={data.goals} total={data.goals_monthly_paise} surplus={i.surplus_paise} onAdd={() => setDialog("goal")} onEdit={(g) => setDialog(g)} />
        </div>
        <div className="grid min-w-0 content-start gap-4">
          <Card
            title="A typical month"
            action={
              <button onClick={() => setDialog("assumptions")} className="flex items-center gap-1 text-sky-300 hover:text-sky-200">
                <Pencil size={11} /> Assumptions
              </button>
            }
          >
            <dl className="space-y-1.5 text-sm">
              <Line label="Comes in" value={i.income_paise} />
              <Line label="Spent" value={-i.spend_paise} note={i.emis_paise ? `with ${compact(i.emis_paise)} of EMIs` : undefined} />
              {i.contributions_paise > 0 && <Line label="Into RDs, PPF, NPS" value={-i.contributions_paise} />}
              {i.existing_investing_paise > 0 && <Line label="Into funds" value={-i.existing_investing_paise} />}
              <div className="border-t border-edge pt-1.5">
                <Line label="Left over" value={i.surplus_paise} strong note={i.surplus_spread_paise ? `swings by about ${compact(i.surplus_spread_paise)}` : undefined} />
              </div>
            </dl>
            <p className="mt-2 text-xs text-slate-500">
              {i.months_of_history ? `From your last ${Math.min(i.months_of_history, 3)} full months` : "No full month of transactions yet"}
              {data.saved_assumptions.income_paise != null || data.saved_assumptions.spend_paise != null ? ", with your own figures where set" : ""}.
            </p>
          </Card>
          {data.insights.length > 0 && (
            <Card title="Worth knowing">
              <ul className="space-y-3">
                {data.insights.map((x) => (
                  <InsightRow key={x.title} x={x} />
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
      {dialog === "assumptions" && <AssumptionsDialog a={data.assumptions} saved={data.saved_assumptions} plan={data} onClose={() => setDialog(null)} />}
      {dialog && dialog !== "assumptions" && <GoalDialog goal={dialog === "goal" ? null : dialog} onClose={() => setDialog(null)} />}
    </>
  );
}

function Line({ label, value, note, strong }: { label: string; value: number; note?: string; strong?: boolean }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className={`flex-1 ${strong ? "text-slate-200" : "text-slate-400"}`}>
        {label}
        {note && <span className="ml-1.5 text-xs text-slate-500">{note}</span>}
      </dt>
      <dd className={`tabular-nums ${strong ? "font-semibold text-slate-100" : "text-slate-200"}`}>{rupees(value, { whole: true })}</dd>
    </div>
  );
}

const TONE = {
  good: { icon: CheckCircle2, colour: "#0ca30c" },
  warning: { icon: AlertTriangle, colour: "#fab219" },
  info: { icon: Info, colour: "#94a3b8" },
} as const;

function InsightRow({ x }: { x: Insight }) {
  const t = TONE[x.tone];
  return (
    <li className="flex gap-2.5 text-sm">
      <t.icon size={15} className="mt-0.5 shrink-0" style={{ color: t.colour }} aria-hidden />
      <span>
        <span className="block text-slate-200">{x.title}</span>
        <span className="block text-xs leading-relaxed text-slate-400">{x.body}</span>
      </span>
    </li>
  );
}

function Goals({ goals, total, surplus, onAdd, onEdit }: { goals: Goal[]; total: number; surplus: number; onAdd: () => void; onEdit: (g: Goal) => void }) {
  return (
    <Card
      title="Goals"
      action={
        <button onClick={onAdd} className="flex items-center gap-1 text-sky-300 hover:text-sky-200">
          <Plus size={12} /> Add a goal
        </button>
      }
    >
      {goals.length ? (
        <>
          <ul className="space-y-3">
            {goals.map((g) => (
              <li key={g.id}>
                <button onClick={() => onEdit(g)} className="block w-full text-left">
                  <span className="flex items-baseline gap-2 text-sm">
                    <Target size={13} className="shrink-0 self-center text-slate-500" />
                    <span className="min-w-0 flex-1 truncate text-slate-100">{g.name}</span>
                    <span className="tabular-nums text-slate-300">
                      {compact(g.have_paise)} of {compact(g.target_paise)}
                    </span>
                  </span>
                  <span className="mt-1.5 block h-1.5 overflow-hidden rounded-full bg-edge" role="meter" aria-valuenow={Math.round(g.progress * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={`${g.name} progress`}>
                    <span className="block h-full rounded-full" style={{ width: `${g.progress * 100}%`, background: SERIES }} />
                  </span>
                  <span className="mt-1 block text-xs text-slate-500">
                    {g.monthly_needed_paise
                      ? `${rupees(g.monthly_needed_paise, { whole: true })} a month to reach it by ${fullDay(g.by_on)}`
                      : `On track for ${fullDay(g.by_on)}`}
                    {g.months_left > 0 && ` · at ${percent(g.rate_bp)} a year${g.months_left < 36 ? ", kept safe as it's under 3 years away" : ""}`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {total > 0 && (
            <p className="mt-3 flex items-center gap-1.5 border-t border-edge pt-2 text-xs text-slate-400">
              {total <= surplus ? <CheckCircle2 size={13} style={{ color: "#0ca30c" }} aria-hidden /> : <AlertTriangle size={13} style={{ color: "#fab219" }} aria-hidden />}
              All goals need {rupees(total, { whole: true })} a month;{" "}
              {total <= surplus ? `that fits in your usual surplus of ${rupees(surplus, { whole: true })}.` : `more than your usual surplus of ${rupees(Math.max(surplus, 0), { whole: true })}.`}
            </p>
          )}
        </>
      ) : (
        <p className="text-sm text-slate-500">An emergency fund, a trip, a laptop, a down payment: set the amount and date, and see what it takes each month.</p>
      )}
    </Card>
  );
}

function GoalDialog({ goal, onClose }: { goal: Goal | null; onClose: () => void }) {
  const fields: Field[] = [
    { key: "name", label: "Goal", kind: "text", value: goal?.name ?? "", placeholder: "Emergency fund, trip to Japan…" },
    { key: "target", label: "Amount", kind: "money", value: moneyText(goal?.target_paise), half: true },
    { key: "by_on", label: "By", kind: "date", value: goal?.by_on ?? addMonths(isoToday(), 12), half: true },
    { key: "saved", label: "Already put aside", kind: "money", value: moneyText(goal?.saved_paise ?? 0), optional: true },
  ];
  return (
    <FormDialog
      title={goal ? goal.name : "Add a goal"}
      fields={fields}
      submitLabel={goal ? "Save" : "Add"}
      onClose={onClose}
      extra={
        goal && (
          <button
            type="button"
            onClick={async () => {
              if (!(await confirmAction({ title: `Delete ${goal.name}?`, confirmLabel: "Delete", danger: true }))) return;
              await api.moneyDeleteGoal(goal.id);
              toast("Deleted");
              onClose();
            }}
            className="flex items-center gap-1 text-xs text-slate-400 hover:text-rose-300"
          >
            <Trash2 size={12} /> Delete
          </button>
        )
      }
      onSubmit={async (v) => {
        const body = { name: v.name.trim(), target_paise: read.money(v.target)!, by_on: v.by_on, saved_paise: read.money(v.saved) ?? 0 };
        if (goal) await api.moneyEditGoal(goal.id, body);
        else await api.moneyAddGoal(body);
        toast(goal ? "Saved" : "Goal added");
      }}
    />
  );
}

function AssumptionsDialog({ a, saved, plan, onClose }: { a: Assumptions; saved: Partial<Assumptions>; plan: Plan; onClose: () => void }) {
  const fields: Field[] = [
    {
      key: "income",
      label: "Monthly income",
      kind: "money",
      value: moneyText(saved.income_paise ?? null),
      optional: true,
      half: true,
      placeholder: String(Math.round(plan.inputs.income_paise / 100)),
      hint: "Empty: from your last three months.",
    },
    {
      key: "spend",
      label: "Monthly spending",
      kind: "money",
      value: moneyText(saved.spend_paise ?? null),
      optional: true,
      half: true,
      placeholder: String(Math.round(plan.inputs.spend_paise / 100)),
      hint: "Including EMIs.",
    },
    { key: "raise", label: "Pay rise a year", kind: "percent", value: percentText(a.income_growth_bp), half: true },
    { key: "inflation", label: "Spending grows a year", kind: "percent", value: percentText(a.inflation_bp), half: true, hint: "India's inflation target is 4%." },
    { key: "fund", label: "Fund return, expected", kind: "percent", value: percentText(a.fund_return_bp), half: true },
    { key: "low", label: "Low", kind: "percent", value: percentText(a.fund_low_bp), half: true },
    { key: "high", label: "High", kind: "percent", value: percentText(a.fund_high_bp), half: true },
    { key: "emergency", label: "Emergency fund, months", kind: "int", value: String(a.emergency_months), half: true },
    { key: "sip", label: "New monthly investment", kind: "money", value: moneyText(a.new_sip_paise), optional: true },
  ];
  return (
    <FormDialog
      title="Forecast assumptions"
      description="Long-run Indian equity funds have returned around 10–12% a year, with bad years well below zero; 10% expected, 6% to 13% for the range, is a cautious default."
      fields={fields}
      submitLabel="Save"
      onClose={onClose}
      onSubmit={async (v) => {
        await api.moneySaveAssumptions({
          income_paise: read.money(v.income),
          spend_paise: read.money(v.spend),
          income_growth_bp: read.percent(v.raise) ?? 0,
          inflation_bp: read.percent(v.inflation) ?? 0,
          fund_return_bp: read.percent(v.fund) ?? 0,
          fund_low_bp: read.percent(v.low) ?? 0,
          fund_high_bp: read.percent(v.high) ?? 0,
          new_sip_paise: read.money(v.sip) ?? 0,
          emergency_months: read.int(v.emergency) ?? 6,
        });
        toast("Assumptions saved");
      }}
    />
  );
}
