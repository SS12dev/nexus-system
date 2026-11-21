import { AlertTriangle, CalendarClock, CheckCircle2, CircleDashed, CreditCard, EyeOff, HandCoins, Landmark, MoreHorizontal, OctagonAlert, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import {
  BILL_LABEL,
  addMonths,
  dayLabel,
  emiFor,
  fullDay,
  isoToday,
  percent,
  rupees,
  whenLabel,
  type AccountView,
  type BillStatus,
  type EmiSeen,
  type Loan,
  type LoanKind,
} from "../../lib/money";
import { confirmAction, toast } from "../Feedback";
import { SERIES } from "./Charts";
import { FormDialog, moneyText, percentText, read, secretChange, type Field, type Values } from "./FormDialog";
import { SecretLine } from "./Secret";
import { AddAccountDialog } from "./Onboard";
import { Empty, Spinner, Stat, useMoney } from "./parts";

const STATUS_STYLE: Record<BillStatus, { icon: typeof CheckCircle2; colour: string }> = {
  none: { icon: CircleDashed, colour: "#94a3b8" },
  paid: { icon: CheckCircle2, colour: "#0ca30c" },
  due: { icon: CalendarClock, colour: "#94a3b8" },
  part: { icon: AlertTriangle, colour: "#fab219" },
  overdue: { icon: OctagonAlert, colour: "#d03b3b" },
};

type Dialog =
  | { kind: "balance"; account: AccountView }
  | { kind: "card"; account: AccountView }
  | { kind: "loan"; loan?: Loan; seen?: EmiSeen }
  | null;

/** Every bank account, card and loan in one place: balances, card bills and what's owed, and
 * EMIs with what's left. */
export function AccountsView() {
  const { data, error } = useMoney(api.moneyBalances);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [adding, setAdding] = useState(false);
  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <Spinner />;
  const banks = data.items.filter((a) => a.kind === "bank" && (showHidden || !a.hidden));
  const cards = data.items.filter((a) => a.kind === "card" && (showHidden || !a.hidden));
  const hidden = data.items.filter((a) => a.hidden).length;
  const loans = data.loans;
  const active = loans.items.filter((l) => l.status === "active" && !l.done);
  const finished = loans.items.filter((l) => l.status !== "active" || l.done);
  const cardOptions = data.items.filter((a) => a.kind === "card");

  return (
    <>
      <div className="mb-4 flex flex-wrap items-start gap-3">
        <p className="min-w-[16rem] flex-1 text-sm text-slate-400">
          Every account and card, from what you add here, alert emails, screenshots and statements, with the latest balance each
          one showed. Card bills come from statement emails; type anything that's missing.
        </p>
        <button
          onClick={() => setAdding(true)}
          className="flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-xs text-slate-200 hover:bg-edge/60"
        >
          <Plus size={12} /> Add an account
        </button>
      </div>
      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat
          label="In the bank"
          value={rupees(data.cash_paise, { whole: true })}
          sub={data.unknown_balances ? `${data.unknown_balances} without a balance yet` : `${banks.length} accounts`}
          title="The latest balance each account showed in an alert or statement, or that you typed"
        />
        <Stat label="Owed on cards" value={rupees(data.owed_paise, { whole: true })} sub={`${cards.length} cards`} title="Unpaid bills plus spending since each statement" />
        <Stat label="EMIs a month" value={rupees(loans.monthly_paise, { whole: true })} sub={`${active.length} running`} />
        <Stat label="Loans left" value={rupees(loans.outstanding_paise, { whole: true })} sub="principal still owed" />
      </div>

      {data.items.length === 0 && (
        <Empty>
          No accounts yet.{" "}
          <button onClick={() => setAdding(true)} className="text-sky-300 hover:text-sky-200">
            Add your accounts and cards
          </button>{" "}
          with their last four digits, then switch on alert emails or add screenshots in{" "}
          <Link to="/money/setup" className="text-sky-300 hover:text-sky-200">
            Import and settings
          </Link>
          .
        </Empty>
      )}

      {cards.length > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
            <CreditCard size={15} className="text-slate-500" /> Credit cards
          </h2>
          <div className="grid gap-3 md:grid-cols-2">
            {cards.map((a) => (
              <CardTile key={a.id} a={a} onEdit={() => setDialog({ kind: "card", account: a })} />
            ))}
          </div>
        </section>
      )}

      {banks.length > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
            <Landmark size={15} className="text-slate-500" /> Bank accounts
          </h2>
          <ul className="divide-y divide-edge/60 rounded-xl border border-edge bg-panel">
            {banks.map((a) => (
              <li key={a.id} className="flex items-center gap-3 px-3 py-2.5">
                <span className="min-w-0 flex-1">
                  <Link to={`/money/transactions?account_id=${a.id}`} className="block truncate text-sm text-slate-100 hover:text-sky-200">
                    {a.label}
                    {a.hidden && <span className="ml-1.5 text-xs text-slate-500">(hidden)</span>}
                  </Link>
                  <span className="block truncate text-xs text-slate-500">
                    Spent {rupees(a.spent_month_paise, { whole: true })} · in {rupees(a.in_month_paise, { whole: true })} this month
                    {a.last_on && ` · last ${dayLabel(a.last_on)}`}
                  </span>
                </span>
                <span className="text-right">
                  {a.balance_paise != null ? (
                    <>
                      <span className="block tabular-nums text-sm text-slate-100">{rupees(a.balance_paise, { whole: true })}</span>
                      <span className="block text-xs text-slate-500">{a.balance_on ? `on ${dayLabel(a.balance_on)}` : ""}</span>
                    </>
                  ) : (
                    <button onClick={() => setDialog({ kind: "balance", account: a })} className="text-xs text-sky-300 hover:text-sky-200">
                      Add balance
                    </button>
                  )}
                </span>
                <button onClick={() => setDialog({ kind: "balance", account: a })} className="p-1 text-slate-400 hover:text-white" aria-label={`Edit ${a.label}`}>
                  <Pencil size={13} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mb-4">
        <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
          <HandCoins size={15} className="text-slate-500" /> Loans and EMIs
          <button
            onClick={() => setDialog({ kind: "loan" })}
            className="ml-auto flex items-center gap-1 rounded-md border border-edge px-2 py-1 text-xs text-slate-300 hover:bg-edge/60"
          >
            <Plus size={12} /> Add a loan or EMI
          </button>
        </h2>
        {loans.untracked.map((e) => (
          <div key={`${e.merchant}-${e.account_id}`} className="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-sky-500/30 bg-sky-500/[0.06] px-3 py-2.5 text-sm">
            <HandCoins size={15} className="shrink-0 text-sky-300" />
            <span className="min-w-0 flex-1 text-slate-300">
              <b className="text-slate-100">{e.merchant}</b> payments of {rupees(e.monthly_paise, { whole: true })} a month
              {e.account && ` on ${e.account}`}. Add the loan to see when it ends and what's left.
            </span>
            <button onClick={() => setDialog({ kind: "loan", seen: e })} className="rounded-md bg-sky-500 px-2.5 py-1 text-xs font-medium text-ink hover:bg-sky-400">
              Track it
            </button>
          </div>
        ))}
        {active.length ? (
          <div className="grid gap-3 md:grid-cols-2">
            {active.map((l) => (
              <LoanTile key={l.id} loan={l} onEdit={() => setDialog({ kind: "loan", loan: l })} />
            ))}
          </div>
        ) : (
          !loans.untracked.length && (
            <Empty>
              No loans or EMIs. Card EMIs are found from the bank's “converted to EMI” email and the EMI lines on statements;
              add any other loan by hand.
            </Empty>
          )
        )}
        {finished.length > 0 && (
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-xs text-slate-400">Finished or closed ({finished.length})</summary>
            <ul className="mt-2 divide-y divide-edge/60 rounded-xl border border-edge bg-panel">
              {finished.map((l) => (
                <li key={l.id} className="flex items-center gap-2 px-3 py-2">
                  <span className="min-w-0 flex-1 truncate text-slate-300">{l.name}</span>
                  <span className="text-xs text-slate-500">ended {dayLabel(l.ends_on)}</span>
                  <button onClick={() => setDialog({ kind: "loan", loan: l })} className="p-1 text-slate-400 hover:text-white" aria-label={`Edit ${l.name}`}>
                    <Pencil size={13} />
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>
      {hidden > 0 && (
        <button onClick={() => setShowHidden((v) => !v)} className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
          <EyeOff size={12} /> {showHidden ? "Hide" : "Show"} {hidden} hidden
        </button>
      )}

      {dialog?.kind === "balance" && <BankDialog a={dialog.account} onClose={() => setDialog(null)} />}
      {dialog?.kind === "card" && <CardDialog a={dialog.account} onClose={() => setDialog(null)} />}
      {dialog?.kind === "loan" && <LoanDialog loan={dialog.loan} seen={dialog.seen} cards={cardOptions} onClose={() => setDialog(null)} />}
      {adding && <AddAccountDialog onClose={() => setAdding(false)} />}
    </>
  );
}

function BillBadge({ status, due }: { status: BillStatus; due?: string | null }) {
  const s = STATUS_STYLE[status];
  const text = status === "due" && due ? `Due ${whenLabel(due)}` : BILL_LABEL[status];
  return (
    <span className="inline-flex items-center gap-1 text-xs text-slate-300">
      <s.icon size={13} style={{ color: s.colour }} aria-hidden />
      {text}
    </span>
  );
}

function CardTile({ a, onEdit }: { a: AccountView; onEdit: () => void }) {
  const bill = a.bill ?? { status: "none" as const, left_paise: 0 };
  const limit = a.credit_limit_paise ?? null;
  const used = limit && a.owed_paise != null ? Math.min(a.owed_paise / limit, 1) : null;
  const markPaid = async (paid: boolean) => {
    try {
      await api.moneyEditAccount(a.id, { paid });
      toast(paid ? "Marked paid" : "Marked not paid");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div className="rounded-xl border border-edge bg-panel p-3.5">
      <div className="flex items-start gap-2">
        <Link to={`/money/transactions?account_id=${a.id}`} className="min-w-0 flex-1 truncate text-sm font-medium text-slate-100 hover:text-sky-200">
          {a.label}
          {a.hidden && <span className="ml-1.5 text-xs font-normal text-slate-500">(hidden)</span>}
        </Link>
        <BillBadge status={bill.status} due={a.due_on} />
        <button onClick={onEdit} className="-m-1 p-1 text-slate-400 hover:text-white" aria-label={`Edit ${a.label}`}>
          <MoreHorizontal size={15} />
        </button>
      </div>
      {bill.status !== "none" && a.due_paise != null ? (
        <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <span className="text-xl font-semibold tabular-nums">{rupees(bill.status === "paid" ? a.due_paise : bill.left_paise, { whole: true })}</span>
          <span className="text-xs text-slate-400">
            {bill.status === "paid" ? "bill paid" : bill.status === "part" ? `left of ${rupees(a.due_paise, { whole: true })}` : "to pay"}
            {a.due_on && ` · due ${fullDay(a.due_on)}`}
          </span>
          {a.min_due_paise != null && bill.status !== "paid" && <span className="text-xs text-slate-500">minimum {rupees(a.min_due_paise, { whole: true })}</span>}
        </div>
      ) : (
        <p className="mt-2 text-xs text-slate-500">
          No statement read yet.{" "}
          <button onClick={onEdit} className="text-sky-300 hover:text-sky-200">
            Type the bill
          </button>
        </p>
      )}
      {used != null && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-edge" role="meter" aria-valuenow={Math.round(used * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Limit used">
            <div className="h-full rounded-full" style={{ width: `${used * 100}%`, background: SERIES }} />
          </div>
          <p className="mt-1 flex text-xs text-slate-500">
            <span>{Math.round(used * 100)}% of limit used</span>
            <span className="ml-auto">{a.available_paise != null && `${rupees(a.available_paise, { whole: true })} left`}</span>
          </p>
        </div>
      )}
      <p className="mt-2 text-xs text-slate-500">
        Spent {rupees(a.spent_month_paise, { whole: true })} this month
        {a.owed_paise != null && ` · about ${rupees(a.owed_paise, { whole: true })} owed now`}
      </p>
      {!!a.emis?.length && (
        <p className="mt-1 truncate text-xs text-slate-500">
          EMIs: {a.emis.map((e) => `${e.name} ${rupees(e.emi_paise, { whole: true })}`).join(", ")}
        </p>
      )}
      {(bill.status === "due" || bill.status === "part" || bill.status === "overdue") && (
        <button onClick={() => markPaid(true)} className="mt-2.5 rounded-md border border-edge px-2.5 py-1 text-xs text-slate-200 hover:bg-edge/60">
          I've paid it
        </button>
      )}
      {bill.status === "paid" && a.paid_on && (
        <button onClick={() => markPaid(false)} className="mt-2.5 text-xs text-slate-400 hover:text-slate-200">
          Not paid after all
        </button>
      )}
    </div>
  );
}

function LoanTile({ loan, onEdit }: { loan: Loan; onEdit: () => void }) {
  const done = loan.paid / loan.months;
  return (
    <div className="rounded-xl border border-edge bg-panel p-3.5">
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-slate-100">{loan.name}</span>
          <span className="block truncate text-xs text-slate-500">
            {loan.kind_label}
            {loan.account && ` · ${loan.account}`}
            {` · ${loan.rate_bp ? `${percent(loan.rate_bp)} a year` : "no interest"}`}
          </span>
        </span>
        <button onClick={onEdit} className="-m-1 p-1 text-slate-400 hover:text-white" aria-label={`Edit ${loan.name}`}>
          <MoreHorizontal size={15} />
        </button>
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-xl font-semibold tabular-nums">{rupees(loan.emi_paise, { whole: true })}</span>
        <span className="text-xs text-slate-400">a month · next {loan.next_due_on ? dayLabel(loan.next_due_on) : "—"}</span>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-edge" role="meter" aria-valuenow={loan.paid} aria-valuemin={0} aria-valuemax={loan.months} aria-label="Instalments paid">
        <div className="h-full rounded-full" style={{ width: `${done * 100}%`, background: SERIES }} />
      </div>
      <p className="mt-1 flex text-xs text-slate-500">
        <span>
          {loan.paid} of {loan.months} paid
        </span>
        <span className="ml-auto">ends {fullDay(loan.ends_on)}</span>
      </p>
      <p className="mt-1 text-xs text-slate-500">
        {rupees(loan.outstanding_paise, { whole: true })} still owed
        {loan.interest_left_paise > 0 && ` · ${rupees(loan.interest_left_paise, { whole: true })} interest to come`}
      </p>
      {loan.note && <p className="mt-1 text-xs text-amber-200/80">{loan.note}</p>}
      {loan.account_no_masked && (
        <SecretLine label="Loan account" masked={loan.account_no_masked} reveal={() => api.moneyLoanDetails(loan.id)} onAdd={() => {}} />
      )}
    </div>
  );
}

function BankDialog({ a, onClose }: { a: AccountView; onClose: () => void }) {
  const fields: Field[] = [
    { key: "name", label: "Name", kind: "text", value: a.name, placeholder: a.label, optional: true },
    {
      key: "balance",
      label: "Balance today",
      kind: "money",
      value: moneyText(a.balance_paise),
      optional: true,
      hint: "Updated again automatically by the next alert or statement that shows a balance.",
    },
    { key: "hidden", label: "Hide this account", kind: "check", value: a.hidden ? "1" : "", hint: "Its transactions still count." },
  ];
  return (
    <FormDialog
      title={a.label}
      fields={fields}
      submitLabel="Save"
      onClose={onClose}
      onSubmit={async (v) => {
        await api.moneyEditAccount(a.id, { name: v.name, balance_paise: read.money(v.balance), hidden: v.hidden === "1" });
        toast("Saved");
      }}
    />
  );
}

function CardDialog({ a, onClose }: { a: AccountView; onClose: () => void }) {
  const fields: Field[] = [
    { key: "name", label: "Name", kind: "text", value: a.name, placeholder: a.label, optional: true },
    {
      key: "limit",
      label: "Credit limit",
      kind: "money",
      value: moneyText(a.credit_limit_paise),
      optional: true,
      hint: "With the limit, what's owed comes from the available limit in the latest alert.",
    },
    { key: "due", label: "Current bill", kind: "money", value: moneyText(a.due_paise), optional: true, half: true },
    { key: "min", label: "Minimum due", kind: "money", value: moneyText(a.min_due_paise), optional: true, half: true },
    { key: "due_on", label: "Due date", kind: "date", value: a.due_on ?? "", optional: true },
    { key: "hidden", label: "Hide this card", kind: "check", value: a.hidden ? "1" : "", hint: "Its transactions still count." },
  ];
  return (
    <FormDialog
      title={a.label}
      description="Bills are read from statement emails when alert emails are on. Type one here if yours don't arrive by email."
      fields={fields}
      submitLabel="Save"
      onClose={onClose}
      onSubmit={async (v) => {
        const due = read.money(v.due);
        const changedBill = due !== (a.due_paise ?? null) || (v.due_on || null) !== (a.due_on ?? null);
        await api.moneyEditAccount(a.id, {
          name: v.name,
          credit_limit_paise: read.money(v.limit),
          hidden: v.hidden === "1",
          min_due_paise: read.money(v.min),
          ...(changedBill ? { due_paise: due, due_on: v.due_on || null } : {}),
        });
        toast("Saved");
      }}
    />
  );
}

const LOAN_KINDS: [LoanKind, string][] = [
  ["card_emi", "Card EMI"],
  ["personal", "Personal loan"],
  ["home", "Home loan"],
  ["vehicle", "Vehicle loan"],
  ["education", "Education loan"],
  ["other", "Other loan"],
];

function LoanDialog({ loan, seen, cards, onClose }: { loan?: Loan; seen?: EmiSeen; cards: AccountView[]; onClose: () => void }) {
  const firstDue = loan?.first_due_on ?? seen?.first_on ?? addMonths(isoToday(), 1);
  const fields: Field[] = [
    { key: "name", label: "Name", kind: "text", value: loan?.name ?? seen?.merchant ?? "", placeholder: "PS5" },
    { key: "kind", label: "Kind", kind: "select", value: loan?.kind ?? "card_emi", options: LOAN_KINDS, half: true },
    {
      key: "account_id",
      label: "On which card",
      kind: "select",
      value: loan?.account_id ?? seen?.account_id ?? "",
      options: [["", "None"], ...cards.map((c) => [c.id, c.label] as [string, string])],
      half: true,
      when: (v) => v.kind === "card_emi",
    },
    { key: "lender", label: "Lender", kind: "text", value: loan?.lender ?? "", optional: true, half: true, when: (v) => v.kind !== "card_emi" },
    { key: "principal", label: "Amount borrowed", kind: "money", value: moneyText(loan?.principal_paise), half: true },
    { key: "rate", label: "Interest a year", kind: "percent", value: percentText(loan?.rate_bp ?? 0), half: true, hint: "0 for a no-cost EMI." },
    { key: "months", label: "Months", kind: "int", value: loan ? String(loan.months) : "", half: true, placeholder: "12" },
    { key: "first_due_on", label: "First instalment", kind: "date", value: firstDue, half: true },
    {
      key: "emi",
      label: "EMI",
      kind: "money",
      value: moneyText(loan?.emi_paise ?? seen?.monthly_paise),
      optional: true,
      hint: "Leave empty to work it out. Card EMIs often add GST on the interest.",
    },
    {
      key: "account_no",
      label: "Loan account number",
      kind: "text",
      value: "",
      optional: true,
      placeholder: "On the loan letter or the bank's app",
      hint: "Kept encrypted; only the last four show. It goes into Export my finances.",
      secret: loan ? { saved: loan.account_no_masked, reveal: async () => (await api.moneyLoanDetails(loan.id)).account_no } : undefined,
    },
    { key: "closed", label: "Paid off or closed early", kind: "check", value: loan?.status === "closed" ? "1" : "", when: () => !!loan },
  ];
  const preview = (v: Values) => {
    const p = read.money(v.principal);
    const r = read.percent(v.rate) ?? 0;
    const n = read.int(v.months);
    if (!p || !n) return null;
    const emi = read.money(v.emi) ?? emiFor(p, r, n);
    return (
      <p className="rounded-md bg-ink/60 px-3 py-2 text-xs text-slate-400">
        {rupees(emi, { whole: true })} a month for {n} months: {rupees(emi * n, { whole: true })} in all,{" "}
        {rupees(Math.max(emi * n - p, 0), { whole: true })} of it interest. Last one {v.first_due_on ? fullDay(addMonths(v.first_due_on, n - 1)) : "—"}.
      </p>
    );
  };
  const remove = async () => {
    if (!loan) return;
    if (!(await confirmAction({ title: `Delete ${loan.name}?`, body: "Its payments stay in your transactions.", confirmLabel: "Delete", danger: true }))) return;
    try {
      await api.moneyDeleteLoan(loan.id);
      toast("Deleted");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <FormDialog
      title={loan ? `Edit ${loan.name}` : "Add a loan or EMI"}
      description={loan ? undefined : "What's left and when it ends are worked out from these terms; the forecast counts the money freed when it ends."}
      fields={fields}
      submitLabel={loan ? "Save" : "Add"}
      onClose={onClose}
      preview={preview}
      extra={
        loan && (
          <button type="button" onClick={remove} className="flex items-center gap-1 text-xs text-slate-400 hover:text-rose-300">
            <Trash2 size={12} /> Delete this loan
          </button>
        )
      }
      onSubmit={async (v) => {
        const body = {
          kind: v.kind as LoanKind,
          name: v.name.trim(),
          account_id: v.kind === "card_emi" ? v.account_id || null : null,
          lender: v.kind === "card_emi" ? null : v.lender || null,
          principal_paise: read.money(v.principal)!,
          rate_bp: read.percent(v.rate) ?? 0,
          months: read.int(v.months)!,
          emi_paise: read.money(v.emi),
          first_due_on: v.first_due_on,
          account_no: secretChange(v, "account_no"),
        };
        if (loan) {
          await api.moneyEditLoan(loan.id, { ...body, emi_paise: body.emi_paise ?? 0, status: v.closed === "1" ? "closed" : "active" });
        } else {
          await api.moneyAddLoan(body);
        }
        toast(loan ? "Saved" : "Loan added");
      }}
    />
  );
}
