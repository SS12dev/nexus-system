import {
  Bell,
  CalendarClock,
  ChevronDown,
  Mail,
  MessageCircle,
  Pause,
  Pencil,
  Play,
  Plus,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { confirmAction, toast } from "../components/Feedback";
import { Guide, GuideHeading } from "../components/Guide";
import { SchedulePicker } from "../components/SchedulePicker";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { currentSubscription, enablePush } from "../lib/push";
import { formatAgo } from "../lib/format";
import { browserTimezone, DEFAULT_SCHEDULE, formatInZone, toRule } from "../lib/schedule";
import {
  hasRole,
  type Channel,
  type Pack,
  type PackRoutine,
  type Routine,
  type RoutineInput,
  type RoutineRun,
  type RoutineRunStatus,
} from "../lib/types";
import { useStore } from "../store/chat";

const STATUS: Record<RoutineRunStatus, { label: string; style: string }> = {
  running: { label: "Running", style: "bg-violet-500/15 text-violet-300" },
  ok: { label: "Done", style: "bg-emerald-500/15 text-emerald-300" },
  quiet: { label: "Nothing new", style: "bg-slate-500/15 text-slate-300" },
  failed: { label: "Failed", style: "bg-rose-500/15 text-rose-300" },
  skipped: { label: "Skipped (budget)", style: "bg-amber-500/15 text-amber-300" },
};

const CHANNELS: { id: Channel; label: string; icon: typeof Mail }[] = [
  { id: "push", label: "Push", icon: Bell },
  { id: "email", label: "Email", icon: Mail },
  { id: "whatsapp", label: "WhatsApp", icon: MessageCircle },
];

/** Prompts that run on a schedule as you, each posting in a chat of its own. */
export function RoutinesPage() {
  const user = useStore((s) => s.user)!;
  const assistant = useAssistantName();
  const version = useStore((s) => s.routinesVersion);
  const [routines, setRoutines] = useState<Routine[] | null>(null);
  const [packs, setPacks] = useState<Pack[]>([]);
  const [editing, setEditing] = useState<Routine | "new" | null>(null);
  const [error, setError] = useState("");
  const canEdit = hasRole(user, "member");
  const timezone = user.timezone || browserTimezone();

  const load = useCallback(
    () =>
      api
        .routines()
        .then(setRoutines)
        .catch((e) => setError(e.message)),
    [],
  );
  useEffect(() => {
    load();
  }, [load, version]);
  useEffect(() => {
    api
      .profiles()
      .then((p) => setPacks(p.packs))
      .catch(() => setPacks([]));
  }, []);

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setError("");
    try {
      await fn();
      if (done) toast(done);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async (r: Routine) => {
    const ok = await confirmAction({
      title: `Delete ${r.name}?`,
      body: "It stops running and its history goes. Its chat stays.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (ok) await act(() => api.deleteRoutine(r.id), "Routine deleted");
  };

  const mine = new Set((routines ?? []).map((r) => r.name));
  const mineFirst = (user.profile?.packs ?? []) as string[];
  const ideas = [...packs].sort(
    (a, b) => Number(mineFirst.includes(b.id)) - Number(mineFirst.includes(a.id)),
  );

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-4 py-6">
        <div className="mb-1 flex items-center gap-3">
          <h1 className="text-xl font-semibold">Routines</h1>
          {canEdit && (
            <button
              onClick={() => setEditing("new")}
              className="ml-auto flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400"
            >
              <Plus size={15} /> New routine
            </button>
          )}
        </div>
        <p className="mb-4 text-sm text-slate-400">
          Things {assistant} does for you on a schedule, like a morning brief or a weekly digest.
          Each one posts in a chat of its own. Times are in {timezone}.
        </p>
        <Guide title="How routines work">
          <div>
            <GuideHeading>Making one</GuideHeading>
            <p>
              Add one here, pick an idea below, or just ask {assistant}: "every weekday at 8, tell
              me what's on my calendar". Write the prompt the way you'd ask it in a chat.
            </p>
          </div>
          <div>
            <GuideHeading>When it runs</GuideHeading>
            <p>
              It's a normal chat turn on your budget, with your memory and connected Google
              services. "Only tell me when there's something new" keeps digests quiet on slow days.
              After three failed runs in a row a routine pauses itself and says why.
            </p>
          </div>
        </Guide>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

        {editing && (
          <RoutineEditor
            routine={editing === "new" ? null : editing}
            timezone={timezone}
            channels={user.channels ?? []}
            hasPhone={!!user.phone}
            onCancel={() => setEditing(null)}
            onSaved={async () => {
              setEditing(null);
              await load();
            }}
          />
        )}

        {routines === null ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : routines.length === 0 ? (
          <div className="rounded-lg border border-dashed border-edge p-6 text-center text-sm text-slate-400">
            <CalendarClock className="mx-auto mb-2 text-slate-500" />
            No routines yet. Add one, or pick an idea below.
          </div>
        ) : (
          <ul className="space-y-3">
            {routines.map((r) => (
              <RoutineCard
                key={r.id}
                routine={r}
                canEdit={canEdit}
                onToggle={() =>
                  act(
                    () => api.updateRoutine(r.id, { enabled: !r.enabled }),
                    r.enabled ? "Paused" : "Turned on",
                  )
                }
                onRun={() => act(() => api.runRoutine(r.id), `${r.name} is running`)}
                onEdit={() => setEditing(r)}
                onDelete={() => remove(r)}
              />
            ))}
          </ul>
        )}

        {canEdit && ideas.length > 0 && (
          <section className="mt-8">
            <h2 className="mb-1 font-medium">Ideas</h2>
            <p className="mb-3 text-xs text-slate-500">
              Ready-made routines. The ones for what you told {assistant} at setup come first.
            </p>
            <div className="space-y-2">
              {ideas.map((p) => (
                <IdeaGroup
                  key={p.id}
                  pack={p}
                  open={mineFirst.includes(p.id)}
                  have={mine}
                  onAdd={(r) =>
                    act(
                      () =>
                        api.createRoutine({
                          name: r.name,
                          prompt: r.prompt,
                          schedule: r.schedule,
                          timezone,
                          delivery: [],
                          only_if_new: r.only_if_new,
                        }),
                      `Added ${r.name}`,
                    )
                  }
                />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function RoutineCard({
  routine: r,
  canEdit,
  onToggle,
  onRun,
  onEdit,
  onDelete,
}: {
  routine: Routine;
  canEdit: boolean;
  onToggle: () => void;
  onRun: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [history, setHistory] = useState<RoutineRun[] | null>(null);
  const [open, setOpen] = useState(false);
  const toggleHistory = async () => {
    setOpen(!open);
    if (!open) setHistory(await api.routineRuns(r.id).catch(() => []));
  };
  const status = r.last_status ? STATUS[r.last_status] : null;
  return (
    <li className={`rounded-lg border border-edge bg-panel p-4 ${r.enabled ? "" : "opacity-70"}`}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-medium">{r.name}</h3>
            {!r.enabled && (
              <span className="rounded bg-slate-500/15 px-1.5 py-0.5 text-xs text-slate-300">
                Paused
              </span>
            )}
            {status && (
              <span className={`rounded px-1.5 py-0.5 text-xs ${status.style}`}>{status.label}</span>
            )}
            {r.delivery.map((c) => {
              const ch = CHANNELS.find((x) => x.id === c);
              return ch ? (
                <ch.icon key={c} size={14} className="text-slate-400" aria-label={ch.label} />
              ) : null;
            })}
          </div>
          <p className="mt-0.5 text-sm text-slate-400">
            {r.schedule_text}
            {r.next_run_at && <> · next {formatInZone(r.next_run_at, r.timezone)}</>}
            {r.last_run_at && <> · last ran {formatAgo(r.last_run_at)}</>}
          </p>
          <p className="mt-2 line-clamp-2 text-sm text-slate-300">{r.prompt}</p>
        </div>
        {canEdit && (
          <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-400">
            <input
              type="checkbox"
              role="switch"
              checked={r.enabled}
              onChange={onToggle}
              className="peer sr-only"
            />
            <span className="relative h-5 w-9 rounded-full bg-edge transition peer-checked:bg-sky-500 after:absolute after:top-0.5 after:left-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:transition peer-checked:after:translate-x-4" />
            {r.enabled ? "On" : "Off"}
          </label>
        )}
      </div>
      <div className="mt-3 flex flex-wrap gap-2 border-t border-edge pt-3 text-sm">
        {r.chat_id && (
          <Link
            to={`/c/${r.chat_id}`}
            className="rounded-md border border-edge px-2.5 py-1 hover:bg-edge/60"
          >
            Open chat
          </Link>
        )}
        {canEdit && (
          <>
            <button
              onClick={onRun}
              className="flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 hover:bg-edge/60"
            >
              <Play size={13} /> Run now
            </button>
            <button
              onClick={onEdit}
              className="flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 hover:bg-edge/60"
            >
              <Pencil size={13} /> Edit
            </button>
            <button
              onClick={onToggle}
              className="flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 hover:bg-edge/60 sm:hidden"
            >
              {r.enabled ? <Pause size={13} /> : <Play size={13} />} {r.enabled ? "Pause" : "Resume"}
            </button>
          </>
        )}
        <button
          onClick={toggleHistory}
          className="flex items-center gap-1 rounded-md px-2 py-1 text-slate-400 hover:text-slate-200"
        >
          History
          <ChevronDown size={13} className={open ? "rotate-180" : ""} />
        </button>
        {canEdit && (
          <button
            onClick={onDelete}
            aria-label={`Delete ${r.name}`}
            className="ml-auto rounded-md p-1.5 text-slate-500 hover:bg-edge/60 hover:text-rose-300"
          >
            <Trash2 size={15} />
          </button>
        )}
      </div>
      {open && (
        <ul className="mt-3 space-y-1 text-xs">
          {history === null ? (
            <li className="text-slate-500">Loading…</li>
          ) : history.length === 0 ? (
            <li className="text-slate-500">It hasn't run yet.</li>
          ) : (
            history.map((h) => (
              <li key={h.id} className="flex flex-wrap items-center gap-2">
                <span className={`rounded px-1.5 py-0.5 ${STATUS[h.status].style}`}>
                  {STATUS[h.status].label}
                </span>
                <span className="text-slate-400">{formatInZone(h.started_at, r.timezone)}</span>
                {h.delivered.length > 0 && (
                  <span className="text-slate-500">sent by {h.delivered.join(" and ")}</span>
                )}
                {h.error && <span className="text-rose-300">{h.error}</span>}
              </li>
            ))
          )}
        </ul>
      )}
    </li>
  );
}

function RoutineEditor({
  routine,
  timezone,
  channels,
  hasPhone,
  onCancel,
  onSaved,
}: {
  routine: Routine | null;
  timezone: string;
  channels: Channel[];
  hasPhone: boolean;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<RoutineInput>(
    routine
      ? {
          name: routine.name,
          prompt: routine.prompt,
          schedule: routine.schedule,
          delivery: routine.delivery,
          only_if_new: routine.only_if_new,
        }
      : {
          name: "",
          prompt: "",
          schedule: toRule(DEFAULT_SCHEDULE),
          delivery: [],
          only_if_new: false,
        },
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const zone = routine?.timezone ?? timezone;

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (routine) await api.updateRoutine(routine.id, draft);
      else await api.createRoutine({ ...draft, timezone: zone });
      toast(routine ? "Routine saved" : `Added ${draft.name}`);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="mb-6 space-y-3 rounded-lg border border-sky-500/40 bg-panel p-4">
      <h2 className="font-medium">{routine ? `Edit ${routine.name}` : "New routine"}</h2>
      <input
        aria-label="Name"
        placeholder="Name, e.g. Morning brief"
        value={draft.name}
        maxLength={80}
        onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        className="w-full rounded-md border border-edge bg-ink px-3 py-2"
      />
      <textarea
        aria-label="What it should do"
        placeholder="What should it do? Write it like a chat message, e.g. “Find the most important AI news from the last day, five items with links.”"
        value={draft.prompt}
        rows={4}
        maxLength={4000}
        onChange={(e) => setDraft({ ...draft, prompt: e.target.value })}
        className="w-full rounded-md border border-edge bg-ink px-3 py-2 text-sm"
      />
      <SchedulePicker
        value={draft.schedule}
        timezone={zone}
        onChange={(schedule) => setDraft({ ...draft, schedule })}
      />
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={draft.only_if_new}
          onChange={(e) => setDraft({ ...draft, only_if_new: e.target.checked })}
        />
        Only tell me when there's something new
      </label>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-slate-400">Also send it by</span>
        {CHANNELS.map((c) => {
          const available = channels.includes(c.id);
          const needsPhone = c.id === "whatsapp" && !hasPhone;
          return (
            <label
              key={c.id}
              className={`flex items-center gap-1.5 ${available ? "" : "opacity-50"}`}
              title={available ? undefined : `${c.label} isn't set up on this Nexus`}
            >
              <input
                type="checkbox"
                disabled={!available}
                checked={draft.delivery.includes(c.id)}
                onChange={async (e) => {
                  const on = e.target.checked;
                  setDraft({
                    ...draft,
                    delivery: on
                      ? [...draft.delivery, c.id]
                      : draft.delivery.filter((x) => x !== c.id),
                  });
                  // Push needs this device's permission, which only a tap can ask for.
                  if (on && c.id === "push" && !(await currentSubscription())) {
                    try {
                      const state = await api.pushState();
                      if (state.public_key) await enablePush(state.public_key);
                    } catch (err) {
                      setError((err as Error).message);
                    }
                  }
                }}
              />
              {c.label}
              {available && needsPhone && draft.delivery.includes(c.id) && (
                <Link to="/settings" className="text-xs text-amber-300 hover:underline">
                  add your number
                </Link>
              )}
            </label>
          );
        })}
      </div>
      {error && <p className="text-sm text-rose-400">{error}</p>}
      <div className="flex gap-2">
        <button
          onClick={save}
          disabled={saving || !draft.name.trim() || !draft.prompt.trim()}
          className="rounded-md bg-sky-500 px-4 py-1.5 text-sm font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
        >
          {routine ? "Save" : "Add routine"}
        </button>
        <button
          onClick={onCancel}
          className="rounded-md border border-edge px-4 py-1.5 text-sm hover:bg-edge/60"
        >
          Cancel
        </button>
      </div>
    </section>
  );
}

function IdeaGroup({
  pack,
  open,
  have,
  onAdd,
}: {
  pack: Pack;
  open: boolean;
  have: Set<string>;
  onAdd: (r: PackRoutine) => void;
}) {
  if (!pack.routines.length) return null;
  return (
    <details open={open} className="group rounded-lg border border-edge bg-panel/60">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-sm select-none">
        <span className="font-medium">{pack.label}</span>
        <span className="truncate text-slate-500">{pack.blurb}</span>
        <ChevronDown size={14} className="ml-auto shrink-0 transition-transform group-open:rotate-180" />
      </summary>
      <ul className="divide-y divide-edge border-t border-edge">
        {pack.routines.map((r) => (
          <li key={r.key} className="flex items-start gap-3 px-4 py-3 text-sm">
            <div className="min-w-0 flex-1">
              <div className="font-medium">{r.name}</div>
              <div className="text-xs text-slate-400">
                {r.schedule_text}
                {r.needs.length > 0 && <> · uses {r.needs.join(", ")}</>}
              </div>
              <p className="mt-1 line-clamp-2 text-xs text-slate-500">{r.prompt}</p>
            </div>
            {have.has(r.name) ? (
              <span className="text-xs text-emerald-400">Added</span>
            ) : (
              <button
                onClick={() => onAdd(r)}
                className="flex shrink-0 items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-xs hover:bg-edge/60"
              >
                <Plus size={13} /> Add
              </button>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
