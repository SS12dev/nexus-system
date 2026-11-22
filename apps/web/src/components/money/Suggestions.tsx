import { Check, Loader2, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { CADENCE_LABEL, dayLabel, rupees, type Suggestion, type Suggestions } from "../../lib/money";
import { toast } from "../Feedback";
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
      return `What is ${s.merchant}? ${amount} over ${s.count ?? "a few"} payment${s.count === 1 ? "" : "s"}`;
    case "emi":
      return `${s.merchant}, ${amount} on EMI: for how many months?`;
    case "yearly":
      return `${s.merchant}, ${amount}${s.on ? ` on ${dayLabel(s.on)}` : ""}: does it renew${s.plan ? ` (${s.plan})` : ""}?`;
  }
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
      <p className="mt-1 mb-3 text-sm text-slate-400">A tap each. The highlighted answer is Money's guess. Anything it was sure of, it has already done.</p>
      <ul className="grid gap-2">
        {shown.map((s) => (
          <li key={s.key} className="rounded-lg border border-edge bg-panel px-3 py-2.5">
            <p className="text-sm text-slate-100">{question(s)}</p>
            {s.why && <p className="mt-0.5 text-xs text-slate-500">{s.why}</p>}
            <div className="mt-2 flex flex-wrap gap-1.5">
              {s.choices.map((c) => {
                const guess = c.id === s.guess;
                return (
                  <button
                    key={c.id}
                    onClick={() => void take(() => api.moneyAnswerSuggestion(s.key, c.id), s.key)}
                    disabled={busy !== null}
                    className={`flex items-center gap-1 rounded-full px-3 py-1 text-sm disabled:opacity-50 ${
                      guess ? "bg-sky-500 font-medium text-ink hover:bg-sky-400" : "border border-edge text-slate-200 hover:bg-edge"
                    }`}
                  >
                    {guess && (busy === s.key ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} aria-hidden />)}
                    {c.label}
                  </button>
                );
              })}
            </div>
          </li>
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
