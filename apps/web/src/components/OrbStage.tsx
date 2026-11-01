import {
  Bot,
  Brain,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Eye,
  EyeOff,
  FolderGit2,
  FolderOpen,
  LayoutGrid,
  List,
  Maximize2,
  Minimize2,
  Orbit,
  Rocket,
  Satellite,
  Sparkles,
  X,
} from "lucide-react";
import { Component, lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../lib/api";
import { AGENT_LABEL, toolLabel } from "../lib/tools";
import { FAMILIES, relationText } from "../lib/entities";
import { hasRole, type KnowledgeGraph, type Memory, type Project, type Routine, type Run, type Universe } from "../lib/types";
import type { FileItem, Folder } from "../lib/types";
import { buildClusters, isArtifact, litAt, litSteps, liveSystems, NODE_COLOR, works, type OrbCluster, type Work } from "../universe/teams";
import { hasWebGL, usePerf } from "../universe/perf";
import { areaLine, DECKS, deckActivity, isEmpty } from "../universe/model";
import type { Selection } from "../universe/UniverseScene";
import { useStore } from "../store/chat";
import { NoticeBell } from "./Notices";
import { FloatingWidgets, WidgetDock } from "./Widgets";
import { Orb } from "./Orb";
import { QuestionCards } from "./QuestionCards";
import { RUN_LABEL, RUN_STYLE } from "./RunControls";
import { useAssistantName } from "../lib/assistant";
import type { Recalled, ScreenMode } from "../lib/types";
import { setScreenMode } from "./Screen";

const UniverseScene = lazy(() => import("../universe/UniverseScene"));

/** The main screen's layouts. The assistant can switch them too ("show me my widgets"), but a
 * switch made here is yours and it leaves it alone for a while. */
const VIEWS: { id: ScreenMode; label: string; icon: ReactNode; hint: string }[] = [
  { id: "orb", label: "Universe", icon: <Orbit size={14} />, hint: "Your universe: who you are, your life areas and teams at work" },
  { id: "list", label: "List", icon: <List size={14} />, hint: "The same as plain lists" },
  { id: "dashboard", label: "Widgets", icon: <LayoutGrid size={14} />, hint: "A board of your widgets" },
];

/** How long what a reply recalled stays lit, and how long each hop of it takes to light up. */
const LIT_MS = 15000;
const HOP_MS = 450;
const PRIVATE_KEY = "nexus.universe.private";

/** The main screen: your universe. Your core (who you are, never named) at the centre, the
 * Nexus hub beside it, a galaxy for each part of your life that Nexus has found, and a ship for
 * each team at work. Or the same as plain lists. Fills the home screen, and stays beside a chat
 * (above it on phones) so you can watch Nexus and its teams work while you talk. */
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
  const [universe, setUniverse] = useState<Universe | null>(null);
  const [routines, setRoutines] = useState<Routine[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const foldersVersion = useStore((s) => s.foldersVersion);
  const projectsVersion = useStore((s) => s.projectsVersion);
  const routinesVersion = useStore((s) => s.routinesVersion);
  const recall = useStore((s) => s.recall);
  const [lit, setLit] = useState<Recalled | null>(null);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [webglFailed, setWebglFailed] = useState(false);
  const [privateMode, setPrivate] = usePrivateMode();
  const admin = hasRole(user, "admin");

  // Fresh lists on open and after a reconnect, in case events were missed meanwhile.
  useEffect(() => {
    const { syncLiveSystems, syncFiles, upsertApprovals } = useStore.getState();
    api.systems().then(syncLiveSystems).catch(() => {});
    api.files().then(syncFiles).catch(() => {});
    api.approvals().then(upsertApprovals).catch(() => {});
  }, [connected]);

  useEffect(() => {
    api.routines().then(setRoutines).catch(() => {});
  }, [routinesVersion, connected]);
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
      api.universe().then(setUniverse).catch(() => {});
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
  const clusters = useMemo(() => buildClusters(live, Object.values(approvals), perf.portrait), [live, approvals, perf.portrait]);
  const fileList = useMemo(() => Object.values(files), [files]);
  const artifactCount = useMemo(() => fileList.filter(isArtifact).length, [fileList]);
  const projectList = useMemo(() => works(projects, folders, fileList), [projects, folders, fileList]);
  const areas = universe?.areas.filter((a) => !a.ended_at).length ?? 0;
  const known = universe?.stars.length ?? 0;

  // "Show in universe" on the Mind page lands here with ?focus=<id>: fly to that star.
  const [params, setParams] = useSearchParams();
  const focusId = params.get("focus");
  useEffect(() => {
    if (!focusId || !universe) return;
    const star = universe.stars.find((s) => s.id === focusId && s.pos);
    if (star) {
      if (view !== "orb") setScreenMode("orb");
      setSelected({ type: "star", star });
    }
    setParams((p) => {
      p.delete("focus");
      return p;
    }, { replace: true });
  }, [focusId, universe, view, setParams]);

  // Drop a selection whose object went away.
  useEffect(() => {
    if (selected?.type === "team" && !clusters.some((c) => c.id === selected.cluster.id)) setSelected(null);
  }, [clusters, selected]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === "Escape" && setSelected(null);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);

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
        : summary(clusters.length, agentCount, working, memories.length, areas, known, projectList.length, artifactCount, routines.length);

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
      // Space is dark in both themes, so what floats over it stays dark.
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
              <UniverseScene
                universe={universe}
                clusters={clusters}
                assistantState={state}
                lit={litIds}
                litSeq={recall?.seq ?? 0}
                memorySeq={memoryVersion}
                perf={perf}
                admin={admin}
                privateMode={privateMode}
                compact={chat}
                selected={selected}
                onSelect={setSelected}
              />
            </Suspense>
          </SceneBoundary>
        </div>
      )}

      {/* Above the widget dock and placed widgets, so the Notices tray opens over them. */}
      <div className={`pointer-events-none relative z-40 flex items-start gap-3 ${chat ? "px-3 pt-2" : "px-4 pt-4"}`}>
        {/* A soft backdrop keeps the greeting and status readable over bright galaxies. */}
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
          {showOrb && (
            <button
              onClick={() => setPrivate(!privateMode)}
              className={`rounded-lg border border-edge bg-panel/80 p-1.5 backdrop-blur ${privateMode ? "text-amber-300" : "text-slate-400 hover:text-white"}`}
              aria-pressed={privateMode}
              aria-label={privateMode ? "Private mode on: names are hidden" : "Hide all names (private mode)"}
              title={privateMode ? "Private mode on: names are hidden. Click to show them" : "Hide all names, for screen sharing"}
            >
              {privateMode ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          )}
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
          {!chat && !selected && isEmpty(universe) && (
            <p className="mx-auto mb-2 max-w-md rounded-xl bg-ink/70 px-4 py-2 text-center text-sm text-slate-300 backdrop-blur">
              Your universe starts here. As you chat, {assistant} learns who you are (the core) and finds the parts of
              your life, each becoming a galaxy. Bring your chat history on the Mind page to start faster.
            </p>
          )}
          {!chat && !selected && !isEmpty(universe) && (
            <p className="mx-auto mb-1 hidden text-center text-xs text-slate-400 sm:block">
              Tap a galaxy, a star, the hub or a ship to look closer. Drag to look around.
            </p>
          )}
        </div>
      ) : (
        <ListView clusters={clusters} memories={memories} areas={areas} known={known} projects={projectList} />
      )}

      {selected && (
        <div className={chat ? "absolute inset-x-2 bottom-2 z-20" : "absolute bottom-3 left-3 right-3 z-30 sm:left-auto sm:right-4 sm:top-20 sm:bottom-auto sm:w-96"}>
          <SelectionCard
            selection={selected}
            universe={universe}
            graph={graph}
            clusters={clusters}
            state={state}
            admin={admin}
            privateMode={privateMode}
            onSelect={setSelected}
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
  areas: number,
  known: number,
  projects: number,
  artifacts: number,
  routines = 0,
) {
  const parts = [
    teams ? `${teams} team${teams > 1 ? "s" : ""} (${agents} agents${working ? `, ${working} working` : ""})` : "No teams right now",
    ...(routines ? [`${routines} routine${routines === 1 ? "" : "s"}`] : []),
    `${memories} memor${memories === 1 ? "y" : "ies"}`,
    ...(areas ? [`${areas} life area${areas === 1 ? "" : "s"}`] : []),
    ...(known ? [`${known} thing${known === 1 ? "" : "s"} known`] : []),
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

/** Private mode hides every name in the universe, for screen sharing; kept in this browser. */
function usePrivateMode(): [boolean, (v: boolean) => void] {
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(PRIVATE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const save = (v: boolean) => {
    setOn(v);
    try {
      localStorage.setItem(PRIVATE_KEY, v ? "1" : "0");
    } catch {
      // Not remembered; it still applies now.
    }
  };
  return [on, save];
}

const KIND_LABEL: Record<string, string> = {
  person: "Person",
  pet: "Pet",
  organisation: "Organisation",
  group: "Group",
  place: "Place",
  role: "Role",
  life_event: "Life event",
  project: "Project",
  app: "App",
  routine: "Routine",
  task: "Task",
  decision: "Decision",
  topic: "Topic",
  tool: "Tool",
  document: "Document",
  source: "Source",
};

const card = "text-xs uppercase tracking-wider";

/** The words for what was picked: the universe is for seeing, the card is for reading. */
function SelectionCard({
  selection,
  universe,
  graph,
  clusters,
  state,
  admin,
  privateMode,
  onSelect,
  onClose,
}: {
  selection: Selection;
  universe: Universe | null;
  graph: KnowledgeGraph | null;
  clusters: OrbCluster[];
  state: string;
  admin: boolean;
  privateMode: boolean;
  onSelect: (s: Selection) => void;
  onClose: () => void;
}) {
  const assistant = useAssistantName();
  let body: ReactNode;
  const areaOf = (id: string | null) => universe?.areas.find((a) => a.id === id);
  if (privateMode && selection.type !== "hub") {
    body = (
      <p className="text-sm text-slate-300">
        Private mode is on, so nothing is named. Turn it off with the eye button to read about this.
      </p>
    );
  } else if (selection.type === "core") {
    body = (
      <>
        <div className={`${card} flex items-center gap-2 text-sky-300`}>
          <Brain size={14} /> Your core
        </div>
        <p className="mt-1 text-sm text-slate-300">
          Who you are, as {assistant} understands it: your values deepest, then goals, traits and skills, with habits
          and preferences near the surface. Nothing in here is ever named on screen.
        </p>
        <p className="mt-1 text-xs text-slate-500">
          {universe?.core.neurons.length ?? 0} things about you, linked by how often they show up together.
        </p>
        <Link to="/mind#core" className="mt-2 inline-flex items-center gap-1 text-sm text-sky-300 hover:text-sky-200">
          Open your core to read and correct it <ExternalLink size={13} />
        </Link>
      </>
    );
  } else if (selection.type === "area") {
    const a = selection.area;
    const inside = (universe?.stars ?? []).filter((s) => s.area === a.id).sort((x, y) => y.salience - x.salience);
    body = (
      <>
        <div className={`${card} flex items-center gap-2`} style={{ color: a.color }}>
          <Sparkles size={14} /> Life area
        </div>
        <div className="mt-1 font-medium">{a.name}</div>
        {a.summary && <p className="mt-0.5 text-sm text-slate-400">{a.summary}</p>}
        <p className="mt-0.5 text-xs text-slate-500">{areaLine(a, universe?.stars ?? [])}</p>
        {inside.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {inside.slice(0, 12).map((s) => (
              <li key={s.id}>
                <button
                  onClick={() => onSelect({ type: "star", star: s })}
                  className="rounded-full bg-white/5 px-2 py-0.5 text-xs text-slate-200 hover:bg-white/10"
                >
                  {s.name}
                </button>
              </li>
            ))}
            {inside.length > 12 && <li className="px-1 text-xs text-slate-500">and {inside.length - 12} more</li>}
          </ul>
        )}
        <Link to={`/mind?area=${a.id}`} className="mt-2 inline-flex items-center gap-1 text-sm text-sky-300 hover:text-sky-200">
          Open in Mind <ExternalLink size={13} />
        </Link>
      </>
    );
  } else if (selection.type === "star") {
    const st = selection.star;
    const area = areaOf(st.area);
    const names = new Map(graph?.entities.map((x) => [x.id, x.is_self ? "You" : x.name]) ?? []);
    const facts = (graph?.relations ?? []).filter((r) => !r.valid_to && (r.src === st.id || r.dst === st.id));
    const e = graph?.entities.find((x) => x.id === st.id);
    body = (
      <>
        <div className={`${card} flex items-center gap-2`} style={{ color: area?.color ?? "#cbd5e1" }}>
          <Sparkles size={14} /> {KIND_LABEL[st.kind] ?? "Thing"}
          {area && <span className="normal-case tracking-normal text-slate-500">· {area.name}</span>}
        </div>
        <div className="mt-1 font-medium">{st.name}</div>
        {st.summary && <p className="mt-0.5 text-sm text-slate-400">{st.summary}</p>}
        {facts.length > 0 && (
          <ul className="mt-1.5 space-y-0.5 text-sm text-slate-300">
            {facts.slice(0, 4).map((r) => (
              <li key={r.id} className="flex items-baseline gap-1.5">
                <span
                  className="inline-block size-1.5 shrink-0 -translate-y-px rounded-full"
                  style={{ background: FAMILIES[r.family ?? "association"]?.color }}
                  title={FAMILIES[r.family ?? "association"]?.label}
                />
                <span>{relationText(r, names)}</span>
              </li>
            ))}
            {facts.length > 4 && <li className="text-xs text-slate-500">and {facts.length - 4} more</li>}
          </ul>
        )}
        {e && (
          <p className="mt-1.5 text-xs text-slate-500">
            In {e.memories} memor{e.memories === 1 ? "y" : "ies"}, {e.chats + e.past_chats} chat
            {e.chats + e.past_chats === 1 ? "" : "s"} and {e.files} file{e.files === 1 ? "" : "s"}
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-4 text-sm">
          <Link to={`/mind?focus=${st.id}`} className="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200">
            Open in Mind <ExternalLink size={13} />
          </Link>
          {area && (
            <button onClick={() => onSelect({ type: "area", area })} className="text-slate-300 hover:text-white">
              Whole area
            </button>
          )}
        </div>
      </>
    );
  } else if (selection.type === "hub") {
    const act = deckActivity({ assistant: state, teams: clusters, recalling: false, routinesDue: false });
    body = (
      <>
        <div className={`${card} flex items-center gap-2 text-sky-300`}>
          <Satellite size={14} /> Nexus hub · {assistant}
        </div>
        <p className="mt-1 text-sm text-slate-400">
          The station is {assistant}. Each deck is a layer of Nexus and lights up while that layer works.
        </p>
        <ul className="mt-2 space-y-1 text-sm">
          {DECKS.filter((d) => admin || !d.admin).map((d) => {
            const level = act[d.id];
            const row = (
              <>
                <span
                  className="inline-block size-2 shrink-0 rounded-full"
                  style={{ background: level > 0.8 ? "#5fe3ff" : level > 0.45 ? "#5fb4ff" : "#3b4560" }}
                />
                <span className="font-medium text-slate-200">{d.label}</span>
                <span className="truncate text-xs text-slate-500">{d.layer}</span>
                {d.admin && <span className="ml-auto shrink-0 text-[10px] uppercase text-pink-300">admin</span>}
              </>
            );
            return (
              <li key={d.id}>
                {d.link ? (
                  <Link to={d.link} className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-white/5">
                    {row}
                  </Link>
                ) : (
                  <div className="flex items-center gap-2 px-1 py-0.5">{row}</div>
                )}
              </li>
            );
          })}
        </ul>
      </>
    );
  } else {
    const team = selection.type === "team" ? selection.cluster : clusters.find((c) => c.id === selection.node.systemId);
    body = team ? (
      <>
        <div className={`${card} flex items-center gap-2 text-slate-400`}>
          <Rocket size={14} /> Team ship
          {team.status in RUN_LABEL && (
            <span className={`normal-case tracking-normal ${RUN_STYLE[team.status as Run["status"]]}`}>
              · {RUN_LABEL[team.status as Run["status"]]}
            </span>
          )}
        </div>
        <div className="mt-1 font-medium">{team.name}</div>
        <ul className="mt-1.5 space-y-0.5 text-sm">
          {team.nodes.map((n) => (
            <li key={n.key} className="flex items-baseline gap-2">
              <span className="inline-block size-1.5 shrink-0 -translate-y-px rounded-full" style={{ background: NODE_COLOR[n.status] }} />
              <span className="text-slate-200">{n.name}</span>
              <span className="truncate text-xs text-slate-500">
                {n.role}, {n.tier} ·{" "}
                {n.tool && ["working", "waiting"].includes(n.status)
                  ? `${AGENT_LABEL[n.status]} · ${toolLabel(n.tool)}`
                  : (AGENT_LABEL[n.status] ?? n.status)}
              </span>
            </li>
          ))}
        </ul>
        <Link to={`/agents/${team.id}`} className="mt-2 inline-flex items-center gap-1 text-sm text-sky-300 hover:text-sky-200">
          Open team <ExternalLink size={13} />
        </Link>
      </>
    ) : (
      <p className="text-sm text-slate-400">This team has finished.</p>
    );
  }
  return (
    <div className="relative max-h-[60vh] overflow-y-auto rounded-xl border border-edge bg-panel/90 p-3 pr-9 shadow-xl backdrop-blur">
      {body}
      <button onClick={onClose} className="absolute right-2 top-2 p-1 text-slate-400 hover:text-white" aria-label="Back to the universe" title="Back (Esc)">
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
  areas,
  known,
  projects,
}: {
  clusters: OrbCluster[];
  memories: Memory[];
  areas: number;
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
            <Brain size={15} /> Mind
          </h2>
          <Link to="/mind" className="block rounded-lg border border-edge bg-panel px-3 py-2 text-sm hover:bg-edge/40">
            {memories.length === 0
              ? `Nothing yet. ${assistant} learns as you chat.`
              : kinds.map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`).join(" · ")}
          </Link>
          {known > 0 && (
            <Link to="/mind" className="mt-2 block rounded-lg border border-edge bg-panel px-3 py-2 text-sm hover:bg-edge/40">
              {known} thing{known === 1 ? "" : "s"} known about you
              {areas > 0 && ` across ${areas} life area${areas === 1 ? "" : "s"}`}: the people, places and interests, and
              how they link
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
