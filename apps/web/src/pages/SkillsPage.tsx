import { AlertTriangle, ArrowLeft, History, Plus, Search, Sparkles, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { InstructionsPanel } from "../components/InstructionsPanel";
import { Markdown } from "../components/Markdown";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import {
  hasRole,
  type Skill,
  type SkillDetail,
  type SkillScope,
  type SkillVersion,
} from "../lib/types";
import { useStore } from "../store/chat";
import { useAssistantName } from "../lib/assistant";

const SCOPE_LABEL: Record<SkillScope, string> = {
  core: "Core",
  domain: "Shared",
  personal: "Yours",
  learned: "Learned",
};

// Shelves in the order the library uses (nexus_common.skills.CATEGORIES).
const SHELVES = ["documents", "research", "writing", "data", "productivity", "agents", "engineering", "other"];

const STATUS_STYLE: Record<Skill["status"], string> = {
  active: "bg-emerald-500/15 text-emerald-300",
  draft: "bg-amber-500/15 text-amber-300",
  disabled: "bg-slate-500/20 text-slate-400",
};

const TEMPLATE = `---
name: my-skill
description: What this skill does and when an agent should use it, in one sentence.
---

# My skill

1. Steps that work.
2. Pitfalls to avoid.
`;

export function SkillsPage() {
  const assistant = useAssistantName();
  const user = useStore((s) => s.user)!;
  const canWrite = hasRole(user, "member");
  const isAdmin = hasRole(user, "admin");
  const version = useStore((s) => s.skillsVersion);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [newScope, setNewScope] = useState<"personal" | "domain">("personal");
  const [versions, setVersions] = useState<SkillVersion[] | null>(null);
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [tab, setTab] = useState<"skills" | "instructions">("skills");
  const [shelf, setShelf] = useState<string | null>(null);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const detail = useRef<HTMLElement>(null);
  // Each new skill or draft starts at the top of the reading pane. Braces matter: newer
  // Chrome returns a promise from scrollTo, and React would call it as the cleanup.
  useEffect(() => {
    detail.current?.scrollTo({ top: 0 });
  }, [selected?.id, draft == null]);

  const act = async (fn: () => Promise<void>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const load = useCallback(async (q?: string) => setSkills(await api.skills(q)), []);
  useEffect(() => {
    load(query || undefined).catch((e) => setError(e.message));
    // Search reruns on submit; live updates refresh the plain list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, version]);

  const open = (id: string) =>
    act(async () => {
      setSelected(await api.skill(id));
      setDraft(null);
      setVersions(null);
      setOpenFile(null);
    });

  const save = () =>
    act(async () => {
      if (draft == null) return;
      const s = selected
        ? await api.editSkill(selected.id, draft)
        : await api.createSkill(draft, newScope);
      setSelected(s);
      setDraft(null);
      setVersions(null);
      await load();
    });

  const importFile = (file: File) =>
    act(async () => {
      const s = await api.importSkill(file, isAdmin ? newScope : "personal");
      setSelected(s);
      await load();
    });

  const search = (e: FormEvent) => {
    e.preventDefault();
    load(query.trim() || undefined).catch((err) => setError(err.message));
  };

  const groups: SkillScope[] = ["core", "domain", "personal", "learned"];
  const shelves = SHELVES.filter((c) => skills.some((s) => s.category === c));
  const shown = shelf ? skills.filter((s) => s.category === shelf) : skills;
  // On a phone the list and the skill take turns filling the screen.
  const reading = selected != null || draft != null;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mx-auto w-full max-w-6xl shrink-0 px-4 pt-6">
        <h1 className="mb-3 text-xl font-semibold">Skills and instructions</h1>
        <div className="mb-4 flex gap-1 border-b border-edge text-sm" role="tablist">
          {(["skills", "instructions"] as const).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={`-mb-px border-b-2 px-3 py-2 ${
                tab === t ? "border-sky-400 text-white" : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              {t === "skills" ? "Skills" : "Instructions"}
            </button>
          ))}
        </div>
        {tab === "skills" && (
          <p className="mb-4 max-w-3xl text-sm text-slate-400">
            Skills are tested how-to guides, often with scripts, that {assistant} and its agents
            load only when a task needs one, such as making a PDF or a spreadsheet, so they don't
            work it out from scratch each time. Skills {assistant} drafts from its own runs, and
            ones you import, stay off until you review them.
          </p>
        )}
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
      </div>

      {tab === "instructions" ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl px-4 pb-6">
            <InstructionsPanel isAdmin={isAdmin} canWrite={canWrite} onError={setError} />
          </div>
        </div>
      ) : (
        // The list and the reading pane scroll on their own, so picking a skill far down the
        // list shows it right beside the list, not at the top of the page.
        <div className="mx-auto grid min-h-0 w-full max-w-6xl flex-1 grid-rows-[minmax(0,1fr)] gap-6 px-4 md:grid-cols-[20rem_minmax(0,1fr)]">
          <aside className={`min-h-0 overflow-y-auto pr-1 pb-6 ${reading ? "hidden md:block" : ""}`}>
            <form onSubmit={search} className="mb-3 flex gap-2">
              <div className="relative flex-1">
                <Search size={14} className="absolute top-2.5 left-2 text-slate-500" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search skills"
                  className="w-full rounded-md border border-edge bg-panel py-1.5 pr-2 pl-7 text-sm"
                />
              </div>
            </form>
            {canWrite && (
              <div className="mb-4 flex flex-wrap gap-2">
                <button
                  onClick={() => {
                    setSelected(null);
                    setDraft(TEMPLATE);
                  }}
                  className="flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60"
                >
                  <Plus size={14} /> New
                </button>
                <button
                  onClick={() => fileInput.current?.click()}
                  className="flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60"
                >
                  <Upload size={14} /> Import
                </button>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".md,.zip"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) importFile(f);
                    e.target.value = "";
                  }}
                />
                {isAdmin && (
                  <Select
                    value={newScope}
                    onChange={(e) => setNewScope(e.target.value as "personal" | "domain")}
                    aria-label="Who new skills are for"
                    title="Who skills you create or import are for"
                  >
                    <option value="personal">For me</option>
                    <option value="domain">For everyone</option>
                  </Select>
                )}
              </div>
            )}
            {shelves.length > 1 && (
              <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="Shelves">
                {[null, ...shelves].map((c) => (
                  <button
                    key={c ?? "all"}
                    onClick={() => setShelf(c)}
                    aria-pressed={shelf === c}
                    className={`rounded-full border px-2.5 py-0.5 text-xs capitalize ${
                      shelf === c ? "border-sky-500/60 bg-edge text-white" : "border-edge text-slate-400 hover:bg-edge/50"
                    }`}
                  >
                    {c ?? "all"} ({c ? skills.filter((s) => s.category === c).length : skills.length})
                  </button>
                ))}
              </div>
            )}
            {groups.map((g) => {
              const items = shown.filter((s) => s.scope === g);
              if (!items.length) return null;
              return (
                <div key={g} className="mb-4">
                  <h2 className="mb-1 text-xs font-medium tracking-wide text-slate-500 uppercase">
                    {SCOPE_LABEL[g]}
                  </h2>
                  <ul className="space-y-1">
                    {items.map((s) => (
                      <li key={s.id}>
                        <button
                          onClick={() => open(s.id)}
                          className={`w-full rounded-md border px-3 py-2 text-left text-sm ${
                            selected?.id === s.id ? "border-sky-500/60 bg-edge" : "border-edge hover:bg-edge/50"
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            <Sparkles size={13} className="shrink-0 text-sky-300" />
                            <span className="truncate font-medium">{s.name}</span>
                            {s.flagged && (
                              <AlertTriangle size={13} className="shrink-0 text-amber-300" aria-label="Often fails" />
                            )}
                            <span className={`ml-auto rounded px-1.5 text-[10px] ${STATUS_STYLE[s.status]}`}>
                              {s.status}
                            </span>
                          </div>
                          <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">{s.description}</p>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </aside>

          <section ref={detail} className={`min-h-0 min-w-0 overflow-y-auto pb-6 ${reading ? "" : "hidden md:block"}`}>
            {reading && (
              <button
                onClick={() => {
                  setSelected(null);
                  setDraft(null);
                }}
                className="mb-3 flex items-center gap-1 text-sm text-slate-400 hover:text-white md:hidden"
              >
                <ArrowLeft size={14} /> All skills
              </button>
            )}
            {draft != null ? (
              <div className="space-y-3">
                <h2 className="font-medium">{selected ? `Edit ${selected.name}` : "New skill"}</h2>
                <p className="text-xs text-slate-500">
                  SKILL.md: a name and a one-sentence description (all an agent sees until it
                  loads the skill), then the instructions. Saving makes a new version.
                </p>
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={24}
                  className="w-full rounded-md border border-edge bg-panel p-3 font-mono text-xs"
                  aria-label="SKILL.md"
                />
                <div className="flex gap-2">
                  <button onClick={save} className="rounded-md bg-sky-600 px-3 py-1.5 text-sm hover:bg-sky-500">
                    Save
                  </button>
                  <button
                    onClick={() => setDraft(null)}
                    className="rounded-md border border-edge px-3 py-1.5 text-sm hover:bg-edge/60"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : selected ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-lg font-semibold">{selected.name}</h2>
                  <code className="text-xs text-slate-500">{selected.slug}</code>
                  <span className="rounded bg-edge px-1.5 text-xs text-slate-300 capitalize">{selected.category}</span>
                  <span className={`rounded px-1.5 text-xs ${STATUS_STYLE[selected.status]}`}>
                    {selected.status}
                  </span>
                  <span className="text-xs text-slate-500">
                    v{selected.version} · used {selected.uses}× · {selected.successes} ok /{" "}
                    {selected.failures} failed
                  </span>
                </div>
                {selected.flagged && (
                  <p className="flex items-center gap-2 rounded-md bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
                    <AlertTriangle size={14} /> Runs using this skill keep failing. Review it.
                  </p>
                )}
                {selected.status === "draft" && (
                  <p className="rounded-md bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
                    {selected.scope === "learned"
                      ? `${assistant} drafted this from one of its runs. Read it, edit if needed, then enable it.`
                      : "This skill is off until it is reviewed and enabled."}
                  </p>
                )}
                {selected.can_edit && (
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={() => setDraft(selected.content)}
                      className="rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() =>
                        act(async () => {
                          const next = selected.status === "active" ? "disabled" : "active";
                          await api.setSkillStatus(selected.id, next);
                          setSelected({ ...selected, status: next });
                          await load();
                        })
                      }
                      className="rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60"
                    >
                      {selected.status === "active" ? "Disable" : "Enable"}
                    </button>
                    <button
                      onClick={() => act(async () => setVersions(await api.skillVersions(selected.id)))}
                      className="flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60"
                    >
                      <History size={14} /> History
                    </button>
                    {isAdmin && selected.owner_id === user.id && (
                      <button
                        onClick={() => {
                          if (!confirm(`Share ${selected.name} with everyone? It moves to the shared library, turned on.`))
                            return;
                          act(async () => {
                            setSelected(await api.shareSkill(selected.id));
                            setVersions(null);
                            await load();
                          });
                        }}
                        className="rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60"
                      >
                        Share with everyone
                      </button>
                    )}
                    {selected.scope !== "core" && (
                      <button
                        onClick={() => {
                          if (!confirm(`Delete ${selected.name}?`)) return;
                          act(async () => {
                            await api.deleteSkill(selected.id);
                            setSelected(null);
                            await load();
                          });
                        }}
                        className="rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-rose-500/20"
                      >
                        Delete
                      </button>
                    )}
                  </div>
                )}
                {versions && (
                  <ul className="space-y-1 rounded-md border border-edge p-3 text-sm">
                    {versions.map((v) => (
                      <li key={v.version} className="flex items-center gap-3">
                        <span className="w-10 text-slate-400">v{v.version}</span>
                        <span className="flex-1 truncate">{v.note || "–"}</span>
                        <span className="text-xs text-slate-500">{formatDate(v.created_at)}</span>
                        {v.version !== selected.version && selected.can_edit && (
                          <button
                            onClick={() =>
                              act(async () => {
                                setSelected(await api.rollbackSkill(selected.id, v.version));
                                setVersions(await api.skillVersions(selected.id));
                              })
                            }
                            className="text-xs text-sky-300 hover:underline"
                          >
                            Restore
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <Markdown size="sm" className="rounded-md border border-edge bg-panel p-4">
                  {selected.content.replace(/^---[\s\S]*?\n---\s*\n/, "")}
                </Markdown>
                {selected.files.length > 0 && (
                  <div>
                    <h3 className="mb-1 text-sm font-medium">Files</h3>
                    <div className="flex flex-wrap gap-2">
                      {selected.files.map((f) => (
                        <button
                          key={f}
                          onClick={() => setOpenFile(openFile === f ? null : f)}
                          className={`rounded-md border px-2 py-0.5 font-mono text-xs ${
                            openFile === f ? "border-sky-500/60 bg-edge" : "border-edge hover:bg-edge/50"
                          }`}
                        >
                          {f}
                        </button>
                      ))}
                    </div>
                    {openFile && (
                      <pre className="mt-2 max-h-96 overflow-auto rounded-md border border-edge bg-ink p-3 text-xs">
                        {selected.file_contents[openFile]}
                      </pre>
                    )}
                  </div>
                )}
                {!!selected.recent_uses?.length && (
                  <div className="text-xs text-slate-400">
                    <h3 className="mb-1 text-sm font-medium text-slate-200">Your recent uses</h3>
                    {selected.recent_uses.map((u, i) => (
                      <div key={i}>
                        {formatDate(u.at)} · {u.agent} · {u.outcome ?? "running"}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <p className="hidden text-sm text-slate-500 md:block">Pick a skill to read it.</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
