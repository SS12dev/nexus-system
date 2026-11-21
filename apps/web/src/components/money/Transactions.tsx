import { ArrowLeftRight, Banknote, CheckCheck, EyeOff, Link2, Loader2, PiggyBank, Plus, Search, Trash2, Undo2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../../lib/api";
import {
  CHANNEL_LABEL,
  dayLabel,
  parseRupees,
  rupees,
  SOURCE_LABEL,
  type MoneyAccount,
  type Linked,
  type MoneyTxn,
  type TxnMatches,
  type TxnPage,
} from "../../lib/money";
import { confirmAction, toast } from "../Feedback";
import { Select } from "../Select";
import { OrdersToConfirm } from "./Orders";
import { Empty, MonthPicker, Spinner, useAccounts, useMoneySettings, useMoneyVersion } from "./parts";

const PAGE = 100;

/** Every transaction, newest first, filtered by month, category, account, direction or words.
 * A row opens to change its category (once, or for the merchant from now on), add a note or
 * leave it out of totals. */
export function TransactionsView({ latest }: { latest: string }) {
  const version = useMoneyVersion();
  const [params, setParams] = useSearchParams();
  const month = params.get("month") ?? latest;
  const category = params.get("category") ?? "";
  const account = params.get("account") ?? "";
  const direction = (params.get("direction") ?? "") as "" | "debit" | "credit";
  const q = params.get("q") ?? "";
  const [draft, setDraft] = useState(q);
  const [page, setPage] = useState<TxnPage | null>(null);
  const [more, setMore] = useState<MoneyTxn[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const accounts = useAccounts();
  const categories = useMoneySettings().data?.categories ?? [];
  const names = useMemo(() => new Map(accounts.map((a) => [a.id, a.label])), [accounts]);

  const query = { month: month === "all" ? undefined : month, category: category || undefined, account_id: account || undefined, direction: direction || undefined, q: q || undefined };
  useEffect(() => {
    const t = window.setTimeout(() => {
      api
        .moneyTxns({ ...query, limit: PAGE })
        .then((p) => {
          setPage(p);
          setMore([]);
        })
        .catch((e) => toast((e as Error).message, "error"));
    }, 120);
    return () => window.clearTimeout(t);
    // The query object is rebuilt each render; its parts are the real dependencies.
  }, [version, month, category, account, direction, q]);

  const set = (k: string, v: string) =>
    setParams(
      (p) => {
        if (v) p.set(k, v);
        else p.delete(k);
        return p;
      },
      { replace: true },
    );
  const rows = page ? [...page.items, ...more] : [];
  const byDay = useMemo(() => {
    const groups: { day: string; items: MoneyTxn[] }[] = [];
    for (const t of rows) {
      const last = groups[groups.length - 1];
      if (last?.day === t.occurred_on) last.items.push(t);
      else groups.push({ day: t.occurred_on, items: [t] });
    }
    return groups;
  }, [rows]);

  return (
    <>
      <OrdersToConfirm />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {month === "all" ? (
          <button onClick={() => set("month", "")} className="rounded-md border border-edge px-2.5 py-1 text-sm text-slate-300 hover:bg-edge">
            All time · show one month
          </button>
        ) : (
          <MonthPicker month={month} latest={latest} onChange={(m) => set("month", m === latest ? "" : m)} />
        )}
        {month !== "all" && (
          <button onClick={() => set("month", "all")} className="text-xs text-slate-400 hover:text-slate-200">
            All time
          </button>
        )}
        <ScanLinks />
        <button
          onClick={() => setAdding((a) => !a)}
          className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-300 hover:bg-edge"
        >
          <Plus size={14} /> Add cash spend
        </button>
      </div>
      {adding && <AddTxn accounts={accounts} categories={categories} onDone={() => setAdding(false)} />}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex min-w-48 flex-1 items-center gap-2 rounded-lg border border-edge bg-panel px-3 py-1.5 focus-within:border-sky-500/60">
          <Search size={15} className="shrink-0 text-slate-400" />
          <input
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              set("q", e.target.value.trim());
            }}
            placeholder="Merchant or note"
            className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-slate-500 sm:text-sm"
            style={{ outline: "none" }}
            aria-label="Search transactions"
          />
        </div>
        <Select value={category} onChange={(e) => set("category", e.target.value)} aria-label="Category" className="max-w-48">
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </Select>
        {accounts.length > 1 && (
          <Select value={account} onChange={(e) => set("account", e.target.value)} aria-label="Account" className="max-w-48">
            <option value="">All accounts</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </Select>
        )}
        <div className="inline-flex rounded-md border border-edge p-0.5" role="radiogroup" aria-label="Money in or out">
          {(
            [
              ["", "All"],
              ["debit", "Out"],
              ["credit", "In"],
            ] as const
          ).map(([v, label]) => (
            <button
              key={label}
              role="radio"
              aria-checked={direction === v}
              onClick={() => set("direction", v)}
              className={`rounded px-2.5 py-1 text-sm ${direction === v ? "bg-edge text-white" : "text-slate-400"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {!page ? (
        <Spinner />
      ) : !rows.length ? (
        <Empty>
          {q || category || account || direction
            ? "No transactions match."
            : "No transactions this month. Upload a statement or switch on alert emails under Import and settings."}
        </Empty>
      ) : (
        <>
          <p className="mb-2 text-xs text-slate-400" title="Counted like the overview: transfers between your own accounts, investments and rows left out of totals aren't spending">
            {page.total} transaction{page.total === 1 ? "" : "s"} · spent {rupees(page.spent_paise, { whole: true })} · came in{" "}
            {rupees(page.income_paise, { whole: true })}
          </p>
          <div className="space-y-4">
            {byDay.map((g) => (
              <section key={g.day}>
                <h3 className="mb-1 text-xs font-medium text-slate-500">{dayLabel(g.day)}</h3>
                <ul className="divide-y divide-edge/60 rounded-xl border border-edge bg-panel">
                  {g.items.map((t) => (
                    <TxnRow
                      key={t.id}
                      txn={t}
                      account={t.account_id ? names.get(t.account_id) : undefined}
                      open={open === t.id}
                      onToggle={() => setOpen(open === t.id ? null : t.id)}
                      categories={categories}
                    />
                  ))}
                </ul>
              </section>
            ))}
          </div>
          {rows.length < page.total && (
            <button
              onClick={() =>
                api
                  .moneyTxns({ ...query, limit: PAGE, offset: rows.length })
                  .then((p) => setMore((m) => [...m, ...p.items]))
                  .catch((e) => toast((e as Error).message, "error"))
              }
              className="mt-4 w-full rounded-md border border-edge py-2 text-sm text-slate-300 hover:bg-edge"
            >
              Show more ({page.total - rows.length} left)
            </button>
          )}
        </>
      )}
    </>
  );
}

/** Look back two years for transfers, card bill payments, refunds and savings payments that
 * aren't tied yet. New rows are tied as they arrive; this catches the ones from before. */
function ScanLinks() {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const c = await api.moneyScanLinks();
      const parts = [
        [c.transfers + c.self_transfers, "transfer"],
        [c.card_payments, "card bill payment"],
        [c.refunds, "refund"],
        [c.savings, "savings payment"],
      ]
        .filter(([n]) => n)
        .map(([n, what]) => `${n} ${what}${n === 1 ? "" : "s"}`);
      toast(parts.length ? `Tied ${parts.join(", ")}` : "Nothing new to tie: everything that matches is already tied");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      onClick={run}
      disabled={busy}
      className="ml-auto flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-300 hover:bg-edge disabled:opacity-50"
      title="Pairs money moved between your own accounts, card bills paid from the bank, refunds with their purchases, and payments into your RDs, PPF and other savings, so none of it counts twice"
    >
      {busy ? <Loader2 size={14} className="animate-spin" /> : <ArrowLeftRight size={14} />} Find transfers
    </button>
  );
}

function Amount({ txn }: { txn: MoneyTxn }) {
  const credit = txn.direction === "credit";
  return (
    <span className={`shrink-0 text-right tabular-nums ${txn.excluded ? "text-slate-500 line-through" : credit ? "text-[#0ca30c]" : "text-slate-100"}`}>
      {credit ? "+" : "−"}
      {rupees(txn.amount_paise).slice(txn.amount_paise < 0 ? 1 : 0)}
    </span>
  );
}

function TxnRow({
  txn,
  account,
  open,
  onToggle,
  categories,
}: {
  txn: MoneyTxn;
  account?: string;
  open: boolean;
  onToggle: () => void;
  categories: string[];
}) {
  return (
    <li className={txn.excluded ? "opacity-70" : ""}>
      <button onClick={onToggle} aria-expanded={open} className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-edge/40">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-slate-100">{txn.merchant}</span>
          <span className="flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
            <span className="text-slate-400">{txn.category}</span>
            {account && <span>{account}</span>}
            {txn.channel !== "other" && <span>{CHANNEL_LABEL[txn.channel] ?? txn.channel}</span>}
            {txn.excluded && (
              <span className="flex items-center gap-0.5">
                <EyeOff size={11} /> left out
              </span>
            )}
            {txn.pair_kind && (
              <span className="flex items-center gap-0.5" title={PAIR_TITLE[txn.pair_kind]}>
                <Link2 size={11} /> {PAIR_SHORT[txn.pair_kind]}
              </span>
            )}
            {txn.asset_id && !txn.pair_kind && (
              <span className="flex items-center gap-0.5">
                <PiggyBank size={11} /> savings
              </span>
            )}
            {txn.note && <span className="truncate italic">{txn.note}</span>}
          </span>
        </span>
        <Amount txn={txn} />
      </button>
      {open && <TxnDetail txn={txn} categories={categories} />}
    </li>
  );
}

function TxnDetail({ txn, categories }: { txn: MoneyTxn; categories: string[] }) {
  const [full, setFull] = useState<MoneyTxn | null>(null);
  const [category, setCategory] = useState(txn.category);
  const [forMerchant, setForMerchant] = useState(txn.merchant !== "Unknown");
  const [note, setNote] = useState(txn.note);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.moneyTxn(txn.id).then(setFull).catch(() => {});
  }, [txn.id]);

  const save = async (body: Parameters<typeof api.moneyUpdateTxn>[1], done: string) => {
    setBusy(true);
    try {
      const r = await api.moneyUpdateTxn(txn.id, body);
      setFull(r.txn);
      setCategory(r.txn.category);
      toast(r.also_changed ? `${done}, and ${r.also_changed} more from ${txn.merchant}` : done);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 border-t border-edge/60 bg-ink/40 px-3 py-3 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1 text-xs text-slate-400">
          Category
          <Select value={category} onChange={(e) => setCategory(e.target.value)} className="min-w-44">
            {categories.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </Select>
        </label>
        {txn.merchant !== "Unknown" && (
          <label className="flex items-center gap-1.5 pb-1.5 text-xs text-slate-400">
            <input type="checkbox" checked={forMerchant} onChange={(e) => setForMerchant(e.target.checked)} className="accent-sky-500" />
            Always for {txn.merchant}
          </label>
        )}
        <button
          disabled={busy || category === txn.category}
          onClick={() => save({ category, for_merchant: forMerchant }, `Now ${category}`)}
          className="rounded-md bg-sky-500 px-3 py-1 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-40"
        >
          Change
        </button>
      </div>
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save({ note }, "Note saved");
        }}
      >
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={500}
          placeholder="Add a note (what it was for, who it was with)"
          className="min-w-0 flex-1 rounded-md border border-edge bg-ink px-2.5 py-1 text-base sm:text-sm"
          aria-label="Note"
        />
        {note !== txn.note && (
          <button type="submit" disabled={busy} className="rounded-md border border-edge px-2.5 py-1 text-sm text-slate-200 hover:bg-edge">
            Save
          </button>
        )}
      </form>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-400">
        <span className="flex items-center gap-1">
          {txn.confirmed ? <CheckCheck size={13} className="text-slate-300" /> : <Banknote size={13} />}
          {txn.confirmed ? "Seen in both an alert email and a statement" : SOURCE_LABEL[txn.source]}
        </span>
        <button
          onClick={() => save({ excluded: !txn.excluded }, txn.excluded ? "Counted again" : "Left out of totals")}
          disabled={busy}
          className="flex items-center gap-1 text-slate-300 hover:text-white"
          title="For money that isn't really spending or income: a transfer to yourself, a refund that cancels a purchase, a bill split you were paid back for"
        >
          <EyeOff size={13} /> {txn.excluded ? "Count it again" : "Leave out of totals"}
        </button>
        {txn.source === "manual" && (
          <button
            onClick={async () => {
              const ok = await confirmAction({ title: "Delete this entry?", body: `${txn.merchant}, ${rupees(txn.amount_paise)}`, confirmLabel: "Delete", danger: true });
              if (!ok) return;
              try {
                await api.moneyDeleteTxn(txn.id);
                toast("Deleted");
              } catch (e) {
                toast((e as Error).message, "error");
              }
            }}
            className="ml-auto flex items-center gap-1 text-rose-300 hover:text-rose-200"
          >
            <Trash2 size={13} /> Delete
          </button>
        )}
      </div>
      {full && <Links txn={full} busy={busy} save={save} />}
      {full?.narration && (
        <p className="break-words rounded-md bg-ink/60 px-2.5 py-1.5 font-mono text-xs text-slate-400" title="What the bank wrote">
          {full.narration}
        </p>
      )}
    </div>
  );
}

const PAIR_SHORT = { transfer: "transfer", card: "card bill", refund: "refund" } as const;
const PAIR_TITLE = {
  transfer: "Money moved between your own accounts: not spending or income",
  card: "A card bill paid from your bank: the card's purchases are what count",
  refund: "A refund tied to its purchase: it lowers that spending",
} as const;

function linkedText(txn: MoneyTxn, other: Linked): string {
  const where = other.account ?? other.merchant;
  const on = dayLabel(other.occurred_on);
  if (txn.pair_kind === "refund")
    return txn.direction === "credit" ? `Refund of ${rupees(other.amount_paise)} at ${other.merchant} on ${on}` : `Refunded ${rupees(other.amount_paise)} on ${on}`;
  const what = txn.pair_kind === "card" ? "Card bill payment" : "Moved";
  return txn.direction === "debit" ? `${what} to ${where}, arrived ${on}` : `${what} from ${where}, sent ${on}`;
}

/** What this row is tied to (a transfer's other half, a refunded purchase, a saving), with
 * Undo; or, when it isn't, the rows and savings it could be tied to by hand. */
function Links({ txn, busy, save }: { txn: MoneyTxn; busy: boolean; save: (body: Parameters<typeof api.moneyUpdateTxn>[1], done: string) => Promise<void> }) {
  const [matches, setMatches] = useState<TxnMatches | null>(null);
  const [looking, setLooking] = useState(false);
  const find = async () => {
    setLooking(true);
    try {
      setMatches(await api.moneyTxnMatches(txn.id));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setLooking(false);
    }
  };
  const row = "flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md bg-ink/60 px-2.5 py-1.5 text-xs text-slate-300";
  const undo = "ml-auto flex items-center gap-1 text-slate-400 hover:text-white";
  return (
    <div className="space-y-1.5">
      {txn.paired && (
        <p className={row}>
          <ArrowLeftRight size={13} className="shrink-0 text-sky-300" />
          <span>{linkedText(txn, txn.paired)}</span>
          <button disabled={busy} onClick={() => save({ unpair: true }, "Untied")} className={undo}>
            <Undo2 size={12} /> Not the same money
          </button>
        </p>
      )}
      {txn.saving && (
        <p className={row}>
          <PiggyBank size={13} className="shrink-0 text-sky-300" />
          <span>
            {txn.direction === "debit" ? "Went into" : "Came back from"} <b className="font-medium text-slate-100">{txn.saving}</b>
          </span>
          <button disabled={busy} onClick={() => save({ asset_id: null }, "Untied from the saving")} className={undo}>
            <Undo2 size={12} /> Not this saving
          </button>
        </p>
      )}
      {!txn.paired && !matches && (
        <button onClick={find} disabled={looking} className="flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200">
          {looking ? <Loader2 size={12} className="animate-spin" /> : <Link2 size={12} />} Tie to a transfer, refund or saving
        </button>
      )}
      {!txn.paired && matches && (
        <div className="rounded-md border border-edge/70 p-2 text-xs text-slate-400">
          <p className="mb-1.5">
            Tied rows don't count twice: a transfer's halves aren't spending or income, and a refund lowers the spending it undoes.
          </p>
          {[...matches.transfers.map((m) => ["transfer", m] as const), ...matches.refunds.map((m) => ["refund", m] as const)].map(([kind, m]) => (
            <div key={`${kind}-${m.id}`} className="flex items-center gap-2 border-t border-edge/50 py-1.5 first:border-t-0">
              <span className="min-w-0 flex-1 truncate">
                {m.direction === "credit" ? "In to" : "Out of"} {m.account ?? "an account"} · {m.merchant} · {dayLabel(m.occurred_on)} · {rupees(m.amount_paise)}
              </span>
              <button
                disabled={busy}
                onClick={() => save({ pair_with: m.id, pair_as: kind }, kind === "refund" ? "Tied as a refund" : "Tied as a transfer")}
                className="shrink-0 rounded border border-edge px-2 py-0.5 text-slate-200 hover:bg-edge"
              >
                {kind === "refund" ? "Refund of this" : "Same money"}
              </button>
            </div>
          ))}
          {!matches.transfers.length && !matches.refunds.length && <p>No row of the same amount the other way within a week, and no earlier purchase here to refund.</p>}
          {!txn.saving && matches.savings.length > 0 && (
            <label className="mt-1.5 flex items-center gap-2 border-t border-edge/50 pt-1.5">
              <PiggyBank size={13} className="shrink-0" />
              {txn.direction === "debit" ? "Went into" : "Came back from"}
              <Select
                value=""
                onChange={(e) => e.target.value && save({ asset_id: e.target.value }, "Tied to the saving")}
                className="min-w-40"
                aria-label="Saving"
              >
                <option value="">Choose a saving…</option>
                {matches.savings.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
            </label>
          )}
        </div>
      )}
    </div>
  );
}

function AddTxn({ accounts, categories, onDone }: { accounts: MoneyAccount[]; categories: string[]; onDone: () => void }) {
  const today = new Date().toLocaleDateString("en-CA");
  const [day, setDay] = useState(today);
  const [amount, setAmount] = useState("");
  const [merchant, setMerchant] = useState("");
  const [category, setCategory] = useState("");
  const [direction, setDirection] = useState<"debit" | "credit">("debit");
  const [account, setAccount] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = parseRupees(amount) !== null && merchant.trim() !== "";
  return (
    <form
      className="mb-4 grid gap-2 rounded-xl border border-edge bg-panel p-3 sm:grid-cols-[auto_8rem_1fr_auto]"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!valid) return;
        setBusy(true);
        try {
          await api.moneyAddTxn({
            occurred_on: day,
            amount,
            direction,
            merchant: merchant.trim(),
            category: category || undefined,
            account_id: account || undefined,
          });
          toast("Added");
          onDone();
        } catch (err) {
          toast((err as Error).message, "error");
        } finally {
          setBusy(false);
        }
      }}
    >
      <p className="text-xs text-slate-400 sm:col-span-4">
        For cash and anything no email or statement will show. Leave the category empty to have it guessed.
      </p>
      <input type="date" value={day} max={today} onChange={(e) => setDay(e.target.value)} aria-label="Date" className="rounded-md border border-edge bg-ink px-2 py-1 text-base [color-scheme:dark] sm:text-sm" />
      <input
        value={amount}
        onChange={(e) => setAmount(e.target.value)}
        inputMode="decimal"
        placeholder="₹ amount"
        aria-label="Amount in rupees"
        className="rounded-md border border-edge bg-ink px-2 py-1 text-base sm:text-sm"
      />
      <input
        value={merchant}
        onChange={(e) => setMerchant(e.target.value)}
        maxLength={120}
        placeholder="Where or who (e.g. Chai stall)"
        aria-label="Where or who"
        className="rounded-md border border-edge bg-ink px-2 py-1 text-base sm:text-sm"
      />
      <div className="inline-flex rounded-md border border-edge p-0.5" role="radiogroup" aria-label="Money in or out">
        {(["debit", "credit"] as const).map((d) => (
          <button key={d} type="button" role="radio" aria-checked={direction === d} onClick={() => setDirection(d)} className={`rounded px-2.5 py-1 text-sm ${direction === d ? "bg-edge text-white" : "text-slate-400"}`}>
            {d === "debit" ? "Spent" : "Received"}
          </button>
        ))}
      </div>
      <Select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category" className="sm:col-span-2">
        <option value="">Guess the category</option>
        {categories.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </Select>
      <Select value={account} onChange={(e) => setAccount(e.target.value)} aria-label="Paid from">
        <option value="">Cash</option>
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.label}
          </option>
        ))}
      </Select>
      <div className="flex items-center gap-2">
        <button type="submit" disabled={!valid || busy} className="rounded-md bg-sky-500 px-3 py-1 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-40">
          Add
        </button>
        <button type="button" onClick={onDone} className="p-1 text-slate-400 hover:text-white" aria-label="Cancel">
          <X size={16} />
        </button>
      </div>
    </form>
  );
}
