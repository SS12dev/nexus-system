import { AlertTriangle, CheckCircle2, ExternalLink, RefreshCw, XCircle } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { formatAgo, formatDateTime, formatSize, formatTokens } from "../lib/format";
import type { AuditEntry, Backup, OpsStatus, Usage } from "../lib/types";

type Tab = "status" | "usage" | "audit" | "backups";
const TABS: { key: Tab; label: string }[] = [
  { key: "status", label: "Status" },
  { key: "usage", label: "Usage" },
  { key: "audit", label: "Audit log" },
  { key: "backups", label: "Backups" },
];

/** Health, traffic, usage, the audit log, backups and maintenance mode. Admins only. */
export function AdminPage() {
  const [tab, setTab] = useState<Tab>("status");
  const [error, setError] = useState("");
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-6">
        <h1 className="mb-1 text-xl font-semibold">Admin</h1>
        <p className="mb-4 text-sm text-slate-400">
          How Jarvis is running, who uses it and what it costs. You see counts, costs and errors
          here, never anyone's chats.
        </p>
        <div className="mb-4 flex flex-wrap gap-2 text-sm">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => {
                setError("");
                setTab(t.key);
              }}
              className={`rounded-md px-3 py-1 ${tab === t.key ? "bg-edge text-white" : "text-slate-400 hover:bg-edge/50"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
        {tab === "status" && <StatusPanel onError={setError} />}
        {tab === "usage" && <UsagePanel onError={setError} />}
        {tab === "audit" && <AuditPanel onError={setError} />}
        {tab === "backups" && <BackupsPanel onError={setError} />}
      </div>
    </div>
  );
}

type PanelProps = { onError: (msg: string) => void };

function usd(n: number): string {
  return `$${n < 10 ? n.toFixed(2) : n.toFixed(0)}`;
}

function Tile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-lg border border-edge bg-panel px-4 py-3">
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="mb-6">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="font-medium">{title}</h2>
        <div className="ml-auto">{action}</div>
      </div>
      {children}
    </section>
  );
}

/** One series of bars over time, with a tooltip per bar. Values are drawn from a zero baseline. */
function Bars({
  points,
  label,
  format = String,
}: {
  points: { key: string; value: number; tip: string }[];
  label: string;
  format?: (n: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const peak = Math.max(0, ...points.map((p) => p.value));
  const max = peak || 1;
  const w = 600;
  const h = 80;
  const step = w / Math.max(1, points.length);
  const bar = Math.max(1, step - 2);
  const shown = hover != null ? points[hover] : null;
  return (
    <figure className="rounded-lg border border-edge bg-panel px-4 py-3">
      <figcaption className="mb-1 flex text-xs text-slate-400">
        <span>{label}</span>
        <span className="ml-auto tabular-nums text-slate-300">
          {shown ? shown.tip : peak ? `peak ${format(peak)}` : "none yet"}
        </span>
      </figcaption>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        className="h-20 w-full"
        role="img"
        aria-label={label}
        onMouseLeave={() => setHover(null)}
      >
        <line x1={0} x2={w} y1={h - 0.5} y2={h - 0.5} className="stroke-edge" strokeWidth={1} />
        {points.map((p, i) => {
          const bh = p.value ? Math.max(2, (p.value / max) * (h - 4)) : 0;
          return (
            <g key={p.key} onMouseEnter={() => setHover(i)} onTouchStart={() => setHover(i)}>
              {/* Hit target: the whole column, bigger than the bar. */}
              <rect x={i * step} y={0} width={step} height={h} fill="transparent" />
              {bh > 0 && (
                <rect
                  x={i * step + 1}
                  y={h - bh}
                  width={bar}
                  height={bh}
                  rx={Math.min(2, bar / 2)}
                  className={hover === i ? "fill-sky-300" : "fill-sky-500"}
                />
              )}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

function StatusPanel({ onError }: PanelProps) {
  const [status, setStatus] = useState<OpsStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api
      .systemStatus()
      .then((s) => {
        setStatus(s);
        setMessage((m) => m || s.maintenance || "");
      })
      .catch((e) => onError(e.message))
      .finally(() => setLoading(false));
  }, [onError]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  if (!status) return <p className="text-sm text-slate-500">Loading…</p>;

  const toggleMaintenance = async () => {
    try {
      await api.setMaintenance(!status.maintenance, message || undefined);
      load();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const down = status.services.filter((s) => !s.ok);
  const last = status.last_24h;
  const traffic = status.traffic;
  const requests = traffic.reduce((n, m) => n + m.requests, 0);
  const errors = traffic.reduce((n, m) => n + m.errors, 0);
  const limited = traffic.reduce((n, m) => n + m.limited, 0);
  const timed = traffic.filter((m) => m.avg_ms != null);
  const avgMs = timed.length
    ? Math.round(timed.reduce((n, m) => n + (m.avg_ms ?? 0) * m.requests, 0) / requests)
    : null;

  return (
    <div>
      <div
        className={`mb-6 flex items-center gap-2 rounded-lg border px-4 py-3 text-sm ${
          down.length ? "border-rose-500/50 bg-rose-500/10" : "border-emerald-500/40 bg-emerald-500/10"
        }`}
      >
        {down.length ? (
          <XCircle size={18} className="shrink-0 text-rose-400" />
        ) : (
          <CheckCircle2 size={18} className="shrink-0 text-emerald-400" />
        )}
        <span>
          {down.length
            ? `${down.map((s) => s.name).join(", ")} ${down.length === 1 ? "is" : "are"} down`
            : "Everything is running"}
        </span>
        <button
          onClick={load}
          className="ml-auto flex items-center gap-1 text-slate-400 hover:text-white"
          aria-label="Refresh"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      <Section title="Services">
        <ul className="grid gap-2 sm:grid-cols-2">
          {status.services.map((s) => (
            <li
              key={s.name}
              className="flex min-w-0 items-center gap-2 rounded-lg border border-edge bg-panel px-3 py-2 text-sm"
            >
              {s.ok ? (
                <CheckCircle2 size={16} className="shrink-0 text-emerald-400" aria-label="Up" />
              ) : (
                <XCircle size={16} className="shrink-0 text-rose-400" aria-label="Down" />
              )}
              <span className="shrink-0">{s.name}</span>
              <span className="ml-auto min-w-0 truncate text-xs text-slate-500" title={s.detail}>
                {s.ok ? s.detail : s.detail || "down"}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-slate-500">
          Waiting in queues: {status.queues.orchestrator ?? "?"} chat and agent jobs,{" "}
          {status.queues.documents ?? "?"} documents. Traces and per-call costs are in{" "}
          <a
            href={status.links.langfuse}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-0.5 text-sky-400 hover:underline"
          >
            Langfuse <ExternalLink size={11} />
          </a>{" "}
          (on the Mac).
        </p>
      </Section>

      <Section title="Last 24 hours">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Tile
            label="Chat turns"
            value={last.turns}
            hint={last.failed_turns ? `${last.failed_turns} failed` : "none failed"}
          />
          <Tile
            label="Tokens"
            value={formatTokens(last.input_tokens + last.output_tokens)}
            hint={`${formatTokens(last.input_tokens)} in · ${formatTokens(last.output_tokens)} out`}
          />
          <Tile
            label="Agent runs"
            value={`${status.runs_live} live`}
            hint={`${usd(last.run_cost_usd)} spent${last.failed_runs ? ` · ${last.failed_runs} failed` : ""}`}
          />
          <Tile
            label="People"
            value={status.people.active}
            hint={`${status.people.signed_in_24h} signed in · ${status.people.invited} invited`}
          />
          <Tile
            label="Model spend this period"
            value={usd(status.spend.period_usd)}
            hint={`of ${usd(status.spend.budgets_usd)} in budgets`}
          />
          <Tile
            label="Files"
            value={status.files.processing ? `${status.files.processing} reading` : "idle"}
            hint={status.files.failed ? `${status.files.failed} failed` : "none failed"}
          />
          <Tile
            label="Last backup"
            value={
              status.backup.last_ok?.finished_at ? formatAgo(status.backup.last_ok.finished_at) : "never"
            }
            hint={
              status.backup.stale ? (
                <span className="inline-flex items-center gap-1 text-amber-400">
                  <AlertTriangle size={12} /> overdue
                </span>
              ) : (
                status.backup.last_ok?.size_bytes != null && formatSize(status.backup.last_ok.size_bytes)
              )
            }
          />
          <Tile
            label="API, last hour"
            value={requests}
            hint={`${avgMs != null ? `${avgMs} ms avg · ` : ""}${errors} errors · ${limited} limited`}
          />
        </div>
      </Section>

      <Section title="Traffic">
        <Bars
          label="API requests per minute, last hour"
          points={traffic.map((m) => ({
            key: m.minute,
            value: m.requests,
            tip: `${new Date(m.minute).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}: ${m.requests} requests${m.errors ? `, ${m.errors} errors` : ""}${m.limited ? `, ${m.limited} limited` : ""}`,
          }))}
        />
      </Section>

      <Section title="Maintenance mode">
        <div className="rounded-lg border border-edge bg-panel p-4 text-sm">
          <p className="mb-3 text-slate-400">
            While it is on, everyone sees the message and can read their chats, but only admins can
            start new turns and agent runs. Use it before restarting or upgrading Jarvis.
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Jarvis is down for maintenance. Back soon."
              maxLength={300}
              className="min-w-56 flex-1 rounded-md border border-edge bg-ink px-3 py-1.5"
            />
            <button
              onClick={toggleMaintenance}
              className={`rounded-md px-3 py-1.5 font-medium ${
                status.maintenance ? "bg-emerald-500 text-ink" : "bg-amber-500 text-ink"
              }`}
            >
              {status.maintenance ? "Turn off" : "Turn on"}
            </button>
          </div>
          {status.maintenance && <p className="mt-2 text-amber-400">On: "{status.maintenance}"</p>}
        </div>
      </Section>

      <Section title="Recent failures">
        {status.failures.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing has failed recently.</p>
        ) : (
          <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel text-sm">
            {status.failures.map((f, i) => (
              <li key={i} className="flex flex-wrap gap-x-3 px-3 py-2">
                <span className="w-12 text-xs uppercase text-slate-500">{f.kind}</span>
                <span className="text-xs text-slate-500">{formatDateTime(f.at)}</span>
                <span className="text-xs text-slate-400">{f.user}</span>
                <span className="w-full break-words text-slate-300">{f.error || "No message"}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

function UsagePanel({ onError }: PanelProps) {
  const [days, setDays] = useState(7);
  const [usage, setUsage] = useState<Usage | null>(null);
  useEffect(() => {
    api.usage(days).then(setUsage).catch((e) => onError(e.message));
  }, [days, onError]);
  if (!usage) return <p className="text-sm text-slate-500">Loading…</p>;
  const dayLabel = (d: string) =>
    new Date(`${d}T00:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return (
    <div>
      <div className="mb-4 flex gap-2 text-sm">
        {[7, 30, 90].map((d) => (
          <button
            key={d}
            onClick={() => setDays(d)}
            className={`rounded-md border border-edge px-2 py-0.5 ${days === d ? "bg-edge text-white" : "text-slate-400"}`}
          >
            {d} days
          </button>
        ))}
      </div>
      <div className="mb-6 grid gap-2 md:grid-cols-2">
        <Bars
          label="Chat turns per day"
          points={usage.series.map((s) => ({
            key: s.day,
            value: s.turns,
            tip: `${dayLabel(s.day)}: ${s.turns} turns${s.failed_turns ? `, ${s.failed_turns} failed` : ""}`,
          }))}
        />
        <Bars
          label="Agent run cost per day"
          format={usd}
          points={usage.series.map((s) => ({
            key: s.day,
            value: s.run_cost_usd,
            tip: `${dayLabel(s.day)}: ${usd(s.run_cost_usd)}`,
          }))}
        />
      </div>
      <div className="overflow-x-auto rounded-lg border border-edge">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="bg-panel text-left text-xs text-slate-400">
            <tr>
              <th className="px-3 py-2 font-normal">Person</th>
              <th className="px-3 py-2 text-right font-normal">Turns</th>
              <th className="px-3 py-2 text-right font-normal">Tokens</th>
              <th className="px-3 py-2 text-right font-normal">Runs</th>
              <th className="px-3 py-2 text-right font-normal">Run cost</th>
              <th className="px-3 py-2 text-right font-normal">Files</th>
              <th className="px-3 py-2 font-normal">Last active</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge">
            {usage.people.map((p) => (
              <tr key={p.user_id}>
                <td className="px-3 py-2">
                  <div className="truncate">{p.name || p.email}</div>
                  {p.name && <div className="text-xs text-slate-500">{p.email}</div>}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {p.turns}
                  {p.failed_turns > 0 && (
                    <span className="ml-1 text-xs text-rose-400">({p.failed_turns} failed)</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {formatTokens(p.input_tokens + p.output_tokens)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{p.runs}</td>
                <td className="px-3 py-2 text-right tabular-nums">{usd(p.run_cost_usd)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{p.files}</td>
                <td className="px-3 py-2 text-slate-400">
                  {p.last_active ? formatAgo(p.last_active) : "–"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        Each person's total model spend and budget is on the People page.
      </p>
    </div>
  );
}

function describe(e: AuditEntry): string {
  const who = e.target_user ?? (e.data.email as string | undefined);
  const details = Object.entries(e.data)
    .filter(([k]) => k !== "email")
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
  return [who, details].filter(Boolean).join(" · ");
}

function AuditPanel({ onError }: PanelProps) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [actions, setActions] = useState<string[]>([]);
  const [action, setAction] = useState("");
  const [more, setMore] = useState(false);

  useEffect(() => {
    api
      .audit({ action: action || undefined })
      .then((r) => {
        setEntries(r.entries);
        setActions(r.actions);
        setMore(r.entries.length >= 100);
      })
      .catch((e) => onError(e.message));
  }, [action, onError]);

  const loadMore = async () => {
    const before = entries[entries.length - 1]?.at;
    try {
      const r = await api.audit({ action: action || undefined, before });
      setEntries((list) => [...list, ...r.entries]);
      setMore(r.entries.length >= 100);
    } catch (e) {
      onError((e as Error).message);
    }
  };

  const prefixes = [...new Set(actions.map((a) => a.split(".")[0] + "."))];
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2 text-sm">
        <select
          value={action}
          onChange={(e) => setAction(e.target.value)}
          className="rounded-md border border-edge bg-panel px-2 py-1"
          aria-label="Filter by action"
        >
          <option value="">All events</option>
          {prefixes.map((p) => (
            <optgroup key={p} label={p.slice(0, -1)}>
              <option value={p}>All {p.slice(0, -1)} events</option>
              {actions
                .filter((a) => a.startsWith(p))
                .map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
            </optgroup>
          ))}
        </select>
      </div>
      {entries.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing recorded yet.</p>
      ) : (
        <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel text-sm">
          {entries.map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline gap-x-3 px-3 py-2">
              <span className="w-28 shrink-0 text-xs text-slate-500">{formatDateTime(e.at)}</span>
              <span className="font-mono text-xs text-sky-300">{e.action}</span>
              <span className="text-xs text-slate-400">{e.actor ?? "system"}</span>
              <span className="w-full break-words pl-0 text-slate-300 sm:w-auto sm:flex-1">
                {describe(e)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {more && (
        <button
          onClick={loadMore}
          className="mt-3 rounded-md border border-edge px-3 py-1 text-sm hover:bg-edge/60"
        >
          Older
        </button>
      )}
    </div>
  );
}

const BACKUP_STATUS: Record<Backup["status"], string> = {
  requested: "text-slate-400",
  running: "text-sky-400",
  ok: "text-emerald-400",
  failed: "text-rose-400",
};

function BackupsPanel({ onError }: PanelProps) {
  const [backups, setBackups] = useState<Backup[]>([]);
  const load = useCallback(() => {
    api.backups().then(setBackups).catch((e) => onError(e.message));
  }, [onError]);
  useEffect(() => {
    load();
    const t = setInterval(load, 15_000);
    return () => clearInterval(t);
  }, [load]);

  const busy = backups.some((b) => b.status === "requested" || b.status === "running");
  const backUp = async () => {
    try {
      await api.backupNow();
      load();
    } catch (e) {
      onError((e as Error).message);
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-edge bg-panel p-4 text-sm">
        <p className="flex-1 text-slate-400">
          Every night Jarvis saves its databases and everyone's files to the backups folder on the
          Mac (BACKUP_DIR) and keeps two weeks of them. Restore one with{" "}
          <code className="text-slate-300">scripts/restore.sh</code>.
        </p>
        <button
          onClick={backUp}
          disabled={busy}
          className="rounded-md bg-sky-500 px-3 py-1.5 font-medium text-ink disabled:opacity-50"
        >
          {busy ? "Backing up…" : "Back up now"}
        </button>
      </div>
      {backups.length === 0 ? (
        <p className="text-sm text-slate-500">
          No backups yet. The backup service makes the first one when it starts.
        </p>
      ) : (
        <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel text-sm">
          {backups.map((b) => (
            <li key={b.id} className="flex flex-wrap items-baseline gap-x-3 px-3 py-2">
              <span className="w-28 text-xs text-slate-500">{formatDateTime(b.created_at)}</span>
              <span className={`w-16 ${BACKUP_STATUS[b.status]}`}>{b.status}</span>
              <span className="text-xs text-slate-500">{b.kind}</span>
              <span className="font-mono text-xs text-slate-400">{b.name ?? ""}</span>
              <span className="ml-auto text-xs tabular-nums text-slate-400">
                {b.size_bytes != null ? formatSize(b.size_bytes) : ""}
              </span>
              {b.error && <span className="w-full break-words text-rose-300">{b.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
