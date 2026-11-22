import { AlertTriangle, ArrowRight, CheckCircle2, CircleDashed, FileText, Loader2, Mail, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import { formatAgo } from "../../lib/format";
import { dayLabel, fullDay, type MailStatement, type Tracking, type TrackingAccount, type TrackingMonth } from "../../lib/money";
import { toast } from "../Feedback";
import { Card, Spinner, useMoney } from "./parts";

const MONTH_LETTERS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const STATUS: Record<TrackingAccount["status"], { label: string; colour: string; icon: typeof CheckCircle2 }> = {
  ok: { label: "Up to date", colour: "#0ca30c", icon: CheckCircle2 },
  gaps: { label: "Months missing", colour: "#fab219", icon: AlertTriangle },
  stale: { label: "Falling behind", colour: "#fab219", icon: AlertTriangle },
  empty: { label: "Nothing read", colour: "#94a3b8", icon: CircleDashed },
};

/** A line on the Overview when Money can't see everything, linking to Tracking. */
export function TrackingStrip() {
  const { data } = useMoney(api.moneyTracking);
  if (!data || (!data.needs_attention && !data.statements_waiting)) return null;
  const bits = [];
  if (data.needs_attention) bits.push(`${data.needs_attention} account${data.needs_attention > 1 ? "s are" : " is"} missing recent months`);
  if (data.statements_waiting) bits.push(`${data.statements_waiting} statement${data.statements_waiting > 1 ? "s" : ""} waiting in your mail`);
  return (
    <Link
      to="/money/tracking"
      className="mb-4 flex items-center gap-2.5 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-sm text-slate-200 hover:border-amber-500/50"
    >
      <AlertTriangle size={15} className="shrink-0 text-amber-300" aria-hidden />
      <span className="min-w-0 flex-1">Money can't see everything yet: {bits.join(", and ")}. Totals below leave those out.</span>
      <ArrowRight size={15} className="shrink-0 text-slate-500" aria-hidden />
    </Link>
  );
}

/** Tracking first: per account, what was read from alert emails and from statements, month by
 * month, and the one thing that fills each gap. Then the statements banks emailed. */
export function TrackingView() {
  const { data, error } = useMoney(api.moneyTracking);
  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <Spinner />;
  return (
    <div className="mb-4 grid gap-4">
      <MailStatements />
      <Coverage data={data} />
    </div>
  );
}

function Coverage({ data }: { data: Tracking }) {
  return (
    <Card title="What Money can see" action={<Legend />}>
      <p className="mb-3 text-xs leading-relaxed text-slate-400">
        Each square is a month. Spending and income only count what was read, so a gap here is a gap in every total and in the forecast. Alert emails keep you
        current; a statement fills in what the emails missed, like money coming in at banks that don't email for it.
      </p>
      {data.accounts.length ? (
        <ul className="divide-y divide-edge">
          {data.accounts.map((a) => (
            <AccountRow key={a.id} a={a} months={data.months} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-slate-500">No accounts yet. Add a statement or connect the Gmail your bank alerts go to.</p>
      )}
      {data.unassigned > 0 && (
        <p className="mt-3 text-xs text-slate-500">
          {data.unassigned} row{data.unassigned > 1 ? "s" : ""} in this period aren't tied to an account (cash, or orders not yet matched to a payment).
        </p>
      )}
      <div className="mt-4 border-t border-edge pt-3">
        <h3 className="mb-2 text-xs font-medium text-slate-400">Mail Money reads</h3>
        <ul className="space-y-1.5 text-sm">
          {data.mail
            .filter((m) => m.on || m.checked_at)
            .map((m) => (
              <li key={m.label} className="flex flex-wrap items-baseline gap-x-2">
                <Mail size={13} className="shrink-0 self-center text-slate-500" aria-hidden />
                <span className="text-slate-200">{m.label}</span>
                <span className="text-xs text-slate-500">
                  {m.checked_at ? `checked ${formatAgo(m.checked_at)}` : "not checked yet"}
                  {m.read_until && ` · newest email read ${formatAgo(m.read_until)}`}
                </span>
                {m.error && <span className="basis-full pl-5 text-xs text-rose-300">{m.error}</span>}
              </li>
            ))}
        </ul>
        <p className="mt-2 text-xs text-slate-500">
          Checked every hour. A quiet spell usually just means no spending on that account;{" "}
          <Link to="/money/setup" className="text-sky-300 hover:text-sky-200">
            Import and settings
          </Link>{" "}
          has the mailboxes and a read back.
        </p>
      </div>
    </Card>
  );
}

function Legend() {
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
      <span className="flex items-center gap-1">
        <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "var(--viz-1)" }} /> Alert emails
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "var(--viz-3)" }} /> Statements
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block h-2.5 w-2.5 rounded-sm border border-dashed border-amber-300/70" /> Expected salary only
      </span>
    </span>
  );
}

