import { ImagePlus, Loader2, X } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../../lib/api";
import { dayLabel, rupees, type BoughtItem, type OrderPayment, type OrderReading, type ReadItem } from "../../lib/money";
import { toast } from "../Feedback";
import { Select } from "../Select";
import { useMoneySettings } from "./parts";

const MAX_SHOTS = 4;

/** Read what was in an order from screenshots of it (Zepto, Blinkit, Instamart, any shop's
 * bill): the model on your own computer reads the items, fees and total, Money finds the
 * payment it belongs to, and nothing is saved until you've checked the list. A long order can
 * take a few screenshots; they're read one after another into the same list.
 *
 * With `txnId` the items go on that payment; without it Money matches the order to a payment by
 * its total and date, or asks which one it was. */
export function OrderShot({
  txnId,
  label = "Read a screenshot of the order",
  onSaved,
  className = "",
}: {
  txnId?: string;
  label?: string;
  onSaved?: (items: BoughtItem[], txnId: string) => void;
  className?: string;
}) {
  const kinds = useMoneySettings().data?.item_kinds ?? [];
  const input = useRef<HTMLInputElement>(null);
  const [reading, setReading] = useState<OrderReading | null>(null);
  const [items, setItems] = useState<ReadItem[]>([]);
  const [payment, setPayment] = useState<string>(txnId ?? "");
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setReading(null);
    setItems([]);
    setPayment(txnId ?? "");
  };

  const readAll = async (files: File[]) => {
    let got = reading;
    let list = items;
    setProgress({ done: 0, total: files.length });
    for (const [i, file] of files.entries()) {
      setProgress({ done: i, total: files.length });
      try {
        const r = await api.moneyReadOrderShot(file, txnId || payment || undefined);
        got = merge(got, r);
        list = mergeItems(list, r.items);
      } catch (e) {
        toast(`${file.name}: ${(e as Error).message}`, "error");
      }
    }
    setProgress(null);
    if (input.current) input.current.value = "";
    if (!got) return;
    if (!list.length) {
      toast("No items could be read from that. Try a screenshot of the order's item list.", "error");
      return;
    }
    setReading(got);
    setItems(list);
    if (!txnId && got.match && (!payment || !got.candidates.some((c) => c.id === payment))) setPayment(got.match.id);
  };

  const save = async () => {
    if (!payment) return;
    setBusy(true);
    try {
      const body = items.map(({ name, quantity, price_paise, kind }) => ({
        name,
        quantity,
        price_paise,
        kind,
      }));
      const saved = (await api.moneySetItems(payment, { items: body })).items;
      toast(`Saved ${saved.length} item${saved.length === 1 ? "" : "s"}`);
      onSaved?.(saved, payment);
      reset();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const picker = (
    <input
      ref={input}
      type="file"
      accept="image/png,image/jpeg,image/webp"
      multiple
      className="hidden"
      onChange={(e) => {
        const files = [...(e.target.files ?? [])].slice(0, MAX_SHOTS);
        if (files.length) void readAll(files);
      }}
    />
  );

  if (progress) {
    return (
      <p className={`flex items-center gap-2 text-sm text-slate-300 ${className}`} role="status">
        <Loader2 size={14} className="animate-spin" />
        Reading {Math.min(progress.done + 1, progress.total)} of {progress.total} on your computer… 10 to 30 seconds each.
      </p>
    );
  }

  if (!reading) {
    return (
      <div className={className}>
        {picker}
        <button
          type="button"
          onClick={() => input.current?.click()}
          className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-200 hover:bg-edge"
        >
          <ImagePlus size={14} /> {label}
        </button>
      </div>
    );
  }

  const total = reading.total_paise;
  const listed = items.reduce((s, i) => s + (i.price_paise ?? 0), 0);
  const short = total != null && listed > 0 && listed < total * 0.9;
  const chosen = reading.candidates.find((c) => c.id === payment) ?? (reading.match?.id === payment ? reading.match : null);
  return (
    <section aria-label="Check the order" className={`rounded-md border border-edge/70 bg-ink/40 p-2.5 ${className}`}>
      {picker}
      <p className="mb-2 text-xs text-slate-400">
        Read{reading.shop ? ` from ${reading.shop}` : ""}
        {reading.occurred_on ? `, ${dayLabel(reading.occurred_on)}` : ""}. Check the list: change a kind, or remove a line that isn't right.
        Nothing is saved until you press Save.
      </p>
      <ul className="mb-2 space-y-1">
        {items.map((it, i) => (
          <li key={`${it.name}-${i}`} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="min-w-0 basis-full break-words text-slate-200 sm:basis-auto sm:flex-1">
              {it.name}
              {it.quantity > 1 && <span className="text-slate-500"> × {it.quantity}</span>}
            </span>
            <span className="ml-auto tabular-nums text-slate-400 sm:ml-0">
              {it.price_paise != null ? rupees(it.price_paise) : "no price"}
            </span>
            <Select
              value={it.kind}
              aria-label={`Kind of ${it.name}`}
              onChange={(e) => {
                const kind = e.target.value;
                const label = kinds.find((k) => k.kind === kind)?.label ?? kind;
                setItems(items.map((x, j) => (j === i ? { ...x, kind, kind_label: label } : x)));
              }}
              className="w-48 text-xs"
            >
              {kinds.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </Select>
            <button
              type="button"
              onClick={() => setItems(items.filter((_, j) => j !== i))}
              aria-label={`Remove ${it.name}`}
              className="rounded p-0.5 text-slate-500 hover:bg-edge hover:text-slate-200"
            >
              <X size={13} />
            </button>
          </li>
        ))}
      </ul>
      <p className="mb-2 text-xs text-slate-500">
        Items and fees add up to {rupees(listed)}
        {total != null && ` of ${rupees(total)} paid`}.{short && " Some lines may be cut off: add another screenshot of this order."}
        {total != null && listed > total * 1.02 && " The rest came off as discounts, so each line counts for its share of what you paid."}
        {reading.dropped > 0 && ` ${reading.dropped} line${reading.dropped === 1 ? " was" : "s were"} unreadable and left out.`}
      </p>
      {!txnId && <Payment reading={reading} payment={payment} onPick={setPayment} />}
      {chosen?.has_items && <p className="mb-2 text-xs text-slate-400">That payment already has items; these replace them.</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || !payment || !items.length}
          onClick={() => void save()}
          className="rounded-md bg-sky-500 px-3 py-1 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
        >
          Save {items.length} item{items.length === 1 ? "" : "s"}
        </button>
        {items.length < 60 && (
          <button
            type="button"
            onClick={() => input.current?.click()}
            className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-200 hover:bg-edge"
          >
            <ImagePlus size={14} /> Add another screenshot
          </button>
        )}
        <button type="button" onClick={reset} className="px-2 py-1 text-sm text-slate-400 hover:text-slate-200">
          Cancel
        </button>
      </div>
    </section>
  );
}

function Payment({ reading, payment, onPick }: { reading: OrderReading; payment: string; onPick: (id: string) => void }) {
  const options = reading.candidates;
  if (!options.length) {
    return (
      <p className="mb-2 text-xs text-slate-400">
        No payment near that date and amount yet. If it was paid from an account Money doesn't track, add the screenshot later from the
        payment in Transactions.
      </p>
    );
  }
  return (
    <label className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
      {reading.match && payment === reading.match.id ? "Matched to" : "Which payment was it?"}
      <Select
        value={payment}
        onChange={(e) => onPick(e.target.value)}
        aria-label="The payment for this order"
        className="min-w-0 flex-1 text-sm"
      >
        {!payment && <option value="">Pick the payment…</option>}
        {options.map((c) => (
          <option key={c.id} value={c.id}>
            {describe(c)}
          </option>
        ))}
      </Select>
    </label>
  );
}

function describe(c: OrderPayment): string {
  return `${c.place ?? c.merchant} · ${rupees(c.amount_paise, { whole: true })} · ${dayLabel(c.occurred_on)}`;
}

/** A later screenshot of the same order fills in what the first one didn't show. */
function merge(a: OrderReading | null, b: OrderReading): OrderReading {
  if (!a) return b;
  // The payment is best found from the screenshot that shows the total (usually the last).
  const found = a.total_paise == null && b.total_paise != null ? b : a.candidates.length ? a : b;
  return {
    ...a,
    shop: a.shop ?? b.shop,
    occurred_on: a.occurred_on ?? b.occurred_on,
    total_paise: a.total_paise ?? b.total_paise,
    dropped: a.dropped + b.dropped,
    match: found.match ?? a.match ?? b.match,
    candidates: found.candidates,
  };
}

/** Screenshots scrolled down an order overlap: a line already read isn't added twice. */
function mergeItems(a: ReadItem[], b: ReadItem[]): ReadItem[] {
  const seen = new Set(a.map(key));
  return [...a, ...b.filter((i) => !seen.has(key(i)))].slice(0, 60);
}

function key(i: ReadItem): string {
  return `${i.name.toLowerCase()}|${i.quantity}|${i.price_paise ?? ""}`;
}
