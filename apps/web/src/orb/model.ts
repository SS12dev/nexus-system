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
  Folder,
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

/** A person, project, place, topic or tool Nexus knows about, or the user at the centre. */
export interface StarPoint {
  id: string;
  name: string;
  type: EntityType;
  summary: string;
  self: boolean;
  /** How big it is drawn: its connections, its importance or its mentions (OrbView.size). */
  weight: number;
  pos: Vec3;
  /** From the ontology, once the graph has it: the full type ("pet", "value"), the shell (0
   * you .. 4 knowledge) and the part of life it belongs to. */
  kind?: string;
  shell?: number;
  domain?: string | null;
}

export interface StarLine {
  a: string;
  b: string;
  /** The relation stopped being true; drawn fainter. */
  ended: boolean;
  /** No relation joins it to anything drawn, but your memories are about it: a faint line to
   * you, so no star floats loose. */
  implied?: boolean;
  /** The relation's family (its colour), weight (its thickness) and wording (on hover). */
  family?: string;
  weight?: number;
  label?: string;
}

/** How the viewer likes the constellation drawn (the Key's controls, kept in the browser). */
export interface OrbView {
  /** What makes a star big: how connected it is, how central to you (nightly ranking), or how
   * often memories, chats and files mention it. */
  size: "connections" | "importance" | "mentions";
  /** 0 to 1: how tightly each cluster pulls together within its part of life. */
  gravity: number;
}

export const DEFAULT_VIEW: OrbView = { size: "connections", gravity: 0.6 };

/** Everything inside the orb: who you are. You at the centre, the people, organisations,
 * places, interests and tools Nexus knows about you, and your memories. Without a knowledge
 * graph yet, memories cluster by kind. What has been made (projects and their files) is
 * outside, as moons (see satellites). */
export interface Inside {
  stars: StarPoint[];
  lines: StarLine[];
  memories: MemoryPoint[];
}

/** Entity types that are work rather than profile: drawn outside the orb as project moons,
 * never as stars inside it. */
export const OUTSIDE_TYPES = new Set<EntityType>(["project"]);

/** A file name or path ("out/tray.3mf", "rocket_model.py"): something a team made, never part
 * of who you are. */
const FILE_LIKE =
  /[\\/]|\.(3mf|stl|stp|step|obj|glb|gltf|scad|png|jpe?g|webp|svg|gif|pdf|md|txt|csv|json|ya?ml|py|ipynb|html?|docx?|xlsx?|pptx?|zip)$/i;

/** The entities that make up your profile, drawn inside the orb: what your memories are about,
 * or what is related to you directly ("Shivam works at UST"). Things that only came up while
 * teams worked (a library they used, a film a model was based on, the files they made) and
 * projects stay out: they are details of the work, which is outside. */
