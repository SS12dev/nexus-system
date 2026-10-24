import {
  Bot,
  Box,
  Brain,
  CalendarClock,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FolderGit2,
  List,
  Maximize2,
  Minimize2,
  Orbit,
  Share2,
  X,
} from "lucide-react";
import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api } from "../lib/api";
import { AGENT_LABEL, toolLabel } from "../lib/tools";
import { ENTITY_COLOR, RECALL_COLOR } from "../lib/entities";
import { formatInZone } from "../lib/schedule";
import type { KnowledgeGraph, Memory, Project, Recalled, Routine, Run } from "../lib/types";
import {
  buildClusters,
  inside as layoutInside,
  isArtifact,
  KIND_COLOR,
  liveSystems,
  NODE_COLOR,
  satellites,
  SATELLITE_COLOR,
  type OrbCluster,
} from "../orb/model";
import { StarLabels } from "../orb/StarLabels";
import type { Selection } from "../orb/OrbScene";
import { hasWebGL, usePerf } from "../orb/perf";
import { useStore } from "../store/chat";
import { Orb } from "./Orb";
import { RUN_LABEL, RUN_STYLE } from "./RunControls";
import { useAssistantName } from "../lib/assistant";
import { ENTITY_TYPES, relationText } from "./Connections";

const OrbScene = lazy(() => import("../orb/OrbScene"));

const VIEW_KEY = "nexus.home.view";
/** How long what a reply recalled stays lit in the orb. */
const LIT_MS = 15000;

function savedView(): "orb" | "list" {
  if (!hasWebGL()) return "list";
  try {
    return localStorage.getItem(VIEW_KEY) === "list" ? "list" : "orb";
  } catch {
    return "orb";
  }
}

/** Nexus as a 3D orb with the live agent network around it and, inside it, what it knows:
 * you at the centre, the people, projects and topics you've mentioned around you, memories as
 * their satellites and the newest artifacts by their project. Just outside the shell, on a
 * ring, what it does for you: routines and software projects. Or the same as plain lists. Fills the home screen, and stays beside a chat (above it on
 * phones) so you can watch Nexus and its teams work while you talk. */
