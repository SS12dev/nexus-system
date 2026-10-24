import { FileText, MessageSquare, Sparkle, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import type { EntityType, GraphEntity, GraphRelation, KnowledgeGraph } from "../lib/types";
import { useStore } from "../store/chat";
import { confirmAction } from "./Feedback";

/** Entity types in the order they're listed, with their chip colour. */
export const ENTITY_TYPES: Record<EntityType, { label: string; plural: string; tone: string }> = {
  person: { label: "Person", plural: "People", tone: "bg-pink-500/15 text-pink-300" },
  project: { label: "Project", plural: "Projects", tone: "bg-sky-500/15 text-sky-300" },
  organisation: { label: "Organisation", plural: "Organisations", tone: "bg-amber-500/15 text-amber-300" },
  place: { label: "Place", plural: "Places", tone: "bg-emerald-500/15 text-emerald-300" },
  topic: { label: "Topic", plural: "Topics", tone: "bg-violet-500/15 text-violet-300" },
  tool: { label: "Tool", plural: "Tools", tone: "bg-indigo-500/15 text-indigo-300" },
  other: { label: "Other", plural: "Other", tone: "bg-slate-500/20 text-slate-300" },
};

/** One relation as a sentence: its own wording when the model gave one. */
export function relationText(r: GraphRelation, names: Map<string, string>): string {
  return r.fact || `${names.get(r.src) ?? "?"} ${r.predicate} ${names.get(r.dst) ?? "?"}`;
}

/** The Memory page's Connections view: who and what your memories are about, and how they
 * relate. Built from your chats, memories and files; deleting one keeps the memories. */
export function Connections({ canEdit, onOpenMemory }: { canEdit: boolean; onOpenMemory: (id: string) => void }) {
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(
    () =>
      api
        .knowledgeGraph()
        .then(setGraph)
        .catch((e) => setError(e.message)),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);
  // What Nexus learns while the page is open shows up here too.
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.memoryVersion !== prev.memoryVersion) load();
      }),
    [load],
  );

  const names = useMemo(() => new Map(graph?.entities.map((e) => [e.id, e.name]) ?? []), [graph]);
  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!graph) return <p className="text-sm text-slate-400">Loading…</p>;

  const me = graph.entities.find((e) => e.is_self);
  const others = graph.entities.filter((e) => !e.is_self);
  const mine = me ? graph.relations.filter((r) => r.src === me.id || r.dst === me.id) : [];
  const groups = (Object.keys(ENTITY_TYPES) as EntityType[])
    .map((t) => ({ type: t, items: others.filter((e) => e.type === t) }))
    .filter((g) => g.items.length > 0);

  const remove = async (e: GraphEntity) => {
    const ok = await confirmAction({
      title: `Remove ${e.name}?`,
      body: "It and its connections are removed. The memories about it stay; if a later chat mentions it again it comes back.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteEntity(e.id);
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  if (others.length === 0)
    return (
      <p className="text-sm text-slate-400">
        Nothing connected yet. As you chat, the people, projects, places, topics and tools you mention are noted
        here with how they relate. Older memories and files are linked in the background within a few minutes.
      </p>
    );

  return (
    <div className="space-y-6">
      {mine.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-medium text-slate-300">About you</h3>
          <ul className="space-y-1 text-sm">
            {mine.map((r) => (
              <Fact key={r.id} r={r} names={names} />
            ))}
          </ul>
        </section>
      )}
      {groups.map((g) => (
        <section key={g.type}>
          <h3 className="mb-2 text-sm font-medium text-slate-300">
            {ENTITY_TYPES[g.type].plural} <span className="text-slate-500">· {g.items.length}</span>
          </h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {g.items.map((e) => (
              <EntityCard
                key={e.id}
                e={e}
                relations={graph.relations.filter(
                  (r) => (r.src === e.id || r.dst === e.id) && r.src !== me?.id && r.dst !== me?.id,
                )}
                names={names}
                open={open === e.id}
                onToggle={() => setOpen(open === e.id ? null : e.id)}
                canEdit={canEdit}
                onRemove={() => remove(e)}
                onOpenMemory={onOpenMemory}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function Fact({ r, names }: { r: GraphRelation; names: Map<string, string> }) {
  return (
    <li className={r.valid_to ? "text-slate-500" : "text-slate-200"}>
      {relationText(r, names)}
      {r.valid_to && <span className="ml-1 text-xs">(until {formatDate(r.valid_to)})</span>}
    </li>
  );
}

function EntityCard({
  e,
  relations,
  names,
  open,
  onToggle,
  canEdit,
  onRemove,
  onOpenMemory,
}: {
  e: GraphEntity;
  relations: GraphRelation[];
  names: Map<string, string>;
  open: boolean;
  onToggle: () => void;
  canEdit: boolean;
  onRemove: () => void;
  onOpenMemory: (id: string) => void;
}) {
  const t = ENTITY_TYPES[e.type] ?? ENTITY_TYPES.other;
  return (
    <div className="min-w-0 rounded-lg border border-edge bg-panel px-3 py-2.5">
      <div className="flex items-start gap-2">
        <button onClick={onToggle} className="min-w-0 flex-1 text-left" aria-expanded={open}>
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-slate-100">{e.name}</span>
            <span className={`rounded px-1.5 py-0.5 text-xs ${t.tone}`}>{t.label}</span>
          </span>
          {e.summary && <span className="mt-0.5 block text-sm text-slate-400">{e.summary}</span>}
          <span className="mt-1 flex flex-wrap gap-x-3 text-xs text-slate-500">
            {e.memories > 0 && (
              <span className="inline-flex items-center gap-1">
                <Sparkle size={11} /> {e.memories} {e.memories === 1 ? "memory" : "memories"}
              </span>
            )}
            {e.chats + e.past_chats > 0 && (
              <span className="inline-flex items-center gap-1">
                <MessageSquare size={11} /> {e.chats} {e.chats === 1 ? "chat" : "chats"}
              </span>
            )}
            {e.files > 0 && (
              <span className="inline-flex items-center gap-1">
                <FileText size={11} /> {e.files} {e.files === 1 ? "file" : "files"}
              </span>
            )}
            {relations.length > 0 && <span>{relations.length} linked</span>}
          </span>
        </button>
        {canEdit && (
          <button
            onClick={onRemove}
            className="p-1 text-slate-500 hover:text-rose-400"
            aria-label={`Remove ${e.name}`}
            title="Remove from connections"
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>
      {open && (
        <div className="mt-2 space-y-2 border-t border-edge pt-2 text-sm">
          {relations.length > 0 ? (
            <ul className="space-y-1">
              {relations.map((r) => (
                <Fact key={r.id} r={r} names={names} />
              ))}
            </ul>
          ) : (
            <p className="text-slate-500">No links to anything else yet.</p>
          )}
          {e.memory_ids.length > 0 && (
            <div className="flex flex-wrap gap-2 text-xs">
              {e.memory_ids.slice(0, 6).map((id, i) => (
                <button key={id} onClick={() => onOpenMemory(id)} className="text-sky-300 hover:underline">
                  Memory {i + 1}
                </button>
              ))}
            </div>
          )}
          {e.file_ids.length > 0 && (
            <Link to="/library" className="block text-xs text-sky-300 hover:underline">
              Open its files in the Library
            </Link>
          )}
          {e.aliases.length > 0 && <p className="text-xs text-slate-500">Also called: {e.aliases.join(", ")}</p>}
        </div>
      )}
    </div>
  );
}
