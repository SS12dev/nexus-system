import { Database, Info, RefreshCw, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { formatDateTime, formatTokens } from "../lib/format";
import type { LlmGroup, LlmUsage } from "../lib/types";
import { Bars, Section, Tile } from "./AdminParts";

type Range = "24h" | "7d" | "30d" | "90d" | "custom";
const RANGES: { key: Range; label: string; hours?: number }[] = [
  { key: "24h", label: "24 hours", hours: 24 },
  { key: "7d", label: "7 days", hours: 24 * 7 },
  { key: "30d", label: "30 days", hours: 24 * 30 },
  { key: "90d", label: "90 days", hours: 24 * 90 },
  { key: "custom", label: "Dates" },
];

const day = (d: Date) => d.toISOString().slice(0, 10);

/** Money with enough places for the small sums model calls cost. */
export function cost(n: number): string {
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 100) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(0)}`;
}

const pct = (n: number | null | undefined) => (n == null ? "–" : `${(n * 100).toFixed(n < 0.1 ? 1 : 0)}%`);

function ms(n: number | null | undefined): string {
  if (n == null) return "–";
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)} s` : `${n} ms`;
}

type Kind = "model" | "purpose" | "person";

function GroupTable({
  rows,
  kind,
  labels,
  onPick,
}: {
  rows: LlmGroup[];
  kind: Kind;
  labels: Record<string, string>;
  onPick?: (key: string) => void;
}) {
  if (rows.length === 0) return <p className="text-sm text-slate-500">No calls in this window.</p>;
  const total = rows.reduce((n, r) => n + r.spend_usd, 0) || 1;
  const name = (r: LlmGroup) =>
    kind === "purpose"
      ? labels[r.key ?? ""] ?? r.key
      : kind === "person"
        ? r.name || r.email || (r.key ? "Removed user or a system key" : "No user (master key)")
        : r.key;
  return (
    <div className="overflow-x-auto rounded-lg border border-edge bg-panel">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="text-left text-xs text-slate-400">
          <tr className="border-b border-edge">
            <th className="px-3 py-2 font-normal">{kind === "model" ? "Model" : kind === "purpose" ? "Used for" : "Person"}</th>
            <th className="px-3 py-2 text-right font-normal">Calls</th>
            <th className="px-3 py-2 text-right font-normal">Failed</th>
            <th className="px-3 py-2 text-right font-normal">Tokens in / out</th>
            <th className="px-3 py-2 text-right font-normal" title="Calls LiteLLM answered from its cache">
              Cache hits
            </th>
            <th className="px-3 py-2 text-right font-normal" title="Typical and slowest 5%, uncached calls">
              Latency
            </th>
            <th className="px-3 py-2 text-right font-normal">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key ?? "none"} className="border-b border-edge/60 last:border-0">
              <td className="max-w-[16rem] px-3 py-1.5">
                {kind === "person" && onPick && r.key ? (
                  <button
                    onClick={() => onPick(r.key!)}
                    className="truncate text-left hover:text-sky-300 hover:underline"
                    title="Show only this person"
                  >
                    {name(r)}
                  </button>
                ) : (
                  <span className={`truncate ${kind === "model" ? "font-mono text-xs" : ""}`}>{name(r)}</span>
                )}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">{r.calls}</td>
              <td className={`px-3 py-1.5 text-right tabular-nums ${r.failed ? "text-rose-400" : "text-slate-500"}`}>
                {r.failed}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">
                {formatTokens(r.input_tokens)} / {formatTokens(r.output_tokens)}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">{pct(r.cache_hit_rate)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-slate-300">
                {ms(r.p50_ms)} <span className="text-slate-500">/ {ms(r.p95_ms)}</span>
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">
                {cost(r.spend_usd)}
                <span className="ml-1 text-xs text-slate-500">{Math.round((r.spend_usd / total) * 100)}%</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Model calls from LiteLLM's spend logs: cost, tokens, latency and caching, by model, person
 * and purpose, over a chosen window. */
export function ModelsPanel({
  onError,
  userIds,
  onPickPerson,
}: {
  onError: (msg: string) => void;
  userIds: string[];
  onPickPerson: (id: string) => void;
}) {
  const [range, setRange] = useState<Range>("7d");
  const [from, setFrom] = useState(day(new Date(Date.now() - 7 * 864e5)));
  const [to, setTo] = useState(day(new Date()));
  const [model, setModel] = useState("");
  const [purpose, setPurpose] = useState("");
  const [data, setData] = useState<LlmUsage | null>(null);
  const [loading, setLoading] = useState(false);

  const window_ = useMemo(() => {
    const r = RANGES.find((x) => x.key === range)!;
    if (r.hours) return { since: new Date(Date.now() - r.hours * 3600e3).toISOString() };
    // Whole days, from the start of `from` to the end of `to`, in UTC.
    return { since: `${from}T00:00:00Z`, until: new Date(new Date(`${to}T00:00:00Z`).getTime() + 864e5).toISOString() };
  }, [range, from, to]);

  const load = useCallback(() => {
    setLoading(true);
    api
      .llmUsage({
        ...window_,
        userIds,
        models: model ? [model] : [],
        purposes: purpose ? [purpose] : [],
      })
      .then(setData)
      .catch((e) => onError(e.message))
      .finally(() => setLoading(false));
  }, [window_, userIds, model, purpose, onError]);

  useEffect(() => {
    load();
  }, [load]);

  const labels = data?.purpose_labels ?? {};
  const t = data?.totals;
  const unit = data?.unit ?? "day";
  const when = (iso: string) =>
    unit === "hour"
      ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
      : new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <div className="flex flex-wrap gap-1 rounded-lg border border-edge bg-panel p-0.5" role="group" aria-label="Time window">
          {RANGES.map((r) => (
            <button
              key={r.key}
              onClick={() => setRange(r.key)}
              aria-pressed={range === r.key}
              className={`rounded-md px-2.5 py-1 ${range === r.key ? "bg-edge text-white" : "text-slate-400 hover:text-slate-200"}`}
            >
              {r.label}
            </button>
          ))}
        </div>
        {range === "custom" && (
          <span className="flex items-center gap-1">
            <input
              type="date"
              value={from}
              max={to}
              onChange={(e) => e.target.value && setFrom(e.target.value)}
              className="rounded-md border border-edge bg-ink px-2 py-1 [color-scheme:dark]"
              aria-label="From"
            />
            <span className="text-slate-500">to</span>
            <input
              type="date"
              value={to}
              min={from}
              max={day(new Date())}
              onChange={(e) => e.target.value && setTo(e.target.value)}
              className="rounded-md border border-edge bg-ink px-2 py-1 [color-scheme:dark]"
              aria-label="To"
            />
          </span>
        )}
        <Select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model">
          <option value="">All models</option>
          {(data?.models ?? []).map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </Select>
        <Select value={purpose} onChange={(e) => setPurpose(e.target.value)} aria-label="Used for">
          <option value="">Everything</option>
          {(data?.purposes ?? []).map((p) => (
            <option key={p} value={p}>
              {labels[p] ?? p}
            </option>
          ))}
        </Select>
        <button onClick={load} className="ml-auto flex items-center gap-1 text-slate-400 hover:text-white" aria-label="Refresh">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      {!data ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : !data.available ? (
        <div className="mb-6 flex gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm">
          <Info size={18} className="mt-0.5 shrink-0 text-amber-400" />
          <div>
            <p className="text-amber-200">Model figures aren't available yet.</p>
            <p className="mt-1 text-slate-400">{data.reason}</p>
          </div>
        </div>
      ) : (
        t && (
          <>
            <Section title="At a glance">
              <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
                <Tile
                  label="Cost"
                  value={cost(t.spend_usd)}
                  hint={t.saved_usd > 0 ? `about ${cost(t.saved_usd)} saved by the cache` : "at the provider's prices"}
                />
                <Tile
                  label="Calls"
                  value={t.calls.toLocaleString()}
                  hint={
                    data.failures_tracked === false
                      ? "failures not tracked by this LiteLLM"
                      : t.failed
                        ? `${t.failed} failed (${pct(t.failed / t.calls)})`
                        : "none failed"
                  }
                />
                <Tile
                  label="Tokens"
                  value={formatTokens(t.input_tokens + t.output_tokens)}
                  hint={`${formatTokens(t.input_tokens)} in · ${formatTokens(t.output_tokens)} out`}
                />
                <Tile label="Response cache" value={pct(t.cache_hit_rate)} hint={`${t.cache_hits} calls answered from it`} />
                <Tile
                  label="Prompt cache"
                  value={pct(t.cached_share)}
                  hint={`${formatTokens(t.cached_tokens)} input tokens at the cached price`}
                />
                <Tile
                  label="Latency"
                  value={ms(t.p50_ms)}
                  hint={`slowest 5% ${ms(t.p95_ms)}${t.p50_first_token_ms != null ? ` · first token ${ms(t.p50_first_token_ms)}` : ""}`}
                />
              </div>
              <p className="mt-2 text-xs text-slate-500">
                From LiteLLM's spend logs: one row per model call, with its tokens, cost, timing and whether a cache
                answered it. Prompts and replies aren't read. The response cache is LiteLLM's exact-match cache in
                Valkey (a repeated identical call costs nothing); the prompt cache is OpenAI's, which bills a long
                repeated prompt start at a fraction of the price. Latency leaves out cache hits and failures.
              </p>
            </Section>

            <Section title={unit === "hour" ? "Per hour" : "Per day"}>
              <div className="grid gap-2 lg:grid-cols-2">
                <Bars
                  label="Cost"
                  tone="violet"
                  format={cost}
                  points={(data.series ?? []).map((p) => ({
                    key: p.at,
                    value: p.spend_usd,
                    tip: `${when(p.at)}: ${cost(p.spend_usd)}, ${p.calls} calls`,
                  }))}
                />
                <Bars
                  label="Calls"
                  points={(data.series ?? []).map((p) => ({
                    key: p.at,
                    value: p.calls,
                    tip: `${when(p.at)}: ${p.calls} calls${p.failed ? `, ${p.failed} failed` : ""}${p.cache_hits ? `, ${p.cache_hits} from cache` : ""}`,
                  }))}
                />
                <Bars
                  label="Tokens"
                  tone="emerald"
                  format={formatTokens}
                  points={(data.series ?? []).map((p) => ({
                    key: p.at,
                    value: p.tokens,
                    tip: `${when(p.at)}: ${formatTokens(p.tokens)} tokens`,
                  }))}
                />
                <Bars
                  label="Slowest 5% of calls"
                  tone="amber"
                  format={ms}
                  points={(data.series ?? []).map((p) => ({
                    key: p.at,
                    value: p.p95_ms ?? 0,
                    tip: `${when(p.at)}: ${ms(p.p95_ms)}`,
                  }))}
                />
              </div>
            </Section>

            <Section title="By model">
              <GroupTable rows={data.by_model ?? []} kind="model" labels={labels} />
            </Section>
            <Section title="By what it was for">
              <p className="mb-2 text-sm text-slate-400">
                Every call Nexus makes is tagged with its job: chat replies, routing a message, designing a team, the
                team's agents, memory, drafting skills, self-review, or embeddings (local in Ollama, so free).
              </p>
              <GroupTable rows={data.by_purpose ?? []} kind="purpose" labels={labels} />
            </Section>
            <Section title="By person">
              <p className="mb-2 text-sm text-slate-400">Tap a name to see only them.</p>
              <GroupTable rows={data.by_user ?? []} kind="person" labels={labels} onPick={onPickPerson} />
            </Section>

            <Section title="Failed calls">
              {(data.failures ?? []).length === 0 ? (
                <p className="text-sm text-slate-500">
                  {data.failures_tracked === false
                    ? "This LiteLLM version doesn't log failed calls; update its image to see them."
                    : "No model call failed in this window."}
                </p>
              ) : (
                <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel text-sm">
                  {(data.failures ?? []).map((f, i) => (
                    <li key={i} className="flex flex-wrap gap-x-3 px-3 py-2">
                      <span className="w-28 text-xs text-slate-500">{formatDateTime(f.at)}</span>
                      <span className="font-mono text-xs text-slate-400">{f.model}</span>
                      <span className="text-xs text-slate-400">{labels[f.purpose] ?? f.purpose}</span>
                      {f.user && <span className="text-xs text-slate-500">{f.user}</span>}
                      <span className="w-full break-words text-slate-300">{f.error || "No message"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </>
        )
      )}

      {data?.valkey && (
        <Section title="Valkey cache">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Tile
              label="Hit rate"
              value={pct(data.valkey.hit_rate)}
              hint={`${data.valkey.hits.toLocaleString()} hits · ${data.valkey.misses.toLocaleString()} misses`}
            />
            <Tile
              label="Cached model replies"
              value={
                <span className="inline-flex items-center gap-1.5">
                  <Zap size={16} className="text-amber-400" />
                  {data.valkey.llm_cached_responses.toLocaleString()}
                </span>
              }
              hint="kept for an hour each"
            />
            <Tile
              label="Memory"
              value={
                <span className="inline-flex items-center gap-1.5">
                  <Database size={16} className="text-slate-400" />
                  {data.valkey.memory ?? "–"}
                </span>
              }
            />
            <Tile label="Evicted keys" value={data.valkey.evicted.toLocaleString()} hint="dropped to free memory" />
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Valkey holds LiteLLM's response cache, sessions, rate limits, job queues and short caches. The hit rate
            covers all of them since Valkey last started, so it is a health figure rather than a model one; a rising
            eviction count means it needs more memory.
          </p>
        </Section>
      )}
    </div>
  );
}
