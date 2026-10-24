/**
 * Pure scene model for the 3D orb: where each live agent, memory and artifact sits, and what
 * colour it is. No three.js here, so it is cheap to test; the scene springs objects toward
 * these targets, which is what makes agents grow out of the orb and fall back into it.
 */
import { TEAM_W, teamLayout } from "../lib/teamLayout";
import type {
  AgentNode,
  AgentSystem,
  Approval,
  EntityType,
  FileItem,
  KnowledgeGraph,
  Memory,
  MemoryKind,
  Project,
  Routine,
} from "../lib/types";

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
  skipped: "#334155",
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
  /** The star it orbits, when it is about someone or something. */
  star?: string;
}

export interface Crystal {
  id: string;
  name: string;
  contentType: string;
  pos: Vec3;
  /** The project (or other star) it belongs to, drawn as a line to it. */
  star?: string;
}

/** A person, project, place, topic or tool Nexus knows about, or the user at the centre. */
export interface StarPoint {
  id: string;
  name: string;
  type: EntityType;
  summary: string;
  self: boolean;
  /** How much mentions it: memories, chats and files. Bigger stars are mentioned more. */
  weight: number;
  pos: Vec3;
}

export interface StarLine {
  a: string;
  b: string;
  /** The relation stopped being true; drawn fainter. */
  ended: boolean;
}

/** Everything inside the orb. Without a knowledge graph yet, memories cluster by kind and
 * artifacts sit on a ring, as before. */
export interface Inside {
  stars: StarPoint[];
  lines: StarLine[];
  memories: MemoryPoint[];
  crystals: Crystal[];
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

// --- The constellation: the knowledge graph inside the orb -----------------------------------


const TYPE_ORDER: EntityType[] = ["person", "project", "organisation", "place", "topic", "tool", "other"];

/** The i-th of n directions spread evenly over a sphere. */
export function fibonacciDir(i: number, n: number): Vec3 {
  if (n <= 1) return norm([1, 0.25, 0.35]);
  const y = 1 - (2 * (i + 0.5)) / n;
  const r = Math.sqrt(1 - y * y);
  const theta = i * GOLDEN + 0.4;
  // Flatten the poles a little: the camera looks from the side, so stars on the equator read best.
  return norm([Math.cos(theta) * r, y * 0.75, Math.sin(theta) * r]);
}

/** The id of the "you" star while the graph has no entity for the user yet. */
export const YOU = "you";

/** Stars sit between these distances from the centre; the most mentioned ones closest. */
const STAR_NEAR = 0.42;
const STAR_FAR = 0.74;

const weightOf = (e: KnowledgeGraph["entities"][number]) => e.memories + e.files + e.chats + e.past_chats;

/** Lay out the knowledge graph: the user at the centre, the most mentioned entities around
 * them grouped by type, memories as satellites of what they're about, and the newest
 * artifacts beside their project. `focus` ids (what a reply just recalled) are always drawn. */
export function inside(
  graph: KnowledgeGraph | null,
  memories: Memory[],
  files: FileItem[],
  limits: { stars: number; memories: number; crystals: number },
  focus: Set<string> = new Set(),
): Inside {
  const others = (graph?.entities ?? []).filter((e) => !e.is_self);
  const self = graph?.entities.find((e) => e.is_self);
  // You are always at the centre, even before the graph has anything else (or anything at all).
  const you: StarPoint = {
    id: self?.id ?? YOU,
    name: self?.name ?? "You",
    type: self?.type ?? "person",
    summary: self?.summary ?? "",
    self: true,
    weight: self ? weightOf(self) : 0,
    pos: [0, 0, 0],
  };
  if (!graph || others.length === 0)
    return {
      stars: [you],
      lines: [],
      memories: memoryPoints(memories, limits.memories),
      crystals: crystals(files, limits.crystals),
    };

  const ranked = [...others].sort(
    (a, b) =>
      Number(focus.has(b.id)) - Number(focus.has(a.id)) ||
      weightOf(b) - weightOf(a) ||
      (a.seen_at < b.seen_at ? 1 : -1),
  );
  const chosen = ranked.slice(0, limits.stars);
  const heaviest = Math.max(1, ...chosen.map(weightOf));
  // Directions spread evenly over a sphere (a Fibonacci lattice), handed out by type so each
  // type keeps its own region, and turned so the first type faces its usual direction. Plain
  // per-type directions bunched up when most stars shared a type (a new user's topics).
  const order = [...chosen].sort(
    (a, b) => TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type) || hash(a.id) - hash(b.id),
  );
  const lattice = new Map(order.map((e, i) => [e.id, fibonacciDir(i, order.length)]));
  const stars: StarPoint[] = chosen.map((e) => {
    const r = rng(hash(e.id));
    // Loose jitter around the lattice point, so the spread is even without looking gridded.
    const j = () => (r() - 0.5) * 0.6;
    const dir = norm(add(lattice.get(e.id)!, [j(), j(), j()]));
    // Only a clear lead in mentions pulls a star in: a new graph, where everything is
    // mentioned once, spreads out through the orb instead of crowding you.
    const near = heaviest > 2 ? Math.sqrt(weightOf(e) / heaviest) : 0.15 + r() * 0.5;
    const dist = STAR_FAR - (STAR_FAR - STAR_NEAR) * near + (r() - 0.5) * 0.1;
    return {
      id: e.id,
      name: e.name,
      type: e.type,
      summary: e.summary,
      self: false,
      weight: weightOf(e),
      pos: scale(dir, dist),
    };
  });
  stars.unshift(you);
  const byId = new Map(stars.map((s) => [s.id, s]));