export function OrbStage({
  mode,
  collapsed = false,
  onCollapse,
  className = "",
}: {
  mode: "home" | "chat";
  /** Chat mode only: folded down to a slim bar. */
  collapsed?: boolean;
  onCollapse?: (collapsed: boolean) => void;
  className?: string;
}) {
  const assistant = useAssistantName();
  const user = useStore((s) => s.user)!;
  const state = useStore((s) => s.assistantState);
  const activity = useStore((s) => s.activity);
  const connected = useStore((s) => s.connected);
  const systems = useStore((s) => s.systems);
  const approvals = useStore((s) => s.approvals);
  const files = useStore((s) => s.files);
  const memoryVersion = useStore((s) => s.memoryVersion);
  const perf = usePerf();
  const [view, setView] = useState(savedView);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null);
  const [routines, setRoutines] = useState<Routine[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const routinesVersion = useStore((s) => s.routinesVersion);
  const recall = useStore((s) => s.recall);
  const [lit, setLit] = useState<Recalled | null>(null);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [webglFailed, setWebglFailed] = useState(false);
  const tags = useRef(new Map<string, HTMLElement>());

  // Fresh lists on open and after a reconnect, in case events were missed meanwhile.
  useEffect(() => {
    const { syncLiveSystems, syncFiles, upsertApprovals } = useStore.getState();
    api.systems().then(syncLiveSystems).catch(() => {});
    api.files().then(syncFiles).catch(() => {});
    api.approvals().then(upsertApprovals).catch(() => {});
  }, [connected]);

  // Routines and projects ride the ring outside the orb.
  useEffect(() => {
    api.routines().then(setRoutines).catch(() => {});
  }, [routinesVersion, connected]);
  useEffect(() => {
    api.projects().then(setProjects).catch(() => {});
  }, [connected, Object.keys(files).length]);

  // Memories change in bursts after a chat; wait for the burst to settle.
  useEffect(() => {
    const t = window.setTimeout(() => {
      api.memories().then(setMemories).catch(() => {});
      api.knowledgeGraph().then(setGraph).catch(() => {});
    }, 300);
    return () => window.clearTimeout(t);
  }, [memoryVersion, connected]);

  // Light up what a reply recalled for a while, then let it fade back.
  useEffect(() => {
    if (!recall) return;
    setLit(recall);
    const t = window.setTimeout(() => setLit(null), LIT_MS);
    return () => window.clearTimeout(t);
  }, [recall]);
  const litIds = useMemo(
    () => new Set(lit ? [...lit.memories, ...lit.entities.map((e) => e.id)] : []),
    [lit],
  );

  const live = useMemo(() => liveSystems(systems), [systems]);
  const clusters = useMemo(
    () => buildClusters(live, Object.values(approvals), perf.portrait),
    [live, approvals, perf.portrait],
  );
  const artifactList = useMemo(() => Object.values(files).filter(isArtifact), [files]);
  const insideData = useMemo(
    () =>
      layoutInside(
        graph,
        memories,
        artifactList,
        { stars: perf.maxStars, memories: perf.maxMemories, crystals: perf.maxCrystals },
        litIds,
      ),
    [graph, memories, artifactList, perf.maxStars, perf.maxMemories, perf.maxCrystals, litIds],
  );
  const known = graph?.entities.filter((e) => !e.is_self).length ?? 0;
  const sats = useMemo(
    () => satellites(routines, projects, perf.lite ? 12 : 24),
    [routines, projects, perf.lite],
  );

  // Drop a selection whose object went away.
  useEffect(() => {
    if (selected?.type === "agent" && !clusters.some((c) => c.id === selected.node.systemId))
      setSelected(null);
  }, [clusters, selected]);

  const changeView = (v: "orb" | "list") => {
    setView(v);
    setSelected(null);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* private mode */
    }
  };

  const chat = mode === "chat";
  const showOrb = view === "orb" && !webglFailed;
  const working = clusters.filter((c) => ["queued", "running", "awaiting_approval"].includes(c.status)).length;
  const agentCount = clusters.reduce((n, c) => n + c.nodes.length, 0);
  const status =
    state !== "idle"
      ? (activity ?? (state === "thinking" ? "Thinking…" : "Answering…"))
      : lit
        ? remembered(lit)
        : summary(clusters.length, agentCount, working, memories.length, known, artifactList.length, routines.length);

  if (chat && collapsed) {
    return (
      <div
        className={`flex shrink-0 items-center gap-3 border-b border-edge bg-panel/60 px-3 py-1.5 lg:w-14 lg:flex-col lg:border-b-0 lg:border-r lg:px-0 lg:py-3 ${className}`}
      >
        <Orb state={state} size={22} />
        <p className="min-w-0 flex-1 truncate text-xs text-slate-400 lg:hidden" aria-live="polite">
          {status}
        </p>
        {working > 0 && (
          <span className="shrink-0 rounded-full bg-sky-500/20 px-1.5 text-xs text-sky-300" title="Teams working">
            {working}
          </span>
        )}
        <button
          onClick={() => onCollapse?.(false)}
          className="shrink-0 rounded-md p-1 text-slate-400 hover:bg-edge hover:text-white"
          aria-label={`Show ${assistant}`}
          title={`Show ${assistant}`}
        >
          <ChevronDown size={16} className="lg:hidden" />
          <Maximize2 size={15} className="hidden lg:block" />
        </button>
      </div>
    );
  }

  return (
    <div
      // The 3D scene is a night sky in both themes, so what floats over it stays dark.
      data-theme={showOrb ? "dark" : undefined}
      className={`relative flex min-h-0 flex-col overflow-hidden ${className}`}
    >
      {showOrb && (
        <div className="absolute inset-0">
          <SceneBoundary onError={() => setWebglFailed(true)}>
            <Suspense
              fallback={
                <div className="grid h-full place-items-center">
                  <Orb state={state} size={chat ? 90 : 140} />
                </div>
              }
            >
              <OrbScene
                assistantState={state}
                activity={state !== "idle" ? activity : null}
                clusters={clusters}
                inside={insideData}
                sats={sats}
                lit={litIds}
                litSeq={recall?.seq ?? 0}
                labels={chat ? 3 : perf.lite ? 5 : 8}
                tags={tags.current}
                perf={perf}
                selected={selected}
                onSelect={setSelected}
              />
            </Suspense>
          </SceneBoundary>
          <StarLabels stars={insideData.stars} lit={litIds} tags={tags.current} />
        </div>
      )}

      <div className={`pointer-events-none relative z-10 flex items-start gap-3 ${chat ? "px-3 pt-2" : "px-4 pt-4"}`}>
        {/* A soft backdrop keeps the greeting and status readable over the bright shell. */}
        <div className={`min-w-0 max-w-full rounded-lg bg-ink/60 backdrop-blur-sm ${chat ? "px-2 py-0.5" : "px-3 py-1.5"}`}>
          {!chat && (
            <p className="text-lg text-slate-200">
              Hi{user.name ? ` ${user.name.split(" ")[0]}` : ""}, what can I do for you?
            </p>
          )}
          <p className={`mt-0.5 truncate text-slate-300 ${chat ? "text-xs" : "text-sm"}`} aria-live="polite">
            {status}
          </p>
        </div>
        <div className="pointer-events-auto ml-auto flex shrink-0 items-center gap-1">
          <div className="flex rounded-lg border border-edge bg-panel/80 p-0.5 text-sm backdrop-blur">
            <button
              onClick={() => changeView("orb")}
              disabled={!hasWebGL()}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 disabled:opacity-40 ${showOrb ? "bg-edge text-white" : "text-slate-400"}`}
              aria-pressed={showOrb}
            >
              <Orbit size={14} /> {!chat && "Orb"}
            </button>
            <button
              onClick={() => changeView("list")}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 ${!showOrb ? "bg-edge text-white" : "text-slate-400"}`}
              aria-pressed={!showOrb}
            >
              <List size={14} /> {!chat && "List"}
            </button>
          </div>
          {chat && (
            <button
              onClick={() => onCollapse?.(true)}
              className="rounded-lg border border-edge bg-panel/80 p-1.5 text-slate-400 backdrop-blur hover:text-white"
              aria-label={`Hide ${assistant}`}
              title={`Hide ${assistant}`}
            >
              <ChevronUp size={15} className="lg:hidden" />
              <Minimize2 size={14} className="hidden lg:block" />
            </button>
          )}
        </div>
      </div>

      {showOrb ? (
        <div className="pointer-events-none relative z-10 flex flex-1 flex-col justify-end px-4 pb-2">
          {!chat && <Legend />}
        </div>
      ) : (
        <ListView clusters={clusters} memories={memories} known={known} artifacts={artifactList} />
      )}

      {selected && (
        <div className={chat ? "absolute inset-x-2 bottom-2 z-20" : "relative z-10 px-3 pb-2"}>
          <SelectionCard
            selection={selected}
            clusters={clusters}
            graph={graph}
            routines={routines}
            projects={projects}
            onClose={() => setSelected(null)}
          />
        </div>
      )}
    </div>
  );
}

