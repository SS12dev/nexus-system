import { Check, ChevronDown, ChevronRight, Loader2, Wrench, X } from "lucide-react";
import { useState } from "react";
import { duration, stepDetail, toolLabel } from "../lib/tools";
import type { ToolStep } from "../lib/types";

export function StepIcon({ status }: { status: ToolStep["status"] }) {
  if (status === "running") return <Loader2 size={13} className="shrink-0 animate-spin text-sky-300" />;
  if (status === "failed") return <X size={13} className="shrink-0 text-rose-400" />;
  return <Check size={13} className="shrink-0 text-emerald-400" />;
}

/** One tool call: what it did, what it was about, how long it took and the gist of the result. */
export function StepRow({
  step,
  label,
  prefix,
  showSummary,
  as: Tag = "li",
}: {
  step: ToolStep;
  label?: string;
  /** Who made the call, for a team's feed. */
  prefix?: string | null;
  showSummary?: boolean;
  as?: "li" | "div";
}) {
  const detail = stepDetail(step.args);
  return (
    <Tag className="py-1">
      <div className="flex items-center gap-2 text-sm">
        <StepIcon status={step.status} />
        {prefix && <span className="max-w-24 shrink-0 truncate text-xs text-slate-500">{prefix}</span>}
        <span className={`shrink-0 ${step.status === "running" ? "text-slate-200" : "text-slate-300"}`}>
          {label ?? toolLabel(step.name)}
        </span>
        {detail && <span className="min-w-0 truncate text-slate-500">“{detail}”</span>}
        {step.ms != null && <span className="ml-auto shrink-0 text-xs text-slate-600">{duration(step.ms)}</span>}
      </div>
      {showSummary && step.summary && step.status !== "running" && (
        <p className="ml-5 mt-0.5 truncate text-xs text-slate-500" title={step.summary}>
          {step.summary}
        </p>
      )}
    </Tag>
  );
}

/** The tools Jarvis used for a reply. Open while it works so you can follow along, folded
 * into one line once the answer is in. */
export function ToolSteps({ steps, live }: { steps: ToolStep[]; live: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  if (!steps.length) return null;
  const expanded = open ?? live;
  const failed = steps.filter((s) => s.status === "failed").length;
  const total = steps.reduce((n, s) => n + (s.ms ?? 0), 0);
  return (
    <div className="mb-2 rounded-lg border border-edge bg-panel/60">
      <button
        onClick={() => setOpen(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-slate-400 hover:text-slate-200"
        aria-expanded={expanded}
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Wrench size={13} />
        {live
          ? `Working · ${steps.length} step${steps.length > 1 ? "s" : ""}`
          : `Used ${steps.length} tool${steps.length > 1 ? "s" : ""}${total ? ` · ${duration(total)}` : ""}`}
        {failed > 0 && <span className="text-rose-400">· {failed} failed</span>}
      </button>
      {expanded && (
        <ol className="border-t border-edge px-3 py-1">
          {steps.map((s) => (
            <StepRow key={s.id} step={s} showSummary />
          ))}
        </ol>
      )}
    </div>
  );
}
