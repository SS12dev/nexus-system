import { CalendarDays, Check, CornerDownLeft, Flag, Inbox, Loader2, Pin } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import { dueLabel, isClosed, TYPE_LABEL } from "../../lib/organiser";
import type { OrgItem, OrgOverview } from "../../lib/types";
import { useStore } from "../../store/chat";
import { toast } from "../Feedback";

/** The organiser's counts, reloaded whenever anything in it changes (here or elsewhere). */
export function useOrgOverview(): OrgOverview | null {
  const version = useStore((s) => s.organiserVersion);
  const [overview, setOverview] = useState<OrgOverview | null>(null);
  useEffect(() => {
    api.orgOverview().then(setOverview).catch(() => {});
  }, [version]);
  return overview;
}

const TONE = {
  late: "border-rose-500/40 bg-rose-500/10 text-rose-200",
  today: "border-amber-500/40 bg-amber-500/10 text-amber-100",
  soon: "border-sky-500/30 bg-sky-500/10 text-sky-100",
  later: "border-edge text-slate-400",
};

export function DueChip({ due, today, done }: { due: string; today: string; done?: boolean }) {
  const { text, tone } = dueLabel(due, today);
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded border px-1.5 text-xs leading-5 ${done ? TONE.later : TONE[tone]}`}
      title={`Due ${due}`}
    >
      <CalendarDays size={11} aria-hidden /> {text}
    </span>
  );
}

const PRIORITY_TONE = ["", "text-slate-400", "text-amber-300", "text-rose-400"];

export function PriorityFlag({ priority }: { priority: number }) {
  if (!priority) return null;
  const label = ["", "Low", "Medium", "High"][priority];
  return <Flag size={12} className={`shrink-0 ${PRIORITY_TONE[priority]}`} aria-label={`${label} priority`} />;
}

/** One task: a tick box, the title (opens it), its due date, priority and where it lives. */
export function TaskRow({
  task,
  today,
  parentTitle,
  onChanged,
  extra,
}: {
  task: OrgItem;
  today: string;
  parentTitle?: string | null;
  onChanged?: () => void;
  extra?: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const done = task.status === "done";
  const toggle = async () => {
    setBusy(true);
    try {
      await api.orgUpdate(task.id, { status: done ? "todo" : "done" });
      if (!done) toast(`Done: ${task.title}`);
      onChanged?.();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="group flex items-start gap-3 rounded-lg px-2 py-2 hover:bg-edge/40">
      <button
        onClick={toggle}
        disabled={busy || task.status === "dropped"}
        role="checkbox"
        aria-checked={done}
        aria-label={done ? `Mark ${task.title} not done` : `Mark ${task.title} done`}
        className={`mt-0.5 flex size-[18px] shrink-0 items-center justify-center rounded-full border transition-colors ${
          done ? "border-emerald-500 bg-emerald-500 text-ink" : "border-slate-500 hover:border-emerald-400"
        }`}
      >
        {busy ? <Loader2 size={11} className="animate-spin" /> : done && <Check size={12} strokeWidth={3} />}
      </button>
      <div className="min-w-0 flex-1">
        <Link
          to={`/organiser/item/${task.id}`}
          className={`block break-words leading-snug ${done || task.status === "dropped" ? "text-slate-500 line-through" : "text-slate-100"}`}
        >
          {task.title}
        </Link>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
          {task.status === "doing" && <span className="text-sky-300">Doing</span>}
          {task.status === "inbox" && (
            <span className="inline-flex items-center gap-1">
              <Inbox size={11} /> Inbox
            </span>
          )}
          {task.due_on && <DueChip due={task.due_on} today={today} done={isClosed(task)} />}
          <PriorityFlag priority={task.priority} />
          {parentTitle && <span className="truncate">{parentTitle}</span>}
          {task.tags.map((t) => (
            <span key={t}>#{t}</span>
          ))}
          {extra}
        </div>
      </div>
    </li>
  );
}

/** A note, project, area or resource in a list: title, a line of its text, when it changed. */
export function ItemCard({ item, meta }: { item: OrgItem; meta?: ReactNode }) {
  return (
    <Link
      to={`/organiser/item/${item.id}`}
      className="block rounded-lg border border-edge bg-panel px-3 py-2.5 hover:border-sky-500/40 hover:bg-edge/40"
    >
      <div className="flex items-baseline gap-2">
        {item.pinned && <Pin size={12} className="shrink-0 text-sky-300" aria-label="Pinned" />}
        <span className="min-w-0 truncate font-medium">{item.title}</span>
        <span className="ml-auto shrink-0 text-xs text-slate-500">{meta}</span>
      </div>
      {item.excerpt && <p className="mt-1 line-clamp-2 text-sm text-slate-400">{item.excerpt}</p>}
      {item.tags.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
          {item.tags.map((t) => (
            <span key={t}>#{t}</span>
          ))}
        </div>
      )}
    </Link>
  );
}

/** The one-line capture box: Enter saves. "[ ] ..." makes a task; #tags and a day at the end
 * ("tomorrow", "friday", "2026-12-01") are understood. */
export function CaptureBar({
  placeholder = "Quick capture: a thought, or [ ] a to-do",
  inbox = true,
  task = false,
  parentId,
  onSaved,
  autoFocus,
  compact,
}: {
  placeholder?: string;
  inbox?: boolean;
  task?: boolean;
  parentId?: string;
  onSaved?: (item: OrgItem) => void;
  autoFocus?: boolean;
  compact?: boolean;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    const value = text.trim();
    if (!value || busy) return;
    setBusy(true);
    try {
      const item = await api.orgCapture(value, { inbox, task, parent_id: parentId });
      setText("");
      if (inbox) toast(`In your Inbox: ${TYPE_LABEL[item.type].toLowerCase()} “${item.title}”`);
      onSaved?.(item);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
      className={`flex items-center gap-2 rounded-lg border border-edge bg-panel px-3 focus-within:border-sky-500/60 ${compact ? "py-1.5" : "py-2"}`}
    >
      {inbox ? <Inbox size={16} className="shrink-0 text-slate-400" /> : <span className="size-[18px] shrink-0 rounded-full border border-dashed border-slate-500" />}
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-slate-500 sm:text-sm"
        style={{ outline: "none" }}
        maxLength={2000}
        autoFocus={autoFocus}
        enterKeyHint="done"
        aria-label={placeholder}
      />
      {busy ? (
        <Loader2 size={15} className="animate-spin text-slate-400" />
      ) : (
        text.trim() && (
          <button type="submit" className="flex items-center gap-1 rounded border border-edge px-1.5 py-0.5 text-xs text-slate-300 hover:bg-edge">
            <CornerDownLeft size={12} /> Save
          </button>
        )
      )}
    </form>
  );
}

export function Section({ title, count, action, children }: { title: string; count?: number; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="mb-6">
      <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
        {title}
        {count != null && <span className="rounded-full bg-edge px-1.5 text-xs text-slate-400">{count}</span>}
        {action && <span className="ml-auto">{action}</span>}
      </h2>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-edge px-4 py-6 text-center text-sm text-slate-400">{children}</div>;
}