function summary(
  teams: number,
  agents: number,
  working: number,
  memories: number,
  known: number,
  artifacts: number,
  routines = 0,
) {
  const parts = [
    teams ? `${teams} team${teams > 1 ? "s" : ""} (${agents} agents${working ? `, ${working} working` : ""})` : "No teams right now",
    ...(routines ? [`${routines} routine${routines === 1 ? "" : "s"}`] : []),
    `${memories} memor${memories === 1 ? "y" : "ies"}`,
    ...(known ? [`${known} connection${known === 1 ? "" : "s"}`] : []),
    `${artifacts} artifact${artifacts === 1 ? "" : "s"}`,
  ];
  return parts.join(" · ");
}

/** The status line while a recall is lit: what the reply drew on, in plain words. */
export function remembered(r: Recalled): string {
  const names = r.entities.slice(0, 3).map((e) => e.name);
  if (r.entities.length > 3) names.push(`${r.entities.length - 3} more`);
  if (r.memories.length) names.push(`${r.memories.length} memor${r.memories.length === 1 ? "y" : "ies"}`);
  if (names.length === 0) return "Remembered nothing for this reply";
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `Remembered ${list}`;
}

function Legend() {
  const dot = (c: string) => <span className="inline-block size-2 shrink-0 rounded-full" style={{ background: c }} />;
  const item = "flex items-center gap-1.5 whitespace-nowrap";
  const group = "text-[10px] font-semibold uppercase tracking-wider text-slate-500";
  return (
    <div className="mx-auto hidden max-w-full flex-col gap-1 rounded-2xl border border-edge bg-ink/75 px-4 py-2 text-xs text-slate-300 backdrop-blur sm:flex">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className={group}>Outside</span>
        <span className={item}>{dot(NODE_COLOR.running)} team working</span>
        <span className={item}>{dot(NODE_COLOR.working)} using a tool</span>
        <span className={item}>{dot(NODE_COLOR.waiting)} needs approval</span>
        <span className={item}>
          <span className="inline-block size-2 rounded-full border-2" style={{ borderColor: SATELLITE_COLOR.routine }} /> routines
        </span>
        <span className={item}>
          <span className="inline-block size-2" style={{ background: SATELLITE_COLOR.project }} /> projects
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className={group}>Inside</span>
        <span className={item}>{dot("#f8fafc")} you</span>
        <span className={item}>{dot(ENTITY_COLOR.project)} people, projects and topics</span>
        <span className={item}>
          <span className="inline-block h-px w-3 bg-slate-300" /> how they relate
        </span>
        <span className={item}>{dot(KIND_COLOR.preference)} memories</span>
        <span className={item}>
          <span className="inline-block size-2 rotate-45 bg-purple-400" /> artifacts
        </span>
        <span className={item}>{dot(RECALL_COLOR)} just remembered</span>
      </div>
    </div>
  );
}

