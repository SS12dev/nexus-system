import type { EntityType, GraphEntity, GraphRelation } from "./types";

export type KnowledgeSort = "mentions" | "recent" | "name";

/** How often something comes up: memories, chats (live and summarised) and files. */
export const mentions = (e: GraphEntity) => e.memories + e.chats + e.past_chats + e.files;

/** The Knowledge page's list: what matches the search (name, other names or summary) and the
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

export interface EgoNode {
  id: string;
  x: number;
  y: number;
  /** Every link to it ended. */
  past: boolean;
  /** The predicates linking it, for the line's label. */
  label: string;
}

/** The small graph on the detail panel: the picked entity in the middle and up to `max`
 * neighbours on a ring around it, current links before past ones. Several relations to the same
 * neighbour share one line. Coordinates are in a 0..1 box. */
export function egoLayout(id: string, relations: GraphRelation[], max = 14): { nodes: EgoNode[]; more: number } {
  const { current, past } = linksOf(id, relations);
  const byOther = new Map<string, { past: boolean; preds: string[] }>();
  for (const l of [...current, ...past]) {
    if (l.other === id) continue;
    const n = byOther.get(l.other) ?? { past: true, preds: [] };
    n.past &&= !!l.relation.valid_to;
    if (!n.preds.includes(l.relation.predicate)) n.preds.push(l.relation.predicate);
    byOther.set(l.other, n);
  }
  const all = [...byOther.entries()];
  const shown = all.slice(0, max);
  const nodes = shown.map(([other, n], i) => {
    // Start at the top and go round; a ring of one or two still reads left to right.
    const a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(shown.length, 1);
    return {
      id: other,
      x: 0.5 + 0.38 * Math.cos(a),
      y: 0.5 + 0.38 * Math.sin(a),
      past: n.past,
      label: n.preds.join(", "),
    };
  });
  return { nodes, more: all.length - shown.length };
}
