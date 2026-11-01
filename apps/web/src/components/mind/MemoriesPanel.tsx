import { Brain, History, Pin, PinOff, RefreshCw, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { Markdown } from "../Markdown";
import { Select } from "../Select";
import { api } from "../../lib/api";
import { formatDate } from "../../lib/format";
import { hasRole, type Memory, type MemoryEvent, type MemoryKind, type Profile } from "../../lib/types";
import { useStore } from "../../store/chat";
import { useAssistantName } from "../../lib/assistant";
import { confirmAction } from "../Feedback";

export const KINDS: MemoryKind[] = ["preference", "fact", "goal", "instruction"];
export const KIND_STYLE: Record<MemoryKind, string> = {
  preference: "bg-sky-500/15 text-sky-300",
  fact: "bg-slate-500/20 text-slate-300",
  goal: "bg-emerald-500/15 text-emerald-300",
  instruction: "bg-amber-500/15 text-amber-300",
};
/** What each kind of memory is for, shown on the chips and in the add form. */
export const KIND_INFO: Record<MemoryKind, { label: string; help: string }> = {
  preference: {
    label: "Preference",
    help: "How you like things: tone, formats, tools, brands.",
  },
  fact: {
    label: "Fact",
    help: "Something true about you: your work, people, places, setup.",
  },
  goal: {
    label: "Goal",
    help: "Something you're working towards over weeks or months.",
  },
  instruction: {
    label: "Instruction",
    help: "A standing rule to always or never do something.",
  },
};

/** The memories tab of the Mind page: your profile, what has been learned, and adding, editing,
 * pinning or forgetting any of it. ?memory=<id> opens one. */
export function MemoriesPanel() {
  const assistant = useAssistantName();
  const user = useStore((s) => s.user)!;
  const canEdit = hasRole(user, "member");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [query, setQuery] = useState("");
  const [content, setContent] = useState("");
  const [kind, setKind] = useState<MemoryKind>("preference");
  const [error, setError] = useState("");
  const [rebuilding, setRebuilding] = useState(false);
  // A memory linked from elsewhere on the Mind page opens here.
  const [params] = useSearchParams();
  const focus = params.get("memory");

  const load = useCallback(async (q?: string) => {
    const [p, list] = await Promise.all([api.profile(), api.memories(q)]);
    setProfile(p);
    setMemories(list);
  }, []);

  const run = async (fn: () => Promise<void>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  // Follow what Nexus learns and rebuilds while the page is open.
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.memoryVersion !== prev.memoryVersion && !query) load(undefined).catch(() => {});
        if (s.memoryVersion !== prev.memoryVersion) setRebuilding(false);
      }),
    [load, query],
  );

  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!content.trim()) return;
    run(async () => {
      const m = await api.addMemory(content.trim(), kind);
      setMemories((ms) => [m, ...ms]);
      setContent("");
      setProfile(await api.profile());
    });
  };

  const replace = (m: Memory) => setMemories((ms) => ms.map((x) => (x.id === m.id ? m : x)));

  const remove = async (m: Memory) => {
    const ok = await confirmAction({
      title: "Forget this memory?",
      body: `"${m.content}"`,
      confirmLabel: "Forget",
      danger: true,
    });
    if (!ok) return;
    run(async () => {
      await api.deleteMemory(m.id, true);
      setMemories((ms) => ms.filter((x) => x.id !== m.id));
      setRebuilding(true);
    });
  };

  const forgetAll = async () => {
    const ok = await confirmAction({
      title: "Forget everything?",
      body: "Every memory, past-chat summary and your profile are deleted. This can't be undone.",
      confirmLabel: "Forget everything",
      danger: true,
    });
    if (!ok) return;
    run(async () => {
      await api.forgetEverything();
      await load();
    });
  };

  return (
    <div>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

        {profile && (
          <section className="mb-8 rounded-lg border border-edge bg-panel p-4">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <Brain size={18} className="text-sky-300" />
              <h2 className="font-medium">Your profile</h2>
              <span className="text-xs text-slate-500">
                {profile.built_at ? `Updated ${formatDate(profile.built_at)}` : "Not built yet"}
                {profile.stale && " · changes since, folded in overnight"}
              </span>
              {canEdit && (
                <button
                  onClick={() =>
                    run(async () => {
                      await api.rebuildProfile();
                      setRebuilding(true);
                    })
                  }
                  disabled={rebuilding || profile.memory_count === 0}
                  className="ml-auto flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60 disabled:opacity-40"
                >
                  <RefreshCw size={14} className={rebuilding ? "animate-spin" : ""} />
                  {rebuilding ? "Rebuilding" : "Rebuild now"}
                </button>
              )}
            </div>
            <p className="mb-3 text-xs text-slate-500">
              A short summary {assistant} writes from your memories and reads at the start of every chat; the
              teams it builds get it too. It has up to four parts: <b>About</b> (who you are),{" "}
              <b>Preferences</b>, <b>How to answer</b> (style and format) and <b>Current goals</b>. Pinned
              memories always make it in.
            </p>
            {profile.content ? (
              <Markdown size="sm">{profile.content}</Markdown>
            ) : (
              <p className="text-sm text-slate-400">
                {assistant} writes this once it has learned a few things about you.
              </p>
            )}
            {canEdit && (
              <label className="mt-4 flex items-start gap-3 border-t border-edge pt-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={profile.memory_enabled}
                  onChange={(e) => run(async () => setProfile(await api.setMemoryEnabled(e.target.checked)))}
                />
                <span>
                  <span className="text-slate-200">
                    Learn from my chats and use what {assistant} remembers
                  </span>
                  <span className="mt-0.5 block text-xs text-slate-500">
                    {profile.memory_enabled
                      ? `On: after each chat ${assistant} saves anything lasting you said, and uses this profile, your memories and summaries of past chats when it answers.`
                      : `Off: ${assistant} learns nothing new and answers without your profile, memories or past chats. Nothing here is deleted; turn it back on any time.`}
                  </span>
                </span>
              </label>
            )}
          </section>
        )}

        {canEdit && (
          <form onSubmit={add} className="mb-4 flex flex-wrap items-start gap-2">
            <input
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={`Tell ${assistant} something to remember`}
              maxLength={1000}
              className="min-w-56 flex-1 rounded-md border border-edge bg-panel px-3 py-2"
            />
            <Select
              size="md"
              value={kind}
              onChange={(e) => setKind(e.target.value as MemoryKind)}
              aria-label="Type of memory"
              className="w-40"
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {KIND_INFO[k].label}
                </option>
              ))}
            </Select>
            <button className="rounded-md bg-sky-500 px-4 py-2 font-medium text-ink">Add</button>
            <p className="w-full text-xs text-slate-500">
              <span className={`mr-1.5 rounded px-1.5 py-0.5 ${KIND_STYLE[kind]}`}>
                {KIND_INFO[kind].label}
              </span>
              {KIND_INFO[kind].help}
            </p>
          </form>
        )}

        <form
            onSubmit={(e) => {
              e.preventDefault();
              run(() => load(query.trim() || undefined));
            }}
            className="mb-4 flex items-center gap-2 rounded-md border border-edge bg-panel px-3"
          >
            <Search size={16} className="text-slate-500" />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                if (!e.target.value) run(() => load());
              }}
              placeholder={`Search ${profile?.memory_count ?? ""} memories`}
              className="min-w-0 flex-1 bg-transparent py-2 outline-none"
            />
          </form>
  
          <div className="space-y-2">
            {memories.length === 0 && (
              <p className="text-slate-400">
                {query ? "Nothing found." : `Nothing yet. ${assistant} learns as you chat.`}
              </p>
            )}
            {memories.map((m) => (
              <MemoryRow
                key={m.id}
                memory={m}
                focused={m.id === focus}
                canEdit={canEdit}
                onChange={(changes) => run(async () => replace(await api.updateMemory(m.id, changes)))}
                onDelete={() => remove(m)}
              />
            ))}
          </div>

        {canEdit && (profile?.memory_count ?? 0) > 0 && (
          <button onClick={forgetAll} className="mt-10 text-sm text-rose-400 hover:text-rose-300">
            Forget everything
          </button>
        )}
    </div>
  );
}

