import { AlertTriangle, ArrowRight, CheckCircle2, CircleHelp, OctagonAlert, Pencil, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import { balanceShares, percent, rupees, type Health, type HealthCheck, type HealthStatus } from "../../lib/money";
import { toast } from "../Feedback";
import { Guide, GuideHeading } from "../Guide";
import { SERIES } from "./Charts";
import { FormDialog, moneyText, percentText, read, type Field } from "./FormDialog";
import { Card, Spinner, useMoney } from "./parts";

/** Status colours from the chart palette, always shown with an icon and a word. */
export const STATUS: Record<HealthStatus, { icon: typeof CheckCircle2; colour: string; word: string }> = {
  good: { icon: CheckCircle2, colour: "#0ca30c", word: "Healthy" },
  watch: { icon: AlertTriangle, colour: "#fab219", word: "Watch" },
  act: { icon: OctagonAlert, colour: "#d03b3b", word: "Act on this" },
  unknown: { icon: CircleHelp, colour: "#94a3b8", word: "Not known" },
};

const VERDICT = {
  good: "In good shape",
  watch: "Mostly safe, a few things to watch",
  act: "Needs attention",
} as const;

/** The Health tab: is the money safe? Checks with a status each, what's safe to spend for the
 * rest of the month, and the balance between essentials, lifestyle and saving. */
export function HealthView() {
  const { data, error } = useMoney(api.moneyHealth);
  const [editing, setEditing] = useState(false);
  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <Spinner />;
  return (
    <>
      <Guide title="How the health check works">
        <div>
          <GuideHeading>Safety before growth</GuideHeading>
          Being safe means a bad month, a job change or a hospital bill doesn't push you into debt. So the checks look at
          a cushion of savings, how much you keep each month, debt you owe, and insurance, before anything about investing.
        </div>
        <div>
          <GuideHeading>Where the numbers come from</GuideHeading>
          Your usual month is the average of the last three full months of transactions. The cushion is what's in your
          bank accounts plus FDs. Card use and bills come from the Accounts tab. Insurance is what you tell Money here; it
          can't see your policies.
        </div>
        <div>
          <GuideHeading>The rules of thumb</GuideHeading>
          Six months of spending put aside. Keep 20% or more of income. EMIs under 30% of take-home pay. Card balances under
          30% of the limit and paid in full. ₹10 lakh of health cover of your own. Term life cover of ten years' income once
          someone depends on you. Change your own targets with the edit button.
        </div>
        <div>
          <GuideHeading>Safe to spend</GuideHeading>
          This month's expected income, less your saving target, what you've already spent, and EMIs and renewals still to
          come. What's left, spread over the days remaining, is what you can spend without eating into savings.
        </div>
      </Guide>

      <Verdict health={data} onEdit={() => setEditing(true)} />

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card title="Checks">
          <ul className="divide-y divide-edge">
            {data.checks.map((c) => (
              <CheckRow key={c.key} c={c} onTell={() => setEditing(true)} />
            ))}
          </ul>
        </Card>
        <div className="grid min-w-0 content-start gap-4">
          <SafeToSpend health={data} onEdit={() => setEditing(true)} />
          <Balance health={data} />
        </div>
      </div>
      {editing && <TargetsDialog health={data} onClose={() => setEditing(false)} />}
    </>
  );
}

function Verdict({ health, onEdit }: { health: Health; onEdit: () => void }) {
  const v = STATUS[health.verdict];
  const c = health.counts;
  const total = health.checks.length;
  const next = health.next_step;
  return (
    <Card>
      <div className="flex flex-wrap items-start gap-3">
        <v.icon size={28} className="mt-0.5 shrink-0" style={{ color: v.colour }} aria-hidden />
        <div className="min-w-[14rem] flex-1">
          <p className="text-lg font-semibold text-slate-100">{VERDICT[health.verdict]}</p>
          <p className="text-sm text-slate-400">
            {c.good} of {total} checks healthy
            {c.watch ? ` · ${c.watch} to watch` : ""}
            {c.act ? ` · ${c.act} to act on` : ""}
            {c.unknown ? ` · ${c.unknown} not known yet` : ""}
          </p>
        </div>
        <button onClick={onEdit} className="ml-10 flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-xs text-slate-200 hover:bg-edge/60 sm:ml-0">
          <Pencil size={11} /> Targets and cover
        </button>
      </div>
      {next && (
        <div className="mt-3 rounded-lg border border-edge bg-ink/40 px-3 py-2.5 text-sm">
          <p className="text-xs uppercase tracking-wide text-slate-500">Most important next</p>
          <p className="mt-0.5 text-slate-100">
            {next.title}: {next.status === "unknown" ? "not known yet" : next.value}
            <span className="text-slate-400"> (aim for {next.target})</span>
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-slate-400">{next.body}</p>
          {next.key === "health" || next.key === "life" ? (
            <button onClick={onEdit} className="mt-1.5 flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200">
              Tell Money your cover <ArrowRight size={11} />
            </button>
          ) : (
            next.link && (
              <Link to={next.link} className="mt-1.5 inline-flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200">
                {LINK_WORDS[next.key]} <ArrowRight size={11} />
              </Link>
            )
          )}
        </div>
      )}
      {!next && (
        <p className="mt-3 flex items-center gap-1.5 text-sm text-slate-300">
          <ShieldCheck size={14} style={{ color: STATUS.good.colour }} aria-hidden />
          Every check is healthy.{" "}
          {health.ready_to_invest && (
            <Link to="/money/plan" className="text-sky-300 hover:text-sky-200">
              The Plan shows what investing the surplus would do.
            </Link>
          )}
        </p>
      )}
    </Card>
  );
}

function StatusPill({ status }: { status: HealthStatus }) {
  const s = STATUS[status];
  return (
    <span className="inline-flex shrink-0 items-center gap-1 text-xs" style={{ color: s.colour }}>
      <s.icon size={13} aria-hidden />
      <span className="text-slate-300">{s.word}</span>
    </span>
  );
}

function CheckRow({ c, onTell }: { c: HealthCheck; onTell: () => void }) {
  const tell = c.key === "health" || c.key === "life";
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="min-w-0 flex-1 text-sm font-medium text-slate-100">{c.title}</span>
        <StatusPill status={c.status} />
      </div>
      <p className="mt-0.5 text-sm tabular-nums text-slate-200">
        {c.value} <span className="text-xs text-slate-500">· aim for {c.target}</span>
      </p>
      <p className="mt-1 text-xs leading-relaxed text-slate-400">{c.body}</p>
      {(c.status !== "good" || tell) && (
        <div className="mt-1.5">
          {tell ? (
            <button onClick={onTell} className="flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200">
              {c.status === "unknown" ? "Tell Money your cover" : "Update cover"} <ArrowRight size={11} />
            </button>
          ) : (
            c.link && (
              <Link to={c.link} className="inline-flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200">
                {LINK_WORDS[c.key]} <ArrowRight size={11} />
              </Link>
            )
          )}
        </div>
      )}
    </li>
  );
}

