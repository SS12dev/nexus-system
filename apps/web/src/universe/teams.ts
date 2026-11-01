/**
 * Pure model for the teams flying round the universe and the things around them: which teams
 * are live, each agent's status, projects, and what a reply recalled lit a step at a time. No
 * three.js here, so it is cheap to test.
 */
import { TEAM_W, teamLayout } from "../lib/teamLayout";
import type { AgentNode, AgentSystem, Approval, FileItem, Folder, Project, Recalled } from "../lib/types";

export type Vec3 = [number, number, number];

export const NODE_COLOR: Record<string, string> = {
  idle: "#64748b",
  queued: "#94a3b8",
  running: "#38bdf8",
  thinking: "#38bdf8",
  working: "#f59e0b",
  waiting: "#f43f5e",
  paused: "#a78bfa",
  done: "#10b981",
  failed: "#ef4444",
  skipped: "#334155",
};

const ACTIVE = new Set(["running", "thinking", "working", "waiting"]);
export const isActive = (status: string) => ACTIVE.has(status);

export interface OrbNode {
  /** Unique across systems: `${systemId}:${agentId}`. */
  key: string;
  systemId: string;
  agentId: string;
  name: string;
  role: string;
  tier: string;
  lead: boolean;
  status: string;
  tool?: string;
  /** Key of the node this one hangs from, or null for the lead (which hangs from the orb). */
  parent: string | null;
  target: Vec3;
}

export interface OrbCluster {
  id: string;
  name: string;
  /** The latest run's status, or the system's own when it has never run. */
  status: string;
  lifetime: AgentSystem["lifetime"];
  anchor: Vec3;
  nodes: OrbNode[];
  /** Agent-to-agent edges inside the team, as node keys. */
  links: [string, string][];
}


/** FNV-1a hash, so an id always lands in the same place across reloads. */
export function hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** mulberry32: a small seeded PRNG returning floats in [0, 1). */
export function rng(seed: number) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// --- Vector helpers ---------------------------------------------------------------------------

const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a: Vec3, k: number): Vec3 => [a[0] * k, a[1] * k, a[2] * k];
const len = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);
const norm = (a: Vec3): Vec3 => {
  const l = len(a) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};
const cross = (a: Vec3, b: Vec3): Vec3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];


// --- Agent systems ----------------------------------------------------------------------------

const WORKING_RUN = new Set(["queued", "running", "awaiting_approval", "paused"]);

/** Systems that belong around the orb: teams with a run in progress (or paused, or waiting for
 * an approval), oldest first so a new team takes the next free slot and existing ones stay put.
 * A team retracts into the orb when its run ends; saved teams wait on the Agents page. */
export function liveSystems(systems: Record<string, AgentSystem>): AgentSystem[] {
  return Object.values(systems)
    .filter((s) => s.status !== "archived" && !!s.run && WORKING_RUN.has(s.run.status))
    .sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0));
}

const GOLDEN = Math.PI * (3 - Math.sqrt(5));

/** Radius of the band the teams sit on. Portrait screens use a tighter band so the network
 * fits the narrow width without shrinking the orb to a dot. */
export function bandRadius(count: number, compact = false): number {
  return compact ? 1.7 + Math.min(count, 12) * 0.06 : 2.6 + Math.min(count, 12) * 0.12;
}

/** Where the i-th team sits: a golden-angle spiral on a band around the orb, so teams spread
 * evenly without piling up at the poles, where they would hide behind it. */
export function clusterAnchor(i: number, count: number, compact = false): Vec3 {
  const radius = bandRadius(count, compact);
  const y = count <= 1 ? 0.15 : 0.9 * (1 - (2 * (i + 0.5)) / count) * Math.min(1, count / 4);
  const r = Math.sqrt(Math.max(0, 1 - y * y));
  const theta = i * GOLDEN + 0.6;
  // Portrait screens have height to spare, so teams spread further up and down there.
  return [Math.cos(theta) * r * radius, y * radius * (compact ? 1.5 : 0.7), Math.sin(theta) * r * radius];
}

/** World units per diagram unit: the chat card's 520-wide diagram becomes about 3 units. */
const LAYOUT_SCALE = 3 / TEAM_W;

/** Each agent's place around its team's anchor, in the plane facing away from the orb: the
 * same shape as the team diagram in the chat card (teamLayout), centred on the anchor. */
export function agentTargets(
  anchor: Vec3,
  nodes: AgentNode[],
  topology: AgentSystem["topology"],
): Record<string, Vec3> {
  if (nodes.length <= 1) return Object.fromEntries(nodes.map((n) => [n.id, anchor]));
  const out = norm(anchor);
  const up: Vec3 = Math.abs(out[1]) > 0.95 ? [1, 0, 0] : [0, 1, 0];
  const u = norm(cross(out, up));
  const v = cross(out, u);
  const { pos, height } = teamLayout(nodes, topology);
  const targets: Record<string, Vec3> = {};
  for (const n of nodes) {
    const p = pos[n.id];
    // Diagram x runs along u; its y runs down the screen, so it goes against v.
    const dx = (p.x - TEAM_W / 2) * LAYOUT_SCALE;
    const dy = (height / 2 - p.y) * LAYOUT_SCALE;
    targets[n.id] = add(anchor, add(scale(u, dx), scale(v, dy)));
  }
  return targets;
}

/** Each agent's display status: a pending approval wins, then the live run's status. Agents of
 * a team that is not running show as idle, or done/failed straight after a run. */
