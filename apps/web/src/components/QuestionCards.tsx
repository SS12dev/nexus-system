import { Check, Loader2, MessageCircleQuestion, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { SHELLS } from "../lib/entities";
import type { CardAnswer, GraphCard } from "../lib/types";
import { toast } from "./Feedback";

const HIDE_KEY = "nexus.cards.later";

/** Short questions that fill in what the assistant knows about you, one at a time: the starter
 * deck from who you are outward, then a few each night where it is unsure. Every card can be
 * skipped; "Later" hides them until the next visit. */
export function QuestionCards({ compact = false, className = "" }: { compact?: boolean; className?: string }) {
  const assistant = useAssistantName();
  const [cards, setCards] = useState<GraphCard[]>([]);
  const [hidden, setHidden] = useState(() => {
    try {
      return sessionStorage.getItem(HIDE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const load = useCallback(() => api.questionCards().then(setCards).catch(() => {}), []);
  useEffect(() => {
    if (!hidden) load();
  }, [hidden, load]);

  const later = () => {
    setHidden(true);
    try {
      sessionStorage.setItem(HIDE_KEY, "1");
    } catch {
      // Hidden for now either way.
    }
  };
  const done = (id: string) => setCards((cs) => cs.filter((c) => c.id !== id));

  const card = cards[0];
  if (hidden || !card) return null;
  return (
    <section
      className={`pointer-events-auto rounded-2xl border border-sky-400/30 bg-ink/85 p-3 text-sm backdrop-blur ${className}`}
      aria-label="Questions to get to know you"
    >
      <div className="mb-2 flex items-center gap-2 text-xs text-sky-200">
        <MessageCircleQuestion size={14} />
        <span>
          {card.kind === "starter" ? `Help ${assistant} know you` : `${assistant} isn't sure`}
          {card.shell != null && card.shell > 0 && (
            <span className="text-slate-500"> · {SHELLS[card.shell]?.label.toLowerCase()}</span>
          )}
        </span>
        <span className="ml-auto text-slate-500">{cards.length} left</span>
        <button onClick={later} className="rounded p-0.5 text-slate-500 hover:text-slate-300" title="Later" aria-label="Hide questions for now">
          <X size={14} />
        </button>
      </div>
      <Card key={card.id} card={card} compact={compact} onDone={() => done(card.id)} />
    </section>
  );
}

function Card({ card, compact, onDone }: { card: GraphCard; compact: boolean; onDone: () => void }) {
  const [one, setOne] = useState("");
  const [many, setMany] = useState<string[]>([]);
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async (answer: CardAnswer) => {
    setBusy(true);
    try {
      const r = await api.answerCard(card.id, answer);
      toast(r.note || "Noted");
      onDone();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const skip = async () => {
    setBusy(true);
    try {
      await api.skipCard(card.id);
      onDone();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const chip = (label: string, on: boolean, onClick: () => void) => (
    <button
      key={label}
      type="button"
      aria-pressed={on}
      disabled={busy}
      onClick={onClick}
      className={`rounded-lg border px-2.5 py-1.5 text-left text-xs transition ${
        on ? "border-sky-400 bg-sky-500/15 text-sky-100" : "border-edge bg-panel text-slate-300 hover:border-sky-400/50"
      }`}
    >
      <span className="flex items-center gap-1.5">
        {on && <Check size={12} />}
        {label}
      </span>
    </button>
  );
  const ready =
    card.form === "one" ? !!one : card.form === "many" ? many.length > 0 : card.form === "text" ? !!words.trim() : true;
  const submit = () => {
    if (card.form === "one") return send({ choice: one });
    if (card.form === "many") return send({ choices: many });
    return send({ text: words.trim() });
  };

  return (
    <div>
      <p className="font-medium text-slate-100">{card.prompt}</p>
      {card.help && <p className="mt-0.5 text-xs text-slate-400">{card.help}</p>}
      <div className="mt-2">
        {card.form === "yesno" ? (
          <div className="flex gap-2">
            {chip("Yes", false, () => void send({ yes: true }))}
            {chip("No", false, () => void send({ yes: false }))}
          </div>
        ) : card.form === "text" ? (
          <textarea
            value={words}
            onChange={(e) => setWords(e.target.value)}
            rows={compact ? 2 : 3}
            maxLength={2000}
            placeholder="A sentence or two is plenty"
            className="w-full resize-none rounded-md border border-edge bg-ink px-3 py-2 text-sm text-slate-100 outline-none focus:border-sky-400"
          />
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {card.options.map((o) =>
              card.form === "one"
                ? chip(o, one === o, () => setOne(o))
                : chip(o, many.includes(o), () =>
                    setMany((m) =>
                      m.includes(o) ? m.filter((x) => x !== o) : m.length < (card.most || 99) ? [...m, o] : m,
                    ),
                  ),
            )}
          </div>
        )}
      </div>
      <div className="mt-2 flex items-center gap-2">
        {card.form !== "yesno" && (
          <button
            type="button"
            disabled={busy || !ready}
            onClick={() => void submit()}
            className="flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-40"
          >
            {busy && <Loader2 size={12} className="animate-spin" />} Save
          </button>
        )}
        {card.form === "many" && card.most > 0 && (
          <span className="text-xs text-slate-500">
            {many.length} of up to {card.most}
          </span>
        )}
        <button type="button" disabled={busy} onClick={() => void skip()} className="ml-auto text-xs text-slate-400 hover:text-slate-200">
          Skip
        </button>
      </div>
    </div>
  );
}
