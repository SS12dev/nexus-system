/** Where things go in the universe view, and what each live thing looks like. Pure functions,
 * so they are tested without a browser. */
import * as THREE from "three";
import type { Universe, UniverseArea, UniverseStar } from "../lib/types";
import type { OrbCluster } from "./teams";

export type Vec3 = [number, number, number];

export const CORE_R = 5;
export const HUB_POS: Vec3 = [9.5, 1.5, 4];
export const HUB_SCALE = 0.75;
/** Where the camera sits by default; galaxy discs turn partly toward it so their arms read. */
export const DEFAULT_EYE: Vec3 = [0, 26, 66];

/** A galaxy's place in the world: centre, size and the orientation of its disc. */
export interface GalaxyLayout {
  area: UniverseArea;
  center: Vec3;
  radius: number;
  /** Euler angles (x, y, z) of the disc. */
  rot: Vec3;
  particles: number;
  /** Born in the last two days: drawn as a nebula igniting. */
  newborn: boolean;
  /** Merged away or emptied in the last two days: fading out. */
  ending: boolean;
}

const DAY = 86_400_000;

/** Galaxies on a ring around the core at their stored angle, sized by how much of life sits in
 * each. More areas, a wider ring. */
export function placeGalaxies(areas: UniverseArea[], budget: number, now = Date.now()): GalaxyLayout[] {
  if (!areas.length) return [];
  const live = areas.filter((a) => !a.ended_at);
  const ring = 22 + 1.6 * live.length;
  const biggest = Math.max(1, ...areas.map((a) => a.size));
  const total = areas.reduce((n, a) => n + Math.max(1, a.size), 0);
  return areas.map((a) => {
    const share = Math.sqrt(Math.max(1, a.size) / biggest);
    const radius = 3.5 + 5.5 * share;
    const y = Math.sin(a.angle * 3) * 4 + a.tilt * 8;
    const center: Vec3 = [Math.cos(a.angle) * ring, y, Math.sin(a.angle) * ring];
    return {
      area: a,
      center,
      radius,
      rot: discRotation(center, a.tilt),
      particles: Math.round(Math.max(1800, Math.min(8000, (budget * Math.max(1, a.size)) / total))),
      newborn: !a.ended_at && now - Date.parse(a.born_at) < 2 * DAY,
      ending: !!a.ended_at,
    };
  });
}

/** A disc turned partly toward the default camera, partly upright, each a little differently,
 * so its spiral reads from the start. */
export function discRotation(center: Vec3, tilt: number): Vec3 {
  const toEye = new THREE.Vector3(...DEFAULT_EYE).sub(new THREE.Vector3(...center)).normalize();
  const normal = toEye.multiplyScalar(0.65).add(new THREE.Vector3(0, 0.35, 0)).normalize();
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), normal);
  q.multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), tilt * 0.8));
  const e = new THREE.Euler().setFromQuaternion(q, "XYZ");
  return [e.x, e.y, e.z];
}

/** Matrix order for an XYZ Euler is Rx·Ry·Rz applied to the point, which is z first. */
export function applyEuler(v: Vec3, [ax, ay, az]: Vec3): Vec3 {
  let [x, y, z] = v;
  let c = Math.cos(az),
    s = Math.sin(az);
  [x, y] = [x * c - y * s, x * s + y * c];
  c = Math.cos(ay);
  s = Math.sin(ay);
  [x, z] = [x * c + z * s, -x * s + z * c];
  c = Math.cos(ax);
  s = Math.sin(ax);
  [y, z] = [y * c - z * s, y * s + z * c];
  return [x, y, z];
}

/** A star's place in the world: its spot in the disc (radius 0..1, angle, height), turned with
 * the disc and moved to the galaxy's centre. */
export function starWorld(g: GalaxyLayout, pos: [number, number, number]): Vec3 {
  const [r, a, h] = pos;
  const local: Vec3 = [Math.cos(a) * r * g.radius, h * g.radius, Math.sin(a) * r * g.radius];
  const w = applyEuler(local, g.rot);
  return [w[0] + g.center[0], w[1] + g.center[1], w[2] + g.center[2]];
}

/** The stars worth drawing as their own objects, most central first, a few per galaxy; the
 * rest are part of the galaxy's dust. */
