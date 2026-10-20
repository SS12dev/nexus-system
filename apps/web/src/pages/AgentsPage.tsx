import { ArrowLeft, Bot, Bookmark, FileText, Play, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Guide, GuideHeading } from "../components/Guide";
import { Markdown } from "../components/Markdown";
import { Link, useNavigate, useParams } from "react-router";
import { ApprovalCard } from "../components/ApprovalCard";
import { RUN_LABEL, RUN_STYLE, RunControls, isLive, money } from "../components/RunControls";
import { TeamGraph } from "../components/TeamGraph";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import { hasRole, type AgentSystem, type RunDetail, type SystemDetail } from "../lib/types";
import { useStore } from "../store/chat";
import { useAssistantName } from "../lib/assistant";

const TOPOLOGY: Record<AgentSystem["topology"], string> = {
  single: "Single agent",
  pipeline: "Pipeline",
  supervisor_workers: "Lead and workers",
  parallel_map_reduce: "Parallel map-reduce",
  critic_loop: "Worker and critic",
  dev_project: "Software team",
};

/** How each shape of team works, in the words of the design prompt (systems/prompts.py). */
const TOPOLOGY_HELP: Record<AgentSystem["topology"], string> = {
  single: "One agent does the whole job with the tools it was given.",
  pipeline:
    "Agents work in order, each building on the previous one's output (e.g. researcher, then writer).",
  supervisor_workers:
    "A lead agent hands tasks to the others one at a time and decides what's next from each result.",
  parallel_map_reduce:
    "One agent splits the job into items, copies of a worker do them in parallel, and a third combines the results.",
  critic_loop: "A worker drafts and a critic reviews, sending it back until it passes.",
  dev_project:
    "Jarvis's team for building software in a project folder: a brief, a plan, builders working side by side, tests, and a review that sends fixes back up to three times.",
};

const LIFETIME: Record<AgentSystem["lifetime"], { label: string; help: string }> = {
  ephemeral: {
    label: "One-off",
    help: "Built for one request. It's archived when the run finishes and drops off the orb.",
  },
  persistent: {
    label: "Saved",
    help: "Kept because you asked, so it can run again on a new request.",
  },
  template: {
    label: "Template",
    help: "Kept and reused automatically when a new request fits, instead of designing a new team.",
  },
};

const TIER_HELP: Record<string, string> = {
  nano: "Cheapest and fastest: sorting, extracting, short summaries.",
  mini: "The default: tool use, research, writing, making files.",
  pro: "About 5x mini: planning, leading a team, reviewing, hard reasoning.",
  max: "The strongest and dearest; only when you ask for the best model.",
};

const EVENT_TEXT: Record<string, (d: Record<string, unknown>) => string> = {
  "tool.call": (d) => `used ${d.name}${d.query ? `: ${d.query}` : d.url ? `: ${d.url}` : ""}`,
  "agent.status": (d) => `${d.status}${d.tier ? ` (${d.tier})` : ""}${d.item ? ` · item ${d.item}` : ""}`,
  "approval.requested": (d) => `asked to use ${d.tool}`,
};