const LINK_WORDS: Record<HealthCheck["key"], string> = {
  emergency: "Plan an emergency fund goal",
  saving: "See where it goes",
  emis: "See loans and EMIs",
  cards: "See cards",
  bills: "See what's due",
  health: "",
  life: "",
};

function SafeToSpend({ health, onEdit }: { health: Health; onEdit: () => void }) {
  const s = health.safe_to_spend;
  if (!s.known)
    return (
      <Card title="Safe to spend this month">
        <p className="text-sm text-slate-500">Once a month of salary or other income shows up, this says how much you can spend and still save.</p>
      </Card>
    );
  const over = s.left_paise < 0;
  return (
    <Card
      title="Safe to spend this month"
      action={
        <button onClick={onEdit} className="flex items-center gap-1 text-sky-300 hover:text-sky-200">
          <Pencil size={11} /> Saving target
        </button>
      }
    >
      <p className="text-2xl font-semibold tabular-nums tracking-tight text-slate-100">{rupees(s.left_paise, { whole: true })}</p>
      <p className="text-xs text-slate-400">
        {over ? (
          <span className="inline-flex items-center gap-1">
            <OctagonAlert size={12} style={{ color: STATUS.act.colour }} aria-hidden /> Over by {rupees(-s.left_paise, { whole: true })}: further spending comes out of savings.
          </span>
        ) : (
          `about ${rupees(s.per_day_paise, { whole: true })} a day for the ${s.days_left} day${s.days_left === 1 ? "" : "s"} left`
        )}
      </p>
      <dl className="mt-3 space-y-1.5 text-sm">
        <Row label="Expected income" value={s.expected_income_paise} />
        <Row label={`Keep as savings (${percent(health.targets.save_target_bp)})`} value={-s.keep_paise} />
        <Row label="Spent so far" value={-s.spent_paise} />
        {s.committed_paise > 0 && <Row label="EMIs and renewals still due" value={-s.committed_paise} />}
        <div className="border-t border-edge pt-1.5">
          <Row label="Safe to spend" value={s.left_paise} strong />
        </div>
      </dl>
    </Card>
  );
}

