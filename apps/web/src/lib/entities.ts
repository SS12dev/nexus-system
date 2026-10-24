import type { EntityType, GraphRelation } from "./types";

/** Each entity type's tint in the orb and on recall chips. Quiet on purpose: in the orb only
 * teams and recalls change colour, so type colours stay in the background. */
export const ENTITY_COLOR: Record<EntityType, string> = {
  person: "#f9a8d4",
  project: "#93c5fd",
  organisation: "#fdba74",
  place: "#86efac",
  topic: "#c4b5fd",
  tool: "#a5b4fc",
  other: "#cbd5e1",
};

/** What a recall lights up in the orb, and the chips under a reply. */
export const RECALL_COLOR = "#fde047";

/** Entity types in the order they're listed, with their chip colour. */
export const ENTITY_TYPES: Record<EntityType, { label: string; plural: string; tone: string }> = {
  person: { label: "Person", plural: "People", tone: "bg-pink-500/15 text-pink-300" },
  project: { label: "Project", plural: "Projects", tone: "bg-sky-500/15 text-sky-300" },
  organisation: { label: "Organisation", plural: "Organisations", tone: "bg-amber-500/15 text-amber-300" },
  place: { label: "Place", plural: "Places", tone: "bg-emerald-500/15 text-emerald-300" },
  topic: { label: "Topic", plural: "Topics", tone: "bg-violet-500/15 text-violet-300" },
  tool: { label: "Tool", plural: "Tools", tone: "bg-indigo-500/15 text-indigo-300" },
  other: { label: "Other", plural: "Other", tone: "bg-slate-500/20 text-slate-300" },
};

/** One relation as a sentence: its own wording when the model gave one. */
export function relationText(r: GraphRelation, names: Map<string, string>): string {
  return r.fact || `${names.get(r.src) ?? "?"} ${r.predicate} ${names.get(r.dst) ?? "?"}`;
}
