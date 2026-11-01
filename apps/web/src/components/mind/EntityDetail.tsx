import { ArrowLeft, FileText, MessageSquare, Sparkle, Trash2 } from "lucide-react";
import { Link } from "react-router";
import { ENTITY_TYPES, relationText } from "../../lib/entities";
import { formatDate } from "../../lib/format";
import { linksOf, type Link as EntityLink } from "../../lib/knowledge";
import type { FileItem, GraphEntity, KnowledgeGraph } from "../../lib/types";

/** One thing Nexus knows: what it's linked to and since when, the memories about it, the chats
 * it came up in and its files. */
export function EntityDetail({
  e,
  graph,
  names,
  memories,
  chatTitle,
  files,
  canEdit,
  onPick,
  onRemove,
  onPreview,
  back,
  extra,
}: {
  back: string;
  /** Shown under the title: its area and a link into the universe. */
  extra?: React.ReactNode;
  e: GraphEntity;
  graph: KnowledgeGraph;
  names: Map<string, string>;
  memories: Map<string, string>;
  chatTitle: (id: string) => string;
  files: Record<string, FileItem>;
  canEdit: boolean;
  onPick: (id: string | null) => void;
  onRemove: () => void;
  onPreview: (f: FileItem) => void;
}) {
  const t = ENTITY_TYPES[e.type] ?? ENTITY_TYPES.other;
  const { current, past } = linksOf(e.id, graph.relations);
  const own = e.file_ids.map((id) => files[id]).filter(Boolean);
  return (
    <div className="space-y-5">
      <button onClick={() => onPick(null)} className="flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
        <ArrowLeft size={15} /> {back}
      </button>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-slate-100">{e.is_self ? "You" : e.name}</h2>
            {!e.is_self && <span className={`rounded px-1.5 py-0.5 text-xs ${t.tone}`}>{t.label}</span>}
          </div>
          {e.summary && <p className="mt-0.5 text-sm text-slate-400">{e.summary}</p>}
          <p className="mt-1 text-xs text-slate-500">
            First noted {formatDate(e.created_at)} · last mentioned {formatDate(e.seen_at)}
            {e.aliases.length > 0 && <> · also called {e.aliases.join(", ")}</>}
          </p>
          {extra}
        </div>
        {canEdit && !e.is_self && (
          <button
            onClick={onRemove}
            className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-400 hover:text-rose-400"
            title="Forget it and its links; its memories stay"
            aria-label={`Forget ${e.name}`}
          >
            <Trash2 size={14} /> <span className="hidden sm:inline">Forget</span>
          </button>
        )}
      </div>

      {current.length + past.length === 0 && (
        <p className="rounded-lg border border-edge bg-panel p-4 text-sm text-slate-500">
          Not linked to anything else yet. Links appear when a chat says how it relates to something.
        </p>
      )}

      {current.length > 0 && (
        <Section title="Linked to" count={current.length}>
          <ul className="space-y-1">
            {current.map((l) => (
              <LinkRow key={l.relation.id} l={l} names={names} onPick={onPick} />
            ))}
          </ul>
        </Section>
      )}
      {past.length > 0 && (
        <Section title="No longer true" count={past.length}>
          <p className="mb-1.5 text-xs text-slate-500">Kept with the dates they held, so the history isn't lost.</p>
          <ul className="space-y-1">
            {past.map((l) => (
              <LinkRow key={l.relation.id} l={l} names={names} onPick={onPick} />
            ))}
          </ul>
        </Section>
      )}

      {e.memory_ids.length > 0 && (
        <Section title="Memories about it" count={e.memory_ids.length} icon={<Sparkle size={14} />}>
          <ul className="space-y-1 text-sm">
            {e.memory_ids.map((id) => (
              <li key={id}>
                <Link to={`/mind?tab=memories&memory=${id}`} className="text-sky-300 hover:underline" title="Open in Memories">
                  {memories.get(id) ?? "A memory"}
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {e.chat_ids.length > 0 && (
        <Section title="Came up in" count={e.chat_ids.length} icon={<MessageSquare size={14} />}>
          <ul className="space-y-1 text-sm">
            {e.chat_ids.map((id) => (
              <li key={id}>
                <Link to={`/c/${id}`} className="text-sky-300 hover:underline">
                  {chatTitle(id)}
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {own.length > 0 && (
        <Section title="Files" count={own.length} icon={<FileText size={14} />}>
          <ul className="space-y-1 text-sm">
            {own.map((f) => (
              <li key={f.id}>
                <button onClick={() => onPreview(f)} className="text-left text-sky-300 hover:underline">
                  {f.name}
                </button>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

export function Section({
  title,
  count,
  icon,
  children,
}: {
  title: string;
  count: number;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-slate-300">
        {icon}
        {title} <span className="text-slate-500">· {count}</span>
      </h3>
      {children}
    </section>
  );
}

function LinkRow({
  l,
  names,
  onPick,
}: {
  l: EntityLink;
  names: Map<string, string>;
  onPick: (id: string) => void;
}) {
  const r = l.relation;
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 text-sm">
      <span className={r.valid_to ? "text-slate-500" : "text-slate-200"}>{relationText(r, names)}</span>
      <span className="text-xs text-slate-500">
        {r.valid_to ? `${formatDate(r.valid_from)} to ${formatDate(r.valid_to)}` : `since ${formatDate(r.valid_from)}`}
      </span>
      <button onClick={() => onPick(l.other)} className="text-xs text-sky-300 hover:underline">
        {names.get(l.other) ?? "Open"} →
      </button>
    </li>
  );
}