function Row({ label, value, strong }: { label: string; value: number; strong?: boolean }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className={`flex-1 ${strong ? "text-slate-200" : "text-slate-400"}`}>{label}</dt>
      <dd className={`tabular-nums ${strong ? "font-semibold text-slate-100" : "text-slate-200"}`}>{rupees(value, { whole: true })}</dd>
    </div>
  );
}

const PARTS = [
  { key: "essentials", label: "Essentials", hint: "rent, bills, groceries, transport, health, EMIs", field: "essentials_paise" },
  { key: "lifestyle", label: "Lifestyle", hint: "eating out, shopping, trips, subscriptions, the rest", field: "lifestyle_paise" },
  { key: "saved", label: "Saved", hint: "what's left, including RDs, PPF and funds", field: "saved_paise" },
] as const;

/** Each part of a usual month against a common 50/30/20 guide: a bar for yours, a tick for
 * the guide. Numbers are written out beside each bar, so the bars never carry meaning alone. */
function Balance({ health }: { health: Health }) {
  const b = health.balance;
  const shares = balanceShares(b);
  return (
    <Card title="The balance of a usual month">
      {shares ? (
        <>
          <ul className="space-y-3">
            {PARTS.map((p) => {
              const share = shares[p.key];
              const guide = b.guide[p.key];
              const amount = b[p.field];
              const off = p.key === "saved" ? share < guide : share > guide + 0.05;
              return (
                <li key={p.key} title={`${p.label}: ${rupees(amount, { whole: true })} a month, ${Math.round(share * 100)}% of income (guide ${Math.round(guide * 100)}%)`}>
                  <div className="flex items-baseline gap-2 text-sm">
                    <span className="flex-1 text-slate-200">{p.label}</span>
                    <span className="tabular-nums text-slate-200">{Math.round(share * 100)}%</span>
                    <span className="w-20 text-right text-xs tabular-nums text-slate-500">{rupees(amount, { whole: true })}</span>
                  </div>
                  <div
                    className="relative mt-1 h-2 rounded-full bg-edge"
                    role="meter"
                    aria-label={`${p.label} share of income`}
                    aria-valuenow={Math.round(share * 100)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <div className="h-full rounded-full" style={{ width: `${Math.min(Math.max(share, 0), 1) * 100}%`, background: SERIES }} />
                    <div className="absolute -top-1 h-4 w-0.5 rounded bg-slate-300" style={{ left: `${guide * 100}%` }} aria-hidden />
                  </div>
                  <p className="mt-0.5 flex items-center gap-1 text-xs text-slate-500">
                    {off && <AlertTriangle size={11} style={{ color: STATUS.watch.colour }} aria-hidden />}
                    Guide {Math.round(guide * 100)}% · {p.hint}
                  </p>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 text-xs text-slate-500">
            Shares of income over the last three full months. The tick marks a common 50/30/20 guide; it's a starting point,
            not a rule. Categories decide which side a payment falls on.
          </p>
        </>
      ) : (
        <p className="text-sm text-slate-500">Needs a full month with income to show how it splits.</p>
      )}
    </Card>
  );
}

function TargetsDialog({ health, onClose }: { health: Health; onClose: () => void }) {
  const t = health.targets;
  const fields: Field[] = [
    { key: "save", label: "Keep each month", kind: "percent", value: percentText(t.save_target_bp), half: true, hint: "Share of income. 20% is a common aim." },
    { key: "months", label: "Emergency fund, months", kind: "int", value: String(t.emergency_months), half: true, hint: "Of your usual spending. 6 is usual." },
    {
      key: "health",
      label: "Health insurance cover",
      kind: "money",
      value: moneyText(t.health_cover_paise),
      optional: true,
      half: true,
      hint: "Your own policy plus any top-up. 0 if none; empty if you'd rather not say.",
    },
    {
      key: "dependants",
      label: "Does anyone depend on your income?",
      kind: "select",
      value: t.dependants == null ? "" : t.dependants ? "yes" : "no",
      options: [
        ["", "Rather not say"],
        ["no", "No, not yet"],
        ["yes", "Yes"],
      ],
      half: true,
    },
    {
      key: "life",
      label: "Term life cover",
      kind: "money",
      value: moneyText(t.life_cover_paise),
      optional: true,
      when: (v) => v.dependants === "yes",
      hint: "The sum assured on term policies. 0 if none.",
    },
  ];
  return (
    <FormDialog
      title="Targets and cover"
      description="Money uses these for the health checks and safe-to-spend. Insurance is only what you type here; nothing reads your policies."
      fields={fields}
      submitLabel="Save"
      onClose={onClose}
      onSubmit={async (v) => {
        await api.moneySaveAssumptions({
          save_target_bp: read.percent(v.save) ?? 2000,
          emergency_months: Math.min(Math.max(read.int(v.months) ?? 6, 1), 24),
          health_cover_paise: read.money(v.health),
          dependants: v.dependants === "" ? null : v.dependants === "yes",
          life_cover_paise: v.dependants === "yes" ? read.money(v.life) : null,
        });
        toast("Saved");
      }}
    />
  );
}

/** A one-card summary for the Overview: the verdict, safe to spend and the next step. */
export function HealthStrip() {
  const { data } = useMoney(api.moneyHealth);
  if (!data) return null;
  const v = STATUS[data.verdict];
  const s = data.safe_to_spend;
  return (
    <Link to="/money/health" className="mb-4 block rounded-xl border border-edge bg-panel p-4 hover:border-slate-600">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="flex min-w-0 items-center gap-2">
          <v.icon size={20} className="shrink-0" style={{ color: v.colour }} aria-hidden />
          <span className="min-w-0">
            <span className="block text-sm font-medium text-slate-100">{VERDICT[data.verdict]}</span>
            <span className="block truncate text-xs text-slate-400">
              {data.next_step ? nextLine(data.next_step) : `${data.counts.good} of ${data.checks.length} checks healthy`}
            </span>
          </span>
        </span>
        {s.known && (
          <span className="ml-auto text-right">
            <span className="block text-xs text-slate-400">Safe to spend this month</span>
            <span className="block text-lg font-semibold tabular-nums text-slate-100">
              {rupees(s.left_paise, { whole: true })}
              {s.left_paise >= 0 && <span className="ml-1 text-xs font-normal text-slate-500">{rupees(s.per_day_paise, { whole: true })}/day</span>}
            </span>
          </span>
        )}
        <ArrowRight size={16} className="shrink-0 text-slate-500" aria-hidden />
      </div>
    </Link>
  );
}

function nextLine(c: HealthCheck): string {
  return c.status === "unknown" ? `Next: tell Money about ${c.title.toLowerCase()}` : `Next: ${c.title} (${c.value})`;
}
