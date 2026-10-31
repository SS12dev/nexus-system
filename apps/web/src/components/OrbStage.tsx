import {
  Bot,
  Brain,
  CalendarClock,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FolderGit2,
  FolderOpen,
  LayoutGrid,
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
import { DOMAIN_COLOR, ENTITY_COLOR, ENTITY_TYPES, FAMILIES, RECALL_COLOR, relationText, SHELLS } from "../lib/entities";
import { formatInZone } from "../lib/schedule";
import type { FileItem, Folder, KnowledgeGraph, Memory, Profiles, Project, Recalled, Routine, Run } from "../lib/types";
import { formatDate } from "../lib/format";
import {
  buildClusters,
  DEFAULT_VIEW,
  inside as layoutInside,
  isArtifact,
  KIND_COLOR,
  litAt,
  litSteps,
  liveSystems,
  NODE_COLOR,
  OUTSIDE_TYPES,
  plain,
  profileEntities,
  satellites,
  SATELLITE_COLOR,
  works,
  type OrbCluster,
  type OrbView,
  type Work,
} from "../orb/model";
import { StarLabels } from "../orb/StarLabels";
import type { Selection } from "../orb/OrbScene";
import { hasWebGL, usePerf } from "../orb/perf";
import { useStore } from "../store/chat";
import { NoticeBell } from "./Notices";
import { FloatingWidgets, WidgetDock } from "./Widgets";
import { Orb } from "./Orb";
import { QuestionCards } from "./QuestionCards";
import { RUN_LABEL, RUN_STYLE } from "./RunControls";
import { useAssistantName } from "../lib/assistant";
import type { ScreenMode } from "../lib/types";
import { setScreenMode } from "./Screen";

const OrbScene = lazy(() => import("../orb/OrbScene"));

/** The main screen's layouts. The assistant can switch them too ("show me my widgets"), but a
 * switch made here is yours and it leaves it alone for a while. */
const VIEWS: { id: ScreenMode; label: string; icon: ReactNode; hint: string }[] = [
  { id: "orb", label: "Orb", icon: <Orbit size={14} />, hint: "The 3D orb: teams at work and what Nexus knows" },
  { id: "list", label: "List", icon: <List size={14} />, hint: "The same as plain lists" },
  { id: "dashboard", label: "Widgets", icon: <LayoutGrid size={14} />, hint: "A board of your widgets" },
];

/** How long what a reply recalled stays lit in the orb, and how long each hop of it takes to
 * light up. */
const LIT_MS = 15000;
const HOP_MS = 450;

/** Nexus as a 3D orb with the live agent network around it and, inside it, who you are: you at
 * the centre, the people, places, interests and tools you've mentioned around you, and
 * memories as their satellites. Just outside the shell, on a ring, what it does and has made
 * for you: routines, and projects (an app, or a folder of files made for one piece of work,
 * as one moon each). Or the same as plain lists. Fills the home screen, and stays beside a
 * chat (above it on phones) so you can watch Nexus and its teams work while you talk. */
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
  const view = useStore((s) => s.screen.mode);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null);
  const [routines, setRoutines] = useState<Routine[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const foldersVersion = useStore((s) => s.foldersVersion);
  const projectsVersion = useStore((s) => s.projectsVersion);
  const [packInfo, setPackInfo] = useState<Pick<Profiles, "packs" | "interests">>({
    packs: [],
    interests: [],
  });
  const routinesVersion = useStore((s) => s.routinesVersion);
  const recall = useStore((s) => s.recall);
  const [lit, setLit] = useState<Recalled | null>(null);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [webglFailed, setWebglFailed] = useState(false);
  const [orbView, setOrbView] = useOrbView();
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
    api.profiles().then(setPackInfo).catch(() => {});
  }, []);
  useEffect(() => {
    api.projects().then(setProjects).catch(() => {});
  }, [connected, Object.keys(files).length, projectsVersion]);
  // A team saving several files announces its folder each time; take the last of the burst.
  useEffect(() => {
    const t = window.setTimeout(() => api.folders().then(setFolders).catch(() => {}), 300);
    return () => window.clearTimeout(t);
  }, [connected, foldersVersion]);

  // Memories change in bursts after a chat; wait for the burst to settle.
  useEffect(() => {
    const t = window.setTimeout(() => {
      api.memories().then(setMemories).catch(() => {});
      api.knowledgeGraph().then(setGraph).catch(() => {});
    }, 300);
    return () => window.clearTimeout(t);
  }, [memoryVersion, connected]);

  // Light up what a reply recalled, from what the message is about outward a hop at a time,
  // hold it for a while, then let it fade back.
  const [litStep, setLitStep] = useState(0);
  useEffect(() => {
    if (!recall) return;
    setLit(recall);
    setLitStep(0);
    const steps = litSteps(recall);
    const timers = Array.from({ length: steps - 1 }, (_, i) =>
      window.setTimeout(() => setLitStep(i + 1), HOP_MS * (i + 1)),
    );
    timers.push(window.setTimeout(() => setLit(null), LIT_MS));
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [recall]);
  const litIds = useMemo(() => litAt(lit, litStep), [lit, litStep]);

  const live = useMemo(() => liveSystems(systems), [systems]);
  const clusters = useMemo(
    () => buildClusters(live, Object.values(approvals), perf.portrait),
    [live, approvals, perf.portrait],
  );
  const fileList = useMemo(() => Object.values(files), [files]);
  const artifactCount = useMemo(() => fileList.filter(isArtifact).length, [fileList]);
  const insideData = useMemo(
    () => layoutInside(graph, memories, { stars: perf.maxStars, memories: perf.maxMemories }, litIds, orbView),
    [graph, memories, perf.maxStars, perf.maxMemories, litIds, orbView],
  );
  const known = useMemo(() => profileEntities(graph).length, [graph]);
  const projectList = useMemo(() => works(projects, folders, fileList), [projects, folders, fileList]);
  // Each moon's thread goes to its topic: a routine from a profile pack to that pack's
  // profession or interest; a project to what the project Nexus knows by that name relates
  // to (the rocket model to 3D printing, say).
  const homes = useMemo(() => {
    const labels = new Map<string, string[]>();
    const interest = new Map(packInfo.interests.map((i) => [i.id, i.label]));
    for (const p of packInfo.packs) {
      const names = [p.label, ...[p.id, ...p.match].flatMap((id) => interest.get(id) ?? [])];
      labels.set(`pack:${p.id}`, names);
    }
    const byId = new Map(graph?.entities.map((e) => [e.id, e]) ?? []);
    for (const e of graph?.entities ?? []) {
      if (!OUTSIDE_TYPES.has(e.type)) continue;
      const near = (graph?.relations ?? [])
        .filter((r) => !r.valid_to && (r.src === e.id || r.dst === e.id))
        .map((r) => byId.get(r.src === e.id ? r.dst : r.src))
        .filter((x) => x && !x.is_self && !OUTSIDE_TYPES.has(x.type))
        .map((x) => x!.name);
      if (near.length) labels.set(`work:${plain(e.name)}`, near);
    }
    return (key: string) => labels.get(key) ?? labels.get(`work:${plain(key)}`) ?? [];
  }, [packInfo, graph]);
  const sats = useMemo(
    () => satellites(routines, projectList, insideData.stars, homes, perf.lite ? 12 : 24),
    [routines, projectList, insideData.stars, homes, perf.lite],
  );

  // Drop a selection whose object went away.
  useEffect(() => {
    if (selected?.type === "agent" && !clusters.some((c) => c.id === selected.node.systemId))
      setSelected(null);
  }, [clusters, selected]);

  const changeView = (v: ScreenMode) => {
    setSelected(null);
    setScreenMode(v);
  };

  const chat = mode === "chat";
  const board = view === "dashboard";
  const showOrb = view === "orb" && hasWebGL() && !webglFailed;
  const working = clusters.filter((c) => ["queued", "running", "awaiting_approval"].includes(c.status)).length;
  const agentCount = clusters.reduce((n, c) => n + c.nodes.length, 0);
  const status =
    state !== "idle"
      ? (activity ?? (state === "thinking" ? "Thinking…" : "Answering…"))
      : lit
        ? remembered(lit)
        : summary(clusters.length, agentCount, working, memories.length, known, projectList.length, artifactCount, routines.length);

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
        <NoticeBell />
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

      {/* Above the widget dock and placed widgets, so the Notices tray opens over them. */}
      <div className={`pointer-events-none relative z-40 flex items-start gap-3 ${chat ? "px-3 pt-2" : "px-4 pt-4"}`}>
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
          <NoticeBell />
          <div
            className="flex rounded-lg border border-edge bg-panel/80 p-0.5 text-sm backdrop-blur"
            role="group"
            aria-label="Main screen layout"
          >
            {VIEWS.map(({ id, label, icon, hint }) => {
              const on = id === "orb" ? showOrb : id === "list" ? !showOrb && !board : board;
              return (
                <button
                  key={id}
                  onClick={() => changeView(id)}
                  disabled={id === "orb" && !hasWebGL()}
                  title={hint}
                  className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 disabled:opacity-40 ${on ? "bg-edge text-white" : "text-slate-400"}`}
                  aria-pressed={on}
                  aria-label={label}
                >
                  {icon} {!chat && <span className="hidden sm:inline">{label}</span>}
                </button>
              );
            })}
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

      {/* Widgets: a row under the top bar on phones, a column on the right on laptops; or, on
          the widget board, the whole main area. */}
      {board ? (
        <div className={`relative z-10 min-h-0 flex-1 overflow-y-auto ${chat ? "px-3 py-2" : "px-4 py-3"}`}>
          <WidgetDock board />
        </div>
      ) : (
        <div
          className={`pointer-events-none relative z-20 ${chat ? "px-3 pt-2" : "px-4 pt-3"} lg:absolute lg:bottom-28 lg:right-4 lg:top-16 lg:px-0 lg:pt-0`}
        >
          <WidgetDock compact={chat} />
        </div>
      )}
      {!board && <FloatingWidgets compact={chat} />}

      {board ? null : showOrb ? (
        <div className="pointer-events-none relative z-10 flex flex-1 flex-col justify-end px-4 pb-2">
          {!chat && <QuestionCards className="mx-auto mb-2 w-full max-w-md" />}
          {!chat && <Legend view={orbView} onView={setOrbView} />}
        </div>
      ) : (
        <ListView clusters={clusters} memories={memories} known={known} projects={projectList} />
      )}

      {selected && (
        <div className={chat ? "absolute inset-x-2 bottom-2 z-20" : "relative z-10 px-3 pb-2"}>
          <SelectionCard
            selection={selected}
            clusters={clusters}
            graph={graph}
            routines={routines}
            projects={projects}
            files={fileList}
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
  projects: number,
  artifacts: number,
  routines = 0,
) {
  const parts = [
    teams ? `${teams} team${teams > 1 ? "s" : ""} (${agents} agents${working ? `, ${working} working` : ""})` : "No teams right now",
    ...(routines ? [`${routines} routine${routines === 1 ? "" : "s"}`] : []),
    `${memories} memor${memories === 1 ? "y" : "ies"}`,
    ...(known ? [`${known} connection${known === 1 ? "" : "s"}`] : []),
    projects
      ? `${projects} project${projects === 1 ? "" : "s"}`
      : `${artifacts} artifact${artifacts === 1 ? "" : "s"}`,
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

const VIEW_KEY = "nexus.orb.view";

/** How the viewer likes the constellation drawn, kept in this browser only. */
function useOrbView(): [OrbView, (v: OrbView) => void] {
  const [view, setView] = useState<OrbView>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? "null");
      if (saved && ["connections", "importance", "mentions"].includes(saved.size) && typeof saved.gravity === "number")
        return { size: saved.size, gravity: Math.min(1, Math.max(0, saved.gravity)) };
    } catch {
      // Private window or blocked storage: the default is fine.
    }
    return DEFAULT_VIEW;
  });
  const save = (v: OrbView) => {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify(v));
    } catch {
      // Not remembered; it still applies now.
    }
  };
  return [view, save];
}

const SIZE_HELP: Record<OrbView["size"], string> = {
  connections: "how many things it connects to",
  importance: "how central it is to you (ranked each night)",
  mentions: "how often memories, chats and files mention it",
};

function Legend({ view, onView }: { view: OrbView; onView: (v: OrbView) => void }) {
  const [open, setOpen] = useState(false);
  const dot = (c: string) => <span className="inline-block size-2 shrink-0 rounded-full" style={{ background: c }} />;
  const item = "flex items-center gap-1.5 whitespace-nowrap";
  const group = "text-[10px] font-semibold uppercase tracking-wider text-slate-500";
  const shape = (cls: string, c = "#cbd5e1") => <span className={`inline-block size-2 shrink-0 ${cls}`} style={{ background: c }} />;
  return (
    <div className="pointer-events-auto mx-auto hidden max-w-full flex-col gap-1 rounded-2xl border border-edge bg-ink/75 px-4 py-2 text-xs text-slate-300 backdrop-blur sm:flex">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className={group}>Outside</span>
        <span className={item}>{dot(NODE_COLOR.running)} team working</span>
        <span className={item}>{dot(NODE_COLOR.working)} using a tool</span>
        <span className={item}>{dot(NODE_COLOR.waiting)} needs approval</span>
        <span className={item}>{dot(SATELLITE_COLOR.routine)} routines</span>
        <span className={item}>
          <span className="inline-block size-2 rotate-45" style={{ background: SATELLITE_COLOR.files }} /> projects
          and their files
        </span>
        <span className={item}>
          <span className="inline-block size-2 rotate-45" style={{ background: SATELLITE_COLOR.project }} /> apps
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className={group}>Inside</span>
        <span className={item}>{dot("#f8fafc")} you</span>
        <span className={item}>{dot(ENTITY_COLOR.person)} what Nexus knows about you</span>
        <span className={item}>
          <span className="inline-block h-px w-3 bg-slate-300" /> how they relate
        </span>
        <span className={item}>{dot(KIND_COLOR.preference)} memories</span>
        <span className={item}>{dot(RECALL_COLOR)} just remembered</span>
        <button onClick={() => setOpen(!open)} className="ml-auto text-sky-300 hover:text-sky-200" aria-expanded={open}>
          {open ? "Less" : "Key and view"}
        </button>
      </div>
      {open && (
        <div className="mt-1 space-y-1.5 border-t border-edge pt-2">
          <p className="max-w-2xl text-slate-400">
            Inside, the nearer the centre the closer a thing is to who you are. Each part of life has its own side of
            the orb, and things that belong together cluster.
          </p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className={group}>Rings</span>
            <span className={item} title={SHELLS[1].help}>{shape("rotate-45")} {SHELLS[1].label.toLowerCase()}</span>
            <span className={item} title={SHELLS[2].help}>{shape("rounded-full")} {SHELLS[2].label.toLowerCase()}</span>
            <span className={item} title={SHELLS[3].help}>{shape("")} {SHELLS[3].label.toLowerCase()}</span>
            <span className={item} title={SHELLS[4].help}>
              <span className="inline-block size-0 border-x-4 border-b-8 border-x-transparent border-b-slate-300" />{" "}
              {SHELLS[4].label.toLowerCase()}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className={group}>Parts of life</span>
            {Object.entries(DOMAIN_COLOR).map(([d, c]) => (
              <span key={d} className={item}>
                {dot(c)} {d}
              </span>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className={group}>Links</span>
            {Object.entries(FAMILIES).map(([f, x]) => (
              <span key={f} className={item} title={x.help}>
                <span className="inline-block h-0.5 w-3" style={{ background: x.color }} /> {x.label.toLowerCase()}
              </span>
            ))}
            <span className={item}>
              <span className="inline-block w-3 border-t border-dashed border-slate-400" /> ended or implied
            </span>
            <span className="text-slate-500">thicker is stronger</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <label className={item}>
              <span className={group}>Size by</span>
              <select
                value={view.size}
                onChange={(e) => onView({ ...view, size: e.target.value as OrbView["size"] })}
                className="rounded-md border border-edge bg-panel px-1.5 py-0.5 text-xs"
              >
                <option value="connections">connections</option>
                <option value="importance">importance</option>
                <option value="mentions">mentions</option>
              </select>
              <span className="text-slate-500">{SIZE_HELP[view.size]}</span>
            </label>
            <label className={item} title="How tightly things that belong together pull into clusters">
              <span className={group}>Gravity</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={view.gravity}
                onChange={(e) => onView({ ...view, gravity: Number(e.target.value) })}
                className="w-24 accent-sky-400"
              />
              <span className="text-slate-500">{view.gravity < 0.2 ? "loose" : view.gravity > 0.8 ? "tight" : "medium"}</span>
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

function SelectionCard({
  selection,
  clusters,
  graph,
  routines,
  projects,
  files,
  onClose,
}: {
  selection: Selection;
  clusters: OrbCluster[];
  graph: KnowledgeGraph | null;
  routines: Routine[];
  projects: Project[];
  files: FileItem[];
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
        <div
          className="flex items-center gap-2 text-xs"
          style={{ color: st.self ? "#f8fafc" : (st.domain && DOMAIN_COLOR[st.domain]) || ENTITY_COLOR[st.type] }}
        >
          <Share2 size={14} /> {st.self ? "You" : t.label}
        </div>
        <div className="mt-1 font-medium">{st.name}</div>
        {st.summary && <p className="mt-0.5 text-sm text-slate-400">{st.summary}</p>}
        {!st.self && st.shell && (
          <p className="mt-0.5 text-xs text-slate-500">
            {SHELLS[st.shell]?.label}
            {st.domain && <> · {st.domain}</>}
          </p>
        )}
        {facts.length > 0 && (
          <ul className="mt-1.5 space-y-0.5 text-sm text-slate-300">
            {facts.slice(0, 3).map((r) => (
              <li key={r.id} className="flex items-baseline gap-1.5">
                {r.family && (
                  <span
                    className="inline-block size-1.5 shrink-0 -translate-y-px rounded-full"
                    style={{ background: FAMILIES[r.family]?.color }}
                    title={FAMILIES[r.family]?.label}
                  />
                )}
                <span>{relationText(r, names)}</span>
              </li>
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
          to={`/knowledge?focus=${st.id}`}
          className="mt-2 inline-flex items-center gap-1 text-sm text-sky-300 hover:text-sky-200"
        >
          Open in Knowledge <ExternalLink size={13} />
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
    const p = sat.kind === "project" && sat.app ? projects.find((x) => x.id === sat.id) : undefined;
    const inside = latestVersions(files.filter((f) => f.folder_id && sat.folderIds.includes(f.folder_id)));
    body = (
      <>
        <div className="flex items-center gap-2 text-xs" style={{ color: sat.color }}>
          {sat.kind === "routine" ? <CalendarClock size={14} /> : sat.app ? <FolderGit2 size={14} /> : <FolderOpen size={14} />}
          {sat.kind === "routine" ? (r?.enabled === false ? "Routine · paused" : "Routine") : sat.app ? "App" : "Project"}
        </div>
        <div className="mt-1 font-medium">{sat.name}</div>
        <p className="mt-0.5 text-sm text-slate-400">
          {sat.detail}
          {r?.next_run_at && ` · next ${formatInZone(r.next_run_at, r.timezone)}`}
          {p?.app_state === "running" && " · live"}
        </p>
        {inside.length > 0 && (
          <ul className="mt-1.5 max-h-36 space-y-0.5 overflow-y-auto text-sm">
            {inside.slice(0, 8).map((f) => (
              <li key={f.id} className="flex items-center gap-2">
                <a
                  href={api.fileUrl(f.id, true)}
                  target="_blank"
                  rel="noreferrer"
                  className="min-w-0 flex-1 truncate text-slate-200 hover:text-sky-300"
                  title={f.name}
                >
                  {f.name}
                  {(f.version ?? 1) > 1 && <span className="text-slate-500"> v{f.version}</span>}
                </a>
                <span className="shrink-0 text-xs text-slate-500">{formatDate(f.created_at)}</span>
              </li>
            ))}
            {inside.length > 8 && <li className="text-xs text-slate-500">and {inside.length - 8} more</li>}
          </ul>
        )}
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
          {sat.kind === "project" && (
            <Link
              to={sat.folderIds.length ? `/library?folder=${sat.folderIds[0]}` : "/library"}
              className="text-slate-300 hover:text-white"
            >
              Open in Library
            </Link>
          )}
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

/** The 2D alternative: the same teams, memories and projects as plain lists. Also the
 * fallback when the device can't draw WebGL. */
function ListView({
  clusters,
  memories,
  known,
  projects,
}: {
  clusters: OrbCluster[];
  memories: Memory[];
  known: number;
  projects: Work[];
}) {
  const assistant = useAssistantName();
  const kinds = Object.entries(
    memories.reduce<Record<string, number>>((acc, m) => ({ ...acc, [m.kind]: (acc[m.kind] ?? 0) + 1 }), {}),
  );
  const newest = projects.slice(0, 8);
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
              to="/knowledge"
              className="mt-2 block rounded-lg border border-edge bg-panel px-3 py-2 text-sm hover:bg-edge/40"
            >
              {known} thing{known === 1 ? "" : "s"} known about you: the people, places and interests, and how they link
            </Link>
          )}
        </section>
        <section>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
            <FolderOpen size={15} /> Projects
          </h2>
          {newest.length === 0 && (
            <p className="text-sm text-slate-500">
              What {assistant} and its teams make shows here, each piece of work as one project with its files.
            </p>
          )}
          <div className="space-y-1">
            {newest.map((w) => (
              <Link
                key={w.id}
                to={w.folderIds.length ? `/library?folder=${w.folderIds[0]}` : "/library"}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-slate-300 hover:bg-edge/50"
              >
                {w.app ? <FolderGit2 size={14} className="shrink-0" /> : <FolderOpen size={14} className="shrink-0" />}
                <span className="min-w-0 flex-1 truncate">{w.name}</span>
                <span className="shrink-0 text-xs text-slate-500">{w.detail}</span>
              </Link>
            ))}
            {projects.length > newest.length && (
              <Link to="/library" className="block px-2 py-1 text-sm text-sky-300">
                All {projects.length} in the Library
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

/** The newest version of each file, newest first: a project lists rocket.stl once, not once
 * per version. */
export function latestVersions(files: FileItem[]): FileItem[] {
  const best = new Map<string, FileItem>();
  for (const f of files) {
    const key = f.version_of_id ?? f.id;
    const had = best.get(key);
    if (!had || (f.version ?? 1) > (had.version ?? 1)) best.set(key, f);
  }
  return [...best.values()].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
}
