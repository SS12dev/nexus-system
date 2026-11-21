import { ArrowLeft, ArrowRight, Check, CheckCircle2, Loader2, PartyPopper, Target } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { api } from "../../lib/api";
import { addDays, shortDay } from "../../lib/organiser";
import type { OrgItem, OrgReview } from "../../lib/types";
import { useStore } from "../../store/chat";
import { toast } from "../Feedback";
import { InboxCard, useContainers } from "./Inbox";
import { CaptureBar, DueChip, Empty, TaskRow } from "./parts";

const STEPS = [
  { key: "mind", title: "Empty your head", minutes: 2 },
  { key: "inbox", title: "Clear the Inbox", minutes: 2 },
  { key: "overdue", title: "Catch up on overdue", minutes: 2 },
  { key: "projects", title: "Check your projects", minutes: 2 },
  { key: "focus", title: "Look back, then pick next week's focus", minutes: 2 },
] as const;
const MAX_FOCUS = 3;

/** The weekly review: five short steps that leave the Inbox empty, nothing silently overdue,
 * every project with a next step, and three things to focus on next week. */
export function ReviewFlow({ today }: { today: string }) {
  const version = useStore((s) => s.organiserVersion);
  const containers = useContainers();
  const [review, setReview] = useState<OrgReview | null>(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState(0);
  const [focus, setFocus] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [started, setStarted] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .orgReview()
      .then((r) => {
        setReview(r);
        setError("");
        if (!started) {
          setFocus(r.focus);
          setNotes(r.notes);
        }
      })
      .catch((e) => setError((e as Error).message));
    // Reloads as things get filed, so the lists shrink while you work through them.
  }, [version]);

  const pickable = useMemo(() => {
    if (!review) return [];
    const seen = new Set<string>();
    return [...review.candidates, ...review.upcoming].filter((t) => !seen.has(t.id) && seen.add(t.id));
  }, [review]);

  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!review)
    return (
      <div className="flex items-center gap-2 py-10 text-sm text-slate-400">
        <Loader2 size={16} className="animate-spin" /> Getting your week together…
      </div>
    );

  const weekLabel = `the week of ${shortDay(review.week_of, today)}`;
  if (!started)
    return (
      <div className="rounded-xl border border-edge bg-panel p-5">
        <h2 className="mb-1 text-lg font-semibold">Weekly review for {weekLabel}</h2>
        {review.completed_at ? (
          <p className="mb-4 flex items-center gap-1.5 text-sm text-emerald-300">
            <CheckCircle2 size={15} /> Done for this week. You can go through it again any time.
          </p>
        ) : (
          <p className="mb-4 text-sm text-slate-400">About ten minutes, best on Sunday evening. Five short steps:</p>
        )}
        <ol className="mb-5 space-y-1.5 text-sm text-slate-300">
          {STEPS.map((s, i) => (
            <li key={s.key} className="flex items-center gap-2">
              <span className="flex size-5 items-center justify-center rounded-full bg-edge text-xs">{i + 1}</span>
              {s.title}
              {s.key === "inbox" && review.inbox.length > 0 && <span className="text-xs text-slate-500">({review.inbox.length} waiting)</span>}
              {s.key === "overdue" && review.overdue.length > 0 && <span className="text-xs text-rose-300/80">({review.overdue.length})</span>}
              {s.key === "projects" && review.stalled.length > 0 && <span className="text-xs text-amber-300/80">({review.stalled.length} need a next step)</span>}
            </li>
          ))}
        </ol>
        <button
          onClick={() => setStarted(true)}
          className="flex items-center gap-1.5 rounded-md bg-sky-500 px-4 py-2 text-sm font-medium text-ink hover:bg-sky-400"
        >
          {review.completed_at ? "Go through it again" : "Start the review"} <ArrowRight size={15} />
        </button>
      </div>
    );

  const finish = async () => {
    setBusy(true);
    try {
      const done = await api.orgFinishReview(focus, notes);
      setReview(done);
      setStarted(false);
      setStep(0);
      toast("Review done. Your focus shows on Today all week.");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const current = STEPS[step];
  const left = STEPS.slice(step).reduce((n, s) => n + s.minutes, 0);
  const reload = () => api.orgReview().then(setReview).catch(() => {});

  return (
    <div>
      <div className="mb-4">
        <div className="mb-1.5 flex items-center gap-2 text-xs text-slate-400">
          Step {step + 1} of {STEPS.length}
          <span className="ml-auto">about {left} min left</span>
        </div>
        <div className="flex gap-1" aria-hidden>
          {STEPS.map((s, i) => (
            <div key={s.key} className={`h-1 flex-1 rounded-full ${i <= step ? "bg-sky-400" : "bg-edge"}`} />
          ))}
        </div>
      </div>
      <h2 className="mb-1 text-lg font-semibold">{current.title}</h2>

      {current.key === "mind" && (
        <>
          <p className="mb-3 text-sm text-slate-400">
            Anything on your mind goes in now, one per line: things you promised someone, something you
            keep meaning to do, an idea, a worry. Don't sort them yet; that's the next step.
          </p>
          <CaptureBar autoFocus onSaved={reload} placeholder="Something on your mind (Enter adds it)" />
          <p className="mt-2 text-xs text-slate-500">
            Prompts: work, home, money, health, people to get back to, things to buy, upcoming trips or
            events.
          </p>
        </>
      )}

      {current.key === "inbox" && (
        <>
          <p className="mb-3 text-sm text-slate-400">
            Give each one a home: accept the suggestion, file it somewhere else, or archive it. Small
            jobs under two minutes? Just do them, then tick them off.
          </p>
          {review.inbox.length ? (
            <ul className="space-y-2">
              {review.inbox.map((i) => (
                <InboxCard key={i.id} item={i} today={today} containers={containers} onDone={reload} />
              ))}
            </ul>
          ) : (
            <Empty>Inbox empty. Nicely done.</Empty>
          )}
        </>
      )}

      {current.key === "overdue" && (
        <>
          <p className="mb-3 text-sm text-slate-400">
            For each: still doing it? Give it a realistic day. Not doing it? Drop it; it stays in the
            Archive.
          </p>
          {review.overdue.length ? (
            <ul className="space-y-1">
              {review.overdue.map((t) => (
                <Reschedule key={t.id} task={t} today={today} weekOf={review.week_of} onDone={reload} />
              ))}
            </ul>
          ) : (
            <Empty>Nothing overdue.</Empty>
          )}
        </>
      )}

      {current.key === "projects" && (
        <>
          <p className="mb-3 text-sm text-slate-400">
            These projects have no open task, or haven't moved in two weeks. Add the very next step, or
            pause or finish the project.
          </p>
          {review.stalled.length ? (
            <ul className="space-y-3">
              {review.stalled.map((p) => (
                <StalledProject key={p.id} project={p} onDone={reload} />
              ))}
            </ul>
          ) : (
            <Empty>Every active project has a next step.</Empty>
          )}
        </>
      )}

      {current.key === "focus" && (
        <>
          {review.done_this_week.length > 0 && (
            <div className="mb-4">
              <p className="mb-2 text-sm text-slate-400">
                You finished {review.done_this_week.length} task{review.done_this_week.length === 1 ? "" : "s"} in the last seven days:
              </p>
              <ul className="max-h-48 space-y-0.5 overflow-y-auto">
                {review.done_this_week.map((t) => (
                  <TaskRow key={t.id} task={t} today={today} onChanged={reload} />
                ))}
              </ul>
            </div>
          )}
          <label className="mb-4 block">
            <span className="mb-1 block text-sm text-slate-400">What went well, what didn't, anything to change? (optional)</span>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              maxLength={20000}
              className="w-full rounded-lg border border-edge bg-panel px-3 py-2 text-base sm:text-sm"
              style={{ outline: "none" }}
            />
          </label>
          <p className="mb-2 text-sm text-slate-400">
            Pick up to {MAX_FOCUS} things that would make next week a good one. They sit at the top of Today
            all week.
          </p>
          {pickable.length ? (
            <ul className="space-y-1">
              {pickable.map((t) => {
                const on = focus.includes(t.id);
                const full = !on && focus.length >= MAX_FOCUS;
                return (
                  <li key={t.id}>
                    <button
                      onClick={() => setFocus((f) => (on ? f.filter((x) => x !== t.id) : [...f, t.id]))}
                      disabled={full}
                      aria-pressed={on}
                      className={`flex w-full items-center gap-2.5 rounded-lg border px-3 py-2 text-left text-sm ${
                        on ? "border-sky-500/60 bg-sky-500/10" : "border-edge hover:bg-edge/40 disabled:opacity-40"
                      }`}
                    >
                      <Target size={15} className={on ? "text-sky-300" : "text-slate-500"} />
                      <span className="min-w-0 flex-1 truncate">{t.title}</span>
                      {t.due_on && <DueChip due={t.due_on} today={today} />}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <Empty>No open tasks to choose from. Add some in Tasks, or skip this.</Empty>
          )}
        </>
      )}

      <div className="mt-6 flex items-center gap-2">
        <button
          onClick={() => (step ? setStep(step - 1) : setStarted(false))}
          className="flex items-center gap-1.5 rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-edge"
        >
          <ArrowLeft size={15} /> Back
        </button>
        {step < STEPS.length - 1 ? (
          <button
            onClick={() => setStep(step + 1)}
            className="ml-auto flex items-center gap-1.5 rounded-md bg-sky-500 px-4 py-2 text-sm font-medium text-ink hover:bg-sky-400"
          >
            Next <ArrowRight size={15} />
          </button>
        ) : (
          <button
            onClick={finish}
            disabled={busy}
            className="ml-auto flex items-center gap-1.5 rounded-md bg-emerald-500 px-4 py-2 text-sm font-medium text-ink hover:bg-emerald-400 disabled:opacity-50"
          >
            {busy ? <Loader2 size={15} className="animate-spin" /> : <PartyPopper size={15} />} Finish the review
          </button>
        )}
      </div>
    </div>
  );
}

function Reschedule({ task, today, weekOf, onDone }: { task: OrgItem; today: string; weekOf: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const set = async (changes: Parameters<typeof api.orgUpdate>[1], done: string) => {
    setBusy(true);
    try {
      await api.orgUpdate(task.id, changes);
      toast(done);
      onDone();
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };
  const chip = "rounded-md border border-edge px-2 py-0.5 text-xs text-slate-300 hover:bg-edge disabled:opacity-40";
  return (
    <li className="rounded-lg border border-edge bg-panel px-3 py-2">
      <div className="flex items-baseline gap-2">
        <Link to={`/organiser/item/${task.id}`} className="min-w-0 flex-1 break-words text-sm hover:text-sky-200">
          {task.title}
        </Link>
        {task.due_on && <DueChip due={task.due_on} today={today} />}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        <button disabled={busy} className={chip} onClick={() => set({ due_on: today }, "Moved to today")}>Today</button>
        <button disabled={busy} className={chip} onClick={() => set({ due_on: addDays(today, 1) }, "Moved to tomorrow")}>Tomorrow</button>
        <button disabled={busy} className={chip} onClick={() => set({ due_on: weekOf > today ? weekOf : addDays(weekOf, 7) }, "Moved to next week")}>Next week</button>
        <button disabled={busy} className={chip} onClick={() => set({ due_on: null }, "Date cleared")}>No date</button>
        <button disabled={busy} className={`${chip} text-emerald-300`} onClick={() => set({ status: "done" }, "Done")}>
          <Check size={11} className="inline" /> Done
        </button>
        <button disabled={busy} className={`${chip} text-slate-400`} onClick={() => set({ status: "dropped" }, "Dropped")}>Drop</button>
      </div>
    </li>
  );
}

function StalledProject({ project, onDone }: { project: OrgItem; onDone: () => void }) {
  const set = async (status: "paused" | "done", done: string) => {
    try {
      await api.orgUpdate(project.id, { status });
      toast(done);
      onDone();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <li className="rounded-xl border border-edge bg-panel p-3">
      <div className="mb-2 flex items-baseline gap-2">
        <Link to={`/organiser/item/${project.id}`} className="min-w-0 flex-1 truncate font-medium hover:text-sky-200">
          {project.title}
        </Link>
        <button onClick={() => set("paused", "Paused")} className="text-xs text-slate-400 hover:text-slate-200">Pause</button>
        <button onClick={() => set("done", "Project done")} className="text-xs text-emerald-300 hover:text-emerald-200">Finished</button>
      </div>
      <CaptureBar inbox={false} task parentId={project.id} compact placeholder="The very next step (Enter to add)" onSaved={onDone} />
    </li>
  );
}
