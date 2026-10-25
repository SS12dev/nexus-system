import type { Widget } from "./types";

/** How long a widget keeps ringing on screen if nobody answers it. */
export const RING_MS = 30 * 60 * 1000;

/** 75 -> "1:15", 3725 -> "1:02:05". */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return `${h ? `${h}:` : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

/** Seconds left on a timer at `now` (ms). */
export function timerLeft(w: Widget, now: number): number {
  const s = w.state;
  if (s.status === "running" && s.ends_at) return Math.max(0, (Date.parse(s.ends_at) - now) / 1000);
  if (s.status === "paused") return s.remaining_s ?? s.duration_s ?? 0;
  return 0;
}

/** Seconds on a stopwatch at `now` (ms). */
export function elapsed(w: Widget, now: number): number {
  const s = w.state;
  const base = s.elapsed_s ?? 0;
  return s.status === "running" && s.started_at ? base + (now - Date.parse(s.started_at)) / 1000 : base;
}

/** Whether a widget is ringing now: the server said so (and not too long ago), or a running
 * timer has reached zero here before the server's next pass. */
export function isRinging(w: Widget, now: number): boolean {
  if (w.state.status === "ringing") return !w.state.rang_at || now - Date.parse(w.state.rang_at) < RING_MS;
  return w.kind === "timer" && w.state.status === "running" && !!w.state.ends_at && Date.parse(w.state.ends_at) <= now;
}

const DAY_NAMES: Record<string, string> = { MO: "Mon", TU: "Tue", WE: "Wed", TH: "Thu", FR: "Fri", SA: "Sat", SU: "Sun" };

/** An alarm's or reminder's repeat in a few words: "Every day", "Weekdays", "Mon, Thu". */
export function repeatText(rule: string | null | undefined): string {
  if (!rule) return "Once";
  const parts = Object.fromEntries(rule.split(";").map((p) => p.split("=") as [string, string]));
  if (parts.FREQ === "DAILY") return "Every day";
  if (parts.FREQ === "MONTHLY") return `Monthly on day ${parts.BYMONTHDAY}`;
  const days = (parts.BYDAY ?? "").split(",").filter(Boolean);
  if (days.join() === "MO,TU,WE,TH,FR") return "Weekdays";
  if (days.join() === "SA,SU") return "Weekends";
  return days.map((d) => DAY_NAMES[d] ?? d).join(", ") || "Repeats";
}

/** "Asia/Kolkata" -> "Kolkata", "America/New_York" -> "New York". */
export function zoneCity(zone: string): string {
  return (zone.split("/").pop() ?? zone).replace(/_/g, " ");
}

/** The time in a zone, "21:05". */
export function timeIn(zone: string, now: number): string {
  try {
    return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: zone }).format(now);
  } catch {
    return "--:--";
  }
}

/** When a widget next rings, in the viewer's own clock: "21:00" today, else "Mon 21:00". */
export function nextRing(iso: string | null, now: number): string {
  if (!iso) return "";
  const t = new Date(iso);
  const time = t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return new Date(now).toDateString() === t.toDateString()
    ? time
    : `${t.toLocaleDateString("en-GB", { weekday: "short" })} ${time}`;
}