function AccountRow({ a, months }: { a: TrackingAccount; months: string[] }) {
  const s = STATUS[a.status];
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="font-medium text-slate-100">{a.label}</span>
        <span className="flex items-center gap-1 text-xs" style={{ color: s.colour }}>
          <s.icon size={12} aria-hidden /> {s.label}
        </span>
        <span className="ml-auto text-xs text-slate-500">
          {a.last_alert_on ? `Last email ${dayLabel(a.last_alert_on)}` : "No alert emails"}
          {" · "}
          {a.last_statement_on ? `statements to ${fullDay(a.last_statement_on)}` : "no statement"}
        </span>
      </div>
      <div className="grid grid-cols-12 gap-1" role="list" aria-label={`${a.label}, month by month`}>
        {a.months.map((m) => (
          <Cell key={m.month} m={m} />
        ))}
      </div>
      <div className="mt-1 grid grid-cols-12 gap-1 text-center text-[10px] text-slate-500" aria-hidden>
        {months.map((m) => (
          <span key={m}>{MONTH_LETTERS[Number(m.slice(5)) - 1]}</span>
        ))}
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-slate-400">{a.todo}</p>
    </li>
  );
}

function Cell({ m }: { m: TrackingMonth }) {
  const name = `${MONTH_NAMES[Number(m.month.slice(5)) - 1]} ${m.month.slice(0, 4)}`;
  const parts = [
    m.alerts && `${m.alerts} from alert emails`,
    m.statements && `${m.statements} from statements`,
    m.other && `${m.other} added or from orders`,
    m.expected && "expected salary only",
  ].filter(Boolean);
  const title = `${name}: ${parts.length ? parts.join(", ") : "nothing read"}`;
  const read = (m.alerts ?? 0) + (m.statements ?? 0) + (m.other ?? 0);
  return (
    <span
      role="listitem"
      title={title}
      aria-label={title}
      className={`flex h-6 flex-col gap-px overflow-hidden rounded-sm ${read ? "" : m.expected ? "border border-dashed border-amber-300/60" : "bg-edge/50"}`}
    >
      {/* One source fills the square; both split it, emails above statements. */}
      {m.alerts ? <span className="flex-1" style={{ background: "var(--viz-1)" }} /> : null}
      {m.statements ? <span className="flex-1" style={{ background: "var(--viz-3)" }} /> : null}
      {read > 0 && !m.alerts && !m.statements && <span className="flex-1 bg-slate-500" />}
    </span>
  );
}

/** Statement PDFs banks emailed: type the password once to read one in. */
function MailStatements() {
  const { data } = useMoney(api.moneyMailStatements);
  if (!data || !data.length) return null;
  const fresh = data.filter((s) => s.status === "new");
  return (
    <Card title="Statements in your mail">
      <p className="mb-3 text-xs leading-relaxed text-slate-400">
        Your banks emailed these. Type the PDF's password to read one in: it's used once to open the file, then dropped, and never saved. Reading one replaces
        what earlier statements gave for the same account and dates, so a badly read file is put right; alert emails and rows you added stay.
      </p>
      <ul className="space-y-3">
        {fresh.map((s) => (
          <MailStatementRow key={s.id} s={s} />
        ))}
        {data
          .filter((s) => s.status === "imported")
          .map((s) => (
            <li key={s.id} className="flex items-center gap-2 text-sm text-slate-400">
              <CheckCircle2 size={14} className="shrink-0 text-[#0ca30c]" aria-hidden />
              <span className="min-w-0 flex-1 truncate">{describe(s)}</span>
              <span className="text-xs">read in</span>
            </li>
          ))}
      </ul>
    </Card>
  );
}

function describe(s: MailStatement): string {
  const who = `${s.institution ?? "Bank"}${s.mask ? ` ••${s.mask}` : ""}`;
  const span = s.period_from && s.period_to ? `${fullDay(s.period_from)} to ${fullDay(s.period_to)}` : `emailed ${fullDay(s.received_at.slice(0, 10))}`;
  return `${who} · ${span}`;
}

function MailStatementRow({ s }: { s: MailStatement }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const r = await api.moneyImportMailStatement(s.id, { password, replace: true });
      const replaced = r.counts.replaced ? ` in place of ${r.counts.replaced}` : "";
      toast(`Read ${r.counts.added ?? 0} transactions${replaced}${r.counts.matched ? `, ${r.counts.matched} matched to alerts` : ""}`);
      setPassword("");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="rounded-lg border border-edge bg-ink/40 p-3">
      <div className="mb-2 flex items-start gap-2 text-sm">
        <FileText size={15} className="mt-0.5 shrink-0 text-slate-400" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="block text-slate-100">{describe(s)}</span>
          <span className="block text-xs text-slate-500">
            In {s.mailbox} · {formatAgo(s.received_at)}
          </span>
        </span>
        <button
          onClick={async () => {
            await api.moneyDismissMailStatement(s.id);
            toast("Hidden");
          }}
          className="p-1 text-slate-500 hover:text-slate-200"
          aria-label="Hide this statement"
          title="Hide it (a duplicate, or one you don't need)"
        >
          <X size={14} />
        </button>
      </div>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="off"
          placeholder="PDF password (the email says what it is)"
          aria-label="PDF password"
          className="min-w-0 flex-1 rounded-md border border-edge bg-ink px-2.5 py-1.5 text-base sm:text-sm"
        />
        <button
          type="submit"
          disabled={busy}
          className="flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
        >
          {busy && <Loader2 size={14} className="animate-spin" />} Read it in
        </button>
      </form>
    </li>
  );
}