  const lines: StarLine[] = graph.relations
    .filter((rel) => rel.src !== rel.dst && byId.has(rel.src) && byId.has(rel.dst))
    .map((rel) => ({ a: rel.src, b: rel.dst, ended: !!rel.valid_to }));

  // Each memory orbits the most mentioned drawn star it is about.
  const home = new Map<string, string>();
  for (const e of chosen) for (const id of e.memory_ids) if (!home.has(id)) home.set(id, e.id);
  const rankedMemories = [...memories].sort(
    (a, b) =>
      Number(focus.has(b.id)) - Number(focus.has(a.id)) ||
      Number(b.pinned) - Number(a.pinned) ||
      (a.reinforced_at < b.reinforced_at ? 1 : -1),
  );
  const points = rankedMemories.slice(0, limits.memories).map((m): MemoryPoint => {
    const star = home.get(m.id);
    const r = rng(hash(m.id));
    const offset = norm([r() - 0.5, r() - 0.5, r() - 0.5]);
    const base = { id: m.id, kind: m.kind, content: m.content, pinned: m.pinned };
    if (star) return { ...base, star, pos: add(byId.get(star)!.pos, scale(offset, 0.06 + r() * 0.06)) };
    // Memories only about you circle the centre, still grouped by kind.
    const centre = scale(KIND_CENTER[m.kind] ?? KIND_CENTER.fact, 0.45);
    return { ...base, pos: add(centre, scale(offset, 0.05 + r() * 0.1)) };
  });

  // The newest artifacts, each beside the project it belongs to (or what else it is about).
  const project = new Map<string, string>();
  for (const pass of [true, false])
    for (const e of chosen)
      if ((e.type === "project") === pass) for (const id of e.file_ids) if (!project.has(id)) project.set(id, e.id);
  const ring = crystals(files, limits.crystals);
  const placed = ring.map((c, i): Crystal => {
    const star = project.get(c.id);
    if (!star) return c;
    const at = byId.get(star)!.pos;
    // Fan a project's artifacts out just beyond it, away from the centre.
    const a = (hash(c.id) % 360) * (Math.PI / 180) + i;
    const out = norm(at);
    const side = norm(cross(out, Math.abs(out[1]) > 0.95 ? [1, 0, 0] : [0, 1, 0]));
    const pos = add(add(at, scale(out, 0.12)), scale(side, Math.sin(a) * 0.07));
    return { ...c, star, pos: len(pos) > 0.9 * ORB_R ? scale(pos, (0.9 * ORB_R) / len(pos)) : pos };
  });

  return { stars, lines, memories: points, crystals: placed };
}

// --- Outside the orb: what Nexus does and has made --------------------------------------------

/** A routine or a software project as a small moon outside the shell: inside is what Nexus
 * knows, outside what it does for you (teams float further out while they work). Each moon
 * has an orbit of its own, so they drift like a natural system rather than sit on a rail, and
 * a thread to what it is about inside the orb. */
export interface Satellite {
  id: string;
  kind: "routine" | "project";
  name: string;
  /** "Every day at 08:00", or the project's stack. */
  detail: string;
  color: string;
  /** A routine that is on, or a project running as an app. */
  active: boolean;
  orbit: Orbit;
  /** Where it is at time 0 (orbitPos(orbit, 0)). */
  pos: Vec3;
  /** The star inside the orb its thread goes to: its topic, its project, or you. */
  home?: string;
}

export interface Orbit {
  radius: number;
  /** Tilt of the orbit's plane, and which way it is tilted, in radians. */
  tilt: number;
  node: number;
  phase: number;
  /** Radians per second; the sign is the direction. */
  speed: number;
}

/** Moons keep between these distances: clear of the shell, well inside the teams' band. */
export const MOON_NEAR = 1.22;
export const MOON_FAR = 1.62;

export const SATELLITE_COLOR = {
  routine: "#c4b5fd",
  routineFailed: "#fb7185",
  paused: "#64748b",
  project: "#fcd34d",
  projectIdle: "#cbd5e1",
};