export function visibleStars(stars: UniverseStar[], perGalaxy: number): UniverseStar[] {
  const by = new Map<string, UniverseStar[]>();
  for (const s of stars) {
    if (!s.area || !s.pos) continue;
    const list = by.get(s.area) ?? [];
    list.push(s);
    by.set(s.area, list);
  }
  return [...by.values()].flatMap((list) =>
    [...list].sort((a, b) => b.salience - a.salience || b.mentions - a.mentions).slice(0, perGalaxy),
  );
}

export type StarLook = "star" | "place" | "dust" | "site" | "outpost" | "pulsar";

/** What a star is drawn as. */
export function starLook(s: UniverseStar): StarLook {
  if (s.source?.kind === "routine" || s.kind === "routine") return "pulsar";
  if (s.kind === "app") return "outpost";
  if (s.source?.kind === "project" || s.source?.kind === "folder" || s.kind === "project") return "site";
  if (s.kind === "place") return "place";
  if (["topic", "tool", "skill", "document", "source", "other"].includes(s.kind) && s.mentions <= 1) return "dust";
  return "star";
}

const KIND_HUE: Record<string, number> = {
  person: 0.09,
  pet: 0.09,
  organisation: 0.6,
  group: 0.6,
  place: 0.93,
  topic: 0.55,
  tool: 0.5,
  project: 0.12,
  app: 0.42,
  routine: 0.75,
};

/** A star's colour: its kind's hue, nudged toward its galaxy's. */
export function starHue(s: UniverseStar, areaHue: number): number {
  const k = KIND_HUE[s.kind] ?? areaHue;
  let d = ((areaHue - k + 1.5) % 1) - 0.5;
  d *= 0.35;
  return (k + d + 1) % 1;
}

// --- Teams as ships and crews -------------------------------------------------------------------

export type ShipType = "scout" | "freighter" | "cutter" | "shuttle" | "courier" | "carrier";
export type CrewRole = "research" | "architect" | "designer" | "builder" | "reviewer" | "tester" | "deployer" | "scribe" | "courier";

/** The character an agent's role gets. */
export function crewRole(role: string): CrewRole {
  const r = role.toLowerCase();
  if (/research|scout|search|investig|analys(t|is)/.test(r)) return "research";
  if (/plan|architect|lead|design(er)? of system|strateg/.test(r)) return "architect";
  if (/\bui\b|ux|design|visual|front/.test(r)) return "designer";
  if (/review|critic|check|audit|qa\b/.test(r)) return "reviewer";
  if (/test/.test(r)) return "tester";
  if (/deploy|release|ops|ship/.test(r)) return "deployer";
  if (/build|develop|engineer|code|backend|program|implement/.test(r)) return "builder";
  if (/routine|courier|remind|notif/.test(r)) return "courier";
  return "scribe";
}

/** The ship a team flies, from who is on it. */
export function shipType(c: OrbCluster): ShipType {
  const roles = c.nodes.map((n) => crewRole(n.role));
  const has = (r: CrewRole) => roles.includes(r);
  if (c.nodes.length >= 6) return "carrier";
  if (has("builder") || (has("designer") && has("architect"))) return "freighter";
  if (has("deployer")) return "shuttle";
  if (roles.length && roles.every((r) => r === "reviewer" || r === "tester")) return "cutter";
  if (has("research")) return "scout";
  return "courier";
}

/** Engine trail colour: blue working, amber waiting for you, red failed, green done. */
export function stateColor(status: string): string {
  if (status === "awaiting_approval") return "#ffb347";
  if (status === "failed" || status === "cancelled") return "#ff5a6a";
  if (status === "completed") return "#5fe39a";
  if (status === "running" || status === "queued") return "#5fb4ff";
  return "#8aa0c8";
}

/** Agent states, as the old orb named them: working, using a tool, waiting, done, failed. */
export function crewLight(status: string): { color: string; on: number } {
  if (status === "waiting") return { color: "#ffb347", on: 4 };
  if (status === "failed") return { color: "#ff5a6a", on: 4 };
  if (status === "working") return { color: "#5fe3ff", on: 3 };
  if (status === "running") return { color: "#5fb4ff", on: 2 };
  return { color: "#5fb4ff", on: 0 };
}

/** Model tier as size: a nano agent is a small drone, a max one a big robot. */
export function tierScale(tier: string): number {
  return { nano: 0.7, mini: 0.85, pro: 1.05, max: 1.2 }[tier] ?? 0.9;
}

