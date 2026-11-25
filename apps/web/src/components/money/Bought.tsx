import { ShoppingBasket } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import { rupees, type BoughtItem, type MoneyTxn } from "../../lib/money";
import { toast } from "../Feedback";
import { Select } from "../Select";
import { OrderShot } from "./OrderShot";
import { useMoneySettings } from "./parts";

/** A transaction's "What you bought": the items read from its order email or told by you, each
 * with the kind Money sorted it into (change one if it's wrong), and a box to type them. */
export function Bought({ txn, onChange }: { txn: MoneyTxn; onChange: (items: BoughtItem[]) => void }) {
  const kinds = useMoneySettings().data?.item_kinds ?? [];
  const items = txn.items ?? [];
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (call: () => Promise<BoughtItem[]>, done: string) => {
    setBusy(true);
    try {
      onChange(await call());
      toast(done);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const fromEmail = items.some((i) => i.source === "email");
  const priced = items.some((i) => i.price_paise != null);
  return (
    <section aria-label="What you bought" className="rounded-md border border-edge/70 bg-ink/40 p-2.5">
      <h3 className="mb-1 flex items-center gap-1.5 text-xs font-medium text-slate-300">
        <ShoppingBasket size={13} /> What you bought
      </h3>
      <p className="mb-2 text-xs text-slate-500">
        {items.length
          ? fromEmail
            ? `Read from the order email${priced ? "" : "; it gives no prices, so the order is split by item count"}. Change a kind if Money got it wrong.`
            : "As you told Money. A screenshot or typing again replaces it."
          : "Money doesn't know what was in this order yet. Read it from a screenshot of the order, pick what it mostly was, or type the items."}
      </p>
      {items.length > 0 && (
        <ul className="mb-2 space-y-1">
          {items.map((it) => (
            <li key={it.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="min-w-0 flex-1 truncate text-slate-200">
                {it.name || <span className="text-slate-400">Whole order</span>}
                {it.quantity > 1 && <span className="text-slate-500"> × {it.quantity}</span>}
              </span>
              {it.price_paise != null && <span className="tabular-nums text-slate-400">{rupees(it.price_paise)}</span>}
              <Select
                value={it.kind}
                disabled={busy}
                aria-label={`Kind of ${it.name || "this order"}`}
                onChange={(e) => {
                  const kind = e.target.value;
                  void run(async () => {
                    const row = await api.moneyUpdateItem(it.id, kind);
                    return items.map((x) => (x.id === it.id ? row : x));
                  }, `Now ${row(kinds, kind)}`);
                }}
                className="w-48 text-xs"
              >
                {kinds.map((k) => (
                  <option key={k.kind} value={k.kind}>
                    {k.label}
                  </option>
                ))}
              </Select>
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!text.trim()) return;
          void run(async () => (await api.moneySetItems(txn.id, { text: text.trim() })).items, "Items saved").then(() => setText(""));
        }}
      >
        {!items.length && (
          <Select
            value=""
            disabled={busy}
            aria-label="What this order mostly was"
            onChange={(e) => e.target.value && void run(async () => (await api.moneySetItems(txn.id, { kind: e.target.value })).items, "Saved")}
            className="w-52 text-sm"
          >
            <option value="">Mostly…</option>
            {kinds
              .filter((k) => !["fees", "other"].includes(k.kind))
              .map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
          </Select>
        )}
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={1000}
          placeholder="Type them: milk 60, chips 40, coke"
          aria-label="Type what you bought"
          className="min-w-0 flex-1 rounded-md border border-edge bg-ink px-2.5 py-1 text-base sm:text-sm"
        />
        {text.trim() && (
          <button type="submit" disabled={busy} className="rounded-md border border-edge px-2.5 py-1 text-sm text-slate-200 hover:bg-edge">
            Save
          </button>
        )}
      </form>
      <OrderShot txnId={txn.id} onSaved={(saved) => onChange(saved)} className="mt-2" />
    </section>
  );
}

function row(kinds: { kind: string; label: string }[], kind: string): string {
  return (kinds.find((k) => k.kind === kind)?.label ?? kind).toLowerCase();
}
