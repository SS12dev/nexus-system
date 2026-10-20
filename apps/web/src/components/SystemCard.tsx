import { Bot } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import type { AgentSystem } from "../lib/types";
import { RUN_LABEL, RUN_STYLE, RunControls, money } from "./RunControls";
import { TeamGraph } from "./TeamGraph";

/** A team Jarvis started from this chat: live graph, status, cost and controls. */
export function SystemCard({ system }: { system: AgentSystem }) {
  const run = system.run;
  const [error, setError] = useState("");
  return (
    <div className="rounded-xl border border-edge bg-panel p-3">
      <div className="mb-1 flex items-center gap-2 text-sm">
        <Bot size={16} className="text-sky-300" />
        <Link to={`/agents/${system.id}`} className="truncate font-medium hover:underline">
          {system.name}
        </Link>
        {run && <span className={`ml-auto text-xs ${RUN_STYLE[run.status]}`}>{RUN_LABEL[run.status]}</span>}
      </div>
      <TeamGraph
        nodes={system.nodes}
        edges={system.edges}
        topology={system.topology}
        live={run?.agents}
        compact
      />
      {run && (
        <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
          <span>
            {money(run.cost_usd)} of {money(run.budget_usd)}
            {run.estimate_usd != null && ` · est. ${money(run.estimate_usd)}`}
          </span>
          <span>{run.steps} steps</span>
          <div className="ml-auto">
            <RunControls run={run} onError={setError} />
          </div>
        </div>
      )}
      {run?.error && run.status === "failed" && <p className="mt-1 text-xs text-rose-400">{run.error}</p>}
      {error && <p className="mt-1 text-xs text-rose-400">{error}</p>}
    </div>
  );
}
