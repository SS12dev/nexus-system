import { Loader2, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { confirmAction, toast } from "../components/Feedback";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { formatAgo, formatDateTime } from "../lib/format";
import type { RecallCheck, RecallSummary } from "../lib/types";
import { Section, Tile } from "./AdminParts";

/** Ways of recalling a check measures, in the order they're shown. */
const MODES: Record<string, { label: string; help: string }> = {
  standard: {
    label: "Standard",
    help: "Meaning and keyword search over memories and past-chat summaries, as every chat uses today.",
  },
  standard_more: {
    label: "Standard, more",
    help: "Standard with as many extra memories as connections can add, to tell a real gain from just sending more.",
  },
  graph: {
    label: "With connections",
    help: "Standard, plus memories about the people, projects and topics linked to what the question is about.",
  },
};
const modeLabel = (m: string) => MODES[m]?.label ?? m;
const pct = (n: number) => `${Math.round(n * 100)}%`;

/** The recall check: does the assistant find the right memory when a question needs it? */
export function RecallPanel({ onError, onOpenConfig }: { onError: (msg: string) => void; onOpenConfig: () => void }) {
  const assistant = useAssistantName();
  const [data, setData] = useState<RecallSummary | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.recall().then(setData).catch((e) => onError(e.message)), [onError]);
  useEffect(() => {
    load();
  }, [load]);

  const latest = data?.checks[0];
  const running = latest?.status === "running";
  // Poll while a check runs; it takes a few seconds to a minute.
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [running, load]);

  const run = async () => {
    setBusy(true);
    try {
      await api.runRecallCheck();
      toast("Recall check started");
      await load();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    const ok = await confirmAction({
      title: "Write new questions?",
      body: "The current questions are dropped and the next check writes a fresh set, so its scores won't compare with earlier ones.",
      confirmLabel: "Drop questions",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.resetRecallQuestions();
      toast("Questions dropped");
      await load();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  if (!data) return <p className="text-sm text-slate-400">Loading…</p>;
  const done = data.checks.find((c) => c.status === "done");
  const modes = done ? Object.keys(done.scores) : ["standard"];
  const empty = data.memories + data.past_chats === 0;

  return (
    <div>
      <p className="mb-4 max-w-2xl text-sm text-slate-400">
        Checks whether {assistant} finds the right memory when a question needs it, using your own memories and
        past chats. The memory model writes one question for each memory and past chat, once; every check then
        looks each question up exactly as a chat does and counts how often the right one comes back. Only your
        own memories are used, and only you see this.
      </p>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <button
          onClick={run}
          disabled={busy || running || empty}
          className="flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink disabled:opacity-40"
        >
          {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          {running ? "Checking" : "Run check"}
        </button>
        {data.questions > 0 && (
          <button
            onClick={reset}
            disabled={running}
            className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 text-sm hover:bg-edge/60 disabled:opacity-40"
          >
            <RotateCcw size={14} /> Write new questions
          </button>
        )}
        <span className="text-xs text-slate-500">
          {empty
            ? `Nothing to check yet: ${assistant} has no memories or past chats for you.`
            : `${data.questions} questions for ${data.memories} memories and ${data.past_chats} past chats. A check only calls the model for items without a question.`}
        </span>
      </div>

      <p className="mb-4 text-sm text-slate-400">
        Chats recall through connections:{" "}
        <b className={data.graph_recall_enabled ? "text-emerald-300" : "text-slate-200"}>
          {data.graph_recall_enabled ? "on" : "off"}
        </b>
        . Turn it on when <i>With connections</i> beats <i>Standard, more</i> here; it's{" "}
        <button onClick={onOpenConfig} className="text-sky-300 hover:underline">
          Recall through connections
        </button>{" "}
        under Memory and context.
      </p>

      {latest?.status === "failed" && (
        <p className="mb-4 text-sm text-rose-400">The last check failed: {latest.error}</p>
      )}

      {done ? (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {modes.map((m) => (
              <Tile
                key={m}
                label={`${modeLabel(m)}: found`}
                value={pct(done.scores[m].hit_rate)}
                hint={`${done.scores[m].hits} of ${done.scores[m].n} · rank score ${done.scores[m].mrr.toFixed(2)}`}
              />
            ))}
          </div>
          <Section title="How to read it">
            <ul className="mb-6 list-disc space-y-1 pl-5 text-sm text-slate-400">
              <li>
                <b className="text-slate-300">Found</b> is how often the right memory or past chat was among those put
                in the prompt (Memories per turn and Past-chat summaries per turn on the Configuration tab).
              </li>
              <li>
                <b className="text-slate-300">Rank score</b> is 1 when it always comes first, 0.5 when it's second on
                average, and lower when it's further down or missing.
              </li>
              {modes.map((m) => (
                <li key={m}>
                  <b className="text-slate-300">{modeLabel(m)}</b>: {MODES[m]?.help ?? ""}
                </li>
              ))}
            </ul>
          </Section>
          <History checks={data.checks} modes={modes} />
          <Misses check={done} />
        </>
      ) : (
        !running && <p className="text-sm text-slate-400">No checks yet. Run one to get a baseline.</p>
      )}
    </div>
  );
}

function History({ checks, modes }: { checks: RecallCheck[]; modes: string[] }) {
  const done = checks.filter((c) => c.status === "done");
  if (done.length < 2) return null;
  return (
    <Section title="Earlier checks">
      <div className="mb-6 overflow-x-auto rounded-lg border border-edge">
        <table className="w-full min-w-[480px] text-sm">
          <thead className="bg-panel text-left text-xs text-slate-400">
            <tr>
              <th className="px-3 py-2 font-medium">When</th>
              <th className="px-3 py-2 text-right font-medium">Questions</th>
              {modes.map((m) => (
                <th key={m} className="px-3 py-2 text-right font-medium">
                  {modeLabel(m)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {done.map((c) => (
              <tr key={c.id} className="border-t border-edge">
                <td className="px-3 py-2" title={formatDateTime(c.created_at)}>
                  {formatAgo(c.created_at)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{c.scores.standard?.n ?? "–"}</td>
                {modes.map((m) => (
                  <td key={m} className="px-3 py-2 text-right tabular-nums">
                    {c.scores[m] ? `${pct(c.scores[m].hit_rate)} · ${c.scores[m].mrr.toFixed(2)}` : "–"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function Misses({ check }: { check: RecallCheck }) {
  if (check.misses.length === 0) return null;
  return (
    <Section title="What standard recall missed">
      <p className="mb-2 text-sm text-slate-400">
        Questions whose memory or past chat wasn't recalled, with what it should have found. A miss usually means
        the question and the memory share little wording.
      </p>
      <ul className="space-y-2">
        {check.misses.map((m, i) => (
          <li key={i} className="rounded-lg border border-edge bg-panel px-4 py-3 text-sm">
            <p className="text-slate-200">“{m.question}”</p>
            <p className="mt-1 text-slate-400">
              Should find the {m.target}: {m.expected}
            </p>
            {m.found_by.length > 0 && (
              <p className="mt-1 text-xs text-emerald-300">Found by: {m.found_by.map(modeLabel).join(", ")}</p>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}