export function orbitPos(o: Orbit, t: number): Vec3 {
  const a = o.phase + o.speed * t;
  const x = Math.cos(a) * o.radius;
  const z = Math.sin(a) * o.radius;
  // Tilt the plane about the x axis, then turn it about the vertical.
  const y = z * Math.sin(o.tilt);
  const z1 = z * Math.cos(o.tilt);
  const c = Math.cos(o.node);
  const sn = Math.sin(o.node);
  return [x * c + z1 * sn, y, -x * sn + z1 * c];
}

function orbitFor(id: string): Orbit {
  const r = rng(hash(id));
  return {
    radius: MOON_NEAR + (MOON_FAR - MOON_NEAR) * r(),
    tilt: (r() - 0.5) * 1.1,
    node: r() * Math.PI * 2,
    phase: r() * Math.PI * 2,
    speed: (0.035 + r() * 0.07) * (r() < 0.8 ? 1 : -1),
  };
}

const plain = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** Routines and projects as moons, newest projects and oldest routines first, at most `max`.
 * `homes` maps a routine's source pack ("pack:cooking") or a project's name to the names of
 * stars it belongs to; the first drawn star with one of those names is its home, else you. */
export function satellites(
  routines: Routine[],
  projects: Project[],
  stars: StarPoint[] = [],
  homes: (key: string) => string[] = () => [],
  max = 24,
): Satellite[] {
  const byName = new Map<string, string>();
  for (const st of stars) if (!st.self) byName.set(plain(st.name), st.id);
  const you = stars.find((st) => st.self)?.id;
  const homeOf = (keys: string[]) => {
    for (const k of keys) {
      const id = byName.get(plain(k));
      if (id) return id;
    }
    return you;
  };
  const rs = [...routines].sort((a, b) => ((a.created_at ?? "") < (b.created_at ?? "") ? -1 : 1));
  const ps = [...projects].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
  // Projects get up to half the slots when there are routines too; routines take the rest.
  const shownP = ps.slice(0, rs.length ? Math.floor(max / 2) : max);
  const shownR = rs.slice(0, max - shownP.length);
  const out: Satellite[] = shownR.map((r) => {
    const orbit = orbitFor(r.id);
    return {
      id: r.id,
      kind: "routine",
      name: r.name,
      detail: r.schedule_text,
      color: !r.enabled
        ? SATELLITE_COLOR.paused
        : r.last_status === "failed"
          ? SATELLITE_COLOR.routineFailed
          : SATELLITE_COLOR.routine,
      active: r.enabled,
      orbit,
      pos: orbitPos(orbit, 0),
      home: homeOf(homes(r.source)),
    };
  });
  for (const p of shownP) {
    const orbit = orbitFor(p.id);
    const live = p.app_state === "running";
    out.push({
      id: p.id,
      kind: "project",
      name: p.name,
      detail: p.stack ?? "project",
      color: live ? SATELLITE_COLOR.project : SATELLITE_COLOR.projectIdle,
      active: live,
      orbit,
      pos: orbitPos(orbit, 0),
      home: homeOf([p.name, ...homes(p.name)]),
    });
  }
  return out;
}

/** A gently bowed path from a to b (a quadratic curve through a point pushed sideways by
 * `bend` of the distance), as `steps` + 1 points. The side it bows to comes from `seed`, so
 * lines don't all curve the same way, which is what makes straight spokes look machine-made. */
export function curve(a: Vec3, b: Vec3, seed: number, bend = 0.16, steps = 10): Vec3[] {
  const d: Vec3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const l = len(d);
  if (l < 1e-6) return [a, b];
  const dir = scale(d, 1 / l);
  const up: Vec3 = Math.abs(dir[1]) > 0.9 ? [1, 0, 0] : [0, 1, 0];
  const side = norm(cross(dir, up));
  const r = rng(seed);
  const angle = r() * Math.PI * 2;
  const other = cross(dir, side);
  const push = add(scale(side, Math.cos(angle)), scale(other, Math.sin(angle)));
  const mid = add(scale(add(a, b), 0.5), scale(push, l * bend));
  const pts: Vec3[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const u = 1 - t;
    pts.push([
      u * u * a[0] + 2 * u * t * mid[0] + t * t * b[0],
      u * u * a[1] + 2 * u * t * mid[1] + t * t * b[1],
      u * u * a[2] + 2 * u * t * mid[2] + t * t * b[2],
    ]);
  }
  return pts;
}

/** How much of the shell to draw at a camera distance: all of it from the home view out,
 * fading to a faint outline when zoomed right in. */
export function shellFade(distance: number): number {
  const t = Math.min(1, Math.max(0, (distance - 2.2) / 1.6));
  return 0.18 + 0.82 * t * t * (3 - 2 * t);
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
  // With no teams out, sit closer so the constellation inside the orb is readable.
  const margin = clusterCount === 0 ? 1.15 : 1.08;
  return (extent / Math.min(tanV, tanH)) * margin;
}
