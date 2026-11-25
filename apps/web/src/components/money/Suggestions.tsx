import { Check, ChevronDown, Loader2, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { CADENCE_LABEL, dayLabel, parseRupees, rupees, type Suggestion, type Suggestions } from "../../lib/money";
import { toast } from "../Feedback";
import { OrderShot } from "./OrderShot";
import { useMoney } from "./parts";

const SHOWN = 4;

/** The question each kind asks, in a line. */
function question(s: Suggestion): string {
  const amount = rupees(s.amount_paise, { whole: s.amount_paise >= 100_00 });
  switch (s.kind) {
    case "order":
      return `How did you pay for ${s.merchant}, ${amount}${s.on ? ` on ${dayLabel(s.on)}` : ""}?`;
    case "subscription":
      return `${s.merchant}, ${amount} ${s.cadence ? CADENCE_LABEL[s.cadence].toLowerCase() : ""}: keep tracking it?`;
    case "rent":
      return `${s.merchant}, about ${amount} most months: is it rent?`;
    case "category":
      if (s.person) return `What was the ${amount} to ${s.merchant} for?${s.count && s.count > 1 ? ` (${s.count} payments)` : ""}`;
      return `What is ${s.merchant}? ${amount} over ${s.count ?? "a few"} payment${s.count === 1 ? "" : "s"}`;
    case "emi":
      return `${s.merchant}, ${amount} on EMI: for how many months?`;
    case "yearly":
      return `${s.merchant}, ${amount}${s.on ? ` on ${dayLabel(s.on)}` : ""}: does it renew${s.plan ? ` (${s.plan})` : ""}?`;
    case "basket":
      return `What did you buy on ${s.merchant}, ${amount}${s.on ? ` on ${dayLabel(s.on)}` : ""}?`;
  }
}

/** When, from where and how: "5 Nov · ICICI Salary ••1234 · UPI", or for repeats
 * "Last 21 Nov, since 21 Jun · …". */
function where(s: Suggestion): string {
  const latest = s.details[0];
  const when = latest
    ? s.details.length > 1 && s.first_on && s.first_on !== latest.on
      ? `Last ${dayLabel(latest.on)}, since ${dayLabel(s.first_on)}`
      : dayLabel(latest.on)
    : s.on
      ? dayLabel(s.on)
      : "";
  return [when, s.account, s.how].filter(Boolean).join(" · ");
}

type Answer = (key: string, choice: string, amount?: number, text?: string) => void;

function Question({ s, busy, answer, onSaved }: { s: Suggestion; busy: string | null; answer: Answer; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [instalment, setInstalment] = useState("");
  const [bought, setBought] = useState("");
  const [typing, setTyping] = useState(false);
  const [shot, setShot] = useState(false);
  const amount = instalment.trim() ? parseRupees(instalment) : undefined;
  const badAmount = amount === null;
  const context = where(s);
  return (
    <li className="min-w-0 rounded-lg border border-edge bg-panel px-3 py-2.5">
      <p className="text-sm text-slate-100">{question(s)}</p>
      {context && <p className="mt-0.5 text-xs text-slate-400">{context}</p>}
      {s.note && (
        <p className="mt-0.5 truncate font-mono text-[11px] text-slate-500" title={s.note}>
          {s.note}
        </p>
      )}
      {s.why && <p className="mt-0.5 text-xs text-slate-500">{s.why}</p>}
      {s.details.length > 1 && (
        <>
          <button onClick={() => setOpen(!open)} className="mt-1 flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200" aria-expanded={open}>
            <ChevronDown size={12} className={open ? "rotate-180" : ""} aria-hidden />
            {open ? "Hide the payments" : `See the ${s.count && s.count > s.details.length ? `latest ${s.details.length}` : s.details.length} payments`}
          </button>
          {open && (
            <ul className="mt-1 grid gap-0.5 text-xs">
              {s.details.map((d, i) => (
                <li key={i} className="flex min-w-0 gap-2 text-slate-400">
                  <span className="w-14 shrink-0">{dayLabel(d.on)}</span>
                  <span className="w-20 shrink-0 text-right tabular-nums text-slate-200">{rupees(d.amount_paise)}</span>
                  <span className="min-w-0 flex-1 truncate" title={d.note}>
                    {[d.account, d.how, d.note].filter(Boolean).join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {s.kind === "emi" && (
        <label className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
          Monthly instalment, if you know it
          <input
            inputMode="decimal"
            value={instalment}
            onChange={(e) => setInstalment(e.target.value)}
            placeholder="e.g. 7450.50"
            aria-invalid={badAmount}
            className={`w-28 rounded-md border bg-ink px-2 py-1 text-sm text-slate-100 ${badAmount ? "border-rose-500" : "border-edge"}`}
          />
          {badAmount && <span className="text-rose-300">Type an amount like 7450.50</span>}
        </label>
      )}
      {s.kind === "basket" && shot && (
        <OrderShot txnId={s.key.slice("basket:".length)} label="Choose screenshots of the order" onSaved={onSaved} className="mt-2" />
      )}
      {s.kind === "basket" && typing && (
        <form
          className="mt-2 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (bought.trim()) answer(s.key, "items", undefined, bought.trim());
          }}
        >
          <input
            value={bought}
            onChange={(e) => setBought(e.target.value)}
            maxLength={1000}
            autoFocus
            placeholder="milk 60, chips 40, coke"
            aria-label={`What you bought on ${s.merchant}`}
            className="min-w-0 flex-1 rounded-md border border-edge bg-ink px-2.5 py-1 text-base text-slate-100 sm:max-w-sm sm:text-sm"
          />
          {bought.trim() && (
            <button type="submit" disabled={busy !== null} className="rounded-md bg-sky-500 px-3 py-1 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50">
              Save items
            </button>
          )}
        </form>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {s.choices.map((c) => {
          const guess = c.id === s.guess;
          return (
            <button
              key={c.id}
              onClick={() => answer(s.key, c.id, amount ?? undefined)}
              disabled={busy !== null || badAmount}
              className={`flex items-center gap-1 rounded-full px-3 py-1 text-sm disabled:opacity-50 ${
                guess ? "bg-sky-500 font-medium text-ink hover:bg-sky-400" : "border border-edge text-slate-200 hover:bg-edge"
              }`}
            >
              {guess && (busy === s.key ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} aria-hidden />)}
              {c.label}
            </button>
          );
        })}
        {s.kind === "basket" && !typing && (
          <button onClick={() => setTyping(true)} className="px-1 py-1 text-xs text-sky-300 hover:text-sky-200">
            Type the items
          </button>
        )}
        {s.kind === "basket" && !typing && !shot && (
          <button onClick={() => setShot(true)} className="px-1 py-1 text-xs text-sky-300 hover:text-sky-200">
            Use a screenshot
          </button>
        )}
        <button
          onClick={() => answer(s.key, "later")}
          disabled={busy !== null}
          title="Hides this question for a week"
          className="ml-auto px-1 py-1 text-xs text-slate-500 hover:text-slate-300 disabled:opacity-50"
        >
          Not sure, ask later
        </button>
      </div>
    </li>
  );
}

/** Money's one-tap questions: what it worked out from your mail and statements, with its best
 * guess first. Anything it was sure of it already did; each answer is applied straight away and
 * never asked again. */
export function MoneySuggests() {
  const { data, reload } = useMoney(api.moneySuggestions);
  const [items, setItems] = useState<Suggestion[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => setItems(null), [data]);
  const list = items ?? data?.items ?? [];
  if (!list.length) return null;
  const guesses = list.filter((s) => s.guess).length;
  const take = async (call: () => Promise<Suggestions & { done?: string; accepted?: number }>, key: string) => {
    setBusy(key);
    try {
      const r = await call();
      setItems(r.items);
      toast(r.done ?? `Took Money's guess for ${r.accepted} question${r.accepted === 1 ? "" : "s"}`);
    } catch (e) {
      toast((e as Error).message, "error");
      reload();
    } finally {
      setBusy(null);
    }
  };
  const shown = all ? list : list.slice(0, SHOWN);
  return (
    <section className="mb-4 rounded-xl border border-sky-500/30 bg-sky-500/5 p-3 sm:p-4" aria-labelledby="money-suggests">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 id="money-suggests" className="flex items-center gap-2 font-medium text-slate-100">
          <Sparkles size={16} className="text-sky-300" aria-hidden /> Money suggests
          <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-xs font-normal text-sky-200">{list.length}</span>
        </h2>
        {guesses > 1 && (
          <button
            onClick={() => void take(api.moneyAcceptSuggestions, "*")}
            disabled={busy !== null}
            className="ml-auto flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
            title="Takes the highlighted answer on every question that has one. Questions without a guess stay."
          >
            {busy === "*" ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Accept {guesses} guesses
          </button>
        )}
      </div>
      <p className="mt-1 mb-3 text-sm text-slate-400">
        A tap each. The highlighted answer is Money's guess. Anything it was sure of, it has already done. Not sure? "Ask later" hides a question for a week.
      </p>
      <ul className="grid grid-cols-1 gap-2">
        {shown.map((s) => (
          <Question key={s.key} s={s} busy={busy} onSaved={reload} answer={(key, choice, amount, text) => void take(() => api.moneyAnswerSuggestion(key, choice, amount, text), key)} />
        ))}
      </ul>
      {list.length > SHOWN && (
        <button onClick={() => setAll(!all)} className="mt-2 text-sm text-sky-300 hover:text-sky-200">
          {all ? "Show fewer" : `Show all ${list.length}`}
        </button>
      )}
    </section>
  );
}
