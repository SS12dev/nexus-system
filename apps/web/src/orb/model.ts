/**
 * Pure scene model for the 3D orb: where each live agent, memory and artifact sits, and what
 * colour it is. No three.js here, so it is cheap to test; the scene springs objects toward
 * these targets, which is what makes agents grow out of the orb and fall back into it.
 */
import type { AgentSystem, Approval, FileItem, Memory, MemoryKind } from "../lib/types";

export type Vec3 = [number, number, number];

/** Radius of the core orb; everything else is laid out relative to it. */
export const ORB_R = 1;

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
};

export const KIND_COLOR: Record<MemoryKind, string> = {
  preference: "#7dd3fc",
  fact: "#cbd5e1",
  goal: "#6ee7b7",
  instruction: "#fcd34d",
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

export interface MemoryPoint {
  id: string;
  kind: MemoryKind;
  content: string;
  pinned: boolean;
  pos: Vec3;
}

export interface Crystal {
  id: string;
  name: string;
  contentType: string;
  pos: Vec3;
}

// --- Deterministic randomness -----------------------------------------------------------------

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

/** Systems that belong around the orb: anything not archived, oldest first so a new team
 * takes the next free slot and existing ones stay put. */
export function liveSystems(systems: Record<string, AgentSystem>): AgentSystem[] {
  return Object.values(systems)
    .filter((s) => s.status !== "archived")
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

/** Offsets for a team's agents around its anchor, in the plane facing away from the orb. */
export function agentTargets(
  anchor: Vec3,
  count: number,
  topology: AgentSystem["topology"],
): Vec3[] {
  const out = norm(anchor);
  const up: Vec3 = Math.abs(out[1]) > 0.95 ? [1, 0, 0] : [0, 1, 0];
  const u = norm(cross(out, up));
  const v = cross(out, u);
  if (count <= 1) return [anchor];
  if (topology === "pipeline" || topology === "critic_loop") {
    // A chain reads as a line, first agent nearest the orb's left.
    const step = 0.55;
    return Array.from({ length: count }, (_, i) =>
      add(anchor, scale(u, (i - (count - 1) / 2) * step)),
    );
  }
  // Lead at the anchor, the rest on a ring around it, pushed slightly outward.
  const ring = 0.5 + count * 0.05;
  const targets: Vec3[] = [anchor];
  const others = count - 1;
  for (let i = 0; i < others; i++) {
    const a = (i / others) * Math.PI * 2 + Math.PI / 2;
    targets.push(
      add(add(anchor, scale(out, 0.35)), add(scale(u, Math.cos(a) * ring), scale(v, Math.sin(a) * ring))),
    );
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
    // The lead goes first so it takes the anchor slot.
    const ordered = [...s.nodes].sort((a, b) => Number(b.lead) - Number(a.lead));
    const targets = agentTargets(anchor, ordered.length, s.topology);
    const leadKey = ordered[0] ? `${s.id}:${ordered[0].id}` : null;
    const chain = s.topology === "pipeline" || s.topology === "critic_loop";
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
        // In a chain each agent hangs from the one before it; otherwise from the lead.
        parent: j === 0 ? null : chain ? `${s.id}:${ordered[j - 1].id}` : leadKey,
        target: targets[j],
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

// --- Inside the orb ---------------------------------------------------------------------------

/** One cluster centre per memory kind, at the corners of a tetrahedron inside the orb, so
 * related memories gather together. */
const KIND_CENTER: Record<MemoryKind, Vec3> = {
  preference: scale(norm([1, 1, 1]), 0.42),
  fact: scale(norm([-1, -1, 1]), 0.42),
  goal: scale(norm([-1, 1, -1]), 0.42),
  instruction: scale(norm([1, -1, -1]), 0.42),
};

/** Memories as points inside the orb. Pinned and recently reinforced ones are kept first
 * when there are more than the device can draw. */
export function memoryPoints(memories: Memory[], max: number): MemoryPoint[] {
  const ranked = [...memories].sort(
    (a, b) =>
      Number(b.pinned) - Number(a.pinned) || (a.reinforced_at < b.reinforced_at ? 1 : -1),
  );
  return ranked.slice(0, max).map((m) => {
    const r = rng(hash(m.id));
    // A rough gaussian (sum of uniforms) around the kind's centre, kept inside the shell.
    const g = () => (r() + r() + r() - 1.5) * 0.32;
    let pos = add(KIND_CENTER[m.kind] ?? KIND_CENTER.fact, [g(), g(), g()]);
    const l = len(pos);
    if (l > 0.82 * ORB_R) pos = scale(pos, (0.82 * ORB_R) / l);
    return { id: m.id, kind: m.kind, content: m.content, pinned: m.pinned, pos };
  });
}

export function isArtifact(f: FileItem) {
  return f.origin === "generated" && f.status === "ready";
}

/** Generated files as crystals on a tilted ring inside the orb, newest first. */
export function crystals(files: FileItem[], max: number): Crystal[] {
  const list = files
    .filter(isArtifact)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
    .slice(0, max);
  return list.map((f, i) => {
    const a = (i / Math.max(list.length, 1)) * Math.PI * 2;
    const y = (rng(hash(f.id))() - 0.5) * 0.35;
    const r = 0.58 * ORB_R;
    return { id: f.id, name: f.name, contentType: f.content_type, pos: [Math.cos(a) * r, y, Math.sin(a) * r] };
  });
}

/** How far back the camera sits so the orb and every team fit the view, with a margin for
 * teams on the near side, which perspective makes look bigger. */
export function cameraDistance(
  clusterCount: number,
  aspect: number,
  compact = false,
  fovDeg = 50,
): number {
  const extent = clusterCount === 0 ? ORB_R * 1.6 : bandRadius(clusterCount, compact) + 0.75;
  const tanV = Math.tan((fovDeg * Math.PI) / 360);
  const tanH = tanV * aspect;
  const margin = clusterCount === 0 ? 1.6 : 1.08;
  return (extent / Math.min(tanV, tanH)) * margin;
}
