import { Pause, Play, Square } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import type { Run } from "../lib/types";

export const RUN_LABEL: Record<Run["status"], string> = {
  queued: "Starting",
  running: "Working",
  awaiting_approval: "Waiting for your approval",
  paused: "Paused",
  completed: "Finished",
  failed: "Failed",
  cancelled: "Stopped",
};

export const RUN_STYLE: Record<Run["status"], string> = {
  queued: "text-slate-300",
  running: "text-sky-300",
  awaiting_approval: "text-rose-300",
  paused: "text-amber-300",
  completed: "text-emerald-300",
  failed: "text-rose-400",
  cancelled: "text-slate-400",
};

export function isLive(run?: Run) {
  return !!run && ["queued", "running", "awaiting_approval", "paused"].includes(run.status);
}

export function money(v: number | null | undefined) {
  if (v == null) return "–";
  return v > 0 && v < 0.01 ? "<$0.01" : `$${v.toFixed(2)}`;
}

export function RunControls({ run, onError }: { run: Run; onError?: (msg: string) => void }) {
  const [busy, setBusy] = useState(false);
  const act = async (action: "pause" | "resume" | "cancel") => {
    if (action === "cancel" && !confirm("Stop this run? Work done so far is kept.")) return;
    setBusy(true);
    try {
      await api.controlRun(run.id, action);
    } catch (e) {
      onError?.((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!isLive(run)) return null;
  const btn =
    "flex items-center gap-1 rounded-md border border-edge px-2 py-1 text-xs hover:bg-edge/60 disabled:opacity-40";
  return (
    <div className="flex gap-2">
      {run.status === "running" && (
        <button className={btn} disabled={busy} onClick={() => act("pause")}>
          <Pause size={12} /> Pause
        </button>
      )}
      {run.status === "paused" && (
        <button className={btn} disabled={busy} onClick={() => act("resume")}>
          <Play size={12} /> Resume
        </button>
      )}
      <button className={`${btn} hover:text-rose-300`} disabled={busy} onClick={() => act("cancel")}>
        <Square size={12} /> Stop
      </button>
    </div>
  );
}
