import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  History,
  ListChecks,
  Loader2,
  Pin,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { Link, useNavigate } from "react-router";
import remarkGfm from "remark-gfm";
import { api, OrgConflict } from "../../lib/api";
import { formatAgo, formatDateTime } from "../../lib/format";
import {
  canContain,
  isClosed,
  linkWikilinks,
  openCheckboxes,
  PRIORITY_LABEL,
  STATUS_LABEL,
  STATUSES,
  TYPE_LABEL,
} from "../../lib/organiser";
import type { OrgItem, OrgItemDetail, OrgItemInput, OrgRevision, OrgType } from "../../lib/types";
import { useStore } from "../../store/chat";
import { confirmAction, toast } from "../Feedback";
import { Select } from "../Select";
import { CaptureBar, Empty, ItemCard, Section, TaskRow } from "./parts";

const SAVE_AFTER_MS = 800;
const REVISION_NOTE: Record<string, string> = {
  assistant: "before your assistant's edit",
  restore: "before a restore",
  conflict: "the other device's version",
};
const MODE_KEY = "nexus.organiser.mode";

type SaveState = { kind: "idle" } | { kind: "saving" } | { kind: "saved"; at: number } | { kind: "error"; message: string };

/** Open, read and edit one organiser item. Text saves itself as you type; a save from another
 * device in between is caught (the server refuses an outdated version) and you choose which
 * copy to keep. */
