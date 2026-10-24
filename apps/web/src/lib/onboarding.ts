import type { Pack, PackRoutine } from "./types";

/** Packs that fit the answers, as the server picks them (nexus_common/profiles.py): the base
 * pack always, and any pack whose id or match names a chosen role or interest. Role packs
 * come first, then interests, then the base pack. */
export function packsFor(packs: Pack[], roles: string[], interests: string[]): Pack[] {
  const chosen = new Set([...roles, ...interests]);
  const rank = { role: 0, interest: 1, base: 2 };
  return packs
    .filter((p) => p.kind === "base" || [p.id, ...p.match].some((id) => chosen.has(id)))
    .sort((a, b) => rank[a.kind] - rank[b.kind]);
}

/** When a pack routine runs, given the person's day start: "day_start+30" with "07:30" is
 * "08:00"; a fixed "18:00" stays. */
export function routineTime(r: Pick<PackRoutine, "at" | "time">, dayStart: string): string {
  const m = /^day_start\s*(?:\+\s*(\d+))?$/.exec(r.at.trim());
  if (!m) return r.time;
  const [h, min] = dayStart.split(":").map((x) => parseInt(x, 10));
  const total = (h * 60 + min + parseInt(m[1] ?? "0", 10)) % (24 * 60);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

/** "Every day at 08:00" with the time swapped for the one it will really run at. */
export function scheduleText(r: Pick<PackRoutine, "schedule_text">, time: string): string {
  return r.schedule_text.replace(/\d{2}:\d{2}/, time);
}
