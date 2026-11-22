import { Banknote, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import { fullDay, isoToday, rupees, type Salary } from "../../lib/money";
import { confirmAction, toast } from "../Feedback";
import { FormDialog, moneyText, read, type Field } from "./FormDialog";
import { Card, useAccounts, useMoney } from "./parts";

/** Take-home pay over time: one row per raise. Some banks send no email when money comes in,
 * so a month whose salary wasn't read gets an expected row until the real credit is read. */
export function SalaryCard() {
  const { data } = useMoney(api.moneySalary, []);
  const accounts = useAccounts();
  const [dialog, setDialog] = useState<Salary | "new" | "raise" | null>(null);
  if (!data) return null;
  const names = new Map(accounts.map((a) => [a.id, a.label]));
  const items = data.items;
  return (
    <Card
      title="Salary"
      action={
        <button onClick={() => setDialog(items.length ? "raise" : "new")} className="flex items-center gap-1 text-sky-300 hover:text-sky-200">
          <Plus size={12} /> {items.length ? "Add a raise" : "Add your salary"}
        </button>
      }
    >
      {items.length ? (
        <>
          <ol className="space-y-2">
            {items.map((s) => {
              const now = s.id === data.current_id;
              return (
                <li key={s.id}>
                  <button onClick={() => setDialog(s)} className="group flex w-full items-baseline gap-2 text-left text-sm">
                    <Banknote size={13} className={`shrink-0 self-center ${now ? "text-[#0ca30c]" : "text-slate-500"}`} aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className={now ? "text-slate-100" : "text-slate-400"}>{rupees(s.monthly_paise, { whole: true })} a month</span>
                      <span className="block text-xs text-slate-500">
                        {s.from_on > isoToday() ? "From" : "Since"} {fullDay(s.from_on)}
                        {s.employer && ` · ${s.employer}`}
                        {s.account_id && names.get(s.account_id) && ` · into ${names.get(s.account_id)}`}
                        {` · around the ${ordinal(s.pay_day)}`}
                      </span>
                    </span>
                    {now && <span className="text-xs text-[#0ca30c]">now</span>}
                    <Pencil size={11} className="shrink-0 text-slate-600 group-hover:text-slate-300" aria-label="Edit" />
                  </button>
                </li>
              );
            })}
          </ol>
          <p className="mt-3 text-xs leading-relaxed text-slate-500">
            The forecast uses today's figure.{" "}
            {data.expected
              ? `${data.expected} month${data.expected > 1 ? "s" : ""} had no credit like it in what was read, so ${data.expected > 1 ? "they show" : "it shows"} an expected salary in Transactions until the real one turns up from an email or statement.`
              : "Every month since it started has a credit like it, so nothing is filled in."}
          </p>
        </>
      ) : (
        <p className="text-xs leading-relaxed text-slate-400">
          Your take-home pay, and each raise from the date it started. Some banks don't email when money comes in, so any month whose salary wasn't read gets an
          expected row (marked "expected") until the real credit is read from an email or a statement.
        </p>
      )}
      {dialog && (
        <SalaryDialog salary={typeof dialog === "object" ? dialog : null} last={dialog === "raise" ? items[0] : undefined} onClose={() => setDialog(null)} />
      )}
    </Card>
  );
}

function ordinal(n: number): string {
  const s = n % 100 >= 11 && n % 100 <= 13 ? "th" : (({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th");
  return `${n}${s}`;
}

function SalaryDialog({ salary, last, onClose }: { salary: Salary | null; last?: Salary; onClose: () => void }) {
  const accounts = useAccounts().filter((a) => a.kind === "bank");
  const base = salary ?? last;
  const fields: Field[] = [
    {
      key: "amount",
      label: "Take-home a month",
      kind: "money",
      value: moneyText(salary?.monthly_paise),
      placeholder: last ? `Was ${rupees(last.monthly_paise, { whole: true })}` : "What reaches your account",
      hint: "After tax, PF and other deductions: what reaches your account.",
      half: true,
    },
    {
      key: "from_on",
      label: last || salary ? "From" : "Since",
      kind: "date",
      value: salary?.from_on ?? (last ? isoToday() : ""),
      hint: last ? "The first month paid at the new amount." : "Roughly when you started at this amount.",
      half: true,
    },
    { key: "employer", label: "Employer", kind: "text", value: base?.employer ?? "", optional: true, placeholder: "Shown as the payee" },
    {
      key: "account_id",
      label: "Paid into",
      kind: "select",
      value: base?.account_id ?? "",
      optional: true,
      options: [["", "Any account"], ...accounts.map((a) => [a.id, a.label] as [string, string])],
      half: true,
    },
    {
      key: "pay_day",
      label: "Pay day",
      kind: "int",
      value: String(base?.pay_day ?? 1),
      hint: "Day of the month it usually arrives; a credit within six days counts.",
      half: true,
    },
  ];
  return (
    <FormDialog
      title={salary ? "Salary" : last ? "Add a raise" : "Add your salary"}
      description={last && !salary ? "The earlier amount stays for the months before; this one applies from the date you give." : undefined}
      fields={fields}
      submitLabel={salary ? "Save" : "Add"}
      onClose={onClose}
      extra={
        salary && (
          <button
            type="button"
            onClick={async () => {
              if (!(await confirmAction({ title: "Delete this salary?", body: "Expected rows it filled in go too.", confirmLabel: "Delete", danger: true })))
                return;
              await api.moneyDeleteSalary(salary.id);
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
        const day = read.int(v.pay_day) ?? 1;
        if (day < 1 || day > 31) throw new Error("Pay day: a day of the month, 1 to 31");
        const body = {
          monthly_paise: read.money(v.amount)!,
          from_on: v.from_on,
          employer: v.employer.trim(),
          account_id: v.account_id || null,
          pay_day: day,
        };
        if (salary) await api.moneyEditSalary(salary.id, body);
        else await api.moneyAddSalary(body);
        toast(salary ? "Saved" : "Salary added");
      }}
    />
  );
}