export function ItemEditor({ id, today, daily = false }: { id: string; today: string; daily?: boolean }) {
  const navigate = useNavigate();
  const change = useStore((s) => s.organiserChange);
  const version = useStore((s) => s.organiserVersion);
  const [item, setItem] = useState<OrgItemDetail | null>(null);
  const [missing, setMissing] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [dirty, setDirty] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [conflict, setConflict] = useState<OrgItem | null>(null);
  const [mode, setMode] = useState<"write" | "read">("write");
  const [history, setHistory] = useState(false);
  // Phones fold the kind/status/date fields away behind one line.
  const [showMeta, setShowMeta] = useState(false);
  // The version the text in the box is based on; refs so the debounced save reads the latest.
  const base = useRef(0);
  const latest = useRef({ title: "", body: "", dirty: false });
  latest.current = { title, body, dirty };

  const load = useCallback(
    async (initial = false) => {
      try {
        const got = await api.orgItem(id);
        setItem(got);
        setMissing("");
        if (initial || !latest.current.dirty) {
          base.current = got.version;
          setTitle(got.title);
          setBody(got.body);
          setDirty(false);
        }
        if (initial) {
          const pref = localStorage.getItem(MODE_KEY);
          setMode(daily || !got.body.trim() ? "write" : pref === "write" ? "write" : "read");
        }
      } catch (e) {
        setMissing((e as Error).message);
      }
    },
    [id, daily],
  );

  useEffect(() => {
    setItem(null);
    setConflict(null);
    setSave({ kind: "idle" });
    setHistory(false);
    void load(true);
  }, [load]);

  // Changes made elsewhere (the phone, the assistant, a task ticked in a list below).
  useEffect(() => {
    if (!change || !item) return;
    if (change.id === id && change.op === "deleted") setMissing("This was moved to Trash.");
    else void load();
  }, [version]);

  const persist = useCallback(
    async (changes: OrgItemInput, force = false) => {
      setSave({ kind: "saving" });
      try {
        const saved = await api.orgUpdate(id, { ...changes, version: force ? undefined : base.current });
        base.current = saved.version;
        setItem((cur) => (cur ? { ...cur, ...saved, body: saved.body ?? cur.body } : cur));
        setSave({ kind: "saved", at: Date.now() });
        setConflict(null);
        return saved;
      } catch (e) {
        if (e instanceof OrgConflict) {
          setConflict(e.current);
          setSave({ kind: "error", message: "Changed somewhere else" });
        } else setSave({ kind: "error", message: (e as Error).message });
        return null;
      }
    },
    [id],
  );

  const saveText = useCallback(async () => {
    const { title: t, body: b, dirty: d } = latest.current;
    if (!d) return;
    setDirty(false);
    const saved = await persist({ title: t, body: b });
    if (!saved) setDirty(true);
  }, [persist]);

  // Debounced autosave while typing (not while a conflict waits for a decision).
  useEffect(() => {
    if (!dirty || conflict) return;
    const timer = window.setTimeout(saveText, SAVE_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [title, body, dirty, conflict, saveText]);

  // Leaving the page (or this item) with unsaved text: save it on the way out.
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (latest.current.dirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      if (latest.current.dirty) void saveText();
    };
  }, [saveText]);

  const setMeta = async (changes: OrgItemInput) => {
    if (latest.current.dirty) await saveText();
    const saved = await persist(changes);
    if (saved) void load();
  };

  const remove = async () => {
    if (!item) return;
    const inside = item.children.length;
    const ok = await confirmAction({
      title: `Delete ${item.title}?`,
      body: inside
        ? `It goes to Trash with the ${inside} thing${inside === 1 ? "" : "s"} inside it. You can restore them for seven days.`
        : "It goes to Trash, where you can restore it for seven days.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      setDirty(false);
      await api.orgDelete(item.id);
      toast("Moved to Trash");
      navigate(-1);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const makeTasks = async () => {
    if (!item) return;
    if (latest.current.dirty) await saveText();
    try {
      const res = await api.orgMakeTasks(item.id);
      toast(`${res.made.length} task${res.made.length === 1 ? "" : "s"} made`);
      base.current = res.item.version;
      setBody(res.item.body ?? "");
      setDirty(false);
      void load();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const createLinked = useCallback(
    async (linkTitle: string) => {
      try {
        const made = await api.orgCreate({ type: "note", title: linkTitle });
        navigate(`/organiser/item/${made.id}`);
      } catch (e) {
        toast((e as Error).message, "error");
      }
    },
    [navigate],
  );

  if (missing)
    return (
      <Empty>
        {missing === "Not found" ? "This item doesn't exist, or it's in Trash." : missing}{" "}
        <Link to="/organiser" className="text-sky-300 hover:text-sky-200">
          Back to Today
        </Link>
      </Empty>
    );
  if (!item)
    return (
      <div className="flex items-center gap-2 py-10 text-sm text-slate-400">
        <Loader2 size={16} className="animate-spin" /> Opening…
      </div>
    );

  const checkboxes = openCheckboxes(body);
  const tasks = item.children.filter((c) => c.type === "task");
  const others = item.children.filter((c) => c.type !== "task");
  const isContainer = ["project", "area", "resource"].includes(item.type);

  return (
    <article className="min-w-0">
      {!daily && (
        <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
          <span>{TYPE_LABEL[item.type]}</span>
          {item.parent && (
            <>
              <span aria-hidden>·</span>
              <Link to={`/organiser/item/${item.parent.id}`} className="hover:text-slate-300">
                in {TYPE_LABEL[item.parent.type].toLowerCase()} {item.parent.title}
              </Link>
            </>
          )}
          <span aria-hidden>·</span>
          <span title={formatDateTime(item.updated_at)}>changed {formatAgo(item.updated_at)}</span>
          {item.source !== "user" && (
            <>
              <span aria-hidden>·</span>
              <span>added {item.source === "telegram" ? "from Telegram" : "by your assistant"}</span>
            </>
          )}
        </div>
      )}
      {conflict && (
        <div role="alert" className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
          <div className="mb-2 flex items-center gap-2 font-medium">
            <AlertTriangle size={15} /> This changed on another device (or your assistant edited it) while you were typing.
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => {
                base.current = conflict.version;
                setTitle(conflict.title);
                setBody(conflict.body ?? "");
                setDirty(false);
                setConflict(null);
                setSave({ kind: "idle" });
                void load();
              }}
              className="rounded-md border border-amber-500/40 px-2.5 py-1 hover:bg-amber-500/10"
            >
              Use the other version
            </button>
            <button
              onClick={async () => {
                const saved = await persist({ title, body, keep_revision: true }, true);
                if (saved) setDirty(false);
              }}
              className="rounded-md bg-amber-400 px-2.5 py-1 font-medium text-ink hover:bg-amber-300"
            >
              Keep mine
            </button>
          </div>
          <p className="mt-2 text-xs text-amber-100/70">
            Keeping yours puts the other version in this item's history, so you can still get it back.
            Using the other version drops what you typed since the last save.
          </p>
        </div>
      )}
      {!daily && (
        <div className="flex items-start gap-2">
          {item.type === "task" && (
            <button
              onClick={() => setMeta({ status: item.status === "done" ? "todo" : "done" })}
              role="checkbox"
              aria-checked={item.status === "done"}
              aria-label={item.status === "done" ? "Mark not done" : "Mark done"}
              className={`mt-2 flex size-5 shrink-0 items-center justify-center rounded-full border ${
                item.status === "done" ? "border-emerald-500 bg-emerald-500 text-ink" : "border-slate-500 hover:border-emerald-400"
              }`}
            >
              {item.status === "done" && <Check size={13} strokeWidth={3} />}
            </button>
          )}
          <textarea
            value={title}
            onChange={(e) => {
              setTitle(e.target.value.replace(/\n/g, ""));
              setDirty(true);
            }}
            rows={1}
            maxLength={300}
            placeholder="Title"
            aria-label="Title"
            className={`min-w-0 flex-1 resize-none bg-transparent text-2xl font-semibold leading-tight [field-sizing:content] placeholder:text-slate-600 ${isClosed(item) ? "text-slate-400" : ""}`}
            style={{ outline: "none" }}
          />
        </div>
      )}
      {!daily && (
        <>
          <button
            onClick={() => setShowMeta((v) => !v)}
            aria-expanded={showMeta}
            className="mt-2 flex w-full flex-wrap items-center gap-x-2 gap-y-1 text-left text-xs text-slate-400 sm:hidden"
          >
            <span>{STATUS_LABEL[item.status]}</span>
            {item.due_on && <span>· due {item.due_on}</span>}
            {item.priority > 0 && <span>· {PRIORITY_LABEL[item.priority]} priority</span>}
            {item.tags.map((t) => (
              <span key={t}>#{t}</span>
            ))}
            <span className="ml-auto text-sky-300">{showMeta ? "Hide details" : "Edit details"}</span>
          </button>
          <div className={showMeta ? "" : "hidden sm:block"}>
            <MetaBar item={item} onChange={setMeta} />
          </div>
        </>
      )}

      <div className="mt-3 flex items-center gap-2 text-xs">
        <div className="inline-flex rounded-md border border-edge p-0.5" role="tablist" aria-label="Edit or read">
          {(["write", "read"] as const).map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={mode === m}
              onClick={() => {
                setMode(m);
                localStorage.setItem(MODE_KEY, m);
              }}
              className={`rounded px-2.5 py-1 ${mode === m ? "bg-edge text-white" : "text-slate-400 hover:text-slate-200"}`}
            >
              {m === "write" ? "Write" : "Read"}
            </button>
          ))}
        </div>
        <SaveStatus state={save} dirty={dirty} />
        <span className="ml-auto hidden text-slate-500 sm:inline">
          Markdown · <code className="text-slate-400">[[Title]]</code> links · <code className="text-slate-400">- [ ]</code> checkboxes
        </span>
      </div>

      {mode === "write" ? (
        <BodyBox
          value={body}
          onChange={(v) => {
            setBody(v);
            setDirty(true);
          }}
          onSaveNow={() => void saveText()}
          daily={daily}
        />
      ) : (
        <div
          className="mt-2 min-h-24 cursor-text rounded-lg border border-transparent px-1 py-2 hover:border-edge"
          onDoubleClick={() => setMode("write")}
          title="Double-click to write"
        >
          {body.trim() ? (
            <Preview text={body} item={item} onCreate={createLinked} />
          ) : (
            <p className="text-sm text-slate-500">Nothing written yet. Switch to Write to add text.</p>
          )}
        </div>
      )}

      {(checkboxes > 0 || item.unresolved.length > 0) && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          {checkboxes > 0 && item.type !== "task" && (
            <button
              onClick={makeTasks}
              className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-slate-300 hover:bg-edge"
              title="Each unticked “- [ ] …” line becomes a task you can tick off, date and see in Tasks"
            >
              <ListChecks size={14} /> Make {checkboxes} checkbox{checkboxes === 1 ? "" : "es"} into tasks
            </button>
          )}
          {item.unresolved.slice(0, 6).map((t) => (
            <button
              key={t}
              onClick={() => createLinked(t)}
              className="flex items-center gap-1 rounded-md border border-dashed border-edge px-2.5 py-1 text-slate-400 hover:border-sky-500/50 hover:text-slate-200"
              title={`Nothing is called “${t}” yet`}
            >
              <Plus size={13} /> Create “{t}”
            </button>
          ))}
        </div>
      )}

      {isContainer && (
        <div className="mt-8">
          <Section title="Tasks" count={tasks.filter((t) => !isClosed(t)).length}>
            <CaptureBar
              inbox={false}
              task
              parentId={item.id}
              compact
              placeholder="Add a task (end with a day to date it)"
              onSaved={() => void load()}
            />
            {tasks.length > 0 && (
              <ul className="mt-2 space-y-0.5">
                {tasks.map((t) => (
                  <TaskRow key={t.id} task={t} today={today} onChanged={() => void load()} />
                ))}
              </ul>
            )}
          </Section>
          <Section
            title={item.type === "area" ? "Projects and notes" : "Notes"}
            count={others.length}
            action={
              <span className="flex gap-3">
                {item.type === "area" && (
                  <NewInside parentId={item.id} type="project" label="New project" />
                )}
                <NewInside parentId={item.id} type="note" label="New note" />
              </span>
            }
          >
            {others.length ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {others.map((c) => (
                  <ItemCard key={c.id} item={c} meta={c.type === "project" ? STATUS_LABEL[c.status] : TYPE_LABEL[c.type]} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-500">
                Nothing here yet. Notes about {item.title} go here, and{" "}
                {item.type === "area" ? "so do its projects." : "you can link to them from anywhere with [[their title]]."}
              </p>
            )}
          </Section>
        </div>
      )}

      {item.backlinks.length > 0 && (
        <Section title="Linked from" count={item.backlinks.length}>
          <ul className="flex flex-wrap gap-2">
            {item.backlinks.map((b) => (
              <li key={b.id}>
                <Link
                  to={`/organiser/item/${b.id}`}
                  className="flex items-center gap-1 rounded-md border border-edge px-2 py-1 text-sm text-slate-300 hover:bg-edge"
                >
                  {b.title} <ArrowUpRight size={12} className="text-slate-500" />
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <div className="mt-8 flex flex-wrap items-center gap-2 border-t border-edge pt-3 text-sm">
        <button
          onClick={() => setHistory((h) => !h)}
          aria-expanded={history}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-slate-400 hover:bg-edge hover:text-slate-200"
        >
          <History size={14} /> History
        </button>
        {item.type !== "daily" && (
          <button onClick={remove} className="flex items-center gap-1.5 rounded-md px-2 py-1 text-slate-400 hover:bg-rose-500/10 hover:text-rose-300">
            <Trash2 size={14} /> Delete
          </button>
        )}
        <span className="ml-auto text-xs text-slate-500">Saved versions are kept for undoing a bad edit.</span>
      </div>
      {history && (
        <Revisions
          itemId={item.id}
          onRestored={(saved) => {
            base.current = saved.version;
            setTitle(saved.title);
            setBody(saved.body ?? "");
            setDirty(false);
            setHistory(false);
            void load();
          }}
          onClose={() => setHistory(false)}
        />
      )}
    </article>
  );
}

function SaveStatus({ state, dirty }: { state: SaveState; dirty: boolean }) {
  if (state.kind === "saving") return <span className="flex items-center gap-1 text-slate-400"><Loader2 size={12} className="animate-spin" /> Saving…</span>;
  if (state.kind === "error") return <span className="text-rose-300">Not saved: {state.message}</span>;
  if (dirty) return <span className="text-slate-500">Editing…</span>;
  if (state.kind === "saved") return <span className="flex items-center gap-1 text-emerald-300/80"><Check size={12} /> Saved</span>;
  return null;
}

/** The text box: grows with its text, Tab indents, Cmd/Ctrl+S saves straight away, and Enter
 * on a list line starts the next item ("- [ ] " carries on as an unticked checkbox). */
function BodyBox({ value, onChange, onSaveNow, daily }: { value: string; onChange: (v: string) => void; onSaveNow: () => void; daily: boolean }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(el.scrollHeight, daily ? 260 : 200)}px`;
  }, [value, daily]);

  const insert = (el: HTMLTextAreaElement, text: string, start: number, end: number) => {
    const next = value.slice(0, start) + text + value.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = start + text.length;
    });
  };

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        const el = e.currentTarget;
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
          e.preventDefault();
          onSaveNow();
        } else if (e.key === "Tab" && !e.shiftKey && el.selectionStart === el.selectionEnd) {
          e.preventDefault();
          insert(el, "  ", el.selectionStart, el.selectionEnd);
        } else if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && el.selectionStart === el.selectionEnd) {
          const start = el.selectionStart;
          const lineStart = value.lastIndexOf("\n", start - 1) + 1;
          const line = value.slice(lineStart, start);
          const m = line.match(/^(\s*)([-*+]|\d+\.)\s(\[[ xX]\]\s)?/);
          if (!m) return;
          e.preventDefault();
          // An empty item ends the list instead.
          if (line.trim() === m[0].trim()) {
            insert(el, "\n", lineStart, start);
            return;
          }
          const bullet = /\d+\./.test(m[2]) ? `${Number.parseInt(m[2], 10) + 1}.` : m[2];
          insert(el, `\n${m[1]}${bullet} ${m[3] ? "[ ] " : ""}`, start, start);
        }
      }}
      placeholder={daily ? "How's today going? Plans, notes, a log…" : "Write anything. Markdown works, [[Title]] links to another item, - [ ] makes a checkbox."}
      aria-label="Text"
      spellCheck
      className="mt-2 block w-full resize-none rounded-lg border border-edge bg-panel/40 px-3 py-2.5 font-mono text-[15px] leading-relaxed text-slate-100 placeholder:font-sans placeholder:text-slate-500 focus:border-sky-500/50 sm:text-sm"
      style={{ outline: "none" }}
    />
  );
}

function Preview({ text, item, onCreate }: { text: string; item: OrgItemDetail; onCreate: (title: string) => void }) {
  const md = useMemo(() => linkWikilinks(text, item.links), [text, item.links]);
  const components = useMemo<Components>(
    () => ({
      a: ({ node: _node, href = "", children, ...props }) => {
        if (href.startsWith("/organiser/new?")) {
          const t = new URLSearchParams(href.split("?")[1]).get("title") ?? "";
          return (
            <button
              type="button"
              onClick={() => onCreate(t)}
              className="text-slate-400 underline decoration-dashed underline-offset-2 hover:text-sky-200"
              title={`Nothing is called “${t}” yet: click to create it`}
            >
              {children}
            </button>
          );
        }
        if (href.startsWith("/"))
          return (
            <Link to={href} {...props}>
              {children}
            </Link>
          );
        return (
          <a href={href} target="_blank" rel="noreferrer" {...props}>
            {children}
          </a>
        );
      },
      pre: ({ node: _node, ...props }) => <pre className="max-w-full overflow-x-auto" {...props} />,
      table: ({ node: _node, ...props }) => (
        <div className="not-prose my-4 max-w-full overflow-x-auto rounded-md border border-edge">
          <table className="w-full border-collapse text-left text-sm" {...props} />
        </div>
      ),
    }),
    [onCreate],
  );
  return (
    <div className="prose dark:prose-invert max-w-none min-w-0 break-words prose-a:text-sky-300 prose-code:before:content-none prose-code:after:content-none">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {md}
      </ReactMarkdown>
    </div>
  );
}

function MetaBar({ item, onChange }: { item: OrgItemDetail; onChange: (c: OrgItemInput) => void }) {
  const [containers, setContainers] = useState<OrgItem[] | null>(null);
  const [tags, setTags] = useState(item.tags.map((t) => `#${t}`).join(" "));
  useEffect(() => setTags(item.tags.map((t) => `#${t}`).join(" ")), [item.tags]);
  useEffect(() => {
    api
      .orgItems({ type: ["project", "area", "resource"], status: ["active", "paused"], order: "title", limit: 500 })
      .then(setContainers)
      .catch(() => setContainers([]));
  }, []);

  const places = (containers ?? []).filter((c) => c.id !== item.id && canContain(c.type, item.type));
  const canMove = item.type !== "area" && item.type !== "resource";
  const types: OrgType[] = ["note", "task", "project", "area", "resource"];
  const label = "text-xs text-slate-500";

  return (
    <div className="mt-3 flex flex-wrap items-end gap-x-3 gap-y-2">
      <label className="flex flex-col gap-1">
        <span className={label}>Kind</span>
        <Select value={item.type} onChange={(e) => onChange({ type: e.target.value as Exclude<OrgType, "daily"> })} aria-label="Kind">
          {types.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABEL[t]}
            </option>
          ))}
        </Select>
      </label>
      <label className="flex flex-col gap-1">
        <span className={label}>Status</span>
        <Select value={item.status} onChange={(e) => onChange({ status: e.target.value as OrgItem["status"] })} aria-label="Status">
          {STATUSES[item.type].map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </Select>
      </label>
      {canMove && (
        <label className="flex min-w-0 flex-col gap-1">
          <span className={label}>{item.type === "project" ? "In area" : "In"}</span>
          <Select
            value={item.parent_id ?? ""}
            onChange={(e) => onChange({ parent_id: e.target.value || null })}
            aria-label="Where it belongs"
            className="max-w-56"
          >
            <option value="">{item.type === "project" ? "No area" : "Nowhere (loose)"}</option>
            {item.parent && !places.some((p) => p.id === item.parent!.id) && <option value={item.parent.id}>{item.parent.title}</option>}
            {(["area", "project", "resource"] as const).map((t) => {
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
        </label>
      )}
      {(item.type === "task" || item.type === "project") && (
        <>
          <label className="flex flex-col gap-1">
            <span className={label}>{item.type === "project" ? "Deadline" : "Due"}</span>
            <span className="flex items-center gap-1">
              <input
                type="date"
                value={item.due_on ?? ""}
                onChange={(e) => onChange({ due_on: e.target.value || null })}
                className="rounded-md border border-edge bg-ink px-2 py-1 text-base [color-scheme:dark] sm:text-sm"
                aria-label="Due date"
              />
              {item.due_on && (
                <button onClick={() => onChange({ due_on: null })} aria-label="Clear the date" className="text-slate-500 hover:text-slate-200">
                  <X size={14} />
                </button>
              )}
            </span>
          </label>
          <label className="flex flex-col gap-1">
            <span className={label}>Priority</span>
            <Select value={String(item.priority)} onChange={(e) => onChange({ priority: Number(e.target.value) })} aria-label="Priority">
              {PRIORITY_LABEL.map((p, i) => (
                <option key={p} value={i}>
                  {p}
                </option>
              ))}
            </Select>
          </label>
        </>
      )}
      <label className="flex min-w-32 flex-1 flex-col gap-1">
        <span className={label}>Tags</span>
        <input
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          onBlur={() => {
            const next = tags.split(/[\s,]+/).map((t) => t.replace(/^#/, "")).filter(Boolean);
            if (next.join(" ") !== item.tags.join(" ")) onChange({ tags: next });
          }}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
          placeholder="#health #admin"
          className="rounded-md border border-edge bg-ink px-2 py-1 text-base placeholder:text-slate-600 sm:text-sm"
          aria-label="Tags"
        />
      </label>
      <button
        onClick={() => onChange({ pinned: !item.pinned })}
        aria-pressed={item.pinned}
        title={item.pinned ? "Unpin" : "Pin to the top of its list"}
        className={`mb-0.5 rounded-md border px-2 py-1 ${item.pinned ? "border-sky-500/50 text-sky-200" : "border-edge text-slate-400 hover:text-slate-200"}`}
      >
        <Pin size={15} className={item.pinned ? "fill-sky-300/40" : ""} />
      </button>
    </div>
  );
}

function NewInside({ parentId, type, label }: { parentId: string; type: "note" | "project"; label: string }) {
  const navigate = useNavigate();
  return (
    <button
      onClick={async () => {
        try {
          const made = await api.orgCreate({ type, title: `New ${type}`, parent_id: parentId });
          navigate(`/organiser/item/${made.id}`);
        } catch (e) {
          toast((e as Error).message, "error");
        }
      }}
      className="flex items-center gap-1 text-xs font-normal text-sky-300 hover:text-sky-200"
    >
      <Plus size={13} /> {label}
    </button>
  );
}

function Revisions({ itemId, onRestored, onClose }: { itemId: string; onRestored: (saved: OrgItem) => void; onClose: () => void }) {
  const [revs, setRevs] = useState<OrgRevision[] | null>(null);
  const [open, setOpen] = useState<OrgRevision | null>(null);
  useEffect(() => {
    api.orgRevisions(itemId).then(setRevs).catch(() => setRevs([]));
  }, [itemId]);
  return (
    <div className="mt-3 rounded-lg border border-edge bg-panel p-3">
      <div className="mb-2 flex items-center gap-2 text-sm font-medium">
        Earlier versions
        <button onClick={onClose} className="ml-auto text-slate-400 hover:text-white" aria-label="Close history">
          <X size={15} />
        </button>
      </div>
      {revs === null ? (
        <Loader2 size={15} className="animate-spin text-slate-400" />
      ) : !revs.length ? (
        <p className="text-sm text-slate-400">No earlier versions yet. One is kept every few minutes while you edit, and before your assistant changes anything.</p>
      ) : (
        <div className="grid gap-3 md:grid-cols-[14rem_1fr]">
          <ul className="max-h-72 space-y-1 overflow-y-auto text-sm">
            {revs.map((r) => (
              <li key={r.id}>
                <button
                  onClick={() => api.orgRevision(itemId, r.id).then(setOpen)}
                  className={`w-full rounded-md px-2 py-1.5 text-left ${open?.id === r.id ? "bg-edge text-white" : "text-slate-300 hover:bg-edge/60"}`}
                >
                  {formatDateTime(r.created_at)}
                  <span className="block text-xs text-slate-500">
                    {REVISION_NOTE[r.actor] ?? "while you edited"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {open ? (
            <div className="min-w-0">
              <div className="mb-2 flex items-center gap-2">
                <span className="truncate font-medium">{open.title}</span>
                <button
                  onClick={async () => {
                    try {
                      onRestored(await api.orgRestore(itemId, open.id));
                      toast("Restored. The text it replaced is in the history too.");
                    } catch (e) {
                      toast((e as Error).message, "error");
                    }
                  }}
                  className="ml-auto rounded-md bg-sky-500 px-2.5 py-1 text-sm font-medium text-ink hover:bg-sky-400"
                >
                  Restore this
                </button>
              </div>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-ink p-2 text-xs text-slate-300">{open.body || "(empty)"}</pre>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Pick a version to see its text.</p>
          )}
        </div>
      )}
    </div>
  );
}
