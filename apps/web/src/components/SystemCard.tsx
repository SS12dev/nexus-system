import { Bot, ChevronDown, ChevronRight, Flag, Play, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { api } from "../lib/api";
import { feedFromEvents } from "../lib/feed";
import { AGENT_LABEL, stepDetail, toolLabel } from "../lib/tools";
import type { AgentSystem, FeedItem } from "../lib/types";
import { NODE_COLOR } from "../orb/model";
import { useStore } from "../store/chat";
import { isLive, money, RUN_LABEL, RUN_STYLE, RunControls } from "./RunControls";
import { StepRow } from "./ToolSteps";
import { TeamGraph } from "./TeamGraph";

const EMPTY: FeedItem[] = [];
const FEED_SHOWN = 8;

/** A team Jarvis started from this chat: who is in it, what each agent is doing right now,
 * a running log of their steps, and cost and controls. Finished runs fold into one line. */
export function SystemCard({ system }: { system: AgentSystem }) {
  const run = system.run;
  const live = isLive(run);
  const feed = useStore((s) => (run ? (s.runFeed[run.id] ?? EMPTY) : EMPTY));
  const [open, setOpen] = useState<boolean | null>(null);
  const [all, setAll] = useState(false);
  const [error, setError] = useState("");
  const expanded = open ?? live;
  const names = useMemo(() => Object.fromEntries(system.nodes.map((n) => [n.id, n.name])), [system.nodes]);

  // A card opened after its events went by (a reload, another device) reads the stored timeline.
  const [seeded, setSeeded] = useState<string | null>(null);
  useEffect(() => {
    if (!expanded || !run || seeded === run.id) return;
    setSeeded(run.id);
    api
      .run(run.id)
      .then((d) => useStore.getState().seedRunFeed(run.id, feedFromEvents(d.events, d.run)))
      .catch(() => {});
    // Once per run; live events fill the feed after that.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, run?.id, seeded]);

  const shown = all ? feed : feed.slice(-FEED_SHOWN);
  return (
    <div className="rounded-xl border border-edge bg-panel">
      <button
        onClick={() => setOpen(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
        aria-expanded={expanded}
      >
        {expanded ? <ChevronDown size={14} className="text-slate-500" /> : <ChevronRight size={14} className="text-slate-500" />}
        <Bot size={16} className="shrink-0 text-sky-300" />
        <span className="truncate font-medium">{system.name}</span>
        {run && (
          <span className={`ml-auto shrink-0 text-xs ${RUN_STYLE[run.status]}`}>
            {RUN_LABEL[run.status]}
            {!live && ` · ${money(run.cost_usd)}`}
          </span>
        )}
      </button>
      {expanded && (
        <div className="space-y-3 border-t border-edge px-3 pb-3 pt-2">
          <TeamGraph nodes={system.nodes} edges={system.edges} topology={system.topology} live={run?.agents} compact />
          <ul className="space-y-1">
            {system.nodes.map((n) => {
              const a = run?.agents?.[n.id];
              const st = a?.status ?? "idle";
              const doing = a?.tool && ["working", "waiting"].includes(st) ? toolLabel(a.tool) : AGENT_LABEL[st] ?? st;
              return (
                <li key={n.id} className="flex items-center gap-2 text-sm">
                  <span className="size-2 shrink-0 rounded-full" style={{ background: NODE_COLOR[st] ?? NODE_COLOR.idle }} />
                  <span className="shrink-0 text-slate-200">{n.name}</span>
                  <span className="shrink-0 text-xs text-slate-500">
                    {n.role} · {a?.tier ?? n.tier}
                  </span>
                  <span className="ml-auto truncate text-xs text-slate-400" title={a?.item}>
                    {doing}
                    {a?.item && st !== "done" ? ` · ${a.item}` : ""}
                  </span>
                </li>
              );
            })}
          </ul>
          {feed.length > 0 && (
            <div>
              <div className="mb-1 flex items-center text-xs text-slate-500">
                Activity
                {feed.length > FEED_SHOWN && (
                  <button onClick={() => setAll(!all)} className="ml-auto text-sky-300 hover:text-sky-200">
                    {all ? "Show recent" : `Show all ${feed.length}`}
                  </button>
                )}
              </div>
              <ol className="max-h-72 overflow-y-auto rounded-lg bg-ink/60 px-2 py-1">
                {shown.map((f) => (
                  <FeedRow key={f.id} item={f} agent={f.agent ? (names[f.agent] ?? f.agent) : null} />
                ))}
              </ol>
            </div>
          )}
          {run && (
            <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
              <span>
                {money(run.cost_usd)} of {money(run.budget_usd)}
                {run.estimate_usd != null && ` · est. ${money(run.estimate_usd)}`}
              </span>
              <span>{run.steps} steps</span>
              <Link to={`/agents/${system.id}`} className="text-sky-300 hover:text-sky-200">
                Details
              </Link>
              <div className="ml-auto">
                <RunControls run={run} onError={setError} />
              </div>
            </div>
          )}
          {run?.error && run.status === "failed" && <p className="text-xs text-rose-400">{run.error}</p>}
          {error && <p className="text-xs text-rose-400">{error}</p>}
        </div>
      )}
    </div>
  );
}

function FeedRow({ item, agent }: { item: FeedItem; agent: string | null }) {
  if (item.kind === "tool") {
    return (
      <li>
        <StepRow step={item} prefix={agent} showSummary as="div" />
      </li>
    );
  }
  const detail = stepDetail(item.args);
  const Icon = item.name === "running" ? Play : item.name === "failed" ? X : Flag;
  const verb = item.name === "running" ? "started" : item.name === "failed" ? "failed" : "finished";
  return (
    <li className="flex items-center gap-2 py-1 text-xs text-slate-400">
      <Icon size={12} className={item.name === "failed" ? "text-rose-400" : item.name === "running" ? "text-sky-300" : "text-emerald-400"} />
      <span>
        <span className="text-slate-300">{agent ?? "Agent"}</span> {verb}
        {detail && <span className="text-slate-500"> · {detail}</span>}
      </span>
    </li>
  );
}