export const plain = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** Where a team flies: the star it is working on (a project or app of the same name, or one the
 * team's name mentions), else null to hold station by the hub. */
export function teamTarget(c: OrbCluster, stars: UniverseStar[]): UniverseStar | null {
  const name = plain(c.name);
  const placed = stars.filter((s) => s.area && s.pos);
  const exact = placed.find((s) => plain(s.name) === name);
  if (exact) return exact;
  const words = (s: string) => plain(s).split(" ").filter((w) => w.length > 3);
  const mine = new Set(words(c.name));
  let best: UniverseStar | null = null;
  let score = 0;
  for (const s of placed) {
    const w = words(s.name);
    if (!w.length) continue;
    const hit = w.filter((x) => mine.has(x)).length / w.length;
    const bonus = s.source ? 0.2 : 0;
    if (hit >= 0.5 && hit + bonus > score) {
      best = s;
      score = hit + bonus;
    }
  }
  return best;
}

// --- The hub's decks ----------------------------------------------------------------------------

export type Deck = "comms" | "bridge" | "hangar" | "workshop" | "archive" | "clock" | "reactor" | "foundation" | "scaffold" | "admin";

export const DECKS: { id: Deck; label: string; layer: string; admin?: boolean; link?: string }[] = [
  { id: "comms", label: "Comms array", layer: "Chat, app, email and push" },
  { id: "bridge", label: "Command bridge", layer: "Orion thinking: plans, picks teams and models" },
  { id: "hangar", label: "Hangar ring", layer: "Agents and teams", link: "/agents" },
  { id: "workshop", label: "Workshop", layer: "Tools, skills and the code workbench", link: "/skills" },
  { id: "archive", label: "Archive vault", layer: "Memories, files and knowledge", link: "/mind" },
  { id: "clock", label: "Clock ring", layer: "Routines and schedules", link: "/routines" },
  { id: "reactor", label: "Reactor", layer: "The four model tiers" },
  { id: "foundation", label: "Foundation", layer: "Database, cache, storage and tunnel" },
  { id: "scaffold", label: "Scaffold", layer: "Self-improvements being built", admin: true, link: "/admin?tab=improvements" },
  { id: "admin", label: "Admin wing", layer: "People, monitoring, configuration and approvals", admin: true, link: "/admin" },
];

/** How lit each deck is (0 idle .. 1 busy) from what Nexus is doing right now. */
export function deckActivity(x: {
  assistant: "idle" | "thinking" | "streaming" | string;
  teams: OrbCluster[];
  recalling: boolean;
  routinesDue: boolean;
}): Record<Deck, number> {
  const working = x.teams.filter((c) => ["queued", "running", "awaiting_approval"].includes(c.status));
  const tooling = working.some((c) => c.nodes.some((n) => n.status === "working" && n.tool));
  const busy = x.assistant !== "idle";
  return {
    comms: busy ? 1 : 0.25,
    bridge: x.assistant === "thinking" ? 1 : busy ? 0.7 : 0.25,
    hangar: working.length ? Math.min(1, 0.5 + working.length * 0.25) : 0.2,
    workshop: tooling ? 1 : 0.2,
    archive: x.recalling ? 1 : 0.3,
    clock: x.routinesDue ? 1 : 0.35,
    reactor: busy || working.length ? 1 : 0.35,
    foundation: 0.6,
    scaffold: 0.4,
    admin: 0.5,
  };
}

/** Each reactor cell (nano, mini, pro, max) lit while an agent on that tier works. */
export function tiersBusy(teams: OrbCluster[], assistantBusy: boolean): [number, number, number, number] {
  const tiers = ["nano", "mini", "pro", "max"];
  const busy = tiers.map((t) =>
    teams.some((c) => c.nodes.some((n) => n.tier === t && ["working", "running"].includes(n.status))) ? 1 : 0.25,
  );
  // The assistant itself runs on the pro tier.
  if (assistantBusy) busy[2] = 1;
  return busy as [number, number, number, number];
}

/** One line for a galaxy on hover: its name and what it holds. */
export function areaLine(a: UniverseArea, stars: UniverseStar[]): string {
  const n = stars.filter((s) => s.area === a.id).length || a.size;
  const busy = a.activity >= 0.5 ? ", busy this month" : a.activity < 0.1 ? ", quiet lately" : "";
  return `${n} thing${n === 1 ? "" : "s"}${busy}`;
}

/** The empty state: nothing known yet. */
export function isEmpty(u: Universe | null): boolean {
  return !u || (!u.areas.length && !u.core.neurons.length);
}