export function agentStatus(system: AgentSystem, agentId: string, waiting: Set<string>): string {
  if (waiting.has(`${system.id}:${agentId}`)) return "waiting";
  const run = system.run;
  if (!run) return "idle";
  if (run.status === "queued") return "queued";
  if (run.status === "paused") return "paused";
  const live = run.agents?.[agentId]?.status;
  if (["running", "awaiting_approval"].includes(run.status)) return live ?? "idle";
  if (run.status === "failed") return live === "failed" ? "failed" : "idle";
  return live === "done" ? "done" : "idle";
}

export function buildClusters(
  systems: AgentSystem[],
  approvals: Approval[] = [],
  compact = false,
): OrbCluster[] {
  const waiting = new Set(
    approvals.filter((a) => a.status === "pending").map((a) => `${a.system_id}:${a.agent}`),
  );
  return systems.map((s, i) => {
    const anchor = clusterAnchor(i, systems.length, compact);
    // The lead goes first, so the team buds off the orb from it.
    const ordered = [...s.nodes].sort((a, b) => Number(b.lead) - Number(a.lead));
    const targets = agentTargets(anchor, s.nodes, s.topology);
    const leadKey = ordered[0] ? `${s.id}:${ordered[0].id}` : null;
    // Each agent hangs from the first agent that hands it work (as the diagram's arrows
    // show), else from the lead; the lead hangs from the orb.
    const feeds: Record<string, string> = {};
    for (const e of s.edges)
      if (e.kind !== "feedback" && e.from !== e.to && !(e.to in feeds)) feeds[e.to] = e.from;
    const nodes: OrbNode[] = ordered.map((n, j) => {
      const key = `${s.id}:${n.id}`;
      const live = s.run?.agents?.[n.id];
      return {
        key,
        systemId: s.id,
        agentId: n.id,
        name: n.name,
        role: n.role,
        tier: live?.tier ?? n.tier,
        lead: j === 0,
        status: agentStatus(s, n.id, waiting),
        tool: live?.tool,
        parent: j === 0 ? null : feeds[n.id] ? `${s.id}:${feeds[n.id]}` : leadKey,
        target: targets[n.id],
      };
    });
    const keys = new Set(nodes.map((n) => n.key));
    const links: [string, string][] = s.edges
      .map((e): [string, string] => [`${s.id}:${e.from}`, `${s.id}:${e.to}`])
      .filter(([a, b]) => a !== b && keys.has(a) && keys.has(b));
    return {
      id: s.id,
      name: s.name,
      status: s.run?.status ?? s.status,
      lifetime: s.lifetime,
      anchor,
      nodes,
      links,
    };
  });
}

/** A file Nexus made, ready to open. */
export function isArtifact(f: FileItem) {
  return f.origin === "generated" && f.status === "ready";
}


/** How many files each folder holds: the live files Nexus has seen, or the folder list's
 * count when none of its files are loaded. */
export function folderCounts(folders: Folder[], files: FileItem[]): Map<string, number> {
  const live = new Map<string, number>();
  for (const f of files) if (f.folder_id) live.set(f.folder_id, (live.get(f.folder_id) ?? 0) + 1);
  return new Map(folders.map((d) => [d.id, live.get(d.id) ?? d.files ?? 0]));
}

/** A project: a software project or a folder of files, before it becomes a moon. */
export interface Work {
  id: string;
  name: string;
  detail: string;
  app: boolean;
  live: boolean;
  folderIds: string[];
  files: number;
  updated: string;
}

/** Software projects and folders of files as one list of projects, newest first. A folder
 * that belongs to a software project joins it; an empty folder is left out. */
export function works(projects: Project[], folders: Folder[], files: FileItem[] = []): Work[] {
  const counts = folderCounts(folders, files);
  const out = new Map<string, Work>();
  for (const p of projects)
    out.set(p.id, {
      id: p.id,
      name: p.name,
      detail: p.stack ?? "project",
      app: true,
      live: p.app_state === "running",
      folderIds: [],
      files: 0,
      updated: p.updated_at,
    });
  for (const d of folders) {
    const n = counts.get(d.id) ?? 0;
    const app = d.project_id ? out.get(d.project_id) : undefined;
    if (app) {
      app.folderIds.push(d.id);
      app.files += n;
      if (d.updated_at > app.updated) app.updated = d.updated_at;
    } else if (n > 0) {
      out.set(d.id, {
        id: d.id,
        name: d.name,
        detail: `${n} file${n === 1 ? "" : "s"}`,
        app: false,
        live: false,
        folderIds: [d.id],
        files: n,
        updated: d.updated_at,
      });
    }
  }
  return [...out.values()].sort((a, b) => (a.updated < b.updated ? 1 : a.updated > b.updated ? -1 : 0));
}

// --- Lighting what a reply recalled -----------------------------------------------------------

/** How many steps lighting a recall takes: one per hop, then the memories. */
export function litSteps(recall: Recalled | null): number {
  if (!recall) return 0;
  const hops = (recall.path ?? []).map((p) => p.hop);
  return (hops.length ? Math.max(...hops) + 1 : 1) + (recall.memories.length ? 1 : 0);
}

/** What is lit `step` steps into lighting a recall: what the message is about first, then
 * each link recall followed from it, a hop at a time, and the memories it drew on last. A
 * recall without a path (an older reply) lights its things together, then its memories. */
export function litAt(recall: Recalled | null, step: number): Set<string> {
  if (!recall) return new Set();
  const hop = new Map((recall.path ?? []).map((p) => [p.id, p.hop]));
  const ids = recall.entities.filter((e) => (hop.get(e.id) ?? 0) <= step).map((e) => e.id);
  const last = litSteps(recall) - 1;
  return new Set(step >= last ? [...ids, ...recall.memories] : ids);
}
