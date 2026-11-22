import { CalendarCheck, Coins, Landmark, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import { dayLabel, daysUntil, fullDay, isoToday, percent, rupees, type Asset, type AssetIn, type AssetKind, type ContributionCadence, type Investments, type Stock } from "../../lib/money";
import { confirmAction, toast } from "../Feedback";
import { SERIES } from "./Charts";
import { FormDialog, moneyText, percentText, read, secretChange, type Field } from "./FormDialog";
import { SecretLine } from "./Secret";
import { Card, Empty, Spinner, Stat, useMoney } from "./parts";
import { GrowthCard } from "./Growth";
import { ShareDialog, SharesSection } from "./Shares";

/** How a deposit's frequency reads after its amount. */
const EVERY: Record<ContributionCadence | "april", string> = {
  monthly: "a month",
  quarterly: "every three months",
  yearly: "a year",
  april: "each April",
};

const ADD: { kind: AssetKind; label: string }[] = [
  { kind: "fd", label: "Fixed deposit" },
  { kind: "rd", label: "Recurring deposit" },
  { kind: "ppf", label: "PPF" },
  { kind: "epf", label: "EPF" },
  { kind: "nps", label: "NPS" },
  { kind: "gold", label: "Gold" },
  { kind: "other", label: "Other" },
];

/** Savings with fixed terms (FD, RD), government schemes (PPF, EPF, NPS), gold and anything
 * else, valued from their terms; then the mutual funds from the CAS. */
export function SavingsView() {
  const { data, error } = useMoney(api.moneySavings);
  const funds = useMoney(api.moneyInvestments).data;
  const stocks = useMoney(api.moneyStocks).data;
  const [dialog, setDialog] = useState<{ kind: AssetKind; asset?: Asset } | null>(null);
  const [share, setShare] = useState<{ stock?: Stock } | null>(null);
  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <Spinner />;
  const active = data.items.filter((a) => a.status === "active");
  const closed = data.items.filter((a) => a.status !== "active");
  const gain = funds && funds.cost_paise ? funds.value_paise - funds.cost_paise : null;
  const market = data.funds_value_paise + data.stocks_value_paise;
  return (
    <>
      <p className="mb-4 text-sm text-slate-400">
        Deposits and schemes you add here are valued from their terms: FDs and RDs compound quarterly, PPF takes a yearly
        deposit each April, EPF a monthly one. Mutual funds come from your CAS and are valued every night, and shares you add
        at NSE's closing price each evening.
      </p>
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Savings worth" value={rupees(data.total_paise, { whole: true })} sub={`${active.length} tracked`} />
        <Stat
          label={data.stocks_value_paise ? "Funds and shares" : "Mutual funds"}
          value={market ? rupees(market, { whole: true }) : "—"}
          sub={
            data.stocks_value_paise
              ? `shares ${rupees(data.stocks_value_paise, { whole: true })}`
              : gain != null
                ? `${rupees(gain, { whole: true, sign: true })} gain`
                : "from your CAS"
          }
        />
        <Stat label="Going in each month" value={rupees(data.monthly_paise, { whole: true })} sub="RDs, PPF, EPF, NPS" />
        <Stat label="Together" value={rupees(data.total_paise + market, { whole: true })} sub="savings, funds and shares" />
      </div>
      <GrowthCard />
      <div className="mb-5 flex flex-wrap gap-1.5" role="group" aria-label="Add savings">
        {ADD.map((a) => (
          <button key={a.kind} onClick={() => setDialog({ kind: a.kind })} className="flex items-center gap-1 rounded-full border border-edge px-2.5 py-1 text-xs text-slate-200 hover:bg-edge/60">
            <Plus size={12} /> {a.label}
          </button>
        ))}
        <button onClick={() => setShare({})} className="flex items-center gap-1 rounded-full border border-edge px-2.5 py-1 text-xs text-slate-200 hover:bg-edge/60">
          <Plus size={12} /> Shares
        </button>
      </div>
      {data.maturing.map((a) => (
        <div key={`m-${a.id}`} className="mb-3 flex items-center gap-2 rounded-xl border border-edge bg-panel px-3 py-2.5 text-sm text-slate-300">
          <CalendarCheck size={15} className="shrink-0 text-sky-300" />
          <span>
            <b className="text-slate-100">{a.name}</b> matures {a.maturity_on && `on ${fullDay(a.maturity_on)}`}
            {a.maturity_value_paise != null && ` with about ${rupees(a.maturity_value_paise, { whole: true })}`}. Decide where it goes next: renew, a
            goal, or your emergency fund.
          </span>
        </div>
      ))}
      {active.length ? (
        <div className="mb-6 grid gap-3 md:grid-cols-2">
          {active.map((a) => (
            <AssetTile key={a.id} a={a} onEdit={() => setDialog({ kind: a.kind, asset: a })} />
          ))}
        </div>
      ) : (
        <div className="mb-6">
          <Empty>
            Add your FDs, RDs, PPF and EPF above to see them grow and count them in your net worth and forecast. The EPF
            balance is on your passbook at passbook.epfindia.gov.in; PPF and deposits are in your bank's app.
          </Empty>
        </div>
      )}
      {closed.length > 0 && (
        <details className="mb-6 text-sm">
          <summary className="cursor-pointer text-xs text-slate-400">Closed ({closed.length})</summary>
          <ul className="mt-2 divide-y divide-edge/60 rounded-xl border border-edge bg-panel">
            {closed.map((a) => (
              <li key={a.id} className="flex items-center gap-2 px-3 py-2">
                <span className="min-w-0 flex-1 truncate text-slate-400">{a.name}</span>
                <button onClick={() => setDialog({ kind: a.kind, asset: a })} className="text-xs text-slate-400 hover:text-slate-200">
                  Edit
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      <SharesSection data={stocks} onAdd={() => setShare({})} onEdit={(stock) => setShare({ stock })} />
      <Funds data={funds} />
      {share && <ShareDialog stock={share.stock} onClose={() => setShare(null)} />}
      {dialog && <AssetDialog kind={dialog.kind} a={dialog.asset} defaults={data.default_rates} onClose={() => setDialog(null)} />}
    </>
  );
}

function AssetTile({ a, onEdit }: { a: Asset; onEdit: () => void }) {
  const term = a.start_on && a.maturity_on ? Math.min(Math.max(-daysUntil(a.start_on) / (daysUntil(a.maturity_on) - daysUntil(a.start_on)), 0), 1) : null;
  const sub: string[] = [a.kind_label];
  if (a.institution) sub.push(a.institution);
  sub.push(a.rate_bp ? `${percent(a.rate_bp)} a year` : "no return set");
  return (
    <div className="rounded-xl border border-edge bg-panel p-3.5">
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-slate-100">{a.name}</span>
          <span className="block truncate text-xs text-slate-500">{sub.join(" · ")}</span>
        </span>
        <button onClick={onEdit} className="-m-1 p-1 text-slate-400 hover:text-white" aria-label={`Edit ${a.name}`}>
          <MoreHorizontal size={15} />
        </button>
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-2">
        <span className="text-xl font-semibold tabular-nums">{rupees(a.value_paise, { whole: true })}</span>
        <span className="text-xs text-slate-400">worth today</span>
        {a.kind === "fd" && a.payout && <span className="text-xs text-slate-500">(interest paid out)</span>}
      </div>
      {term != null && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-edge" role="meter" aria-valuenow={Math.round(term * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Term elapsed">
            <div className="h-full rounded-full" style={{ width: `${term * 100}%`, background: SERIES }} />
          </div>
          <p className="mt-1 flex text-xs text-slate-500">
            <span>{a.matured ? "Matured" : `${Math.round(term * 100)}% of the term`}</span>
            <span className="ml-auto">
              {a.maturity_on && (a.matured ? `on ${fullDay(a.maturity_on)}` : `matures ${fullDay(a.maturity_on)}`)}
              {a.maturity_value_paise != null && !a.matured && ` · ${rupees(a.maturity_value_paise, { whole: true })}`}
            </span>
          </p>
        </div>
      )}
      <p className="mt-1.5 text-xs text-slate-500">
        {a.kind === "fd" && `${rupees(a.amount_paise, { whole: true })} deposited${a.start_on ? ` on ${dayLabel(a.start_on)}` : ""}`}
        {a.kind === "rd" && `${rupees(a.amount_paise, { whole: true })} a month`}
        {["ppf", "epf", "nps", "gold", "other"].includes(a.kind) &&
          `${rupees(a.amount_paise, { whole: true })} on ${a.balance_on ? dayLabel(a.balance_on) : "—"}${a.contribution_paise ? ` · ${rupees(a.contribution_paise, { whole: true })} ${EVERY[a.kind === "ppf" && a.contribution_cadence === "yearly" ? "april" : a.contribution_cadence]}` : ""}`}
      </p>
      {(a.last_paid_on || a.received_on) && (
        <p className="mt-1 text-xs text-slate-500">
          {a.received_on
            ? `Money came back on ${dayLabel(a.received_on)}${a.status === "active" ? ": mark it closed if it has ended" : ""}`
            : a.kind === "rd" && a.status === "active" && !a.paid_this_month && !a.matured && new Date().getDate() > Number(a.start_on?.slice(8, 10) ?? 1) + 3
              ? `This month's instalment not seen yet · last paid ${dayLabel(a.last_paid_on!)}`
              : `Last paid on ${dayLabel(a.last_paid_on!)}`}
        </p>
      )}
      <SecretLine
        label={NUMBER_LABEL[a.kind]}
        masked={a.account_no_masked}
        branch={a.branch && (a.kind === "epf" ? a.branch : `${a.branch} branch`)}
        nominee={a.has_nominee ? a.nominee_relation || "set" : null}
        reveal={() => api.moneySavingDetails(a.id)}
        onAdd={onEdit}
      />
    </div>
  );
}

const NUMBER_LABEL: Record<AssetKind, string> = {
  fd: "FD account number",
  rd: "RD account number",
  ppf: "PPF account number",
  epf: "UAN or member ID",
  nps: "PRAN",
  gold: "Folio or account number",
  other: "Account or reference number",
};

const HELP: Record<AssetKind, string> = {
  fd: "The deposit, its rate, and the start and maturity dates from the FD receipt.",
  rd: "The monthly instalment, the rate, and the start and maturity dates.",
  ppf: "Today's balance from your bank's PPF passbook and what you put in each year (up to ₹1.5 lakh). Matures 15 years after the end of the financial year it was opened.",
  epf: "Today's balance from passbook.epfindia.gov.in and what goes in each month: your 12% plus the employer's 3.67% share (the rest goes to EPS).",
  nps: "Today's value from the NPS app and what you put in each month. The return is your guess; equity-heavy NPS has averaged about 9–11% over long periods.",
  gold: "Today's value of gold, gold bonds or gold funds, and the yearly return you expect.",
  other: "Anything else you save: a chit fund, a friend's loan to you, a savings pot. Its value and the return you expect.",
};

function AssetDialog({ kind, a, defaults, onClose }: { kind: AssetKind; a?: Asset; defaults: Partial<Record<AssetKind, number>>; onClose: () => void }) {
  const term = kind === "fd" || kind === "rd";
  const balance = !term;
  const amountLabel = kind === "fd" ? "Deposit" : kind === "rd" ? "Each month" : "Balance today";
  const usual: ContributionCadence = kind === "ppf" ? "yearly" : "monthly";
  const fields: Field[] = [
    { key: "name", label: "Name", kind: "text", value: a?.name ?? "", optional: true, placeholder: ADD.find((x) => x.kind === kind)?.label, half: true },
    { key: "institution", label: "Where", kind: "text", value: a?.institution ?? "", optional: true, placeholder: kind === "epf" ? "EPFO" : "SBI, HDFC Bank…", half: true },
    { key: "amount", label: amountLabel, kind: "money", value: moneyText(a?.amount_paise), half: true },
    {
      key: "rate",
      label: kind === "nps" || kind === "gold" || kind === "other" ? "Expected return a year" : "Interest a year",
      kind: "percent",
      value: percentText(a?.rate_bp ?? defaults[kind]),
      half: true,
      hint: kind === "ppf" ? "7.1% for Oct–Dec 2026, set each quarter." : kind === "epf" ? "8.25% for 2025-26." : undefined,
    },
    ...(term
      ? ([
          { key: "start_on", label: "Started", kind: "date", value: a?.start_on ?? isoToday(), half: true },
          { key: "maturity_on", label: "Matures", kind: "date", value: a?.maturity_on ?? "", half: true },
        ] as Field[])
      : ([
          { key: "contribution", label: "Put in", kind: "money", value: moneyText(a?.contribution_paise || null), optional: true, half: true },
          {
            key: "cadence",
            label: "How often",
            kind: "select",
            value: a?.contribution_cadence ?? usual,
            options: [
              ["monthly", "Every month"],
              ["quarterly", "Every three months"],
              ["yearly", kind === "ppf" ? "Once a year, in April" : "Once a year"],
            ],
            half: true,
            hint: kind === "ppf" ? "PPF takes ₹500 to ₹1.5 lakh a year, in one go or monthly. Deposits by the 5th earn that month's interest." : undefined,
          },
          { key: "maturity_on", label: "Matures", kind: "date", value: a?.maturity_on ?? "", optional: true, half: true, when: () => kind === "ppf" || kind === "nps" },
        ] as Field[])),
    ...(kind === "fd" ? ([{ key: "payout", label: "Interest is paid out to my account", kind: "check", value: a?.payout ? "1" : "", hint: "Rather than added to the deposit." }] as Field[]) : []),
    ...(a ? ([{ key: "closed", label: "Closed or withdrawn", kind: "check", value: a.status === "closed" ? "1" : "" }] as Field[]) : []),
    {
      key: "for_family",
      label: "For whoever has to claim it",
      kind: "heading",
      value: "",
      hint: "Kept encrypted. Only the last four digits and the relation show until you press the eye, and they go into Export my finances.",
    },
    {
      key: "account_no",
      label: NUMBER_LABEL[kind],
      kind: "text",
      value: "",
      optional: true,
      half: true,
      placeholder: kind === "epf" ? "100912345678" : kind === "nps" ? "110012345678" : "Printed on the receipt or passbook",
      secret: a ? { saved: a.account_no_masked, reveal: async () => (await api.moneySavingDetails(a.id)).account_no } : undefined,
    },
    { key: "branch", label: kind === "epf" ? "Employer or EPFO office" : "Branch", kind: "text", value: a?.branch ?? "", optional: true, half: true },
    {
      key: "nominee",
      label: "Nominee",
      kind: "text",
      value: "",
      optional: true,
      half: true,
      placeholder: "Their full name",
      secret: a ? { saved: a.has_nominee ? "A nominee is" : null, reveal: async () => (await api.moneySavingDetails(a.id)).nominee ?? null } : undefined,
    },
    { key: "nominee_relation", label: "They are your", kind: "text", value: a?.nominee_relation ?? "", optional: true, half: true, placeholder: "Mother, spouse…" },
  ];
  const remove = async () => {
    if (!a) return;
    if (!(await confirmAction({ title: `Delete ${a.name}?`, confirmLabel: "Delete", danger: true }))) return;
    await api.moneyDeleteSaving(a.id);
    toast("Deleted");
    onClose();
  };
  return (
    <FormDialog
      title={a ? `Edit ${a.name}` : `Add ${ADD.find((x) => x.kind === kind)?.label}`}
      description={HELP[kind]}
      fields={fields}
      submitLabel={a ? "Save" : "Add"}
      onClose={onClose}
      extra={
        a && (
          <button type="button" onClick={remove} className="flex items-center gap-1 text-xs text-slate-400 hover:text-rose-300">
            <Trash2 size={12} /> Delete
          </button>
        )
      }
      onSubmit={async (v) => {
        const body: AssetIn = {
          kind,
          name: v.name.trim(),
          institution: v.institution.trim() || null,
          amount_paise: read.money(v.amount)!,
          rate_bp: read.percent(v.rate) ?? 0,
          payout: v.payout === "1",
          status: v.closed === "1" ? "closed" : "active",
          branch: v.branch.trim() || null,
          nominee_relation: v.nominee_relation.trim() || null,
          account_no: secretChange(v, "account_no"),
          nominee: secretChange(v, "nominee"),
        };
        if (term) Object.assign(body, { start_on: v.start_on, maturity_on: v.maturity_on });
        if (balance)
          Object.assign(body, {
            contribution_paise: read.money(v.contribution ?? "") ?? 0,
            contribution_cadence: (v.cadence as ContributionCadence) || usual,
            maturity_on: v.maturity_on || null,
          });
        if (a) {
          const { kind: _k, ...patch } = body;
          void _k;
          await api.moneyEditSaving(a.id, a.amount_paise === body.amount_paise ? { ...patch, amount_paise: undefined } : patch);
        } else await api.moneyAddSaving(body);
        toast(a ? "Saved" : "Added");
      }}
    />
  );
}

function Funds({ data }: { data: Investments | null }) {
  if (!data) return null;
  if (!data.items.length)
    return (
      <Card title="Mutual funds">
        <p className="flex gap-2 text-sm text-slate-400">
          <Coins size={15} className="mt-0.5 shrink-0 text-slate-500" />
          <span>
            None yet. Upload a CAS PDF from CAMS in{" "}
            <Link to="/money/setup" className="text-sky-300 hover:text-sky-200">
              Import and settings
            </Link>{" "}
            and every fund shows here, valued nightly.
          </span>
        </p>
      </Card>
    );
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
        <Landmark size={15} className="text-slate-500" /> Mutual funds
        <span className="ml-auto text-xs font-normal text-slate-500">
          {data.nav_date && `prices of ${dayLabel(data.nav_date)}`}
          {data.as_of && ` · units as of ${dayLabel(data.as_of)}`}
        </span>
      </h2>
      <ul className="divide-y divide-edge/60 rounded-xl border border-edge bg-panel">
        {data.items.map((h) => {
          const g = h.value_paise != null && h.cost_paise ? h.value_paise - h.cost_paise : null;
          return (
            <li key={h.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
              <span className="min-w-0 flex-1">
                <span className="block text-slate-100 sm:truncate">{h.scheme}</span>
                <span className="block truncate text-xs text-slate-500">
                  {h.amc} · folio {h.folio} · {h.units.toLocaleString("en-IN", { maximumFractionDigits: 3 })} units
                  {h.nav != null && ` × ₹${h.nav.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`}
                </span>
              </span>
              <span className="text-right">
                <span className="block tabular-nums text-slate-100">{h.value_paise != null ? rupees(h.value_paise, { whole: true }) : "—"}</span>
                {g != null && <span className="block tabular-nums text-xs text-slate-400">{rupees(g, { whole: true, sign: true })}</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
