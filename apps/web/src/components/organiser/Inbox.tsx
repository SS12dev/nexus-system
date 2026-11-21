import { Archive, CheckSquare, Loader2, NotebookPen, Sparkles, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import { formatAgo } from "../../lib/format";
import { canContain, filedStatus, TYPE_LABEL } from "../../lib/organiser";
import type { OrgItem, OrgItemInput } from "../../lib/types";
import { useStore } from "../../store/chat";
import { toast } from "../Feedback";
import { Select } from "../Select";
import { DueChip } from "./parts";

/** Projects, areas and resources that things can be filed into, kept fresh. */
export function useContainers(): OrgItem[] {
  const version = useStore((s) => s.organiserVersion);
  const [items, setItems] = useState<OrgItem[]>([]);
  useEffect(() => {
    api
      .orgItems({ type: ["project", "area", "resource"], status: ["active", "paused"], order: "title", limit: 500 })
      .then(setItems)
      .catch(() => {});
  }, [version]);
  return items;
}

/** One Inbox item with the assistant's suggestion and the ways to file it. */
export function InboxCard({ item, today, containers, onDone }: { item: OrgItem; today: string; containers: OrgItem[]; onDone?: () => void }) {
  const [picking, setPicking] = useState(false);
  const [type, setType] = useState<"note" | "task">(item.type === "task" ? "task" : "note");
  const [parent, setParent] = useState(item.suggestion?.parent_id ?? "");
  const [due, setDue] = useState(item.due_on ?? item.suggestion?.due_on ?? "");
  const [busy, setBusy] = useState(false);
  const s = item.suggestion;
  const thinking = !s && Date.now() - Date.parse(item.created_at) < 60_000;

  const file = async (changes: OrgItemInput, done: string) => {
    setBusy(true);
    try {
      await api.orgUpdate(item.id, changes);
      toast(done);
      onDone?.();
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };

  const accept = () =>
    s &&
    file(
      {
        type: s.type,
        status: filedStatus(s.type),
        parent_id: s.parent_id,
        due_on: s.type === "task" ? (item.due_on ?? s.due_on) : null,
      },
      s.parent_title ? `Filed in ${s.parent_title}` : `Filed as a ${s.type}`,
    );
  const places = containers.filter((c) => canContain(c.type, type));
  const Icon = item.type === "task" ? CheckSquare : NotebookPen;

  return (
    <li className="rounded-xl border border-edge bg-panel p-3">
      <div className="flex items-start gap-2.5">
        <Icon size={16} className="mt-0.5 shrink-0 text-slate-400" aria-label={TYPE_LABEL[item.type]} />
        <div className="min-w-0 flex-1">
          <Link to={`/organiser/item/${item.id}`} className="break-words font-medium hover:text-sky-200">
            {item.title}
          </Link>
          {item.excerpt && <p className="mt-0.5 line-clamp-2 text-sm text-slate-400">{item.excerpt}</p>}
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span>{formatAgo(item.created_at)}</span>
            {item.source === "telegram" && <span>from Telegram</span>}
            {item.source === "assistant" && <span>from your assistant</span>}
            {item.due_on && <DueChip due={item.due_on} today={today} />}
            {item.tags.map((t) => (
              <span key={t}>#{t}</span>
            ))}
          </div>
        </div>
      </div>

      {s ? (
        <div className="mt-2.5 flex flex-wrap items-center gap-2 rounded-lg bg-sky-500/5 px-2.5 py-2 text-sm">
          <Sparkles size={14} className="shrink-0 text-sky-300" />
          <span className="min-w-0 flex-1 text-slate-300">
            {TYPE_LABEL[s.type]}
            {s.parent_title ? ` in ${s.parent_title}` : ", not in any project"}
            {s.type === "task" && s.due_on ? `, due ${s.due_on}` : ""}
            {s.why && <span className="block text-xs text-slate-500">{s.why}</span>}
          </span>
          <button
            onClick={accept}
            disabled={busy}
            className="rounded-md bg-sky-500 px-2.5 py-1 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
          >
            Accept
          </button>
        </div>
      ) : (
        thinking && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
            <Loader2 size={12} className="animate-spin" /> Your assistant is looking for where this belongs…
          </p>
        )
      )}

      {picking ? (
        <form
          className="mt-2.5 flex flex-wrap items-end gap-2 border-t border-edge pt-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            const place = containers.find((c) => c.id === parent);
            void file(
              { type, status: filedStatus(type), parent_id: parent || null, due_on: type === "task" ? due || null : null },
              place ? `Filed in ${place.title}` : `Filed as a ${type}`,
            );
          }}
        >
          <div className="inline-flex rounded-md border border-edge p-0.5" role="radiogroup" aria-label="Kind">
            {(["task", "note"] as const).map((t) => (
              <button
                key={t}
                type="button"
                role="radio"
                aria-checked={type === t}
                onClick={() => setType(t)}
                className={`rounded px-2.5 py-1 text-sm ${type === t ? "bg-edge text-white" : "text-slate-400"}`}
              >
                {TYPE_LABEL[t]}
              </button>
            ))}
          </div>
          <Select value={parent} onChange={(e) => setParent(e.target.value)} aria-label="Where it goes" className="max-w-60 min-w-36 flex-1">
            <option value="">Not in a project or area</option>
            {(["project", "area", "resource"] as const).map((t) => {
              const group = places.filter((p) => p.type === t);
              return group.length ? (
                <optgroup key={t} label={`${TYPE_LABEL[t]}s`}>
                  {group.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.title}
                    </option>
                  ))}
                </optgroup>
              ) : null;
            })}
          </Select>
          {type === "task" && (
            <input
              type="date"
              value={due}
              onChange={(e) => setDue(e.target.value)}
              aria-label="Due date"
              className="rounded-md border border-edge bg-ink px-2 py-1 text-base [color-scheme:dark] sm:text-sm"
            />
          )}
          <button type="submit" disabled={busy} className="rounded-md bg-sky-500 px-3 py-1 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50">
            File it
          </button>
          <button type="button" onClick={() => setPicking(false)} className="px-1 text-sm text-slate-400 hover:text-white">
            Cancel
          </button>
        </form>
      ) : (
        <div className="mt-2.5 flex flex-wrap gap-2 text-sm">
          <button onClick={() => setPicking(true)} className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-slate-300 hover:bg-edge">
            <Wand2 size={14} /> {s ? "File somewhere else" : "File it"}
          </button>
          <button
            onClick={() =>
              file(
                item.type === "task" ? { status: "dropped" } : { status: "archived" },
                item.type === "task" ? "Dropped" : "Archived",
              )
            }
            disabled={busy}
            className="flex items-center gap-1.5 rounded-md px-2.5 py-1 text-slate-400 hover:bg-edge hover:text-slate-200"
            title={item.type === "task" ? "Not doing it: keep it in the Archive" : "Keep it, out of the way, in the Archive"}
          >
            <Archive size={14} /> {item.type === "task" ? "Drop" : "Archive"}
          </button>
        </div>
      )}
    </li>
  );
}
