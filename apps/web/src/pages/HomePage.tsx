import { Bot, Brain, Box, ExternalLink, List, Orbit, X } from "lucide-react";
import { Component, lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { Composer } from "../components/Composer";
import { Orb } from "../components/Orb";
import { RUN_LABEL, RUN_STYLE } from "../components/RunControls";
import { api } from "../lib/api";
import { hasRole, type Memory, type Run } from "../lib/types";
import { useSend } from "../lib/useSend";
import {
  buildClusters,
  crystals as toCrystals,
  isArtifact,
  KIND_COLOR,
  liveSystems,
  memoryPoints,
  NODE_COLOR,
  type OrbCluster,
} from "../orb/model";
import type { Selection } from "../orb/OrbScene";
import { hasWebGL, usePerf } from "../orb/perf";
import { useStore } from "../store/chat";

const OrbScene = lazy(() => import("../orb/OrbScene"));

const AGENT_LABEL: Record<string, string> = {
  idle: "Idle",
  queued: "Starting",
  running: "Working",
  thinking: "Thinking",
  working: "Using a tool",
  waiting: "Waiting for your approval",
  paused: "Paused",
  done: "Done",
  failed: "Failed",
};

const VIEW_KEY = "jarvis.home.view";

function savedView(): "orb" | "list" {
  if (!hasWebGL()) return "list";
  try {
    return localStorage.getItem(VIEW_KEY) === "list" ? "list" : "orb";
  } catch {
    return "orb";
  }
}

/** The home screen: Jarvis as a 3D orb with the live agent network around it and memories and
 * artifacts inside it, a chat dock underneath, and a plain list one tap away. */
export function HomePage() {
  const user = useStore((s) => s.user)!;
  const jarvis = useStore((s) => s.jarvis);
  const activity = useStore((s) => s.activity);
  const connected = useStore((s) => s.connected);
  const systems = useStore((s) => s.systems);
  const approvals = useStore((s) => s.approvals);
  const files = useStore((s) => s.files);
  const memoryVersion = useStore((s) => s.memoryVersion);
  const perf = usePerf();
  const { send, error } = useSend();
  const [view, setView] = useState(savedView);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [webglFailed, setWebglFailed] = useState(false);

  // Fresh lists on open and after a reconnect, in case events were missed meanwhile.
  useEffect(() => {
    const { syncLiveSystems, syncFiles, upsertApprovals } = useStore.getState();
    api.systems().then(syncLiveSystems).catch(() => {});
    api.files().then(syncFiles).catch(() => {});
    api.approvals().then(upsertApprovals).catch(() => {});
  }, [connected]);

  // Memories change in bursts after a chat; wait for the burst to settle.
  useEffect(() => {
    const t = window.setTimeout(() => api.memories().then(setMemories).catch(() => {}), 300);
    return () => window.clearTimeout(t);
  }, [memoryVersion, connected]);

  const live = useMemo(() => liveSystems(systems), [systems]);
  const clusters = useMemo(
    () => buildClusters(live, Object.values(approvals), perf.portrait),
    [live, approvals, perf.portrait],
  );
  const points = useMemo(() => memoryPoints(memories, perf.maxMemories), [memories, perf.maxMemories]);
  const artifactList = useMemo(() => Object.values(files).filter(isArtifact), [files]);
  const crystalList = useMemo(
    () => toCrystals(artifactList, perf.maxCrystals),
    [artifactList, perf.maxCrystals],
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

  const canChat = hasRole(user, "member");
  const showOrb = view === "orb" && !webglFailed;
  const working = clusters.filter((c) => ["queued", "running", "awaiting_approval"].includes(c.status)).length;
  const agentCount = clusters.reduce((n, c) => n + c.nodes.length, 0);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      {showOrb && (
        <div className="absolute inset-0">
          <SceneBoundary onError={() => setWebglFailed(true)}>
            <Suspense
              fallback={
                <div className="grid h-full place-items-center">
                  <Orb state={jarvis} size={140} />
                </div>
              }
            >
              <OrbScene
                jarvis={jarvis}
                clusters={clusters}
                memories={points}
                artifacts={crystalList}
                perf={perf}
                selected={selected}
                onSelect={setSelected}
              />
            </Suspense>
          </SceneBoundary>
        </div>
      )}

      <div className="pointer-events-none relative z-10 flex items-start gap-3 px-4 pt-4">
        <div className="min-w-0 flex-1">
          <p className="text-lg text-slate-200">
            Hi{user.name ? ` ${user.name.split(" ")[0]}` : ""}, what can I do for you?
          </p>
          <p className="mt-0.5 text-sm text-slate-400" aria-live="polite">
            {jarvis !== "idle"
              ? (activity ?? (jarvis === "thinking" ? "Thinking…" : "Answering…"))
              : summary(clusters.length, agentCount, working, memories.length, artifactList.length)}
          </p>
        </div>
        <div className="pointer-events-auto flex rounded-lg border border-edge bg-panel/80 p-0.5 text-sm backdrop-blur">
          <button
            onClick={() => changeView("orb")}
            disabled={!hasWebGL()}
            className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 disabled:opacity-40 ${showOrb ? "bg-edge text-white" : "text-slate-400"}`}
            aria-pressed={showOrb}
          >
            <Orbit size={14} /> Orb
          </button>
          <button
            onClick={() => changeView("list")}
            className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 ${!showOrb ? "bg-edge text-white" : "text-slate-400"}`}
            aria-pressed={!showOrb}
          >
            <List size={14} /> List
          </button>
        </div>
      </div>

      {showOrb ? (
        <div className="pointer-events-none relative z-10 flex flex-1 flex-col justify-end px-4 pb-2">
          <Legend />
        </div>
      ) : (
        <ListView clusters={clusters} memories={memories} artifacts={artifactList} />
      )}

      {selected && (
        <div className="relative z-10 px-3 pb-2">
          <SelectionCard selection={selected} clusters={clusters} onClose={() => setSelected(null)} />
        </div>
      )}
      {error && <p className="relative z-10 mx-auto max-w-3xl px-4 pb-2 text-sm text-rose-400">{error}</p>}
      <div className="relative z-10">
        <Composer
          onSend={send}
          disabled={!canChat}
          placeholder={canChat ? "Ask Jarvis anything" : "Viewers can read chats but not send messages"}
        />
      </div>
    </div>
  );
}

