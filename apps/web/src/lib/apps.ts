import type { AppItem } from "./types";

/** How an app's state reads on the Apps page, and its colour. */
export const APP_STATE: Record<string, { label: string; style: string }> = {
  running: { label: "Running", style: "text-emerald-400" },
  building: { label: "Deploying", style: "text-sky-300" },
  stopped: { label: "Stopped", style: "text-slate-400" },
  degraded: { label: "Partly running", style: "text-amber-400" },
  failed: { label: "Failed", style: "text-rose-400" },
};

export function appState(a: AppItem): { label: string; style: string } {
  return APP_STATE[a.state ?? ""] ?? { label: "Not deployed", style: "text-slate-500" };
}

/** Which buttons make sense for an app in its current state. */
export function appActions(a: AppItem): { stop: boolean; restart: boolean; redeploy: boolean } {
  const busy = a.state === "building";
  return {
    stop: !busy && (a.state === "running" || a.state === "degraded"),
    restart: !busy && a.state !== null && a.state !== "failed",
    redeploy: !busy,
  };
}
