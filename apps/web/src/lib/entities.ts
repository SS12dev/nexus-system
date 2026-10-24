import type { EntityType } from "./types";

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
