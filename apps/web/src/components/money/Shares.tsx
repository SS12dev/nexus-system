import { Loader2, MoreHorizontal, Search, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { dayLabel, rupees, type Stock, type StockFound, type Stocks } from "../../lib/money";
import { confirmAction, toast } from "../Feedback";
import { FormDialog, moneyText, read, type Field } from "./FormDialog";
import { Card } from "./parts";

function units(n: number): string {
  return n.toLocaleString("en-IN", { maximumFractionDigits: 4 });
}

/** Shares held directly, priced each evening from NSE's closing prices. */
export function SharesSection({ data, onAdd, onEdit }: { data: Stocks | null; onAdd: () => void; onEdit: (s: Stock) => void }) {
  if (!data) return null;
  if (!data.items.length)
    return (
      <div className="mb-6">
        <Card title="Shares">
          <p className="flex gap-2 text-sm text-slate-400">
            <TrendingUp size={15} className="mt-0.5 shrink-0 text-slate-500" />
            <span>
              Shares you hold directly in a demat account.{" "}
              <button onClick={onAdd} className="text-sky-300 hover:text-sky-200">
                Add one
              </button>{" "}
              by its name, and Nexus values it every evening at NSE's closing price.
            </span>
          </p>
        </Card>
      </div>
    );
  return (
    <section className="mb-6">
      <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
        <TrendingUp size={15} className="text-slate-500" /> Shares
        <span className="ml-auto text-xs font-normal text-slate-500">{data.price_on && `NSE close of ${dayLabel(data.price_on)}`}</span>
      </h2>
      <ul className="divide-y divide-edge/60 rounded-xl border border-edge bg-panel">
        {data.items.map((s) => (
          <li key={s.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
            <span className="min-w-0 flex-1">
              <span className="block text-slate-100 sm:truncate">{s.name || s.symbol}</span>
              <span className="block truncate text-xs text-slate-500">
                {s.symbol} · {units(s.quantity)} {s.quantity === 1 ? "share" : "shares"}
                {s.price_paise != null && ` × ${rupees(s.price_paise)}`}
              </span>
            </span>
            <span className="text-right">
              <span className="block tabular-nums text-slate-100">{s.value_paise != null ? rupees(s.value_paise, { whole: true }) : "—"}</span>
              {s.gain_paise != null && <span className="block tabular-nums text-xs text-slate-400">{rupees(s.gain_paise, { whole: true, sign: true })}</span>}
            </span>
            <button onClick={() => onEdit(s)} className="-m-1 p-1 text-slate-400 hover:text-white" aria-label={`Edit ${s.name || s.symbol}`}>
              <MoreHorizontal size={15} />
            </button>
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-xs text-slate-500">
        Total {rupees(data.value_paise, { whole: true })}
        {data.gain_paise != null && ` · ${rupees(data.gain_paise, { whole: true, sign: true })} on what you paid`}. Counted in your net worth
        and forecast, growing like funds do.
      </p>
    </section>
  );
}

/** Search NSE's list by company name, symbol or ISIN. */
function StockSearch({ picked, onPick }: { picked: StockFound | null; onPick: (s: StockFound | null) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<StockFound[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const text = q.trim();
    if (text.length < 2) {
      setFound([]);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      setBusy(true);
      api
        .moneyFindStock(text)
        .then((r) => {
          if (!live) return;
          setFound(r.items);
          setError("");
        })
        .catch((e) => live && setError((e as Error).message))
        .finally(() => live && setBusy(false));
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [q]);

  if (picked)
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border border-sky-500/30 bg-sky-500/[0.06] px-3 py-2 text-sm">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-slate-100">{picked.name}</span>
          <span className="block text-xs text-slate-400">
            {picked.symbol} · {rupees(picked.price_paise)} at NSE's close on {dayLabel(picked.price_on)}
          </span>
        </span>
        <button type="button" onClick={() => onPick(null)} className="shrink-0 text-xs text-sky-300 hover:text-sky-200">
          Change
        </button>
      </div>
    );
  return (
    <div className="mt-3">
      <label className="block text-sm text-slate-300">
        <span className="mb-1 block">Company</span>
        <span className="relative block">
          <Search size={14} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-slate-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Name, NSE symbol or ISIN"
            autoComplete="off"
            className="w-full rounded-md border border-edge bg-ink py-1.5 pr-8 pl-8 text-base text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none sm:text-sm"
          />
          {busy && <Loader2 size={14} className="absolute top-1/2 right-2.5 -translate-y-1/2 animate-spin text-slate-500" />}
        </span>
      </label>
      {error && <p className="mt-1.5 text-xs text-rose-300">{error}</p>}
      {found.length > 0 && (
        <ul className="mt-1.5 max-h-56 divide-y divide-edge/60 overflow-y-auto rounded-md border border-edge bg-ink" role="listbox" aria-label="Matching companies">
          {found.map((s) => (
            <li key={s.symbol}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => onPick(s)}
                className="flex w-full items-center gap-2 px-2.5 py-2 text-left text-sm hover:bg-edge/60"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-slate-100">{s.name}</span>
                  <span className="block text-xs text-slate-500">
                    {s.symbol} · {s.isin}
                  </span>
                </span>
                <span className="shrink-0 tabular-nums text-xs text-slate-300">{rupees(s.price_paise)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {q.trim().length >= 2 && !busy && !error && !found.length && <p className="mt-1.5 text-xs text-slate-500">Nothing on NSE matches that.</p>}
      <p className="mt-1.5 text-xs text-slate-500">
        Can't find it? A renamed or merged company keeps its ISIN (the INE… code on your demat or broker statement): search by that.
      </p>
    </div>
  );
}

/** Add a holding (search, then how many), or edit one. */
export function ShareDialog({ stock, onClose }: { stock?: Stock; onClose: () => void }) {
  const [picked, setPicked] = useState<StockFound | null>(null);
  const fields: Field[] = [
    { key: "quantity", label: "How many shares", kind: "text", value: stock ? String(stock.quantity) : "", placeholder: "1", half: true },
    {
      key: "avg",
      label: "Paid per share",
      kind: "money",
      value: moneyText(stock?.avg_price_paise),
      optional: true,
      half: true,
      hint: "The average, if you bought more than once. Shows the gain.",
    },
    { key: "bought", label: "Bought on", kind: "date", value: stock?.bought_on ?? "", optional: true, half: true },
    { key: "note", label: "Note", kind: "text", value: stock?.note ?? "", optional: true, placeholder: "Broker, demat account…" },
  ];
  const remove = async () => {
    if (!stock) return;
    if (!(await confirmAction({ title: `Remove ${stock.name || stock.symbol}?`, body: "It stops counting in your savings and net worth.", confirmLabel: "Remove", danger: true })))
      return;
    try {
      await api.moneyDeleteStock(stock.id);
      toast("Removed");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <FormDialog
      title={stock ? stock.name || stock.symbol : "Add shares"}
      description={
        stock ? (
          <>
            {stock.symbol}
            {stock.price_paise != null && stock.price_on && ` · ${rupees(stock.price_paise)} at NSE's close on ${dayLabel(stock.price_on)}`}
          </>
        ) : (
          <>
            Shares you hold in a demat account, valued every evening at NSE's closing price. Only the company, how many and what you paid
            are kept.
            <StockSearch picked={picked} onPick={setPicked} />
          </>
        )
      }
      fields={fields}
      submitLabel={stock ? "Save" : "Add"}
      onClose={onClose}
      extra={
        stock && (
          <button type="button" onClick={remove} className="text-xs text-rose-300 hover:text-rose-200">
            Remove this holding
          </button>
        )
      }
      onSubmit={async (v) => {
        const quantity = v.quantity.replace(/,/g, "").trim();
        if (!/^\d+(\.\d{1,4})?$/.test(quantity) || Number(quantity) <= 0) throw new Error("Type how many shares, like 1 or 25.");
        const body = { quantity, avg_price_paise: read.money(v.avg), bought_on: v.bought || null, note: v.note.trim() || null };
        if (stock) {
          await api.moneyEditStock(stock.id, body);
          toast("Saved");
          return;
        }
        if (!picked) throw new Error("Search for the company and pick it first.");
        const s = await api.moneyAddStock({ symbol: picked.symbol, ...body });
        toast(`Added ${s.name}: ${rupees(s.value_paise ?? 0, { whole: true })} today`);
      }}
    />
  );
}
