import { ArrowLeft, FileText, MessageSquare, Search, Share2, Sparkle, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { confirmAction } from "../components/Feedback";
import { FilePreview } from "../components/FilePreview";
import { ImportHistory } from "../components/ImportHistory";
import { QuestionCards } from "../components/QuestionCards";
import { Guide, GuideHeading } from "../components/Guide";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { ENTITY_COLOR, ENTITY_TYPES, relationText } from "../lib/entities";
import { formatDate } from "../lib/format";
import { egoLayout, filterEntities, linksOf, mentions, type KnowledgeSort, type Link as EntityLink } from "../lib/knowledge";
import { hasRole, type EntityType, type FileItem, type GraphEntity, type KnowledgeGraph } from "../lib/types";
import { useStore } from "../store/chat";

const SORTS: Record<KnowledgeSort, string> = {
  mentions: "Most mentioned",
  recent: "Recently mentioned",
  name: "A to Z",
};

/** Everything the assistant knows, as one explorable graph: search and filter the people,
 * projects, places, topics and tools from your chats, memories and files, pick one to see what
 * it's linked to (and since when), and follow the links. ?focus=<id> opens one. */
export function KnowledgePage() {
  const assistant = useAssistantName();
  const user = useStore((s) => s.user)!;
  const canEdit = hasRole(user, "member");
  const chats = useStore((s) => s.chats);
  const files = useStore((s) => s.files);
  const syncFiles = useStore((s) => s.syncFiles);
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null);
  const [memories, setMemories] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [type, setType] = useState<EntityType | "all">("all");
  const [sort, setSort] = useState<KnowledgeSort>("mentions");
  const [preview, setPreview] = useState<FileItem | null>(null);
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus");

  const load = useCallback(
    () =>
      Promise.all([api.knowledgeGraph(), api.memories()])
        .then(([g, ms]) => {
          setGraph(g);
          setMemories(new Map(ms.map((m) => [m.id, m.content])));
        })
        .catch((e) => setError(e.message)),
    [],
  );
  useEffect(() => {
    load();
    api.files().then(syncFiles).catch(() => {});
  }, [load, syncFiles]);
  // What the assistant learns while the page is open shows up here too.
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.memoryVersion !== prev.memoryVersion) load();
      }),
    [load],
  );

  const names = useMemo(
    () => new Map(graph?.entities.map((e) => [e.id, e.is_self ? "You" : e.name]) ?? []),
    [graph],
  );
  const list = useMemo(
    () => (graph ? filterEntities(graph.entities, { query, type, sort }) : []),
    [graph, query, type, sort],
  );
  const counts = useMemo(() => {
    const c = new Map<EntityType, number>();
    for (const e of graph?.entities ?? []) if (!e.is_self) c.set(e.type, (c.get(e.type) ?? 0) + 1);
    return c;
  }, [graph]);
  const picked = graph?.entities.find((e) => e.id === focus) ?? null;
  const pick = (id: string | null) => setParams(id ? { focus: id } : {}, { replace: !id });

  const remove = async (e: GraphEntity) => {
    const ok = await confirmAction({
      title: `Remove ${e.name}?`,
      body: "It and its links are removed. The memories about it stay; if a later chat mentions it again it comes back.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteEntity(e.id);
      pick(null);
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const others = graph?.entities.filter((e) => !e.is_self).length ?? 0;
  const chatTitle = (id: string) => chats.find((c) => c.id === id)?.title || "Untitled chat";

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 py-6">
        {/* On a phone the list and the detail take turns; the header goes with the list. */}
        <div className={picked ? "hidden lg:block" : ""}>
          <h1 className="mb-1 text-xl font-semibold">Knowledge</h1>
          <p className="mb-3 text-sm text-slate-400">
            Everything {assistant} knows about the people, projects, places, topics and tools in your life, and
            how they're linked. Pick one to see what it's connected to and since when, then follow the links.
            Only you can see this.
          </p>
          <HowItWorks assistant={assistant} />
          {canEdit && <QuestionCards className="mb-4 max-w-xl" />}
          {canEdit && <ImportHistory assistant={assistant} />}
        </div>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
        {!graph ? (
          !error && <p className="text-sm text-slate-400">Loading…</p>
        ) : others === 0 ? (
          <p className="rounded-lg border border-edge bg-panel p-4 text-sm text-slate-400">
            Nothing here yet. As you chat, the people, projects, places, topics and tools you mention are noted
            with how they relate. Older memories and files are linked in the background within a few minutes.
          </p>
        ) : (
          <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
            <aside className={`min-w-0 ${picked ? "hidden lg:block" : ""}`}>
              <div className="mb-3 flex items-center gap-2 rounded-md border border-edge bg-panel px-3">
                <Search size={16} className="text-slate-500" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={`Search ${others} things`}
                  aria-label="Search what's known"
                  className="min-w-0 flex-1 bg-transparent py-2 outline-none"
                />
              </div>
              <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Filter by type">
                <TypeChip active={type === "all"} onClick={() => setType("all")} label={`All · ${others}`} />
                {(Object.keys(ENTITY_TYPES) as EntityType[])
                  .filter((t) => counts.get(t))
                  .map((t) => (
                    <TypeChip
                      key={t}
                      active={type === t}
                      onClick={() => setType(type === t ? "all" : t)}
                      label={`${ENTITY_TYPES[t].plural} · ${counts.get(t)}`}
                      dot={ENTITY_COLOR[t]}
                    />
                  ))}
              </div>
              <Select
                value={sort}
                onChange={(e) => setSort(e.target.value as KnowledgeSort)}
                aria-label="Sort"
                className="mb-3 w-full"
              >
                {(Object.keys(SORTS) as KnowledgeSort[]).map((s) => (
                  <option key={s} value={s}>
                    {SORTS[s]}
                  </option>
                ))}
              </Select>
              {list.length === 0 ? (
                <p className="text-sm text-slate-500">Nothing matches.</p>
              ) : (
                <ul className="space-y-1 lg:max-h-[calc(100dvh-16rem)] lg:overflow-y-auto lg:pr-1">
                  {list.map((e) => (
                    <li key={e.id}>
                      <button
                        onClick={() => pick(e.id)}
                        aria-current={e.id === focus}
                        className={`flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left ${e.id === focus ? "bg-edge" : "hover:bg-edge/50"}`}
                      >
                        <span
                          className="mt-1.5 size-2 shrink-0 rounded-full"
                          style={{ background: e.is_self ? "var(--color-slate-100)" : ENTITY_COLOR[e.type] }}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm text-slate-100">{e.is_self ? "You" : e.name}</span>
                          <span className="block truncate text-xs text-slate-500">
                            {e.is_self ? "What's known about you" : e.summary || ENTITY_TYPES[e.type].label}
                          </span>
                        </span>
                        {!e.is_self && <span className="mt-0.5 text-xs text-slate-500">{mentions(e)}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </aside>
            <section className={`min-w-0 ${picked ? "" : "hidden lg:block"}`}>
              {picked ? (
                <Detail
                  e={picked}
                  graph={graph}
                  names={names}
                  memories={memories}
                  chatTitle={chatTitle}
                  files={files}
                  canEdit={canEdit}
                  onPick={pick}
                  onRemove={() => remove(picked)}
                  onPreview={setPreview}
                />
              ) : (
                <div className="flex h-64 items-center justify-center rounded-lg border border-dashed border-edge text-sm text-slate-500">
                  Pick something on the left to see what it's linked to.
                </div>
              )}
            </section>
          </div>
        )}
      </div>
      {preview && <FilePreview file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

function TypeChip({
  active,
  onClick,
  label,
  dot,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  dot?: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${active ? "border-sky-500/60 bg-sky-500/15 text-sky-200" : "border-edge text-slate-400 hover:text-slate-200"}`}
    >
      {dot && <span className="size-1.5 rounded-full" style={{ background: dot }} />}
      {label}
    </button>
  );
}

function Detail({
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
}: {
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
      <button onClick={() => onPick(null)} className="flex items-center gap-1 text-sm text-slate-400 lg:hidden">
        <ArrowLeft size={15} /> Everything known
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
        </div>
        {canEdit && !e.is_self && (
          <button
            onClick={onRemove}
            className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-400 hover:text-rose-400"
            title="Remove it and its links; its memories stay"
            aria-label={`Remove ${e.name}`}
          >
            <Trash2 size={14} /> <span className="hidden sm:inline">Remove</span>
          </button>
        )}
      </div>

      {current.length + past.length > 0 ? (
        <EgoGraph e={e} graph={graph} names={names} onPick={onPick} />
      ) : (
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
                <Link to={`/memory?focus=${id}`} className="text-sky-300 hover:underline" title="Open on the Memory page">
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

function Section({
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

const short = (s: string, n = 18) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** The picked entity in the middle with what it's linked to around it. Dashed lines are links
 * that ended. Tap a neighbour to move to it. */
function EgoGraph({
  e,
  graph,
  names,
  onPick,
}: {
  e: GraphEntity;
  graph: KnowledgeGraph;
  names: Map<string, string>;
  onPick: (id: string) => void;
}) {
  const { nodes, more } = egoLayout(e.id, graph.relations);
  // A phone gets a narrower, taller drawing so names stay readable at its width.
  const narrow = window.matchMedia("(max-width: 639px)").matches;
  const [W, H, spread, chars] = narrow ? [400, 440, 0.85, 13] : [640, 380, 1, 18];
  const px = (n: { x: number }) => W / 2 + (n.x - 0.5) * W * spread;
  const byId = new Map(graph.entities.map((x) => [x.id, x]));
  const colour = (x: GraphEntity | undefined) => (!x || x.is_self ? "var(--color-slate-100)" : ENTITY_COLOR[x.type]);
  // Line labels only while there's room for them; otherwise they're in each line's tooltip.
  const labelled = nodes.length <= 8;
  const cx = W / 2;
  const cy = H / 2;
  return (
    <figure className="rounded-lg border border-edge bg-panel/60 p-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`What ${names.get(e.id)} is linked to`}>
        {nodes.map((n) => {
          const x = px(n);
          const y = n.y * H;
          return (
            <g key={`l${n.id}`}>
              <title>{n.label}</title>
              <line
                x1={cx}
                y1={cy}
                x2={x}
                y2={y}
                stroke={n.past ? "var(--color-slate-500)" : "var(--color-slate-400)"}
                strokeOpacity={n.past ? 0.6 : 0.8}
                strokeWidth={2}
                strokeDasharray={n.past ? "6 6" : undefined}
              />
              {labelled && (
                <text
                  x={(cx + x) / 2}
                  y={(cy + y) / 2 - 6}
                  textAnchor="middle"
                  fontSize={13}
                  className="fill-slate-400"
                  paintOrder="stroke"
                  stroke="var(--color-panel)"
                  strokeWidth={4}
                >
                  {short(n.label, 24)}
                </text>
              )}
            </g>
          );
        })}
        {nodes.map((n) => {
          const x = px(n);
          const y = n.y * H;
          const other = byId.get(n.id);
          const below = y >= cy;
          return (
            <g
              key={n.id}
              role="button"
              tabIndex={0}
              aria-label={`Open ${names.get(n.id)}`}
              onClick={() => onPick(n.id)}
              onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") && onPick(n.id)}
              className="cursor-pointer outline-none [&:focus-visible_circle]:stroke-sky-300 [&:hover_circle]:stroke-white"
            >
              {/* A wider, invisible target so it's easy to tap on a phone. */}
              <circle cx={x} cy={y} r={24} fill="transparent" strokeWidth={0} />
              <circle cx={x} cy={y} r={11} fill={colour(other)} fillOpacity={n.past ? 0.45 : 1} stroke="var(--color-panel)" strokeWidth={3} />
              <text
                x={x}
                y={below ? y + 30 : y - 19}
                textAnchor="middle"
                fontSize={15}
                className={n.past ? "fill-slate-500" : "fill-slate-200"}
                paintOrder="stroke"
                stroke="var(--color-panel)"
                strokeWidth={4}
              >
                {short(names.get(n.id) ?? "?", chars)}
              </text>
            </g>
          );
        })}
        <circle cx={cx} cy={cy} r={17} fill={colour(e)} stroke="var(--color-slate-100)" strokeOpacity={0.5} strokeWidth={3} />
        <text
          x={cx}
          y={cy + 38}
          textAnchor="middle"
          fontSize={17}
          fontWeight={600}
          className="fill-slate-100"
          paintOrder="stroke"
          stroke="var(--color-panel)"
          strokeWidth={5}
        >
          {short(names.get(e.id) ?? "", chars)}
        </text>
      </svg>
      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 pb-1 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1.5">
          <Share2 size={12} /> Tap one to move to it
        </span>
        <span className="inline-flex items-center gap-1.5">
          <svg width="22" height="4" aria-hidden>
            <line x1="0" y1="2" x2="22" y2="2" stroke="var(--color-slate-500)" strokeWidth="2" strokeDasharray="4 4" />
          </svg>
          No longer true
        </span>
        {more > 0 && <span>and {more} more in the list below</span>}
      </figcaption>
    </figure>
  );
}

function HowItWorks({ assistant }: { assistant: string }) {
  return (
    <Guide title="How Knowledge works">
      <div>
        <GuideHeading>Where it comes from</GuideHeading>
        <p>
          After each chat {assistant} notes who and what you talked about, and how they relate ("You work at
          UST", "Nexus uses Postgres"). Your memories and the files you upload are read the same way in the
          background. The same person or project mentioned twice is kept as one, with its other names.
        </p>
      </div>
      <div>
        <GuideHeading>How it's used</GuideHeading>
        <ul className="list-disc space-y-1 pl-4">
          <li>
            When you ask about something, {assistant} also looks up what it's linked to, so related facts
            reach the answer. The chips under a reply show what was recalled.
          </li>
          <li>
            When a fact stops being true it isn't deleted: it moves to <b>No longer true</b> with the dates it
            held.
          </li>
          <li>
            <b>Remove</b> deletes it and its links but keeps the memories about it. If a later chat mentions it
            again it comes back. Forgetting a memory is on the <Link to="/memory" className="text-sky-300 hover:underline">Memory</Link> page.
          </li>
        </ul>
      </div>
    </Guide>
  );
}
