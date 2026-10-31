import { CheckCircle2, Loader2, Play, RefreshCw, Shuffle, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "../components/Feedback";
import { api } from "../lib/api";
import { formatAgo, formatDateTime } from "../lib/format";
import type { GraphAdmin, GraphRun } from "../lib/types";
import { Section, Tile } from "./AdminParts";

/** What each relation family means, in the words the orb will use for its edge colours. */
const FAMILY_HELP: Record<string, string> = {
  identity: "who you are: traits, values, likes",
  social: "people and how they relate",
  belonging: "where things belong: work, home, groups",
  activity: "what you build, use and run",
  causal: "what led to or depends on what",
  knowledge: "what is about or learned from what",
  association: "related, without a clearer word",
};

const SHELL_HELP: Record<string, string> = {
  "0": "you, at the centre",
  "1": "traits, values, goals, skills",
  "2": "people, pets, places, organisations",
  "3": "projects, apps, routines, tasks",
  "4": "topics, tools, documents",
};

/** Admin > Graph: who builds the knowledge graph, whether the local model answers, what is
 * waiting for it and how the graph is made up. Counts only, never content. */
export function GraphPanel({ onError, onOpenConfig }: { onError: (msg: string) => void; onOpenConfig: () => void }) {
  const [data, setData] = useState<GraphAdmin | null>(null);
  const [busy, setBusy] = useState<"" | "extract" | "rearrange">("");

  const load = useCallback(() => api.graphAdmin().then(setData).catch((e) => onError(e.message)), [onError]);
  useEffect(() => {
    load();
  }, [load]);

  const run = async (what: "extract" | "rearrange") => {
    setBusy(what);
    try {
      await api.runGraph(what);
      toast(what === "extract" ? "Reading what is waiting now" : "Tidying and arranging the graph now");
      // Both take a while; look again shortly.
      setTimeout(load, 15_000);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  if (!data) return <p className="text-sm text-slate-400">Loading…</p>;
  const waiting = data.backlog.memories + data.backlog.chats + data.backlog.files;
  const local = data.extractor === "local";
  const o = data.ollama;

  return (
    <div>
      <p className="mb-4 text-sm text-slate-400">
        The knowledge graph is the map of each person's world that the orb and the Knowledge page draw: who they are,
        the people and places in their life, what they work on and what they know, and how those connect.{" "}
        {local
          ? "A model on this machine reads new memories, finished chats and files for it every ten minutes, at no cost."
          : "The memory model notes it in the same calls that save memories."}{" "}
        Each night duplicates are merged, links nobody has confirmed for a month fade a little, links recall keeps
        using grow stronger, and everything is ranked and grouped into clusters. When a reply draws on the graph, the
        orb lights what it touched from the centre outward, a link at a time.
      </p>
      {!data.enabled && (
        <p className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
          Building the graph is off, so nothing new is added. Turn on “Build the knowledge graph” in{" "}
          <button className="underline" onClick={onOpenConfig}>
            Configuration
          </button>
          .
        </p>
      )}

      <Section
        title="Who builds it"
        action={
          <button
            onClick={load}
            className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-white/5"
            title="Check again"
          >
            <RefreshCw size={13} /> Check again
          </button>
        }
      >
        <div className="rounded-lg border border-edge bg-panel p-4 text-sm">
          {local && o ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {o.reachable && o.installed ? (
                  <CheckCircle2 size={16} className="text-emerald-400" />
                ) : (
                  <XCircle size={16} className="text-rose-400" />
                )}
                <span className="font-medium">Local model {o.model}</span>
                <span className="text-slate-500">in Ollama at {o.url}</span>
              </div>
              <p className="mt-1 text-slate-400">
                {!o.reachable
                  ? `Ollama does not answer${o.error ? ` (${o.error})` : ""}. Start the Ollama app on the Mac; until then everything waits and nothing is lost.`
                  : !o.installed
                    ? `Ollama answers but doesn't have ${o.model}. Run \`ollama pull ${o.model}\` on the Mac.`
                    : o.loaded_gb
                      ? `Ready, and in memory now (${o.loaded_gb} GB). It unloads ten minutes after its last use.`
                      : "Ready. It loads when there is something to read (about 8 GB) and unloads ten minutes later."}
              </p>
            </>
          ) : (
            <p className="text-slate-400">
              The memory model, in its own calls. Switch to the free local model with “Who builds the knowledge graph” in{" "}
              <button className="underline" onClick={onOpenConfig}>
                Configuration
              </button>
              .
            </p>
          )}
        </div>
      </Section>

      <Section
        title="Waiting to be read"
        action={
          local && (
            <button
              disabled={!!busy || waiting === 0}
              onClick={() => run("extract")}
              className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-xs hover:bg-white/5 disabled:opacity-40"
            >
              {busy === "extract" ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Read now
            </button>
          )
        }
      >
        <div className="grid grid-cols-3 gap-3">
          <Tile label="Memories" value={data.backlog.memories} />
          <Tile label="Finished chats" value={data.backlog.chats} />
          <Tile label="Files" value={data.backlog.files} />
        </div>
        <RunLine run={data.last_extract} what="Last read" empty="Nothing read yet." />
      </Section>

      <Section
        title="Nightly upkeep"
        action={
          <button
            disabled={!!busy}
            onClick={() => run("rearrange")}
            className="flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-xs hover:bg-white/5 disabled:opacity-40"
          >
            {busy === "rearrange" ? <Loader2 size={13} className="animate-spin" /> : <Shuffle size={13} />} Run now
          </button>
        }
      >
        <p className="mb-2 text-xs text-slate-500">Every night at {data.nightly_utc} UTC, after the memory upkeep.</p>
        <RunLine run={data.last_nightly} what="Last run" empty="Not run yet." />
      </Section>

      <Section title="What the graph holds">
        <div className="mb-3 grid grid-cols-2 gap-3">
          <Tile label="Things" value={data.entities.total} hint="people, projects, topics and more" />
          <Tile label="Connections" value={data.relations.total} hint="current ones; ended ones are kept as history" />
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <Breakdown
            title="By shell, from the centre out"
            rows={Object.entries(data.shells).map(([k, name]) => ({
              key: k,
              label: `${k} · ${name}`,
              help: SHELL_HELP[k],
              value: data.entities.by_shell[k] ?? 0,
            }))}
          />
          <Breakdown
            title="Connections by kind"
            rows={data.families.map((f) => ({
              key: f,
              label: f,
              help: FAMILY_HELP[f],
              value: data.relations.by_family[f] ?? 0,
            }))}
          />
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Made by: {originLine(data.entities.by_origin)} things, {originLine(data.relations.by_origin)} connections.
          “app” ones come from your projects, apps and routines and leave when those are deleted.
        </p>
      </Section>
    </div>
  );
}

function originLine(counts: Record<string, number>): string {
  const parts = Object.entries(counts).map(([k, n]) => `${n} ${k === "model" ? "by a model" : k}`);
  return parts.join(", ") || "none";
}

function RunLine({ run, what, empty }: { run: GraphRun | null; what: string; empty: string }) {
  if (!run) return <p className="mt-2 text-xs text-slate-500">{empty}</p>;
  const parts =
    run.items !== undefined
      ? [`${run.items} items`, `${run.entities} things`, `${run.relations} connections`]
      : [
          `${run.merged} duplicates merged`,
          `${run.faded} connections faded`,
          `${run.strengthened ?? 0} strengthened by use`,
          `${run.communities} clusters`,
        ];
  return (
    <div className="mt-2 text-xs text-slate-400">
      <span title={formatDateTime(run.at)}>
        {what} {formatAgo(run.at)}
      </span>
      {run.seconds !== undefined && <> in {Math.round(run.seconds)} s</>}: {parts.join(", ")}.
      {run.error && <p className="mt-1 text-rose-300">Stopped: {run.error}</p>}
      {!!run.failed && <p className="mt-1 text-rose-300">Failed for {run.failed} people; see Monitoring.</p>}
    </div>
  );
}

function Breakdown({
  title,
  rows,
}: {
  title: string;
  rows: { key: string; label: string; help?: string; value: number }[];
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="rounded-lg border border-edge bg-panel p-3">
      <div className="mb-2 text-xs text-slate-400">{title}</div>
      <ul className="space-y-1.5">
        {rows.map((r) => (
          <li key={r.key} className="text-sm" title={r.help}>
            <div className="flex items-baseline gap-2">
              <span className="capitalize">{r.label}</span>
              <span className="truncate text-xs text-slate-500">{r.help}</span>
              <span className="ml-auto tabular-nums">{r.value}</span>
            </div>
            <div className="mt-0.5 h-1 rounded bg-white/5">
              <div className="h-1 rounded bg-sky-500/70" style={{ width: `${(r.value / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
