import { Banknote, EyeOff, ShoppingBag } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { dayLabel, rupees, type OrderPaid, type OrderToConfirm } from "../../lib/money";
import { toast } from "../Feedback";
import { Select } from "../Select";
import { useAccounts, useMoney } from "./parts";

const SAID: Record<string, string> = {
  upi: "the email says UPI",
  card: "the email says a card",
  neft: "the email says net banking",
};

/** Orders from shop emails with no matching bank or card payment: "how did you pay?". Shown
 * on Overview and Transactions until each is answered. */
export function OrdersToConfirm() {
  const { data, reload } = useMoney(api.moneyOrdersToConfirm);
  const [items, setItems] = useState<OrderToConfirm[] | null>(null);
  // A fresh load (Money changed elsewhere) replaces the list kept after an answer.
  useEffect(() => setItems(null), [data]);
  const list = items ?? data?.items ?? [];
  if (!list.length) return null;
  const answer = async (o: OrderToConfirm, body: OrderPaid, done: string) => {
    try {
      setItems((await api.moneyOrderPaid(o.id, body)).items);
      toast(done);
    } catch (e) {
      toast((e as Error).message, "error");
      reload();
    }
  };
  return (
    <section className="mb-4 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 sm:p-4" aria-labelledby="orders-ask">
      <h2 id="orders-ask" className="flex items-center gap-2 font-medium text-slate-100">
        <ShoppingBag size={16} className="text-amber-300" /> How did you pay?
      </h2>
      <p className="mt-1 mb-3 text-sm text-slate-400">
        These came from shop emails, but no bank or card payment matches them. Each one already counts as spending; tell Money how it
        was paid, or leave it out if someone else paid or it's already counted.
      </p>
      <ul className="grid gap-2">
        {list.map((o) => (
          <OrderRow key={o.id} order={o} onAnswer={answer} />
        ))}
      </ul>
    </section>
  );
}

function OrderRow({ order, onAnswer }: { order: OrderToConfirm; onAnswer: (o: OrderToConfirm, body: OrderPaid, done: string) => Promise<void> }) {
  // A hidden account (one you've stopped using) isn't offered for new payments.
  const accounts = useAccounts().filter((a) => !a.hidden);
  const [busy, setBusy] = useState(false);
  const run = async (body: OrderPaid, done: string) => {
    setBusy(true);
    try {
      await onAnswer(order, body, done);
    } finally {
      setBusy(false);
    }
  };
  const button =
    "flex shrink-0 items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-200 hover:bg-edge disabled:opacity-60";
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-edge bg-panel px-3 py-2.5">
      <div className="min-w-[10rem] flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-medium text-slate-100">{order.merchant}</span>
          <span className="tabular-nums text-slate-200">{rupees(order.amount_paise)}</span>
        </div>
        <div className="text-xs text-slate-500">
          {dayLabel(order.occurred_on)} · {order.category}
          {SAID[order.paid_by] && ` · ${SAID[order.paid_by]}`}
        </div>
      </div>
      <div className="flex w-full items-center gap-1.5 sm:w-auto">
        <button onClick={() => void run({ how: "cash" }, "Recorded as cash")} disabled={busy} className={button}>
          <Banknote size={13} /> Cash
        </button>
        <Select
          aria-label={`Account that paid ${order.merchant}`}
          value=""
          disabled={busy || !accounts.length}
          onChange={(e) => {
            const a = accounts.find((x) => x.id === e.target.value);
            if (a) void run({ how: "account", account_id: a.id }, `Put on ${a.label}`);
          }}
          className="min-w-0 flex-1 sm:w-44 sm:flex-none"
        >
          <option value="">{accounts.length ? "Paid from…" : "No accounts yet"}</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </Select>
        <button
          onClick={() => void run({ how: "leave_out" }, "Left out of totals")}
          disabled={busy}
          className={button}
          title="Someone else paid, or the same spend is already in Money under another name"
        >
          <EyeOff size={13} /> <span className="hidden min-[400px]:inline">Leave out</span>
          <span className="min-[400px]:hidden">Leave</span>
        </button>
      </div>
    </li>
  );
}