export function profileEntities(graph: KnowledgeGraph | null): KnowledgeGraph["entities"] {
  if (!graph) return [];
  const self = graph.entities.find((e) => e.is_self)?.id;
  const near = new Set<string>();
  for (const r of graph.relations)
    if (!r.valid_to && self && (r.src === self || r.dst === self)) near.add(r.src === self ? r.dst : r.src);
  return graph.entities.filter(
    (e) =>
      !e.is_self &&
      !OUTSIDE_TYPES.has(e.type) &&
      !FILE_LIKE.test(e.name.trim()) &&
      (e.memories > 0 || near.has(e.id)),
  );
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

/** Lay out the user's profile from the knowledge graph: the user at the centre, the most
 * mentioned people, places, interests and tools around them grouped by type, and memories as
 * satellites of what they're about. Projects stay out (they are moons outside). `focus` ids
 * (what a reply just recalled) are always drawn. */
export function inside(
  graph: KnowledgeGraph | null,
  memories: Memory[],
  limits: { stars: number; memories: number },
  focus: Set<string> = new Set(),
  view: OrbView = DEFAULT_VIEW,
): Inside {
  const others = profileEntities(graph);
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
    return { stars: [you], lines: [], memories: memoryPoints(memories, limits.memories) };

  const ranked = [...others].sort(
    (a, b) =>
      Number(focus.has(b.id)) - Number(focus.has(a.id)) ||
      weightOf(b) - weightOf(a) ||
      (a.seen_at < b.seen_at ? 1 : -1),
  );
  const chosen = ranked.slice(0, limits.stars);
  const ontology = chosen.some((e) => e.shell != null);
  const stars: StarPoint[] = ontology
    ? concentric(chosen, graph.relations, self?.id ?? YOU, view)
    : legacyStars(chosen);
  stars.unshift(you);
  const byId = new Map(stars.map((s) => [s.id, s]));

  const lines: StarLine[] = graph.relations
    .filter((rel) => rel.src !== rel.dst && byId.has(rel.src) && byId.has(rel.dst))
    .map((rel) =>
      rel.family
        ? {
            a: rel.src,
            b: rel.dst,
            ended: !!rel.valid_to,
            family: rel.family,
            weight: rel.weight ?? 0.5,
            label: rel.label || rel.predicate,
          }
        : { a: rel.src, b: rel.dst, ended: !!rel.valid_to },
    );
  // A star whose relations all lead to things not drawn (a project, a team's tool) would float
  // loose; your memories are about it, so it hangs from you.
  const joined = new Set(lines.flatMap((l) => [l.a, l.b]));
  for (const st of stars)
    if (!st.self && !joined.has(st.id)) lines.push({ a: you.id, b: st.id, ended: false, implied: true });

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

  return { stars, lines, memories: points };
}

/** The constellation before the graph had shells: stars spread evenly over a sphere, grouped
 * by type, the most mentioned nearest you. */
function legacyStars(chosen: KnowledgeGraph["entities"]): StarPoint[] {
  const heaviest = Math.max(1, ...chosen.map(weightOf));
  // Directions spread evenly over a sphere (a Fibonacci lattice), handed out by type so each
  // type keeps its own region, and turned so the first type faces its usual direction. Plain
  // per-type directions bunched up when most stars shared a type (a new user's topics).
  const order = [...chosen].sort(
    (a, b) => TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type) || hash(a.id) - hash(b.id),
  );
  const lattice = new Map(order.map((e, i) => [e.id, fibonacciDir(i, order.length)]));
  return chosen.map((e) => {
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
}

// --- Concentric layout ------------------------------------------------------------------------

/** Each shell's distance from the centre, inside the orb: 1 who you are (traits, values,
 * goals), 2 the people and places in your life, 3 what you do, 4 what you know. */
export const SHELL_R = [0, 0.3, 0.46, 0.6, 0.74];
/** Parts of life, each a slice of the sphere (longitude), in this order round the equator. */
export const DOMAINS = ["work", "learning", "home", "health", "social", "hobbies", "finance"] as const;
const UNSORTED = "other";

/** Stars on concentric shells: the radius says how close a thing is to who you are, the
 * longitude which part of life it belongs to, and things the nightly pass clustered together
 * sit together within that slice, as tightly as `view.gravity` says. Only the parts of life
 * that have something take room, so a new graph isn't one thin wedge. */
export function concentric(
  chosen: KnowledgeGraph["entities"],
  relations: KnowledgeGraph["relations"],
  selfId: string,
  view: OrbView,
): StarPoint[] {
  const ids = new Set(chosen.map((e) => e.id));
  const degree = new Map<string, number>();
  const neighbours = new Map<string, { id: string; w: number }[]>();
  for (const r of relations) {
    if (r.valid_to || r.src === r.dst) continue;
    for (const [a, b] of [
      [r.src, r.dst],
      [r.dst, r.src],
    ]) {
      degree.set(a, (degree.get(a) ?? 0) + 1);
      if (ids.has(b) && b !== selfId) {
        const list = neighbours.get(a) ?? [];
        list.push({ id: b, w: r.weight ?? 0.5 });
        neighbours.set(a, list);
      }
    }
  }
  // A thing without a part of life of its own takes its strongest neighbour's.
  const own = new Map(chosen.map((e) => [e.id, e.domain ?? null]));
  const domainOf = (id: string): string => {
    const d = own.get(id);
    if (d) return d;
    const best = [...(neighbours.get(id) ?? [])].sort((a, b) => b.w - a.w).find((n) => own.get(n.id));
    return (best && own.get(best.id)) || UNSORTED;
  };
  const domains = new Map(chosen.map((e) => [e.id, domainOf(e.id)]));

  // Slices: as wide as the square root of what is in them, in a fixed order.
  const order = [...DOMAINS, UNSORTED];
  const counts = new Map<string, number>();
  for (const d of domains.values()) counts.set(d, (counts.get(d) ?? 0) + 1);
  const used = order.filter((d) => counts.has(d));
  const total = used.reduce((t, d) => t + Math.sqrt(counts.get(d)!), 0) || 1;
  const slice = new Map<string, [number, number]>();
  let at = 0.3;
  for (const d of used) {
    const width = (Math.sqrt(counts.get(d)!) / total) * Math.PI * 2;
    slice.set(d, [at, width]);
    at += width;
  }

  // Each cluster's centre within its slice: spread along it, at a latitude of its own.
  const clusters = new Map<string, string[]>();
  for (const e of chosen) {
    const key = `${domains.get(e.id)}:${e.community ?? `solo-${e.id}`}`;
    clusters.set(key, [...(clusters.get(key) ?? []), e.id]);
  }
  const centre = new Map<string, [number, number]>();
  for (const d of used) {
    const keys = [...clusters.keys()].filter((k) => k.startsWith(`${d}:`)).sort();
    const [start, width] = slice.get(d)!;
    keys.forEach((k, i) => {
      const lat = ((hash(k) % 1000) / 1000 - 0.5) * 1.1;
      centre.set(k, [start + (width * (i + 0.5)) / keys.length, lat]);
    });
  }

  const sizeOf = (e: KnowledgeGraph["entities"][number]) =>
    view.size === "importance" && e.salience
      ? e.salience * 10
      : view.size === "mentions"
        ? weightOf(e)
        : (degree.get(e.id) ?? 0);
  const biggest = Math.max(1, ...chosen.map(sizeOf));
  const g = Math.min(1, Math.max(0, view.gravity));

  return chosen.map((e) => {
    const r = rng(hash(e.id));
    const d = domains.get(e.id)!;
    const [start, width] = slice.get(d)!;
    const key = `${d}:${e.community ?? `solo-${e.id}`}`;
    const [cLon, cLat] = centre.get(key)!;
    // Its own place anywhere in the slice, pulled toward its cluster's centre.
    const lon0 = start + width * (0.1 + 0.8 * r());
    const lat0 = (r() - 0.5) * 1.6;
    const lon = lon0 + (cLon - lon0) * g + (r() - 0.5) * 0.12;
    const lat = lat0 + (cLat - lat0) * g + (r() - 0.5) * 0.12;
    const shell = Math.min(4, Math.max(1, e.shell ?? 4));
    // Within its shell, bigger things sit a little nearer you.
    const radius = SHELL_R[shell] + (r() - 0.5) * 0.05 - 0.03 * (sizeOf(e) / biggest);
    const pos: Vec3 = [
      radius * Math.cos(lat) * Math.cos(lon),
      radius * Math.sin(lat) * 0.8,
      radius * Math.cos(lat) * Math.sin(lon),
    ];
    return {
      id: e.id,
      name: e.name,
      type: e.type,
      summary: e.summary,
      self: false,
      weight: sizeOf(e),
      pos,
      kind: e.kind ?? e.type,
      shell,
      domain: d === UNSORTED ? null : d,
    };
  });
}

// --- Outside the orb: what Nexus does and has made --------------------------------------------

/** A routine or a project as a small moon outside the shell: inside is who you are, outside
 * what Nexus does and has made for you (teams float further out while they work). A project is
 * a software project or a folder of files made for one piece of work, so a rocket model's
 * .3mf, .step and .stl are one moon, not three. Each moon has an orbit of its own, so they
 * drift like a natural system rather than sit on a rail, and may have a thread to what it is
 * about inside the orb. */
export interface Satellite {
  id: string;
  kind: "routine" | "project";
  name: string;
  /** "Every day at 08:00", the project's stack, or how many files it holds. */
  detail: string;
  color: string;
  /** A routine that is on, or a project running as an app. */
  active: boolean;
  /** A software project (an app); otherwise a folder of files. */
  app: boolean;
  /** The project's folders and how many files they hold. */
  folderIds: string[];
  files: number;
  orbit: Orbit;
  /** Where it is at time 0 (orbitPos(orbit, 0)). */
  pos: Vec3;
  /** The star inside the orb its thread goes to: a routine's topic (or you), or what a
   * project is about. A project about nothing drawn inside has no thread. */
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
  /** A folder of files: the colour artifacts always had. */
  files: "#c084fc",
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

export const plain = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

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

/** Routines and projects as moons, newest projects and oldest routines first, at most `max`.
 * `homes` maps a routine's source pack ("pack:cooking") or a project's name to the names of
 * stars it belongs to; the first drawn star with one of those names is its home. A routine
 * with none hangs from you; a project with none has no thread, so a long list of projects
 * doesn't crowd the centre with lines. */
export function satellites(
  routines: Routine[],
  projects: Work[],
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
    return undefined;
  };
  const rs = [...routines].sort((a, b) => ((a.created_at ?? "") < (b.created_at ?? "") ? -1 : 1));
  // Projects get up to half the slots when there are routines too; routines take the rest.
  const shownP = projects.slice(0, rs.length ? Math.floor(max / 2) : max);
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
      app: false,
      folderIds: [],
      files: 0,
      orbit,
      pos: orbitPos(orbit, 0),
      home: homeOf(homes(r.source)) ?? you,
    };
  });
  for (const p of shownP) {
    const orbit = orbitFor(p.id);
    out.push({
      id: p.id,
      kind: "project",
      name: p.name,
      detail: p.detail,
      color: !p.app ? SATELLITE_COLOR.files : p.live ? SATELLITE_COLOR.project : SATELLITE_COLOR.projectIdle,
      active: p.live,
      app: p.app,
      folderIds: p.folderIds,
      files: p.files,
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
