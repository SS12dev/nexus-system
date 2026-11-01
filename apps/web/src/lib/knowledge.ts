import type { EntityType, GraphEntity, GraphRelation, Universe, UniverseArea, UniverseStar } from "./types";

export type KnowledgeSort = "mentions" | "recent" | "name";

/** How often something comes up: memories, chats (live and summarised) and files. */
export const mentions = (e: GraphEntity) => e.memories + e.chats + e.past_chats + e.files;

/** The Mind page's search: what matches the search (name, other names or summary) and the
 * type filter, sorted. "You" stays first whatever the sort, and only when nothing filters it out. */
export function filterEntities(
  entities: GraphEntity[],
  { query, type, sort }: { query: string; type: EntityType | "all"; sort: KnowledgeSort },
): GraphEntity[] {
  const q = query.trim().toLowerCase();
  const hit = (e: GraphEntity) =>
    !q ||
    e.name.toLowerCase().includes(q) ||
    e.summary.toLowerCase().includes(q) ||
    e.aliases.some((a) => a.toLowerCase().includes(q));
  const order: Record<KnowledgeSort, (a: GraphEntity, b: GraphEntity) => number> = {
    mentions: (a, b) => mentions(b) - mentions(a) || a.name.localeCompare(b.name),
    recent: (a, b) => b.seen_at.localeCompare(a.seen_at),
    name: (a, b) => a.name.localeCompare(b.name),
  };
  const rest = entities.filter((e) => !e.is_self && (type === "all" || e.type === type) && hit(e));
  rest.sort(order[sort]);
  const me = entities.find((e) => e.is_self);
  return me && type === "all" && hit(me) ? [me, ...rest] : rest;
}

/** One link from the picked entity to a neighbour, as the detail panel and graph show it. */
export interface Link {
  relation: GraphRelation;
  other: string;
  /** True when the picked entity is the subject ("You work at UST"). */
  outgoing: boolean;
}

/** Everything the entity is linked to, current first (newest first), then the ones that ended. */
export function linksOf(id: string, relations: GraphRelation[]): { current: Link[]; past: Link[] } {
  const links = relations
    .filter((r) => r.src === id || r.dst === id)
    .map((r) => ({ relation: r, other: r.src === id ? r.dst : r.src, outgoing: r.src === id }));
  const newest = (a: Link, b: Link) => b.relation.valid_from.localeCompare(a.relation.valid_from);
  return {
    current: links.filter((l) => !l.relation.valid_to).sort(newest),
    past: links
      .filter((l) => l.relation.valid_to)
      .sort((a, b) => b.relation.valid_to!.localeCompare(a.relation.valid_to!)),
  };
}

// --- The Mind page ----------------------------------------------------------------------------

/** The parts of your core, deepest first, and the ontology kinds each holds. */
export const CORE_GROUPS: { id: string; label: string; help: string; kinds: string[] }[] = [
  { id: "values", label: "Values and beliefs", help: "What matters to you and what you hold true", kinds: ["value", "belief"] },
  { id: "goals", label: "Goals", help: "What you're working towards", kinds: ["goal"] },
  { id: "traits", label: "Traits and skills", help: "How you are and what you're good at", kinds: ["trait", "skill"] },
  { id: "interests", label: "Interests", help: "What you like spending time on", kinds: ["interest"] },
  { id: "habits", label: "Preferences and habits", help: "How you like things and what you do often", kinds: ["preference", "habit", "state"] },
  { id: "style", label: "How you communicate", help: "How you like to talk and be talked to", kinds: ["communication_style"] },
];

const CORE_KINDS = new Set(CORE_GROUPS.flatMap((g) => g.kinds));
export const isCore = (e: GraphEntity) => !e.is_self && !!e.kind && CORE_KINDS.has(e.kind);

/** Your core, grouped, most mentioned first in each group; empty groups left out. */
export function coreGroups(entities: GraphEntity[]) {
  return CORE_GROUPS.map((g) => ({
    ...g,
    items: entities
      .filter((e) => !e.is_self && e.kind && g.kinds.includes(e.kind))
      .sort((a, b) => mentions(b) - mentions(a) || a.name.localeCompare(b.name)),
  })).filter((g) => g.items.length);
}

/** How sure Nexus is of something, from how often it has come up. */
export function sureness(e: GraphEntity): { label: string; level: 1 | 2 | 3 } {
  const n = mentions(e);
  if (n >= 5) return { label: "Sure", level: 3 };
  if (n >= 2) return { label: "Fairly sure", level: 2 };
  return { label: "A hunch", level: 1 };
}

const WEEK = 7 * 24 * 3600 * 1000;

export interface AreaCard {
  area: UniverseArea;
  count: number;
  /** Its most central things. */
  top: UniverseStar[];
  isNew: boolean;
  fading: boolean;
}

/** One card per live area, biggest first. */
export function areaCards(u: Universe | null, now = Date.now(), topN = 4): AreaCard[] {
  if (!u) return [];
  return u.areas
    .filter((a) => !a.ended_at)
    .map((area) => {
      const stars = u.stars.filter((s) => s.area === area.id).sort((a, b) => b.salience - a.salience);
      return {
        area,
        count: stars.length,
        top: stars.slice(0, topN),
        isNew: now - Date.parse(area.born_at) < WEEK,
        fading: area.activity < 0.1,
      };
    })
    .sort((a, b) => b.count - a.count || a.area.name.localeCompare(b.area.name));
}

/** An area's things grouped by kind ("person", "project"...), biggest group first, each
 * group's most central first. */
export function byKind(stars: UniverseStar[]): [string, UniverseStar[]][] {
  const groups = new Map<string, UniverseStar[]>();
  for (const s of stars) groups.set(s.kind, [...(groups.get(s.kind) ?? []), s]);
  for (const list of groups.values()) list.sort((a, b) => b.salience - a.salience || a.name.localeCompare(b.name));
  return [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
}

/** "communication_style" -> "Communication styles". */
export function kindLabel(kind: string, plural = true): string {
  const w = kind.replace(/_/g, " ");
  const p = !plural ? w : w === "person" ? "people" : /(s|x|ch|sh)$/.test(w) ? `${w}es` : /[^aeiou]y$/.test(w) ? `${w.slice(0, -1)}ies` : `${w}s`;
  return p[0].toUpperCase() + p.slice(1);
}
