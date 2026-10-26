import { CheckCircle2, Circle } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { formatAgo } from "../lib/format";
import type { FeedbackItem, FeedbackKind } from "../lib/types";

export const KIND_STYLE: Record<FeedbackKind, { label: string; style: string }> = {
  problem: { label: "Problem", style: "bg-rose-500/15 text-rose-300" },
  idea: { label: "Idea", style: "bg-sky-500/15 text-sky-300" },
  praise: { label: "Praise", style: "bg-emerald-500/15 text-emerald-300" },
};

/** What people sent from Send feedback in the menu. The self-review reads it too (without
 * names), so a recurring problem can turn into a proposal. */
export function FeedbackPanel({ onError }: { onError: (msg: string) => void }) {
  const [items, setItems] = useState<FeedbackItem[] | null>(null);
  const [openOnly, setOpenOnly] = useState(true);

  const load = () =>
    api
      .feedbackList(openOnly)
      .then(setItems)
      .catch((e) => onError(e.message));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openOnly]);

  const toggle = async (f: FeedbackItem) => {
    try {
      await api.markFeedback(f.id, !f.done_at);
      await load();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  return (
    <div>
      <label className="mb-4 flex items-center gap-2 text-sm text-slate-400">
        <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} />
        Only what isn't handled yet
      </label>
      {items === null ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing here. People send feedback from the menu.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((f) => (
            <li key={f.id} className={`rounded-lg border border-edge bg-panel p-3 ${f.done_at ? "opacity-60" : ""}`}>
              <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
                <span className={`rounded px-1.5 py-0.5 ${KIND_STYLE[f.kind].style}`}>{KIND_STYLE[f.kind].label}</span>
                <span className="text-slate-300">{f.name || f.email || "Someone who left"}</span>
                {f.page && <span className="text-slate-500">on {f.page}</span>}
                <span className="text-slate-500">{formatAgo(f.created_at)}</span>
                <button
                  onClick={() => toggle(f)}
                  className="ml-auto flex items-center gap-1 rounded-md border border-edge px-2 py-0.5 hover:bg-edge/60"
                >
                  {f.done_at ? <CheckCircle2 size={12} className="text-emerald-400" /> : <Circle size={12} />}
                  {f.done_at ? "Handled" : "Mark handled"}
                </button>
              </div>
              <p className="whitespace-pre-wrap text-sm">{f.message}</p>
              {!!f.screenshots?.length && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {f.screenshots.map((s, n) => (
                    <a
                      key={n}
                      href={api.feedbackShotUrl(f.id, n)}
                      target="_blank"
                      rel="noopener"
                      title={`${s.name} (opens in a new tab)`}
                      className="block overflow-hidden rounded-md border border-edge hover:border-sky-400"
                    >
                      <img
                        src={api.feedbackShotUrl(f.id, n)}
                        alt={`Screenshot ${n + 1}: ${s.name}`}
                        loading="lazy"
                        className="h-24 w-36 object-cover object-top"
                      />
                    </a>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