function MemoryRow({
  memory: m,
  focused,
  canEdit,
  onChange,
  onDelete,
}: {
  memory: Memory;
  focused: boolean;
  canEdit: boolean;
  onChange: (changes: { content?: string; kind?: MemoryKind; pinned?: boolean }) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(m.content);
  const [events, setEvents] = useState<MemoryEvent[] | null>(null);

  const row = useRef<HTMLDivElement>(null);

  useEffect(() => setDraft(m.content), [m.content]);
  useEffect(() => {
    if (focused) row.current?.scrollIntoView({ block: "center" });
  }, [focused]);

  const save = () => {
    setEditing(false);
    if (draft.trim() && draft.trim() !== m.content) onChange({ content: draft.trim() });
  };

  return (
    <div
      ref={row}
      className={`rounded-lg border bg-panel px-4 py-3 ${focused ? "border-sky-400 ring-1 ring-sky-400/50" : "border-edge"}`}
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          {editing ? (
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={save}
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
                if (e.key === "Escape") {
                  setDraft(m.content);
                  setEditing(false);
                }
              }}
              className="w-full rounded border border-edge bg-ink px-2 py-1"
            />
          ) : (
            <button
              disabled={!canEdit}
              onClick={() => setEditing(true)}
              className="text-left disabled:cursor-default"
              title={canEdit ? "Click to edit" : undefined}
            >
              {m.content}
            </button>
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            {canEdit ? (
              <Select
                size="xs"
                tone={KIND_STYLE[m.kind]}
                value={m.kind}
                onChange={(e) => onChange({ kind: e.target.value as MemoryKind })}
                aria-label="Type of memory"
                title={`${KIND_INFO[m.kind].help} Click to change the type.`}
              >
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {KIND_INFO[k].label}
                  </option>
                ))}
              </Select>
            ) : (
              <span className={`rounded px-1.5 py-0.5 ${KIND_STYLE[m.kind]}`} title={KIND_INFO[m.kind].help}>
                {KIND_INFO[m.kind].label}
              </span>
            )}
            <span
              className="inline-flex items-center gap-1"
              title={`How sure it is: ${Math.round(m.confidence * 100)}%. Fades if nothing confirms it for 60 days; dropped below 20%. Pinned memories never fade.`}
            >
              <span className="h-1 w-12 overflow-hidden rounded bg-edge">
                <span
                  className="block h-full bg-sky-400"
                  style={{ width: `${Math.round(m.confidence * 100)}%` }}
                />
              </span>
            </span>
            <span>
              {m.source === "user" ? "Added by you" : "Learned"}
              {m.source_chat_id && (
                <>
                  {" in "}
                  <Link to={`/c/${m.source_chat_id}`} className="hover:text-sky-300">
                    a chat
                  </Link>
                </>
              )}{" "}
              · {formatDate(m.updated_at)}
            </span>
          </div>
          {events && (
            <ul className="mt-2 space-y-1 border-l border-edge pl-3 text-xs text-slate-400">
              {events.map((e, i) => (
                <li key={i}>
                  {formatDate(e.created_at)} · {e.actor} {e.op}
                  {e.content && e.op !== "add" ? `: ${e.content}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex items-center text-slate-400">
          <button
            onClick={() =>
              events
                ? setEvents(null)
                : api
                    .memoryEvents(m.id)
                    .then(setEvents)
                    .catch(() => {})
            }
            className="p-1.5 hover:text-white"
            aria-label="History"
            title="History"
          >
            <History size={15} />
          </button>
          {canEdit && (
            <>
              <button
                onClick={() => onChange({ pinned: !m.pinned })}
                className={`p-1.5 hover:text-white ${m.pinned ? "text-sky-300" : ""}`}
                aria-label={m.pinned ? "Unpin" : "Pin"}
                title={
                  m.pinned
                    ? "Pinned: always in your profile, never changed or faded. Click to unpin."
                    : "Pin: keep it in your profile and stop it changing or fading"
                }
              >
                {m.pinned ? <Pin size={15} /> : <PinOff size={15} />}
              </button>
              <button
                onClick={onDelete}
                className="p-1.5 hover:text-rose-400"
                aria-label="Delete"
                title="Delete"
              >
                <Trash2 size={15} />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