function SelectionCard({
  selection,
  clusters,
  graph,
  routines,
  projects,
  onClose,
}: {
  selection: Selection;
  clusters: OrbCluster[];
  graph: KnowledgeGraph | null;
  routines: Routine[];
  projects: Project[];
  onClose: () => void;
}) {
  let body: ReactNode;
  if (selection.type === "entity") {
    const st = selection.star;
    const e = graph?.entities.find((x) => x.id === st.id);
    const names = new Map(graph?.entities.map((x) => [x.id, x.is_self ? "You" : x.name]) ?? []);
    const facts = (graph?.relations ?? []).filter((r) => !r.valid_to && (r.src === st.id || r.dst === st.id));
    const t = ENTITY_TYPES[st.type] ?? ENTITY_TYPES.other;
    body = (
      <>
        <div className="flex items-center gap-2 text-xs" style={{ color: st.self ? "#f8fafc" : ENTITY_COLOR[st.type] }}>
          <Share2 size={14} /> {st.self ? "You" : t.label}
        </div>
        <div className="mt-1 font-medium">{st.name}</div>
        {st.summary && <p className="mt-0.5 text-sm text-slate-400">{st.summary}</p>}
        {facts.length > 0 && (
          <ul className="mt-1.5 space-y-0.5 text-sm text-slate-300">
            {facts.slice(0, 3).map((r) => (
              <li key={r.id}>{relationText(r, names)}</li>
            ))}
            {facts.length > 3 && <li className="text-xs text-slate-500">and {facts.length - 3} more</li>}
          </ul>
        )}
        {e && !st.self && (
          <p className="mt-1.5 text-xs text-slate-500">
            In {e.memories} memor{e.memories === 1 ? "y" : "ies"}, {e.chats + e.past_chats} chat
            {e.chats + e.past_chats === 1 ? "" : "s"} and {e.files} file{e.files === 1 ? "" : "s"}
          </p>
        )}
        <Link
          to="/memory?view=connections"
          className="mt-2 inline-flex items-center gap-1 text-sm text-sky-300 hover:text-sky-200"
        >
          Open in Connections <ExternalLink size={13} />
        </Link>
      </>
    );
  } else if (selection.type === "agent") {
    const n = selection.node;
    const team = clusters.find((c) => c.id === n.systemId);
    body = (
      <>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <Bot size={14} /> {team?.name ?? "Team"}
          {team && team.status in RUN_LABEL && (
            <span className={RUN_STYLE[team.status as Run["status"]]}>· {RUN_LABEL[team.status as Run["status"]]}</span>
          )}
        </div>
        <div className="mt-1 font-medium">
          {n.name} <span className="text-sm font-normal text-slate-400">({n.role}, {n.tier})</span>
        </div>
        <div className="mt-0.5 text-sm" style={{ color: NODE_COLOR[n.status] }}>
          {n.tool && ["working", "waiting"].includes(n.status)
            ? `${AGENT_LABEL[n.status]} · ${toolLabel(n.tool)}`
            : (AGENT_LABEL[n.status] ?? n.status)}
        </div>
        <Link to={`/agents/${n.systemId}`} className="mt-2 inline-flex items-center gap-1 text-sm text-sky-300 hover:text-sky-200">
          Open team <ExternalLink size={13} />
        </Link>
      </>
    );
  } else if (selection.type === "memory") {
    const m = selection.point;
    body = (
      <>
        <div className="flex items-center gap-2 text-xs" style={{ color: KIND_COLOR[m.kind] }}>
          <Brain size={14} /> {m.kind}
          {m.pinned && <span className="text-slate-400">· pinned</span>}
        </div>
        <p className="mt-1 text-sm text-slate-200">{m.content}</p>
        <Link to={`/memory?focus=${m.id}`} className="mt-2 inline-flex items-center gap-1 text-sm text-sky-300 hover:text-sky-200">
          Open in Memory <ExternalLink size={13} />
        </Link>
      </>
    );
  } else if (selection.type === "satellite") {
    const sat = selection.sat;
    const r = sat.kind === "routine" ? routines.find((x) => x.id === sat.id) : undefined;
    const p = sat.kind === "project" ? projects.find((x) => x.id === sat.id) : undefined;
    body = (
      <>
        <div className="flex items-center gap-2 text-xs" style={{ color: sat.color }}>
          {sat.kind === "routine" ? <CalendarClock size={14} /> : <FolderGit2 size={14} />}
          {sat.kind === "routine" ? (r?.enabled === false ? "Routine · paused" : "Routine") : "Project"}
        </div>
        <div className="mt-1 font-medium">{sat.name}</div>
        <p className="mt-0.5 text-sm text-slate-400">
          {sat.detail}
          {r?.next_run_at && ` · next ${formatInZone(r.next_run_at, r.timezone)}`}
          {p?.app_state === "running" && " · live"}
        </p>
        <div className="mt-2 flex gap-4 text-sm">
          {r?.chat_id && (
            <Link to={`/c/${r.chat_id}`} className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200">
              Open chat <ExternalLink size={13} />
            </Link>
          )}
          {r && (
            <Link to="/routines" className="text-slate-300 hover:text-white">
              Routines
            </Link>
          )}
          {p?.live_url && (
            <a href={p.live_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200">
              Open app <ExternalLink size={13} />
            </a>
          )}
          {p && (
            <Link to="/library" className="text-slate-300 hover:text-white">
              Library
            </Link>
          )}
        </div>
      </>
    );
  } else {
    const c = selection.crystal;
    body = (
      <>
        <div className="flex items-center gap-2 text-xs text-purple-300">
          <Box size={14} /> Artifact
        </div>
        <p className="mt-1 truncate text-sm font-medium text-slate-200">{c.name}</p>
        <div className="mt-2 flex gap-4 text-sm">
          <a href={api.fileUrl(c.id, true)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200">
            Open <ExternalLink size={13} />
          </a>
          <a href={api.fileUrl(c.id)} className="text-slate-300 hover:text-white">
            Download
          </a>
          <Link to="/library" className="text-slate-300 hover:text-white">
            Library
          </Link>
        </div>
      </>
    );
  }
  return (
    <div className="relative mx-auto max-w-md rounded-xl border border-edge bg-panel/90 p-3 pr-9 shadow-xl backdrop-blur">
      {body}
      <button onClick={onClose} className="absolute right-2 top-2 p-1 text-slate-400 hover:text-white" aria-label="Close">
        <X size={16} />
      </button>
    </div>
  );
}

/** The 2D alternative: the same teams, memories and artifacts as plain lists. Also the
 * fallback when the device can't draw WebGL. */
function ListView({
  clusters,
  memories,
  known,
  artifacts,
}: {
  clusters: OrbCluster[];
  memories: Memory[];
  known: number;
  artifacts: { id: string; name: string; created_at: string }[];
}) {
  const assistant = useAssistantName();
  const kinds = Object.entries(
    memories.reduce<Record<string, number>>((acc, m) => ({ ...acc, [m.kind]: (acc[m.kind] ?? 0) + 1 }), {}),
  );
  const newest = [...artifacts].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, 8);
  return (
    <div className="relative z-10 min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-4">
        <section>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
            <Bot size={15} /> Teams
          </h2>
          {clusters.length === 0 && (
            <p className="text-sm text-slate-500">No teams right now. {assistant} builds one when an ask needs it.</p>
          )}
          <div className="space-y-2">
            {clusters.map((c) => (
              <Link key={c.id} to={`/agents/${c.id}`} className="block rounded-lg border border-edge bg-panel px-3 py-2 hover:bg-edge/40">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{c.name}</span>
                  <span className={`ml-auto shrink-0 text-xs ${RUN_STYLE[c.status as Run["status"]] ?? "text-slate-400"}`}>
                    {RUN_LABEL[c.status as Run["status"]] ?? (c.lifetime === "template" ? "Template" : "Idle")}
                  </span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {c.nodes.map((n) => (
                    <span key={n.key} className="flex items-center gap-1 rounded-full bg-ink px-2 py-0.5 text-xs text-slate-300">
                      <span className="size-1.5 rounded-full" style={{ background: NODE_COLOR[n.status] }} />
                      {n.name}
                    </span>
                  ))}
                </div>
              </Link>
            ))}
          </div>
        </section>
        <section>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
            <Brain size={15} /> Memory
          </h2>
          <Link to="/memory" className="block rounded-lg border border-edge bg-panel px-3 py-2 text-sm hover:bg-edge/40">
            {memories.length === 0
              ? `Nothing yet. ${assistant} learns as you chat.`
              : kinds.map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`).join(" · ")}
          </Link>
          {known > 0 && (
            <Link
              to="/memory?view=connections"
              className="mt-2 block rounded-lg border border-edge bg-panel px-3 py-2 text-sm hover:bg-edge/40"
            >
              {known} connection{known === 1 ? "" : "s"}: the people, projects, places and topics in your memories
            </Link>
          )}
        </section>
        <section>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
            <Box size={15} /> Artifacts
          </h2>
          {newest.length === 0 && <p className="text-sm text-slate-500">Files {assistant} and its teams make show here.</p>}
          <div className="space-y-1">
            {newest.map((f) => (
              <a key={f.id} href={api.fileUrl(f.id, true)} target="_blank" rel="noreferrer" className="block truncate rounded-md px-2 py-1.5 text-sm text-slate-300 hover:bg-edge/50">
                {f.name}
              </a>
            ))}
            {artifacts.length > newest.length && (
              <Link to="/library" className="block px-2 py-1 text-sm text-sky-300">
                All {artifacts.length} in the Library
              </Link>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

/** If the 3D scene throws (lost WebGL context, driver bug), fall back to the list view. */
class SceneBoundary extends Component<{ children: ReactNode; onError: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    console.error("Orb scene failed, showing the list instead", error);
    this.props.onError();
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}
