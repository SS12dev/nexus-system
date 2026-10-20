import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ExternalLink,
  RefreshCw,
  Users,
  X,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { formatAgo, formatDateTime, formatSize, formatTokens } from "../lib/format";
import type { AuditEntry, Backup, OpsStatus, Usage, User } from "../lib/types";
import { APP_NAME } from "../lib/assistant";

type Tab = "status" | "usage" | "audit" | "backups";
const TABS: { key: Tab; label: string }[] = [
  { key: "status", label: "Status" },
  { key: "usage", label: "Usage" },
  { key: "audit", label: "Audit log" },
  { key: "backups", label: "Backups" },
];

/** Health, traffic, usage, the audit log, backups and maintenance mode. Owners only. */
export function AdminPage() {
  const [tab, setTab] = useState<Tab>("status");
  const [error, setError] = useState("");
  const [people, setPeople] = useState<User[]>([]);
  // Who the per-person figures cover; empty means everyone.
  const [picked, setPicked] = useState<string[]>([]);

  useEffect(() => {
    api.users().then(setPeople).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-6">
        <h1 className="mb-1 text-xl font-semibold">Admin</h1>
        <p className="mb-4 text-sm text-slate-400">
          How {APP_NAME} is running, who uses it and what it costs. You see counts, costs and errors
          here, never anyone's chats.
        </p>
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
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
          {tab !== "backups" && (
            <div className="ml-auto">
              <PeopleFilter people={people} picked={picked} onChange={setPicked} />
            </div>
          )}
        </div>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
        {tab === "status" && <StatusPanel onError={setError} userIds={picked} />}
        {tab === "usage" && <UsagePanel onError={setError} userIds={picked} />}
        {tab === "audit" && <AuditPanel onError={setError} userIds={picked} />}
        {tab === "backups" && <BackupsPanel onError={setError} />}
      </div>
    </div>
  );
}

type PanelProps = { onError: (msg: string) => void };
type FilteredProps = PanelProps & { userIds: string[] };

const personName = (u: Pick<User, "name" | "email">) => u.name || u.email;

/** Pick one person (tap their name) or several (tick the boxes). None picked means everyone. */
function PeopleFilter({
  people,
  picked,
  onChange,
}: {
  people: User[];
  picked: string[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent) {
        // Escape closes and hands focus back to the button, like a native dropdown.
        if (e.key !== "Escape") return;
        setOpen(false);
        trigger.current?.focus();
      } else if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const chosen = people.filter((p) => picked.includes(p.id));
  const summary =
    picked.length === 0
      ? "Everyone"
      : picked.length === 1
        ? (chosen[0] && personName(chosen[0])) || "1 person"
        : `${picked.length} people`;
  const q = query.trim().toLowerCase();
  const shown = q
    ? people.filter((p) => `${p.name ?? ""} ${p.email}`.toLowerCase().includes(q))
    : people;
  const toggle = (id: string) =>
    onChange(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);

  return (
    <div ref={box} className="relative">
      <div className="flex items-center rounded-md border border-edge bg-panel">
        <button
          ref={trigger}
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-label={`Show data for: ${summary}`}
          className="flex max-w-56 items-center gap-1.5 rounded-md py-1 pr-2 pl-2.5 hover:bg-edge/40"
        >
          <Users size={14} className="shrink-0 text-slate-400" />
          <span className="truncate">{summary}</span>
          <ChevronDown size={14} className="shrink-0 text-slate-400" />
        </button>
        {picked.length > 0 && (
          <button
            onClick={() => onChange([])}
            className="border-l border-edge px-1.5 py-1 text-slate-400 hover:text-white"
            aria-label="Show everyone"
            title="Show everyone"
          >
            <X size={14} />
          </button>
        )}
      </div>
      {open && (
        <div
          role="dialog"
          aria-label="Choose people"
          className="absolute right-0 z-20 mt-1 w-72 max-w-[calc(100vw-2rem)] rounded-lg border border-edge bg-panel p-2 shadow-xl"
        >
          {people.length > 6 && (
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a person"
              className="mb-2 w-full rounded-md border border-edge bg-ink px-2 py-1"
            />
          )}
          <button
            onClick={() => {
              onChange([]);
              setOpen(false);
            }}
            className={`mb-1 w-full rounded-md px-2 py-1.5 text-left hover:bg-edge/60 ${picked.length === 0 ? "text-sky-300" : ""}`}
          >
            Everyone
          </button>
          <ul aria-label="People" className="max-h-72 overflow-y-auto">
            {shown.map((p) => (
              <li key={p.id} className="flex items-center gap-2 rounded-md px-2 hover:bg-edge/60">
                <input
                  type="checkbox"
                  checked={picked.includes(p.id)}
                  onChange={() => toggle(p.id)}
                  aria-label={`Include ${personName(p)}`}
                />
                <button
                  onClick={() => {
                    onChange([p.id]);
                    setOpen(false);
                  }}
                  className="min-w-0 flex-1 py-1.5 text-left"
                  title={`Only ${personName(p)}`}
                >
                  <div className="truncate">{personName(p)}</div>
                  {p.name && <div className="truncate text-xs text-slate-500">{p.email}</div>}
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-2 border-t border-edge px-2 pt-2 text-xs text-slate-500">
            Tap a name to see only them, or tick several.
          </p>
        </div>
      )}
    </div>
  );
}

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
