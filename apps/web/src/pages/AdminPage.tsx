import {
  Activity,
  AlertTriangle,
  Archive,
  BarChart3,
  Bell,
  CheckCircle2,
  ChevronDown,
  Cpu,
  ExternalLink,
  Gauge,
  MessageSquareHeart,
  RefreshCw,
  ScrollText,
  SlidersHorizontal,
  Sparkles,
  Target,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { formatAgo, formatDateTime, formatSize, formatTokens } from "../lib/format";
import type { AuditEntry, Backup, LoggedError, Monitoring, MonitorRow, OpsStatus, Usage, User } from "../lib/types";
import { APP_NAME } from "../lib/assistant";
import { ConfigPanel } from "./AdminConfig";
import { FeedbackPanel } from "./AdminFeedback";
import { AlertsPanel } from "./AdminAlerts";
import { ImprovementsPanel } from "./AdminImprovements";
import { ModelsPanel } from "./AdminModels";
import { RecallPanel } from "./AdminRecall";
import { Bars, PeopleFilter, Section, Tile, usd } from "./AdminParts";

type Tab =
  | "status"
  | "alerts"
  | "models"
  | "usage"
  | "improvements"
  | "feedback"
  | "recall"
  | "monitoring"
  | "audit"
  | "backups"
  | "config";

type TabInfo = { key: Tab; label: string; icon: LucideIcon; hint: string };

/** Grouped as on the side of the page; on a phone they run in one scrolling row. */
const GROUPS: { title: string; tabs: TabInfo[] }[] = [
  {
    title: "Overview",
    tabs: [
      { key: "status", label: "Status", icon: Activity, hint: "Services, the last day and maintenance" },
      { key: "alerts", label: "Alerts", icon: Bell, hint: "What needs you, and what is checked" },
    ],
  },
  {
    title: "Usage and cost",
    tabs: [
      { key: "models", label: "Models", icon: Cpu, hint: "Model calls, cost, latency and caching" },
      { key: "usage", label: "Usage", icon: BarChart3, hint: "Turns, runs and files per person" },
    ],
  },
  {
    title: "Improve",
    tabs: [
      {
        key: "improvements",
        label: "Improvements",
        icon: Sparkles,
        hint: "Changes the assistant proposes, for you to approve",
      },
      { key: "feedback", label: "Feedback", icon: MessageSquareHeart, hint: "What people sent you from the app" },
      { key: "recall", label: "Recall", icon: Target, hint: "How well memory finds the right thing" },
    ],
  },
  {
    title: "Operations",
    tabs: [
      { key: "monitoring", label: "Monitoring", icon: Gauge, hint: "Routes, jobs and errors" },
      { key: "audit", label: "Audit log", icon: ScrollText, hint: "Who did what" },
      { key: "backups", label: "Backups", icon: Archive, hint: "Nightly backups and restores" },
      { key: "config", label: "Configuration", icon: SlidersHorizontal, hint: "Runtime settings" },
    ],
  },
];
const ALL_TABS = GROUPS.flatMap((g) => g.tabs);
const FILTERED: Tab[] = ["status", "usage", "audit", "models"];

/** Health, alerts, model usage, improvements, monitoring, the audit log, backups and runtime
 * settings. Owners only. The open tab is in the address (?tab=models), so it can be linked. */
export function AdminPage() {
  const [params, setParams] = useSearchParams();
  const asked = params.get("tab") as Tab | null;
  const tab: Tab = asked && ALL_TABS.some((t) => t.key === asked) ? asked : "status";
  const [error, setError] = useState("");
  const [people, setPeople] = useState<User[]>([]);
  // Who the per-person figures cover; empty means everyone.
  const [picked, setPicked] = useState<string[]>([]);
  const [badges, setBadges] = useState({ alerts: 0, proposals: 0 });

  useEffect(() => {
    api.users().then(setPeople).catch((e) => setError(e.message));
  }, []);

  const refreshBadges = useCallback(() => {
    api.adminSummary().then(setBadges).catch(() => {});
  }, []);
  useEffect(() => {
    refreshBadges();
    const t = setInterval(refreshBadges, 60_000);
    return () => clearInterval(t);
  }, [refreshBadges]);

  const go = (key: Tab) => {
    setError("");
    setParams(key === "status" ? {} : { tab: key }, { replace: true });
  };
  const badge = (key: Tab) => (key === "alerts" ? badges.alerts : key === "improvements" ? badges.proposals : 0);
  const current = ALL_TABS.find((t) => t.key === tab)!;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 py-6">
        <h1 className="mb-1 text-xl font-semibold">Admin</h1>
        <p className="mb-4 text-sm text-slate-400">
          How {APP_NAME} is running, what it costs, what it proposes to improve and how it is set up. You see
          counts, costs and errors here, never anyone's chats.
        </p>
        {(badges.alerts > 0 || badges.proposals > 0) && (
          <div className="mb-4 flex flex-wrap gap-2 text-sm">
            {badges.alerts > 0 && tab !== "alerts" && (
              <button
                onClick={() => go("alerts")}
                className="flex items-center gap-2 rounded-lg border border-rose-500/50 bg-rose-500/10 px-3 py-1.5 text-rose-200 hover:bg-rose-500/20"
              >
                <AlertTriangle size={15} /> {badges.alerts} active {badges.alerts === 1 ? "alert" : "alerts"}
              </button>
            )}
            {badges.proposals > 0 && tab !== "improvements" && (
              <button
                onClick={() => go("improvements")}
                className="flex items-center gap-2 rounded-lg border border-violet-500/50 bg-violet-500/10 px-3 py-1.5 text-violet-200 hover:bg-violet-500/20"
              >
                <Sparkles size={15} /> {badges.proposals}{" "}
                {badges.proposals === 1 ? "improvement waits" : "improvements wait"} for you
              </button>
            )}
          </div>
        )}
        <div className="md:flex md:gap-6">
          <nav aria-label="Admin sections" className="mb-4 md:mb-0 md:w-48 md:shrink-0">
            {/* Phone: one scrolling row. */}
            <div className="-mx-4 flex gap-1 overflow-x-auto px-4 pb-1 md:hidden">
              {ALL_TABS.map((t) => (
                <TabButton key={t.key} t={t} active={tab === t.key} count={badge(t.key)} onClick={() => go(t.key)} compact />
              ))}
            </div>
            {/* Larger screens: grouped down the side. */}
            <div className="sticky top-0 hidden space-y-4 md:block">
              {GROUPS.map((g) => (
                <div key={g.title}>
                  <div className="mb-1 px-2 text-xs font-medium tracking-wide text-slate-500 uppercase">{g.title}</div>
                  <div className="space-y-0.5">
                    {g.tabs.map((t) => (
                      <TabButton key={t.key} t={t} active={tab === t.key} count={badge(t.key)} onClick={() => go(t.key)} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </nav>
          <main className="min-w-0 flex-1">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <div className="min-w-0">
                <h2 className="text-lg font-medium">{current.label}</h2>
                <p className="text-xs text-slate-500">{current.hint}</p>
              </div>
              {FILTERED.includes(tab) && (
                <div className="ml-auto">
                  <PeopleFilter people={people} picked={picked} onChange={setPicked} />
                </div>
              )}
            </div>
            {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
            {tab === "status" && <StatusPanel onError={setError} userIds={picked} />}
            {tab === "alerts" && <AlertsPanel onError={setError} onOpen={(t) => go(t as Tab)} onChange={refreshBadges} />}
            {tab === "models" && <ModelsPanel onError={setError} userIds={picked} onPickPerson={(id) => setPicked([id])} />}
            {tab === "usage" && <UsagePanel onError={setError} userIds={picked} />}
            {tab === "improvements" && (
              <ImprovementsPanel onError={setError} onOpen={(t) => go(t as Tab)} onChange={refreshBadges} />
            )}
            {tab === "feedback" && <FeedbackPanel onError={setError} />}
            {tab === "recall" && <RecallPanel onError={setError} onOpenConfig={() => go("config")} />}
            {tab === "monitoring" && <MonitoringPanel onError={setError} onOpenAlerts={() => go("alerts")} />}
            {tab === "audit" && <AuditPanel onError={setError} userIds={picked} />}
            {tab === "backups" && <BackupsPanel onError={setError} />}
            {tab === "config" && <ConfigPanel onError={setError} />}
          </main>
        </div>
      </div>
    </div>
  );
}

function TabButton({
  t,
  active,
  count,
  onClick,
  compact = false,
}: {
  t: TabInfo;
  active: boolean;
  count: number;
  onClick: () => void;
  compact?: boolean;
}) {
  const Icon = t.icon;
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      title={t.hint}
      className={`flex shrink-0 items-center gap-2 rounded-md text-sm ${compact ? "px-3 py-1.5" : "w-full px-2 py-1.5"} ${
        active ? "bg-edge text-white" : "text-slate-400 hover:bg-edge/50 hover:text-slate-200"
      }`}
    >
      <Icon size={15} className="shrink-0" />
      <span className="whitespace-nowrap">{t.label}</span>
      {count > 0 && (
        <span
          className={`ml-auto rounded-full px-1.5 text-xs font-medium tabular-nums ${
            t.key === "alerts" ? "bg-rose-500 text-white" : "bg-violet-500 text-white"
          }`}
        >
          {count}
        </span>
      )}
    </button>
  );
}

type PanelProps = { onError: (msg: string) => void };
type FilteredProps = PanelProps & { userIds: string[] };

function StatusPanel({ onError, userIds }: FilteredProps) {
  const [status, setStatus] = useState<OpsStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api
      .systemStatus(userIds)
      .then((s) => {
        setStatus(s);
        setMessage((m) => m || s.maintenance || "");
      })
      .catch((e) => onError(e.message))
      .finally(() => setLoading(false));
  }, [onError, userIds]);

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

      <Section
        title="Last 24 hours"
        action={
          userIds.length > 0 && (
            <span className="text-xs text-slate-500">
              For the people picked. Services and traffic are for all of {APP_NAME}.
            </span>
          )
        }
      >
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
            start new turns and agent runs. Use it before restarting or upgrading {APP_NAME}.
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder={`${APP_NAME} is down for maintenance. Back soon.`}
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

function UsagePanel({ onError, userIds }: FilteredProps) {
  const [days, setDays] = useState(7);
  const [usage, setUsage] = useState<Usage | null>(null);
  useEffect(() => {
    api.usage(days, userIds).then(setUsage).catch((e) => onError(e.message));
  }, [days, userIds, onError]);
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
  if (e.action === "config.changed" && e.data.changes && typeof e.data.changes === "object") {
    const changes = e.data.changes as Record<string, { from: unknown; to: unknown }>;
    return Object.entries(changes)
      .map(([name, c]) => `${name}: ${String(c.from) || "empty"} → ${String(c.to) || "empty"}`)
      .join(", ");
  }
  const who = e.target_user ?? (e.data.email as string | undefined);
  const details = Object.entries(e.data)
    .filter(([k]) => k !== "email")
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
  return [who, details].filter(Boolean).join(" · ");
}

function AuditPanel({ onError, userIds }: FilteredProps) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [actions, setActions] = useState<string[]>([]);
  const [action, setAction] = useState("");
  const [more, setMore] = useState(false);

  useEffect(() => {
    api
      .audit({ action: action || undefined, userIds })
      .then((r) => {
        setEntries(r.entries);
        setActions(r.actions);
        setMore(r.entries.length >= 100);
      })
      .catch((e) => onError(e.message));
  }, [action, userIds, onError]);

  const loadMore = async () => {
    const before = entries[entries.length - 1]?.at;
    try {
      const r = await api.audit({ action: action || undefined, before, userIds });
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
        <Select
          value={action}
          onChange={(e) => setAction(e.target.value)}
          aria-label="Filter by action"
          className="max-w-full"
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
        </Select>
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
          Every night {APP_NAME} saves its databases and everyone's files to the backups folder on the
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

const WINDOWS = [
  { minutes: 60, label: "Last hour" },
  { minutes: 360, label: "6 hours" },
  { minutes: 1440, label: "24 hours" },
];

function ms(n: number | null | undefined, slow = false): string {
  if (n == null) return slow ? "over 2 min" : "–";
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)} s` : `${n} ms`;
}

/** Routes or jobs: calls, failures and how long they took. */
function MonitorTable({ rows, kind }: { rows: MonitorRow[]; kind: "route" | "job" }) {
  if (rows.length === 0) return <p className="text-sm text-slate-500">Nothing in this window.</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-edge bg-panel">
      <table className="w-full min-w-[560px] text-sm">
        <thead className="text-left text-xs text-slate-400">
          <tr className="border-b border-edge">
            <th className="px-3 py-2 font-normal">{kind === "route" ? "Route" : "Job"}</th>
            <th className="px-3 py-2 text-right font-normal">Calls</th>
            <th className="px-3 py-2 text-right font-normal">Failed</th>
            <th className="px-3 py-2 text-right font-normal">Typical</th>
            <th className="px-3 py-2 text-right font-normal">Slowest 5%</th>
            {kind === "job" && <th className="px-3 py-2 text-right font-normal">Queue wait</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className="border-b border-edge/60 last:border-0">
              <td className="max-w-[22rem] truncate px-3 py-1.5 font-mono text-xs" title={r.name}>
                {r.name}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">{r.count}</td>
              <td className={`px-3 py-1.5 text-right tabular-nums ${r.failed ? "text-rose-400" : "text-slate-500"}`}>
                {r.failed}
                {r.failed > 0 && r.count > 0 && (
                  <span className="ml-1 text-xs">({Math.round((r.failed / r.count) * 100)}%)</span>
                )}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">≤ {ms(r.p50_ms, r.count > 0)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">≤ {ms(r.p95_ms, r.count > 0)}</td>
              {kind === "job" && (
                <td className="px-3 py-1.5 text-right tabular-nums text-slate-400">{ms(r.avg_wait_ms)}</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ErrorRow({ e }: { e: LoggedError }) {
  const [open, setOpen] = useState(false);
  const ids = (["request_id", "user_id", "chat_id", "run_id", "job", "trace_id"] as const).filter((k) => e[k]);
  return (
    <li className="px-3 py-2">
      <button onClick={() => setOpen(!open)} className="flex w-full flex-wrap items-baseline gap-x-3 text-left">
        <span className="w-28 shrink-0 text-xs text-slate-500">{formatDateTime(e.ts)}</span>
        <span className="w-24 shrink-0 text-xs text-slate-400">{e.service}</span>
        <ChevronDown
          size={14}
          className={`order-last ml-auto shrink-0 text-slate-500 transition sm:order-none ${open ? "rotate-180" : ""}`}
        />
        <span className="order-last min-w-0 basis-full break-words text-slate-200 sm:order-none sm:basis-0 sm:flex-1">
          {e.error ?? e.msg}
        </span>
      </button>
      {open && (
        <div className="mt-2 space-y-2 text-xs">
          {e.error && <p className="text-slate-300">{e.msg}</p>}
          {e.page && <p className="text-slate-400">Page: {e.page}</p>}
          {ids.length > 0 && (
            <p className="flex flex-wrap gap-x-3 font-mono text-slate-400">
              {ids.map((k) => (
                <span key={k}>
                  {k}={e[k]}
                </span>
              ))}
            </p>
          )}
          {e.stack && (
            <pre className="max-h-64 overflow-auto rounded-md bg-ink p-2 whitespace-pre-wrap text-slate-400">{e.stack}</pre>
          )}
        </div>
      )}
    </li>
  );
}

function MonitoringPanel({ onError, onOpenAlerts }: PanelProps & { onOpenAlerts: () => void }) {
  const [minutes, setMinutes] = useState(60);
  const [data, setData] = useState<Monitoring | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api
      .monitoring(minutes)
      .then(setData)
      .catch((e) => onError(e.message))
      .finally(() => setLoading(false));
  }, [minutes, onError]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  if (!data) return <p className="text-sm text-slate-500">Loading…</p>;
  const active = data.alerts.active;

  return (
    <div>
      <div
        className={`mb-6 flex items-center gap-2 rounded-lg border px-4 py-3 text-sm ${
          active.length ? "border-rose-500/50 bg-rose-500/10" : "border-emerald-500/40 bg-emerald-500/10"
        }`}
      >
        {active.length ? (
          <AlertTriangle size={18} className="shrink-0 text-rose-400" />
        ) : (
          <CheckCircle2 size={18} className="shrink-0 text-emerald-400" />
        )}
        <span>{active.length ? `${active.length} active ${active.length === 1 ? "alert" : "alerts"}` : "No alerts"}</span>
        <button onClick={onOpenAlerts} className="text-sky-400 hover:underline">
          {active.length ? "See them" : "What is checked"}
        </button>
        <button
          onClick={load}
          className="ml-auto flex items-center gap-1 text-slate-400 hover:text-white"
          aria-label="Refresh"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-400">Figures for</span>
        {WINDOWS.map((w) => (
          <button
            key={w.minutes}
            onClick={() => setMinutes(w.minutes)}
            className={`rounded-md px-3 py-1 ${minutes === w.minutes ? "bg-edge text-white" : "text-slate-400 hover:bg-edge/50"}`}
          >
            {w.label}
          </button>
        ))}
      </div>

      <Section title="API routes">
        <p className="mb-2 text-sm text-slate-400">
          Every API call, by route. "Failed" counts server errors (5xx), not refusals like a wrong
          password. "Typical" is the time half the calls beat; "Slowest 5%" is the time 95% beat. Live
          chat streams over the WebSocket, so a reply's length doesn't show here.
        </p>
        <MonitorTable rows={data.routes} kind="route" />
      </Section>

      <Section title="Background jobs">
        <p className="mb-2 text-sm text-slate-400">
          Work the orchestrator and document worker do off the request: chat turns, agent runs, memory,
          file reading and the timed jobs. "Queue wait" is how long a job sat before a worker took it.
        </p>
        <MonitorTable rows={data.jobs} kind="job" />
      </Section>

      <Section title="Recent errors">
        <p className="mb-2 text-sm text-slate-400">
          The last errors logged by the API, the workers and people's browsers, newest first. Tap one for
          its ids and stack. Find everything a request did with{" "}
          <code className="text-slate-300">compose logs api orchestrator | grep &lt;request_id&gt;</code>; the
          trace_id opens the same turn in Langfuse.
        </p>
        {data.errors.length === 0 ? (
          <p className="text-sm text-slate-500">No errors logged.</p>
        ) : (
          <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel text-sm">
            {data.errors.map((e, i) => (
              <ErrorRow key={`${e.ts}-${i}`} e={e} />
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}
