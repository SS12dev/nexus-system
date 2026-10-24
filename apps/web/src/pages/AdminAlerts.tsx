import { AlertTriangle, CheckCircle2, Mail, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { APP_NAME } from "../lib/assistant";
import { formatAgo, formatDateTime } from "../lib/format";
import type { AlertRule, Alerts } from "../lib/types";
import { Section } from "./AdminParts";

const UNITS: Record<string, (n: number) => string> = {
  backup_stale_hours: (n) => `older than ${n} h`,
  alert_api_errors_5m: (n) => `${n} in 5 min`,
  alert_job_failures_15m: (n) => `${n} in 15 min`,
  alert_llm_failures_15m: (n) => `${n} in 15 min`,
  alert_daily_spend_usd: (n) => `over $${n}`,
  alert_budget_percent: (n) => `${n}% of budget`,
};

function threshold(r: AlertRule): string {
  if (!r.setting) return "always on";
  if (!r.on) return "off";
  return UNITS[r.setting]?.(r.threshold ?? 0) ?? String(r.threshold);
}

/** Active alerts, what is checked with today's thresholds, and the last changes. */
export function AlertsPanel({
  onError,
  onOpen,
  onChange,
}: {
  onError: (msg: string) => void;
  onOpen: (tab: string) => void;
  onChange: () => void;
}) {
  const [data, setData] = useState<Alerts | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api
      .alerts()
      .then((d) => {
        setData(d);
        onChange();
      })
      .catch((e) => onError(e.message))
      .finally(() => setLoading(false));
  }, [onError, onChange]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  if (!data) return <p className="text-sm text-slate-500">Loading…</p>;
  const active = data.active;

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
        <span>
          {active.length
            ? `${active.length} ${active.length === 1 ? "thing needs" : "things need"} you`
            : "Nothing needs you right now"}
        </span>
        <button
          onClick={load}
          className="ml-auto flex items-center gap-1 text-slate-400 hover:text-white"
          aria-label="Refresh"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      {active.length > 0 && (
        <Section title="Active">
          <ul className="space-y-2">
            {active.map((a) => (
              <li key={a.key} className="rounded-lg border border-rose-500/40 bg-panel px-4 py-3 text-sm">
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <span className="font-medium text-rose-300">{a.title}</span>
                  {a.since && <span className="ml-auto text-xs text-slate-500">since {formatAgo(a.since)}</span>}
                </div>
                {a.detail && <p className="mt-1 text-slate-400">{a.detail}</p>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section
        title="What is checked"
        action={
          <button onClick={() => onOpen("config")} className="text-xs text-sky-400 hover:underline">
            Change thresholds
          </button>
        }
      >
        <p className="mb-2 text-sm text-slate-400">
          {data.interval_s > 0
            ? `Every ${data.interval_s} seconds ${APP_NAME} runs these checks.`
            : "Alert checks are off (Configuration, Alerts and backups)."}{" "}
          A problem seen twice in a row becomes an alert, so one slow answer doesn't raise one. A note follows
          when it clears. Model-call checks read LiteLLM's spend logs, which arrive about a minute after each
          call.
        </p>
        <div className="overflow-x-auto rounded-lg border border-edge bg-panel">
          <table className="w-full min-w-[480px] text-sm">
            <thead className="text-left text-xs text-slate-400">
              <tr className="border-b border-edge">
                <th className="px-3 py-2 font-normal">Check</th>
                <th className="px-3 py-2 font-normal">Raised when</th>
                <th className="px-3 py-2 text-right font-normal">Threshold</th>
              </tr>
            </thead>
            <tbody>
              {data.rules.map((r) => (
                <tr key={r.name} className="border-b border-edge/60 last:border-0">
                  <td className="px-3 py-2 whitespace-nowrap">{r.name}</td>
                  <td className="px-3 py-2 text-slate-400">{r.what}</td>
                  <td className={`px-3 py-2 text-right whitespace-nowrap tabular-nums ${r.on ? "" : "text-slate-500"}`}>
                    {threshold(r)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
          <Mail size={12} />
          {data.email
            ? "Owners also get an email when an alert is raised and when it clears."
            : "Alerts show here only. Set up email (SMTP) and turn on Email alerts on the Configuration tab to get them by email too."}
        </p>
      </Section>

      <Section title="History">
        {data.recent.length > 0 ? (
          <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel text-sm">
            {data.recent.slice(0, 25).map((a, i) => (
              <li key={i} className="flex flex-wrap gap-x-3 px-3 py-1.5">
                <span className="w-28 text-xs text-slate-500">{a.at ? formatDateTime(a.at) : ""}</span>
                <span className={`w-16 text-xs ${a.state === "raised" ? "text-rose-400" : "text-emerald-400"}`}>
                  {a.state}
                </span>
                <span className="text-slate-300">{a.title}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">No alerts so far.</p>
        )}
      </Section>
    </div>
  );
}
