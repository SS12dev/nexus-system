import { ArrowLeft, Brain, Orbit, Search, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router";
import { confirmAction } from "../components/Feedback";
import { FilePreview } from "../components/FilePreview";
import { Guide, GuideHeading } from "../components/Guide";
import { ImportHistory } from "../components/ImportHistory";
import { EntityDetail } from "../components/mind/EntityDetail";
import { KIND_INFO, KIND_STYLE, KINDS, MemoriesPanel } from "../components/mind/MemoriesPanel";
import { QuestionCards } from "../components/QuestionCards";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { ENTITY_COLOR, ENTITY_TYPES } from "../lib/entities";
import { formatDate } from "../lib/format";
import {
  areaCards,
  byKind,
  coreGroups,
  filterEntities,
  isCore,
  kindLabel,
  mentions,
  sureness,
} from "../lib/knowledge";
import { hasRole, type FileItem, type GraphEntity, type KnowledgeGraph, type Memory, type Universe, type UniverseStar } from "../lib/types";
import { useStore } from "../store/chat";

type Tab = "overview" | "memories";

/** Everything the assistant knows about you on one page: your core (who you are), the areas of
 * your life it has found, each thing in them, and the memories underneath. The universe on the
 * home screen draws the same data; this is where you read and correct it.
 * ?area=<id> opens an area, ?focus=<id> one thing, ?tab=memories the memories (?memory=<id>). */
export function MindPage() {
  const assistant = useAssistantName();
  const user = useStore((s) => s.user)!;
  const canEdit = hasRole(user, "member");
  const chats = useStore((s) => s.chats);
  const files = useStore((s) => s.files);
  const syncFiles = useStore((s) => s.syncFiles);
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null);
  const [universe, setUniverse] = useState<Universe | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState<FileItem | null>(null);
  const [params, setParams] = useSearchParams();
  const { hash } = useLocation();
  const tab: Tab = params.get("tab") === "memories" ? "memories" : "overview";
  const areaId = params.get("area");
  const focus = params.get("focus");

  const load = useCallback(
    () =>
      Promise.all([api.knowledgeGraph(), api.universe(), api.memories()])
        .then(([g, u, ms]) => {
          setGraph(g);
          setUniverse(u);
          setMemories(ms);
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
  // /mind#core scrolls to your core once it's drawn.
  useEffect(() => {
    if (hash && graph) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [hash, graph]);

  const names = useMemo(
    () => new Map(graph?.entities.map((e) => [e.id, e.is_self ? "You" : e.name]) ?? []),
    [graph],
  );
  const memoryText = useMemo(() => new Map(memories.map((m) => [m.id, m.content])), [memories]);
  const byId = useMemo(() => new Map(graph?.entities.map((e) => [e.id, e]) ?? []), [graph]);
  const starOf = useMemo(() => new Map(universe?.stars.map((s) => [s.id, s]) ?? []), [universe]);
  const areaOf = (id: string) => universe?.areas.find((a) => a.id === id) ?? null;

  const go = (next: Record<string, string>, push = true) => setParams(next, { replace: !push });
  const openThing = (id: string | null) => go(id ? { focus: id } : areaId ? { area: areaId } : {});

  const forget = async (e: GraphEntity | UniverseStar) => {
    const ok = await confirmAction({
      title: `Forget ${e.name}?`,
      body: "It and its links are removed. The memories about it stay; if a later chat mentions it again it comes back.",
      confirmLabel: "Forget",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteEntity(e.id);
      if (focus === e.id) go(areaId ? { area: areaId } : {}, false);
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const chatTitle = (id: string) => chats.find((c) => c.id === id)?.title || "Untitled chat";
  const picked = focus ? (byId.get(focus) ?? null) : null;
  const area = areaId ? areaOf(areaId) : null;

  let body: React.ReactNode;
  if (error && !graph) body = null;
  else if (!graph) body = <p className="text-sm text-slate-400">Loading…</p>;
  else if (tab === "memories") body = <MemoriesPanel />;
  else if (picked) {
    const star = starOf.get(picked.id);
    const home = star?.area ? areaOf(star.area) : null;
    body = (
      <EntityDetail
        e={picked}
        graph={graph}
        names={names}
        memories={memoryText}
        chatTitle={chatTitle}
        files={files}
        canEdit={canEdit}
        back={area ? area.name : "Your mind"}
        onPick={openThing}
        onRemove={() => forget(picked)}
        onPreview={setPreview}
        extra={
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            {home && (
              <button
                onClick={() => go({ area: home.id })}
                className="inline-flex items-center gap-1.5 rounded-full border border-edge px-2 py-0.5 text-slate-300 hover:bg-edge/60"
              >
                <span className="size-1.5 rounded-full" style={{ background: home.color }} /> {home.name}
              </button>
            )}
            {isCore(picked) && <span className="rounded-full border border-edge px-2 py-0.5 text-slate-300">Part of your core</span>}
            {star?.pos && (
              <Link to={`/?focus=${picked.id}`} className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200">
                <Orbit size={12} /> Show in universe
              </Link>
            )}
          </div>
        }
      />
    );
  } else if (area && universe) {
    const stars = universe.stars.filter((s) => s.area === area.id);
    body = (
      <div className="space-y-5">
        <button onClick={() => go({})} className="flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
          <ArrowLeft size={15} /> Your mind
        </button>
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-100">
            <span className="size-2.5 rounded-full" style={{ background: area.color }} /> {area.name}
          </h2>
          {area.summary && <p className="mt-0.5 text-sm text-slate-400">{area.summary}</p>}
          <p className="mt-1 text-xs text-slate-500">
            {stars.length} thing{stars.length === 1 ? "" : "s"} · found {formatDate(area.born_at)}
            {area.parent && areaOf(area.parent) && <> · split from {areaOf(area.parent)!.name}</>}
            {area.ended_at && " · merged away; it fades from the universe in a day or two"}
          </p>
        </div>
        {byKind(stars).map(([kind, list]) => (
          <section key={kind}>
            <h3 className="mb-1.5 text-sm font-medium text-slate-300">
              {kindLabel(kind)} <span className="text-slate-500">· {list.length}</span>
            </h3>
            <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel">
              {list.map((s) => (
                <ThingRow
                  key={s.id}
                  name={s.name}
                  why={whyText(s, byId.get(s.id))}
                  color={area.color}
                  onOpen={() => openThing(s.id)}
                  universe={s.pos ? `/?focus=${s.id}` : null}
                  onForget={canEdit ? () => forget(s) : undefined}
                />
              ))}
            </ul>
          </section>
        ))}
      </div>
    );
  } else {
    body = (
      <Overview
        graph={graph}
        universe={universe}
        memories={memories}
        query={query}
        canEdit={canEdit}
        assistant={assistant}
        onOpenThing={openThing}
        onOpenArea={(id) => go({ area: id })}
        onForget={forget}
      />
    );
  }

  const tabs: [Tab, string][] = [
    ["overview", "Overview"],
    ["memories", `Memories${memories.length ? ` · ${memories.length}` : ""}`],
  ];

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-6">
        <div className="mb-1 flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">Mind</h1>
          <Link
            to="/"
            className="ml-auto flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-300 hover:bg-edge/60"
            title="The same, drawn as your universe"
          >
            <Orbit size={14} /> Universe
          </Link>
        </div>
        <p className="mb-3 text-sm text-slate-400">
          What {assistant} knows about you: who you are at your core, the areas of your life, everything in them and
          the memories underneath. Correct or forget anything. Only you can see this.
        </p>
        <HowItWorks assistant={assistant} />
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <div role="tablist" aria-label="Mind" className="flex rounded-md border border-edge p-0.5 text-sm">
            {tabs.map(([t, label]) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                onClick={() => go(t === "memories" ? { tab: t } : {})}
                className={`rounded px-3 py-1 ${tab === t ? "bg-edge text-slate-100" : "text-slate-400 hover:text-slate-200"}`}
              >
                {label}
              </button>
            ))}
          </div>
          {tab === "overview" && (
            <div className="flex min-w-48 flex-1 items-center gap-2 rounded-md border border-edge bg-panel px-3">
              <Search size={16} className="text-slate-500" />
              <input
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  if (e.target.value && (focus || areaId)) go({}, false);
                }}
                placeholder="Search everything known"
                aria-label="Search everything known"
                className="min-w-0 flex-1 bg-transparent py-2 outline-none"
              />
            </div>
          )}
        </div>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
        {body}
      </div>
      {preview && <FilePreview file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

/** Why it's known: its summary, else how often it came up and when. */
function whyText(s: UniverseStar, e: GraphEntity | undefined): string {
  const n = e ? mentions(e) : s.mentions;
  const seen = s.seen_at ? `, last ${formatDate(s.seen_at)}` : "";
  const count = `Came up ${n} time${n === 1 ? "" : "s"}${seen}`;
  return s.summary ? `${s.summary} · ${count}` : count;
}

function ThingRow({
  name,
  why,
  color,
  badge,
  onOpen,
  universe,
  onForget,
}: {
  name: string;
  why: string;
  color: string;
  badge?: React.ReactNode;
  onOpen: () => void;
  universe?: string | null;
  onForget?: () => void;
}) {
  return (
    <li className="flex items-start gap-3 px-3 py-2">
      <span className="mt-1.5 size-2 shrink-0 rounded-full" style={{ background: color }} />
      <button onClick={onOpen} className="min-w-0 flex-1 text-left">
        <span className="flex items-center gap-2 text-sm text-slate-100">
          <span className="truncate">{name}</span>
          {badge}
        </span>
        <span className="block text-xs text-slate-500">{why}</span>
      </button>
      {universe && (
        <Link
          to={universe}
          className="p-1 text-slate-500 hover:text-sky-300"
          title="Show in universe"
          aria-label={`Show ${name} in the universe`}
        >
          <Orbit size={14} />
        </Link>
      )}
      {onForget && (
        <button onClick={onForget} className="p-1 text-slate-500 hover:text-rose-400" title="Forget" aria-label={`Forget ${name}`}>
          <Trash2 size={14} />
        </button>
      )}
    </li>
  );
}

function Sureness({ e }: { e: GraphEntity }) {
  const s = sureness(e);
  return (
    <span className="inline-flex items-center gap-0.5" title={`${s.label}: came up ${mentions(e)} time${mentions(e) === 1 ? "" : "s"}`}>
      {[1, 2, 3].map((i) => (
        <span key={i} className={`h-1.5 w-1.5 rounded-full ${i <= s.level ? "bg-sky-400" : "bg-edge"}`} />
      ))}
    </span>
  );
}

function Overview({
  graph,
  universe,
  memories,
  query,
  canEdit,
  assistant,
  onOpenThing,
  onOpenArea,
  onForget,
}: {
  graph: KnowledgeGraph;
  universe: Universe | null;
  memories: Memory[];
  query: string;
  canEdit: boolean;
  assistant: string;
  onOpenThing: (id: string) => void;
  onOpenArea: (id: string) => void;
  onForget: (e: GraphEntity) => void;
}) {
  const core = useMemo(() => coreGroups(graph.entities), [graph]);
  const cards = useMemo(() => areaCards(universe), [universe]);
  const areaColor = useMemo(() => {
    const colour = new Map(universe?.areas.map((a) => [a.id, a.color]) ?? []);
    return new Map(universe?.stars.filter((s) => s.area).map((s) => [s.id, colour.get(s.area!)!]) ?? []);
  }, [universe]);
  const loose = useMemo(
    () =>
      filterEntities(graph.entities, { query: "", type: "all", sort: "mentions" }).filter(
        (e) => !e.is_self && !isCore(e) && !areaColor.has(e.id),
      ),
    [graph, areaColor],
  );
  const [allLoose, setAllLoose] = useState(false);
  const colourOf = (e: GraphEntity) => areaColor.get(e.id) ?? ENTITY_COLOR[e.type] ?? ENTITY_COLOR.other;

  if (query.trim()) {
    const q = query.trim().toLowerCase();
    const things = filterEntities(graph.entities, { query, type: "all", sort: "mentions" }).filter((e) => !e.is_self);
    const mems = memories.filter((m) => m.content.toLowerCase().includes(q));
    const areas = cards.filter((c) => c.area.name.toLowerCase().includes(q) || c.area.summary.toLowerCase().includes(q));
    if (!things.length && !mems.length && !areas.length) return <p className="text-sm text-slate-500">Nothing matches.</p>;
    return (
      <div className="space-y-5">
        {areas.length > 0 && (
          <section>
            <h3 className="mb-1.5 text-sm font-medium text-slate-300">Areas</h3>
            <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel">
              {areas.map((c) => (
                <ThingRow key={c.area.id} name={c.area.name} why={c.area.summary || `${c.count} things`} color={c.area.color} onOpen={() => onOpenArea(c.area.id)} />
              ))}
            </ul>
          </section>
        )}
        {things.length > 0 && (
          <section>
            <h3 className="mb-1.5 text-sm font-medium text-slate-300">
              Things <span className="text-slate-500">· {things.length}</span>
            </h3>
            <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel">
              {things.slice(0, 50).map((e) => (
                <ThingRow
                  key={e.id}
                  name={e.name}
                  why={e.summary || (e.kind ? kindLabel(e.kind, false) : ENTITY_TYPES[e.type].label)}
                  color={colourOf(e)}
                  onOpen={() => onOpenThing(e.id)}
                />
              ))}
            </ul>
          </section>
        )}
        {mems.length > 0 && (
          <section>
            <h3 className="mb-1.5 text-sm font-medium text-slate-300">
              Memories <span className="text-slate-500">· {mems.length}</span>
            </h3>
            <ul className="space-y-1 text-sm">
              {mems.slice(0, 30).map((m) => (
                <li key={m.id}>
                  <Link to={`/mind?tab=memories&memory=${m.id}`} className="text-sky-300 hover:underline">
                    {m.content}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    );
  }

  const empty = !core.length && !cards.length && !loose.length;
  return (
    <div className="space-y-8">
      {canEdit && <QuestionCards className="max-w-xl" />}
      {empty && (
        <p className="rounded-lg border border-edge bg-panel p-4 text-sm text-slate-400">
          Nothing here yet. As you chat, the people, projects, places and topics you mention are noted with how
          they relate, and who you are takes shape at your core. Older memories and files are read in the
          background within a few minutes.
        </p>
      )}

      <section id="core" className="scroll-mt-4">
        <h2 className="mb-1 flex items-center gap-2 font-medium">
          <Brain size={17} className="text-sky-300" /> Your core
        </h2>
        <p className="mb-3 text-xs text-slate-500">
          Who you are, from what you've said: the bright network at the centre of your universe. The dots show how
          sure {assistant} is. It is never named in the universe itself.
        </p>
        {core.length ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {core.map((g) => (
              <div key={g.id} className="rounded-lg border border-edge bg-panel p-3">
                <h3 className="text-sm font-medium text-slate-200">{g.label}</h3>
                <p className="mb-2 text-xs text-slate-500">{g.help}</p>
                <ul className="space-y-1">
                  {g.items.map((e) => (
                    <li key={e.id} className="group flex items-center gap-2 text-sm">
                      <button onClick={() => onOpenThing(e.id)} className="min-w-0 flex-1 truncate text-left text-slate-200 hover:text-sky-200">
                        {e.name}
                      </button>
                      <Sureness e={e} />
                      {canEdit && (
                        <button
                          onClick={() => onForget(e)}
                          className="p-0.5 text-slate-600 opacity-60 hover:text-rose-400 group-hover:opacity-100"
                          title="Forget"
                          aria-label={`Forget ${e.name}`}
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-500">Nothing about who you are yet. It fills in as you chat.</p>
        )}
      </section>

      <section>
        <h2 className="mb-1 flex items-center gap-2 font-medium">
          <Sparkles size={17} className="text-sky-300" /> Your areas
        </h2>
        <p className="mb-3 text-xs text-slate-500">
          The parts of your life {assistant} has found, each a galaxy in your universe. They're worked out each
          night from what goes together, so a new one can appear, split in two or merge with another.
        </p>
        {cards.length ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {cards.map((c) => (
              <button
                key={c.area.id}
                onClick={() => onOpenArea(c.area.id)}
                className="rounded-lg border border-edge bg-panel p-3 text-left hover:border-slate-500"
              >
                <span className="flex items-center gap-2">
                  <span className="size-2.5 rounded-full" style={{ background: c.area.color }} />
                  <span className="min-w-0 flex-1 truncate font-medium text-slate-100">{c.area.name}</span>
                  {c.isNew && <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-xs text-emerald-300">New this week</span>}
                  {c.fading && !c.isNew && <span className="rounded bg-slate-500/20 px-1.5 py-0.5 text-xs text-slate-400">Quiet lately</span>}
                </span>
                {c.area.summary && <span className="mt-1 block text-xs text-slate-400">{c.area.summary}</span>}
                <span className="mt-2 block truncate text-xs text-slate-500">
                  {c.count} thing{c.count === 1 ? "" : "s"}
                  {c.top.length > 0 && <> · {c.top.map((s) => s.name).join(", ")}</>}
                </span>
              </button>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-500">
            No areas yet. They're found overnight once enough is known; an admin can run it now from Admin › Graph.
          </p>
        )}
      </section>

      {loose.length > 0 && (
        <section>
          <h2 className="mb-1 font-medium">Not in an area yet</h2>
          <p className="mb-3 text-xs text-slate-500">
            Things mentioned too rarely, or not linked enough to anything, to belong to an area. They drift between
            the galaxies.
          </p>
          <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel">
            {(allLoose ? loose : loose.slice(0, 12)).map((e) => (
              <ThingRow
                key={e.id}
                name={e.name}
                why={e.summary || (e.kind ? kindLabel(e.kind, false) : ENTITY_TYPES[e.type].label)}
                color={colourOf(e)}
                onOpen={() => onOpenThing(e.id)}
                onForget={canEdit ? () => onForget(e) : undefined}
              />
            ))}
          </ul>
          {loose.length > 12 && (
            <button onClick={() => setAllLoose(!allLoose)} className="mt-2 text-sm text-sky-300 hover:text-sky-200">
              {allLoose ? "Show fewer" : `Show all ${loose.length}`}
            </button>
          )}
        </section>
      )}

      {canEdit && <ImportHistory assistant={assistant} />}
    </div>
  );
}

function HowItWorks({ assistant }: { assistant: string }) {
  return (
    <Guide title="How your mind works">
      <div>
        <GuideHeading>Where it comes from</GuideHeading>
        <p>
          After each chat {assistant} keeps anything lasting you said and notes who and what you talked about and
          how they relate ("You work at UST", "Nexus uses Postgres"). Your files and imported chat history are read
          the same way in the background. Each night it works out the areas of your life from what goes together.
        </p>
      </div>
      <div>
        <GuideHeading>What's on this page</GuideHeading>
        <ul className="list-disc space-y-1 pl-4">
          <li>
            <b>Your core</b> is who you are: values, goals, traits, interests, habits and how you like to talk.
          </li>
          <li>
            <b>Your areas</b> are the parts of your life. Open one to see everything in it, or any thing to see what
            it's linked to and since when. Links that stopped being true are kept with their dates.
          </li>
          <li>
            <b>Memories</b> are the sentences underneath, with the profile {assistant} reads at the start of every
            chat. Each has a type:{" "}
            {KINDS.map((k) => (
              <span key={k} className={`mr-1 rounded px-1.5 py-0.5 text-xs ${KIND_STYLE[k]}`} title={KIND_INFO[k].help}>
                {KIND_INFO[k].label}
              </span>
            ))}
            . Pin one to keep it in your profile and stop it fading.
          </li>
          <li>
            <b>Forget</b> on a thing removes it and its links but keeps its memories; if a later chat mentions it
            again it comes back. Forgetting a memory is on the Memories tab.
          </li>
        </ul>
      </div>
    </Guide>
  );
}

/** The old Memory and Knowledge pages are the Mind page now; their links keep working. */
export function MindRedirect({ from }: { from: "memory" | "knowledge" }) {
  const [params] = useSearchParams();
  const focus = params.get("focus");
  const to =
    from === "memory"
      ? focus
        ? `/mind?tab=memories&memory=${focus}`
        : params.get("view") === "connections"
          ? "/mind"
          : "/mind?tab=memories"
      : focus
        ? `/mind?focus=${focus}`
        : "/mind";
  return <Navigate to={to} replace />;
}
