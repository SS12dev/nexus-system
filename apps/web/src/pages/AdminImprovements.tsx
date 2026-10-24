import {
  BookOpen,
  Check,
  ChevronDown,
  Code2,
  ExternalLink,
  GitPullRequest,
  Loader2,
  MessageSquareText,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { confirmAction, toast } from "../components/Feedback";
import { Markdown } from "../components/Markdown";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { APP_NAME } from "../lib/assistant";
import { formatAgo, formatDate, formatDateTime } from "../lib/format";
import type { Improvements, Proposal, ProposalKind, ProposalStatus, Review } from "../lib/types";
import { Section, Tile } from "./AdminParts";

const KINDS: Record<ProposalKind, { label: string; icon: LucideIcon; tone: string; what: string }> = {
  skill: {
    label: "Skill",
    icon: BookOpen,
    tone: "text-sky-300 border-sky-500/40 bg-sky-500/10",
    what: "Approving adds it as a shared skill, on for everyone at once.",
  },
  instruction: {
    label: "Instruction",
    icon: MessageSquareText,
    tone: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
    what: "Approving adds this text to the standing instructions.",
  },
  setting: {
    label: "Setting",
    icon: SlidersHorizontal,
    tone: "text-amber-300 border-amber-500/40 bg-amber-500/10",
    what: "Approving changes the setting at once, as on the Configuration tab.",
  },
  tool: {
    label: "New tool",
    icon: Wrench,
    tone: "text-violet-300 border-violet-500/40 bg-violet-500/10",
    what: "Approving opens a draft pull request with this spec. Nothing merges until you merge it.",
  },
  code: {
    label: "Code change",
    icon: Code2,
    tone: "text-fuchsia-300 border-fuchsia-500/40 bg-fuchsia-500/10",
    what: "Approving opens a draft pull request with this spec. Nothing merges until you merge it.",
  },
};

const VIEWS: { key: string; label: string; statuses: ProposalStatus[] }[] = [
  { key: "pending", label: "Waiting for you", statuses: ["pending", "failed", "applying"] },
  { key: "applied", label: "Approved", statuses: ["applied"] },
  { key: "denied", label: "Denied", statuses: ["denied", "reverted"] },
  { key: "all", label: "All", statuses: [] },
];

const STATUS_LABEL: Record<ProposalStatus, { label: string; tone: string }> = {
  pending: { label: "waiting", tone: "text-violet-300" },
  applying: { label: "opening pull request", tone: "text-sky-300" },
  applied: { label: "approved", tone: "text-emerald-300" },
  denied: { label: "denied", tone: "text-slate-400" },
  failed: { label: "failed to apply", tone: "text-rose-300" },
  reverted: { label: "undone", tone: "text-slate-400" },
};

function Level({ label, value }: { label: string; value: string }) {
  const tone = value === "high" ? "text-slate-100" : value === "low" ? "text-slate-500" : "text-slate-300";
  return (
    <span className="text-xs text-slate-500">
      {label} <span className={tone}>{value}</span>
    </span>
  );
}

function Change({ p }: { p: Proposal }) {
  const d = p.payload;
  if (p.kind === "skill")
    return (
      <div>
        <p className="mb-1 text-xs text-slate-400">
          <span className="font-mono text-slate-200">domain/{d.name}</span> · {d.description}
        </p>
        <div className="max-h-96 overflow-auto rounded-md border border-edge bg-ink p-3">
          <Markdown size="sm">{(d.content ?? "").replace(/^---[\s\S]*?---\s*/, "")}</Markdown>
        </div>
      </div>
    );
  if (p.kind === "instruction")
    return (
      <div>
        <p className="mb-1 text-xs text-slate-400">
          Added to the {d.layer === "role" ? <>instructions for the <b className="text-slate-200">{d.role}</b> role</> : "global instructions every agent gets"}:
        </p>
        <blockquote className="rounded-md border-l-2 border-emerald-500/60 bg-ink px-3 py-2 text-sm whitespace-pre-wrap text-slate-200">
          {d.text}
        </blockquote>
      </div>
    );
  if (p.kind === "setting")
    return (
      <p className="text-sm">
        <span className="text-slate-400">{d.label ?? d.name}</span> becomes{" "}
        <code className="rounded bg-ink px-1.5 py-0.5 text-amber-200">{String(d.value)}</code>
      </p>
    );
  return (
    <div>
      <div className="max-h-[28rem] overflow-auto rounded-md border border-edge bg-ink p-3">
        <Markdown size="sm">{d.spec ?? ""}</Markdown>
      </div>
      {(d.files ?? []).length > 0 && (
        <p className="mt-2 text-xs text-slate-500">
          Likely touches:{" "}
          {(d.files ?? []).map((f) => (
            <code key={f} className="mr-1.5 text-slate-400">
              {f}
            </code>
          ))}
        </p>
      )}
    </div>
  );
}

function ProposalCard({
  p,
  prReady,
  defaultOpen,
  onDone,
  onError,
}: {
  p: Proposal;
  prReady: boolean;
  defaultOpen: boolean;
  onDone: () => void;
  onError: (msg: string) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [denying, setDenying] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const kind = KINDS[p.kind];
  const Icon = kind.icon;
  const status = STATUS_LABEL[p.status];
  const local = p.kind === "skill" || p.kind === "instruction" || p.kind === "setting";

  const act = async (fn: () => Promise<Proposal>, done: string) => {
    setBusy(true);
    try {
      await fn();
      toast(done);
      onDone();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!local && !prReady) {
      const ok = await confirmAction({
        title: "Approve as a spec only?",
        body:
          "No repository is set for pull requests (Self-review on the Configuration tab), so this stays an approved spec you can copy from here.",
        confirmLabel: "Approve",
      });
      if (!ok) return;
    }
    await act(() => api.approveProposal(p.id), local ? "Approved and applied" : prReady ? "Approved; opening the pull request" : "Approved");
  };

  const undo = async () => {
    const ok = await confirmAction({
      title: "Undo this change?",
      body:
        p.kind === "skill"
          ? "The skill is turned off. It stays on the Skills page, where you can turn it on again."
          : p.kind === "setting"
            ? "The setting goes back to what it was before."
            : "The instructions go back to what they were before.",
      confirmLabel: "Undo",
      danger: true,
    });
    if (ok) await act(() => api.revertProposal(p.id), "Undone");
  };

  return (
    <li className={`rounded-lg border bg-panel ${p.status === "pending" ? "border-violet-500/40" : "border-edge"}`}>
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-3 px-4 py-3 text-left" aria-expanded={open}>
        <span className={`mt-0.5 flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-xs ${kind.tone}`}>
          <Icon size={12} /> {kind.label}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-medium text-slate-100">{p.title}</span>
          <span className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5">
            <Level label="Impact" value={p.impact} />
            <Level label="Effort" value={p.effort} />
            <span className="text-xs text-slate-500">{formatDate(p.created_at)}</span>
            {p.status !== "pending" && <span className={`text-xs ${status.tone}`}>{status.label}</span>}
          </span>
        </span>
        <ChevronDown size={16} className={`mt-1 shrink-0 text-slate-500 transition ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-edge px-4 py-3 text-sm">
          {p.summary && <p className="text-slate-300">{p.summary}</p>}
          {p.evidence.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-slate-400">Evidence</div>
              <ul className="list-disc space-y-0.5 pl-5 text-slate-400">
                {p.evidence.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <div className="mb-1 text-xs font-medium text-slate-400">What would change</div>
            <Change p={p} />
          </div>
          {p.note && <p className="text-xs text-slate-400">Your note: {p.note}</p>}
          {p.error && <p className="rounded-md bg-rose-500/10 px-3 py-2 text-rose-300">{p.error}</p>}
          {p.result.pr_url && (
            <a
              href={p.result.pr_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-sky-400 hover:underline"
            >
              <GitPullRequest size={14} /> Open the pull request <ExternalLink size={12} />
            </a>
          )}
          {p.result.spec_only && (
            <p className="text-xs text-slate-500">Approved as a spec. Set a repository for pull requests to have it opened as one.</p>
          )}

          {(p.status === "pending" || p.status === "failed") && !denying && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <button
                onClick={approve}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-md bg-emerald-500 px-3 py-1.5 font-medium text-ink hover:bg-emerald-400 disabled:opacity-50"
              >
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {p.status === "failed" ? "Try again" : "Approve"}
              </button>
              <button
                onClick={() => setDenying(true)}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 text-slate-300 hover:bg-edge/60"
              >
                <X size={14} /> Deny
              </button>
              <span className="text-xs text-slate-500">{kind.what}</span>
            </div>
          )}
          {denying && (
            <div className="space-y-2 pt-1">
              <textarea
                autoFocus
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={1000}
                rows={2}
                placeholder="Why not? Optional; later reviews read it, so the same idea isn't proposed again."
                className="w-full rounded-md border border-edge bg-ink px-3 py-2"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => act(() => api.denyProposal(p.id, note.trim()), "Denied")}
                  disabled={busy}
                  className="rounded-md bg-rose-500 px-3 py-1.5 font-medium text-white hover:bg-rose-400 disabled:opacity-50"
                >
                  Deny
                </button>
                <button onClick={() => setDenying(false)} className="rounded-md px-3 py-1.5 text-slate-400 hover:text-white">
                  Cancel
                </button>
              </div>
            </div>
          )}
          {p.status === "applied" && local && (
            <button onClick={undo} disabled={busy} className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white">
              <RotateCcw size={12} /> Undo this change
            </button>
          )}
        </div>
      )}
    </li>
  );
}

function Counted({ rows, label }: { rows: { name: string; count: number; note?: string }[]; label: string }) {
  if (rows.length === 0) return <p className="text-xs text-slate-500">None.</p>;
  const max = Math.max(...rows.map((r) => r.count));
  return (
    <ul className="space-y-1" aria-label={label}>
      {rows.map((r) => (
        <li key={r.name} className="flex items-center gap-2 text-sm">
          <span className="w-40 shrink-0 truncate text-slate-300 sm:w-48" title={r.name}>
            {r.name}
          </span>
          <span className="h-2 flex-1 rounded-full bg-edge/60">
            <span className="block h-2 rounded-full bg-violet-500" style={{ width: `${(r.count / max) * 100}%` }} />
          </span>
          <span className="w-16 shrink-0 text-right text-xs tabular-nums text-slate-400">
            {r.count}
            {r.note ? ` ${r.note}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Findings({ review }: { review: Review }) {
  const f = review.findings;
  const a = f.activity;
  if (!a) return <p className="text-sm text-slate-500">{review.error ?? "Nothing recorded."}</p>;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Tile label="Chat turns" value={a.turns} hint={`${a.failed_turns} failed · ${a.people} people`} />
        <Tile label="Team runs" value={a.team_runs} hint={`${a.failed_team_runs} failed · $${a.team_cost_usd.toFixed(2)}`} />
        <Tile label="Teams designed" value={a.teams_designed} />
        <Tile
          label="Requests read for topics"
          value={f.requests_read ?? 0}
          hint={f.topics ? "only the topic counts were kept" : "topics are off"}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {f.topics && (
          <div>
            <div className="mb-1.5 text-xs font-medium text-slate-400">What people asked about</div>
            <Counted label="Topics" rows={f.topics.map((t) => ({ name: t.topic, count: t.count }))} />
          </div>
        )}
        {f.gaps && (
          <div>
            <div className="mb-1.5 text-xs font-medium text-slate-400">What the assistant couldn't do</div>
            <Counted label="Gaps" rows={f.gaps.map((g) => ({ name: g.gap, count: g.count }))} />
          </div>
        )}
        {f.tools && f.tools.length > 0 && (
          <div>
            <div className="mb-1.5 text-xs font-medium text-slate-400">Tools used</div>
            <Counted
              label="Tools"
              rows={f.tools.slice(0, 10).map((t) => ({
                name: t.tool,
                count: t.calls,
                note: t.failed ? `(${t.failed}✕)` : undefined,
              }))}
            />
          </div>
        )}
        {f.teams && f.teams.roles.length > 0 && (
          <div>
            <div className="mb-1.5 text-xs font-medium text-slate-400">Roles in the teams built</div>
            <Counted label="Roles" rows={f.teams.roles.slice(0, 10).map((r) => ({ name: r.role, count: r.count }))} />
          </div>
        )}
      </div>
      {f.failures && f.failures.length > 0 && (
        <div>
          <div className="mb-1.5 text-xs font-medium text-slate-400">Failures, as patterns</div>
          <ul className="divide-y divide-edge rounded-lg border border-edge text-sm">
            {f.failures.map((x) => (
              <li key={x.pattern} className="flex gap-3 px-3 py-1.5">
                <span className="min-w-0 flex-1 break-words text-slate-300">{x.pattern}</span>
                <span className="text-xs tabular-nums text-slate-400">×{x.count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {f.skills && (
        <p className="text-xs text-slate-500">
          Shared skills used: {f.skills.used.length}; not used: {f.skills.unused.length}
          {f.skills.learned_drafts_waiting ? `; ${f.skills.learned_drafts_waiting} learned drafts wait on the Skills page` : ""}.
        </p>
      )}
    </div>
  );
}

/** Self-review: what the last reviews saw, and the improvements they propose. */
export function ImprovementsPanel({
  onError,
  onOpen,
  onChange,
}: {
  onError: (msg: string) => void;
  onOpen: (tab: string) => void;
  onChange: () => void;
}) {
  const [data, setData] = useState<Improvements | null>(null);
  const [view, setView] = useState("pending");
  const [kind, setKind] = useState("");
  const [loading, setLoading] = useState(false);
  const [showFindings, setShowFindings] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api
      .improvements()
      .then((d) => {
        setData(d);
        onChange();
      })
      .catch((e) => onError(e.message))
      .finally(() => setLoading(false));
  }, [onError, onChange]);

  useEffect(() => {
    load();
  }, [load]);

  const running = data?.reviews.find((r) => r.status === "running");
  const applying = data?.proposals.some((p) => p.status === "applying");
  // While a review runs or a pull request is being opened, look again every few seconds.
  useEffect(() => {
    if (!running && !applying) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [running, applying, load]);

  if (!data) return <p className="text-sm text-slate-500">Loading…</p>;

  const reviewNow = async () => {
    try {
      await api.reviewNow();
      toast("Review started. It takes a minute or two.");
      load();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const statuses = VIEWS.find((v) => v.key === view)!.statuses;
  const shown = data.proposals.filter(
    (p) => (statuses.length === 0 || statuses.includes(p.status)) && (!kind || p.kind === kind),
  );
  const count = (v: (typeof VIEWS)[number]) =>
    v.statuses.length ? v.statuses.reduce((n, s) => n + (data.counts[s] ?? 0), 0) : data.proposals.length;
  const last = data.reviews.find((r) => r.status !== "running");
  const s = data.schedule;

  return (
    <div>
      <div className="mb-6 rounded-lg border border-edge bg-panel p-4 text-sm">
        <div className="flex flex-wrap items-start gap-3">
          <Sparkles size={20} className="mt-0.5 shrink-0 text-violet-400" />
          <div className="min-w-0 flex-1">
            <p className="text-slate-200">
              {APP_NAME} reviews how it is used and proposes improvements to itself: new skills, instructions,
              settings, tools or code. Nothing changes until you approve it here.
            </p>
            <p className="mt-1 text-slate-400">
              {s.every_days > 0
                ? `It reviews every ${s.every_days} ${s.every_days === 1 ? "day" : "days"}${s.next_at ? `; next around ${formatDate(s.next_at)}` : ""}.`
                : "The schedule is off; review whenever you like."}{" "}
              {last
                ? `Last review ${formatAgo(last.finished_at ?? last.created_at)}: ${
                    last.status === "failed" ? `failed (${last.error ?? "unknown error"})` : `${last.proposals} proposals`
                  }.`
                : "No review yet."}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={load} className="flex items-center gap-1 text-slate-400 hover:text-white" aria-label="Refresh">
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            </button>
            <button
              onClick={reviewNow}
              disabled={!!running}
              className="flex items-center gap-1.5 rounded-md bg-violet-500 px-3 py-1.5 font-medium text-white hover:bg-violet-400 disabled:opacity-60"
            >
              {running ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              {running ? "Reviewing…" : "Review now"}
            </button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t border-edge pt-3 text-xs text-slate-400">
          <span className="flex items-center gap-1.5">
            <ShieldCheck size={13} className="text-emerald-400" />
            {s.read_requests
              ? `Requests become anonymous topics, counted only when asked ${s.min_count}+ times. Chats, names and ids are never kept; people with memory off are skipped.`
              : "Request topics are off: it uses counts only."}
          </span>
          <span className="flex items-center gap-1.5">
            <GitPullRequest size={13} className={data.pull_requests.ready ? "text-sky-400" : "text-slate-500"} />
            {data.pull_requests.ready
              ? `Approved code and tool proposals open a draft pull request on ${data.pull_requests.repo}.`
              : "Code and tool proposals stay specs until you set a repository for pull requests."}
          </span>
          <button onClick={() => onOpen("config")} className="text-sky-400 hover:underline">
            Settings
          </button>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <div className="flex flex-wrap gap-1 rounded-lg border border-edge bg-panel p-0.5" role="group" aria-label="Show">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              onClick={() => setView(v.key)}
              aria-pressed={view === v.key}
              className={`rounded-md px-2.5 py-1 ${view === v.key ? "bg-edge text-white" : "text-slate-400 hover:text-slate-200"}`}
            >
              {v.label}
              <span className="ml-1.5 text-xs text-slate-500 tabular-nums">{count(v)}</span>
            </button>
          ))}
        </div>
        <Select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
          <option value="">Every kind</option>
          {(Object.keys(KINDS) as ProposalKind[]).map((k) => (
            <option key={k} value={k}>
              {KINDS[k].label}
            </option>
          ))}
        </Select>
      </div>

      {shown.length === 0 ? (
        <p className="mb-6 rounded-lg border border-dashed border-edge px-4 py-6 text-center text-sm text-slate-500">
          {view === "pending" ? "Nothing waiting. New proposals appear here after a review." : "None here."}
        </p>
      ) : (
        <ul className="mb-6 space-y-2">
          {shown.map((p, i) => (
            <ProposalCard
              key={p.id}
              p={p}
              prReady={data.pull_requests.ready}
              defaultOpen={i === 0 && p.status === "pending"}
              onDone={load}
              onError={onError}
            />
          ))}
        </ul>
      )}

      {last && last.status === "done" && (
        <Section
          title="What the last review saw"
          action={
            <button onClick={() => setShowFindings(!showFindings)} className="text-xs text-sky-400 hover:underline">
              {showFindings ? "Hide" : "Show"}
            </button>
          }
        >
          <p className="mb-2 text-sm text-slate-400">
            {formatDateTime(last.window_start)} to {formatDateTime(last.window_end)}. This is everything the
            proposal model was given about how {APP_NAME} was used.
          </p>
          {showFindings && <Findings review={last} />}
        </Section>
      )}
    </div>
  );
}