export function AgentsPage() {
  const assistant = useAssistantName();
  const { systemId } = useParams();
  const navigate = useNavigate();
  const user = useStore((s) => s.user)!;
  const canEdit = hasRole(user, "member");
  const live = useStore((s) => s.systems);
  const approvals = useStore((s) => s.approvals);
  const version = useStore((s) => s.agentsVersion);
  const { upsertSystems, upsertApprovals } = useStore.getState();
  const [list, setList] = useState<AgentSystem[]>([]);
  const [archived, setArchived] = useState(false);
  const [detail, setDetail] = useState<SystemDetail | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const pane = useRef<HTMLElement>(null);
  // A newly picked team starts at the top of the reading pane (braces: see SkillsPage).
  useEffect(() => {
    pane.current?.scrollTo({ top: 0 });
  }, [systemId]);
  const [request, setRequest] = useState("");
  const [error, setError] = useState("");

  const act = async (fn: () => Promise<void>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const loadList = useCallback(async () => {
    const [systems, pending] = await Promise.all([api.systems({ archived }), api.approvals()]);
    upsertSystems(systems);
    upsertApprovals(pending);
    setList(systems);
  }, [archived, upsertSystems, upsertApprovals]);

  useEffect(() => {
    loadList().catch((e) => setError(e.message));
  }, [loadList, version]);

  useEffect(() => {
    if (!systemId) {
      setDetail(null);
      return;
    }
    api
      .system(systemId)
      .then((d) => {
        setDetail(d);
        setRunId((cur) => (cur && d.runs.some((r) => r.id === cur) ? cur : (d.runs[0]?.id ?? null)));
      })
      .catch((e) => setError(e.message));
  }, [systemId, version]);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      return;
    }
    api
      .run(runId)
      .then(setRun)
      .catch(() => setRun(null));
  }, [runId, version]);

  const pending = useMemo(() => Object.values(approvals).filter((a) => a.status === "pending"), [approvals]);
  // The store has the live run state (agent statuses stream in between page reloads).
  const current = systemId ? live[systemId] : undefined;
  const liveRun = current?.run && current.run.id === runId ? current.run : run?.run;

  const runAgain = (e: FormEvent) => {
    e.preventDefault();
    if (!detail || !request.trim()) return;
    act(async () => {
      const s = await api.runSystem(detail.id, { request: request.trim() });
      upsertSystems([s]);
      setRequest("");
      setRunId(s.run?.id ?? null);
    });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mx-auto w-full max-w-6xl shrink-0 px-4 pt-6">
        <h1 className="mb-1 text-xl font-semibold">Agents</h1>
        <p className="mb-6 text-sm text-slate-400">
          Teams of sub-agents {assistant} builds for bigger jobs: who is on each team, what every run did and
          cost, and the steps waiting for your approval. Ask {assistant} in a chat to build one.
        </p>
        <Guide title="How teams work">
          <div>
            <GuideHeading>Building a team</GuideHeading>
            <p>
              Anything bigger than a quick lookup goes to a team. {assistant} picks the smallest team that
              fits: how many agents, how they hand work on (the team's shape), each agent's role, model, tools
              and skills, and a task brief for each. You can open an agent's brief in the table on a team.
            </p>
            <ul className="mt-2 space-y-1">
              {(Object.keys(TOPOLOGY) as AgentSystem["topology"][]).map((t) => (
                <li key={t}>
                  <b className="text-slate-300">{TOPOLOGY[t]}:</b> {TOPOLOGY_HELP[t]}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <GuideHeading>Keeping or clearing teams</GuideHeading>
            <ul className="space-y-1">
              {(Object.keys(LIFETIME) as AgentSystem["lifetime"][]).map((l) => (
                <li key={l}>
                  <b className="text-slate-300">{LIFETIME[l].label}:</b> {LIFETIME[l].help}
                </li>
              ))}
            </ul>
            <p className="mt-2">
              Teams are one-off unless you say keep, save or reuse. <b>Keep as template</b> turns any team
              into a template. <b>Delete</b> removes a team and its run history; files it made stay in your
              Library. Every run has a spending limit and stops when it reaches it.
            </p>
          </div>
        </Guide>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

        {pending.length > 0 && (
          <section className="mb-6 max-h-[40vh] space-y-3 overflow-y-auto">
            <h2 className="text-sm font-medium text-rose-300">Waiting for you</h2>
            {pending.map((a) => (
              <ApprovalCard key={a.id} approval={a} systemName={live[a.system_id]?.name} />
            ))}
          </section>
        )}
      </div>

      {/* The list and the team scroll on their own, so a team picked far down the list shows
          right beside it; on a phone they take turns filling the screen. */}
      <div className="mx-auto grid min-h-0 w-full max-w-6xl flex-1 grid-rows-[minmax(0,1fr)] gap-6 px-4 md:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className={`min-h-0 overflow-y-auto pr-1 pb-6 ${systemId ? "hidden md:block" : ""}`}>
          <label className="mb-2 flex items-center gap-2 text-xs text-slate-400">
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
            Show finished one-off teams
          </label>
          <ul className="space-y-1">
            {list.length === 0 && (
              <li className="text-sm text-slate-500">
                No teams yet. Try asking {assistant} for a researched report with a spreadsheet.
              </li>
            )}
            {list.map((s) => {
              const r = live[s.id]?.run ?? s.run;
              return (
                <li key={s.id}>
                  <Link
                    to={`/agents/${s.id}`}
                    className={`block rounded-md border px-3 py-2 text-sm ${
                      s.id === systemId ? "border-sky-500/60 bg-edge" : "border-edge hover:bg-edge/50"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <Bot size={14} className="shrink-0 text-sky-300" />
                      <span className="truncate font-medium">{s.name}</span>
                      {s.lifetime === "template" && (
                        <Bookmark
                          size={12}
                          className="ml-auto shrink-0 text-amber-300"
                          aria-label="Template"
                        />
                      )}
                    </div>
                    <div className="mt-0.5 flex gap-2 text-xs text-slate-500">
                      <span title={TOPOLOGY_HELP[s.topology]}>{TOPOLOGY[s.topology]}</span>
                      {r && <span className={RUN_STYLE[r.status]}>{RUN_LABEL[r.status]}</span>}
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        </aside>

        {detail ? (
          <section ref={pane} className="min-h-0 min-w-0 space-y-5 overflow-y-auto pb-6">
            <Link
              to="/agents"
              className="flex items-center gap-1 text-sm text-slate-400 hover:text-white md:hidden"
            >
              <ArrowLeft size={14} /> All teams
            </Link>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold">{detail.name}</h2>
              <span
                className="rounded bg-edge px-2 py-0.5 text-xs text-slate-300"
                title={TOPOLOGY_HELP[detail.topology]}
              >
                {TOPOLOGY[detail.topology]}
              </span>
              <span
                className="rounded bg-edge px-2 py-0.5 text-xs text-slate-300"
                title={LIFETIME[detail.lifetime].help}
              >
                {LIFETIME[detail.lifetime].label}
              </span>
              {canEdit && (
                <div className="ml-auto flex gap-2">
                  {detail.lifetime !== "template" && (
                    <button
                      onClick={() =>
                        act(async () => {
                          const s = await api.keepAsTemplate(detail.id);
                          upsertSystems([s]);
                        })
                      }
                      title="Keep this team and reuse it automatically when a new request fits"
                      className="flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60"
                    >
                      <Bookmark size={14} /> Keep as template
                    </button>
                  )}
                  <button
                    disabled={isLive(liveRun)}
                    onClick={() => {
                      if (!confirm(`Delete ${detail.name} and its run history?`)) return;
                      act(async () => {
                        await api.deleteSystem(detail.id);
                        navigate("/agents");
                      });
                    }}
                    className="flex items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-rose-500/20 disabled:opacity-40"
                  >
                    <Trash2 size={14} /> Delete
                  </button>
                </div>
              )}
            </div>
            <p className="text-sm text-slate-300">{detail.goal}</p>
            <p className="-mt-3 text-xs text-slate-500">
              {TOPOLOGY[detail.topology]}: {TOPOLOGY_HELP[detail.topology]} {LIFETIME[detail.lifetime].label}:{" "}
              {LIFETIME[detail.lifetime].help}
            </p>

            <div className="rounded-lg border border-edge bg-panel p-3">
              <TeamGraph
                nodes={detail.nodes}
                edges={detail.edges}
                topology={detail.topology}
                live={liveRun?.agents}
              />
            </div>

            <div className="overflow-x-auto rounded-lg border border-edge">
              <table className="w-full text-left text-sm">
                <thead className="bg-panel text-xs text-slate-400">
                  <tr>
                    <th
                      className="px-3 py-2"
                      title="Name and role. The role picks the agent's job description (Skills, Instructions, Agent roles)."
                    >
                      Agent and role
                    </th>
                    <th className="px-3 py-2" title="The model tier: higher tiers are smarter and cost more.">
                      Model
                    </th>
                    <th
                      className="px-3 py-2"
                      title="Tools it can call and skills (how-to guides) it can read."
                    >
                      Tools and skills
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {detail.spec.agents.map((a) => (
                    <tr key={a.id} className="border-t border-edge align-top">
                      <td className="px-3 py-2">
                        <div className="font-medium">{a.name}</div>
                        <div className="text-xs text-slate-500">{a.role}</div>
                        <details className="mt-1 text-xs text-slate-400">
                          <summary className="cursor-pointer">Task brief from {assistant}</summary>
                          <p className="mt-1 whitespace-pre-wrap">{a.instructions}</p>
                        </details>
                      </td>
                      <td className="px-3 py-2 text-xs" title={TIER_HELP[a.tier]}>
                        {a.tier}
                        <div className="mt-0.5 text-slate-500">{TIER_HELP[a.tier]?.split(":")[0]}</div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {[...a.tools, ...a.skills].map((t) => (
                            <span key={t} className="rounded bg-edge px-1.5 py-0.5 text-xs">
                              {t}
                            </span>
                          ))}
                          {!a.tools.length && !a.skills.length && (
                            <span className="text-xs text-slate-500">none</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {detail.spec.deliverables.length > 0 && (
              <p className="text-xs text-slate-400">Delivers: {detail.spec.deliverables.join(", ")}</p>
            )}

            {canEdit && (
              <form onSubmit={runAgain} className="flex gap-2">
                <input
                  value={request}
                  onChange={(e) => setRequest(e.target.value)}
                  placeholder="Run this team on a new request…"
                  className="min-w-0 flex-1 rounded-md border border-edge bg-panel px-3 py-1.5 text-sm"
                />
                <button
                  disabled={!request.trim() || isLive(liveRun)}
                  className="flex items-center gap-1 rounded-md bg-sky-600 px-3 py-1.5 text-sm hover:bg-sky-500 disabled:opacity-40"
                >
                  <Play size={14} /> Run
                </button>
              </form>
            )}

            <div>
              <h3 className="text-sm font-medium">Runs</h3>
              <p className="mb-2 text-xs text-slate-500">
                Each run is one request to this team. Pick one to see what it was asked, its result, files,
                cost and every step.
              </p>
              <div className="flex flex-wrap gap-2">
                {detail.runs.map((r) => (
                  <button
                    key={r.id}
                    onClick={() => setRunId(r.id)}
                    className={`rounded-md border px-2.5 py-1 text-xs ${
                      r.id === runId ? "border-sky-500/60 bg-edge" : "border-edge hover:bg-edge/50"
                    }`}
                  >
                    {formatDate(r.created_at)} ·{" "}
                    <span className={RUN_STYLE[r.status]}>{RUN_LABEL[r.status]}</span> · {money(r.cost_usd)}
                  </button>
                ))}
              </div>
            </div>

            {run && liveRun && (
              <div className="space-y-4 rounded-lg border border-edge bg-panel p-4">
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <span className={RUN_STYLE[liveRun.status]}>{RUN_LABEL[liveRun.status]}</span>
                  <span className="text-slate-400">
                    {money(liveRun.cost_usd)} of {money(liveRun.budget_usd)} · {liveRun.steps} model calls ·{" "}
                    {liveRun.input_tokens + liveRun.output_tokens} tokens
                  </span>
                  {liveRun.trace_id && (
                    <span className="text-xs text-slate-500" title="Langfuse trace id">
                      trace {liveRun.trace_id.slice(0, 8)}
                    </span>
                  )}
                  <div className="ml-auto">
                    <RunControls run={liveRun} onError={setError} />
                  </div>
                </div>
                <p className="text-sm text-slate-300">
                  <span className="text-slate-500">Asked: </span>
                  {run.run.input}
                </p>
                {run.approvals.map((a) => (
                  <ApprovalCard key={a.id} approval={approvals[a.id] ?? a} />
                ))}
                {run.files.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {run.files.map((f) => (
                      <a
                        key={f.id}
                        href={api.fileUrl(f.id)}
                        className="flex items-center gap-1 rounded-md border border-edge px-2 py-1 text-xs hover:bg-edge/60"
                      >
                        <FileText size={12} /> {f.name}
                        {f.version > 1 && ` (v${f.version})`}
                      </a>
                    ))}
                  </div>
                )}
                {run.run.output && (
                  <details open className="text-sm">
                    <summary className="cursor-pointer text-slate-400">Result</summary>
                    <Markdown size="sm" className="mt-2">
                      {run.run.output}
                    </Markdown>
                  </details>
                )}
                {run.run.error && <p className="text-sm text-rose-400">{run.run.error}</p>}
                <details className="text-sm">
                  <summary className="cursor-pointer text-slate-400">
                    Timeline ({run.events.length} events)
                  </summary>
                  <ol className="mt-2 space-y-1 text-xs">
                    {run.events.map((e, i) => (
                      <li key={i} className="flex gap-2">
                        <span className="shrink-0 text-slate-500">{new Date(e.at).toLocaleTimeString()}</span>
                        <span className="shrink-0 font-medium text-slate-300">{e.agent}</span>
                        <span className="min-w-0 truncate text-slate-400">
                          {(EVENT_TEXT[e.type] ?? (() => e.type))(e.data)}
                        </span>
                      </li>
                    ))}
                  </ol>
                </details>
              </div>
            )}
          </section>
        ) : (
          <section className={`text-sm text-slate-500 ${systemId ? "" : "hidden md:block"}`}>
            {systemId ? "Loading…" : "Pick a team to see it."}
          </section>
        )}
      </div>
    </div>
  );
}