function summary(teams: number, agents: number, working: number, memories: number, artifacts: number) {
  const parts = [
    teams ? `${teams} team${teams > 1 ? "s" : ""} (${agents} agents${working ? `, ${working} working` : ""})` : "No teams right now",
    `${memories} memor${memories === 1 ? "y" : "ies"}`,
    `${artifacts} artifact${artifacts === 1 ? "" : "s"}`,
  ];
  return parts.join(" · ");
}

function Legend() {
  const dot = (c: string) => <span className="inline-block size-2 rounded-full" style={{ background: c }} />;
  return (
    <div className="mx-auto hidden flex-wrap justify-center gap-x-4 gap-y-1 text-xs text-slate-500 sm:flex">
      <span className="flex items-center gap-1.5">{dot(NODE_COLOR.running)} working</span>
      <span className="flex items-center gap-1.5">{dot(NODE_COLOR.working)} using a tool</span>
      <span className="flex items-center gap-1.5">{dot(NODE_COLOR.waiting)} needs approval</span>
      <span className="flex items-center gap-1.5">{dot(NODE_COLOR.idle)} idle</span>
      <span className="flex items-center gap-1.5">{dot(KIND_COLOR.preference)} memories inside</span>
      <span className="flex items-center gap-1.5">{dot("#c084fc")} artifacts inside</span>
    </div>
  );
}

function SelectionCard({
  selection,
  clusters,
  onClose,
}: {
  selection: Selection;
  clusters: OrbCluster[];
  onClose: () => void;
}) {
  let body: ReactNode;
  if (selection.type === "agent") {
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
          {AGENT_LABEL[n.status] ?? n.status}
          {n.tool && ["working", "waiting"].includes(n.status) ? ` · ${n.tool}` : ""}
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
  artifacts,
}: {
  clusters: OrbCluster[];
  memories: Memory[];
  artifacts: { id: string; name: string; created_at: string }[];
}) {
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
            <p className="text-sm text-slate-500">No teams right now. Jarvis builds one when an ask needs it.</p>
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
              ? "Nothing yet. Jarvis learns as you chat."
              : kinds.map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`).join(" · ")}
          </Link>
        </section>
        <section>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-300">
            <Box size={15} /> Artifacts
          </h2>
          {newest.length === 0 && <p className="text-sm text-slate-500">Files Jarvis and its teams make show here.</p>}
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
