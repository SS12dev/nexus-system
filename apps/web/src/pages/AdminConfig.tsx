import { AlertTriangle, CheckCircle2, Copy, KeyRound, RotateCcw, Search, Server } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { confirmAction, toast } from "../components/Feedback";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { APP_NAME } from "../lib/assistant";
import { formatAgo } from "../lib/format";
import type { ConfigOption, ConfigValue, ConfigView } from "../lib/types";

/** What the owner has typed but not saved: a value, null for "back to .env", or the raw text of
 * a number field while it is being typed. */
type Draft = Record<string, ConfigValue | null>;

const SERVICE_NAMES: Record<string, string> = {
  api: "API",
  orchestrator: "Orchestrator worker",
  "doc-worker": "Document worker",
};

const show = (v: ConfigValue | null | undefined, opt: ConfigOption): string => {
  if (v === null || v === undefined || v === "") {
    if (opt.name === "route_model") return "Off";
    return "empty";
  }
  if (typeof v === "boolean") return v ? "on" : "off";
  return opt.unit ? `${v} ${opt.unit}` : String(v);
};

/** The error for a draft value, or "" when it can be saved. The server checks again. */
function problem(opt: ConfigOption, v: ConfigValue | null): string {
  if (v === null) return "";
  if (opt.kind === "int" || opt.kind === "float") {
    const n = typeof v === "number" ? v : Number(String(v).trim());
    if (String(v).trim() === "" || Number.isNaN(n)) return "Enter a number.";
    if (opt.kind === "int" && !Number.isInteger(n)) return "Enter a whole number.";
    if (opt.min !== null && n < opt.min) return `The smallest allowed is ${opt.min}.`;
    if (opt.max !== null && n > opt.max) return `The largest allowed is ${opt.max}.`;
  }
  return "";
}

const asSent = (opt: ConfigOption, v: ConfigValue | null): ConfigValue | null =>
  v !== null && (opt.kind === "int" || opt.kind === "float") ? Number(v) : v;

/** Runtime settings: change them here and every part of the app picks them up within seconds. */
export function ConfigPanel({ onError }: { onError: (msg: string) => void }) {
  const [view, setView] = useState<ConfigView | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    api.config().then(setView).catch((e) => onError(e.message));
  }, [onError]);
  useEffect(() => {
    load();
    // Keeps "which services run which version" fresh while the tab is open.
    const t = setInterval(() => api.config().then(setView).catch(() => {}), 15_000);
    return () => clearInterval(t);
  }, [load]);

  const byName = useMemo(
    () => Object.fromEntries((view?.options ?? []).map((o) => [o.name, o])),
    [view],
  );
  // Draft entries that differ from what is stored.
  const changes = useMemo(
    () =>
      Object.entries(draft).filter(([name, v]) => {
        const opt = byName[name];
        if (!opt) return false;
        if (v === null) return opt.overridden;
        return String(asSent(opt, v)) !== String(opt.value);
      }),
    [draft, byName],
  );
  const errors = changes.filter(([name, v]) => problem(byName[name], v));

  if (!view) return <p className="text-sm text-slate-500">Loading…</p>;

  const q = query.trim().toLowerCase();
  const visible = view.options.filter(
    (o) =>
      (!group || o.group === group) &&
      (!q || [o.label, o.help, o.env, o.group].some((t) => t.toLowerCase().includes(q))),
  );
  const changedCount = view.options.filter((o) => o.overridden).length;
  const waiting = view.options.filter((o) => o.waiting_for_restart.length > 0);

  const edit = (opt: ConfigOption, v: ConfigValue | null) =>
    setDraft((d) => {
      const next = { ...d, [opt.name]: v };
      // Typing the stored value back counts as no change.
      if (v !== null && String(asSent(opt, v)) === String(opt.value)) delete next[opt.name];
      return next;
    });

  const save = async () => {
    const values = Object.fromEntries(changes.map(([n, v]) => [n, asSent(byName[n], v)]));
    if (values.signup_policy === "open") {
      const ok = await confirmAction({
        title: "Let anyone join?",
        body: "With an open sign-up, anyone with a Google account can sign in and becomes a member, with a budget. Turn it back to invite only when you're done.",
        confirmLabel: "Open sign-up",
        danger: true,
      });
      if (!ok) return;
    }
    setSaving(true);
    try {
      const next = await api.saveConfig(values);
      setView(next);
      setDraft({});
      const restart = Object.keys(values).filter((n) => byName[n]?.restart.length);
      toast(
        restart.length
          ? "Saved. Settings marked 'after restart' wait for the restart shown at the top."
          : `Saved. ${APP_NAME} uses the new values within a few seconds.`,
      );
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="mb-4 rounded-lg border border-edge bg-panel p-4 text-sm text-slate-400">
        <p>
          Settings {APP_NAME} reads while it runs. A change here is stored in the database and picked
          up by every API replica and worker within about five seconds, with no redeploy. Values
          from <code className="text-slate-300">.env</code> are the defaults;{" "}
          <span className="text-slate-300">Reset</span> goes back to them. Every save is in the audit
          log.
        </p>
        <p className="mt-2">
          A few settings are read only when a service starts; they are marked{" "}
          <span className="text-amber-300">after restart</span>, and once saved this page shows the
          command that applies them.
        </p>
        <p className="mt-2 flex items-start gap-1.5">
          <KeyRound size={14} className="mt-0.5 shrink-0 text-slate-500" />
          <span>
            Not here on purpose: secrets (API keys, passwords, tokens), which stay encrypted and change
            with <code className="text-slate-300">./scripts/secrets.sh set NAME</code>, and addresses
            and wiring (database, domains, ports, replicas), which need{" "}
            <code className="text-slate-300">.env</code> and a redeploy.
          </span>
        </p>
      </div>

      <Running view={view} />

      {waiting.length > 0 && (
        <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <p className="flex items-center gap-2 font-medium text-amber-200">
            <AlertTriangle size={15} /> Waiting for a restart
          </p>
          <ul className="mt-2 space-y-2">
            {waiting.map((o) => (
              <li key={o.name} className="text-slate-300">
                {o.label} is saved as {show(o.value, o)}; it takes effect after restarting{" "}
                {o.waiting_for_restart.map((s) => SERVICE_NAMES[s] ?? s).join(" and ")}. On the Mac:
                {o.restart_command && <Command text={o.restart_command} />}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <label className="relative min-w-0 flex-1 basis-56">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search settings"
            aria-label="Search settings"
            className="w-full rounded-md border border-edge bg-ink py-1 pl-8 pr-3 text-base text-slate-100 sm:text-sm"
          />
        </label>
        <Select value={group} onChange={(e) => setGroup(e.target.value)} aria-label="Show one group">
          <option value="">All groups</option>
          {view.groups.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </Select>
        <span className="text-xs text-slate-500">
          {changedCount === 0 ? "All from .env" : `${changedCount} changed from .env`}
          {view.updated_at &&
            ` · last saved ${formatAgo(view.updated_at)}${view.updated_by ? ` by ${view.updated_by}` : ""}`}
        </span>
      </div>

      {visible.length === 0 && <p className="text-sm text-slate-500">No setting matches “{query}”.</p>}

      {view.groups.map((g) => {
        const rows = visible.filter((o) => o.group === g);
        if (rows.length === 0) return null;
        return (
          <section key={g} className="mb-6">
            <h2 className="mb-2 font-medium">{g}</h2>
            <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel">
              {rows.map((o) => (
                <Row
                  key={o.name}
                  opt={o}
                  draft={o.name in draft ? draft[o.name] : undefined}
                  onChange={(v) => edit(o, v)}
                />
              ))}
            </ul>
          </section>
        );
      })}

      {changes.length > 0 && (
        <div className="sticky bottom-0 z-30 -mx-4 border-t border-edge bg-ink/95 px-4 py-3 backdrop-blur pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-slate-300">
              {changes.length} unsaved {changes.length === 1 ? "change" : "changes"}
              {errors.length > 0 && <span className="text-rose-400"> · fix {errors.length} first</span>}
            </span>
            <button
              onClick={() => setDraft({})}
              className="ml-auto rounded-md border border-edge px-3 py-1.5 hover:bg-edge/60"
            >
              Discard
            </button>
            <button
              onClick={save}
              disabled={saving || errors.length > 0}
              className="rounded-md bg-sky-500 px-3 py-1.5 font-medium text-ink hover:bg-sky-400 disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Which processes are running and whether they have the latest saved version. */
function Running({ view }: { view: ConfigView }) {
  if (view.services.length === 0) {
    return (
      <p className="mb-4 text-xs text-slate-500">
        No service has reported in yet. Each one reports every few seconds once it is running.
      </p>
    );
  }
  const counts = new Map<string, { n: number; behind: number }>();
  for (const s of view.services) {
    const c = counts.get(s.service) ?? { n: 0, behind: 0 };
    c.n += 1;
    if (s.version < view.version) c.behind += 1;
    counts.set(s.service, c);
  }
  const allCurrent = [...counts.values()].every((c) => c.behind === 0);
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
      <span className="flex items-center gap-1.5">
        {allCurrent ? (
          <CheckCircle2 size={14} className="text-emerald-400" />
        ) : (
          <Server size={14} className="text-sky-400" />
        )}
        {allCurrent ? "Every service runs the latest settings" : "Some services are picking up the latest save"}
      </span>
      {[...counts.entries()].map(([name, c]) => (
        <span key={name}>
          {SERVICE_NAMES[name] ?? name} ×{c.n}
          {c.behind > 0 && <span className="text-sky-300"> ({c.behind} updating)</span>}
        </span>
      ))}
    </div>
  );
}

function Command({ text }: { text: string }) {
  return (
    <span className="mt-1 flex items-center gap-2">
      <code className="min-w-0 truncate rounded bg-ink px-2 py-1 text-xs text-slate-200">{text}</code>
      <button
        onClick={() =>
          navigator.clipboard.writeText(text).then(
            () => toast("Copied"),
            () => toast("Couldn't copy; select the text instead", "error"),
          )
        }
        className="rounded p-1 text-slate-400 hover:bg-edge/60 hover:text-slate-200"
        aria-label="Copy command"
      >
        <Copy size={14} />
      </button>
    </span>
  );
}

function Row({
  opt,
  draft,
  onChange,
}: {
  opt: ConfigOption;
  draft: ConfigValue | null | undefined;
  onChange: (v: ConfigValue | null) => void;
}) {
  const editing = draft !== undefined;
  // What the control shows: the draft, the .env value after a pending reset, or what is stored.
  const shown: ConfigValue = draft === null ? opt.default : editing ? draft : opt.value;
  const error = editing ? problem(opt, draft) : "";
  const willReset = draft === null;
  const id = `cfg-${opt.name}`;

  return (
    <li className={`flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-start ${editing ? "bg-sky-500/5" : ""}`}>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={id} className="font-medium text-slate-100">
            {opt.label}
          </label>
          {opt.overridden && !willReset && (
            <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] text-sky-300">changed</span>
          )}
          {willReset && (
            <span className="rounded bg-slate-500/20 px-1.5 py-0.5 text-[11px] text-slate-300">
              back to .env on save
            </span>
          )}
          {opt.restart.length > 0 && (
            <span
              className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] text-amber-300"
              title={`Read when ${opt.restart.map((s) => SERVICE_NAMES[s] ?? s).join(" and ")} starts`}
            >
              after restart
            </span>
          )}
        </div>
        <p className="mt-1 text-sm text-slate-400">{opt.help}</p>
        <p className="mt-1 text-xs text-slate-500">
          <code>{opt.env}</code> · from .env: {show(opt.default, opt)}
        </p>
        {error && <p className="mt-1 text-xs text-rose-400">{error}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-2 sm:w-64 sm:justify-end">
        <Control id={id} opt={opt} value={shown} invalid={!!error} onChange={onChange} />
        {(opt.overridden || (editing && draft !== null)) && !willReset ? (
          <button
            onClick={() => onChange(opt.overridden ? null : opt.default)}
            className="rounded p-1.5 text-slate-400 hover:bg-edge/60 hover:text-slate-200"
            title="Back to the .env value"
            aria-label={`Reset ${opt.label} to the .env value`}
          >
            <RotateCcw size={14} />
          </button>
        ) : (
          <span className="w-[26px]" aria-hidden />
        )}
      </div>
    </li>
  );
}

function Control({
  id,
  opt,
  value,
  invalid,
  onChange,
}: {
  id: string;
  opt: ConfigOption;
  value: ConfigValue;
  invalid: boolean;
  onChange: (v: ConfigValue) => void;
}) {
  const box = `rounded-md border bg-ink px-2.5 py-1 text-base text-slate-100 sm:text-sm ${
    invalid ? "border-rose-500" : "border-edge"
  }`;
  if (opt.kind === "bool") {
    const on = value === true;
    return (
      <button
        id={id}
        role="switch"
        aria-checked={on}
        onClick={() => onChange(!on)}
        className={`relative h-6 w-11 rounded-full transition-colors ${on ? "bg-sky-500" : "bg-edge"}`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`}
        />
        <span className="sr-only">{on ? "On" : "Off"}</span>
      </button>
    );
  }
  if (opt.kind === "choice") {
    return (
      <Select id={id} value={String(value)} onChange={(e) => onChange(e.target.value)} className="w-full sm:w-48">
        {opt.choices.map((c) => (
          <option key={c} value={c}>
            {c === "" ? "Off" : c}
          </option>
        ))}
      </Select>
    );
  }
  if (opt.kind === "list") {
    const picked = String(value)
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const toggle = (c: string) => {
      const next = picked.includes(c) ? picked.filter((p) => p !== c) : [...picked, c];
      onChange(opt.choices.filter((x) => next.includes(x)).join(","));
    };
    return (
      <div id={id} className="flex flex-wrap gap-1.5 sm:justify-end" role="group" aria-label={opt.label}>
        {opt.choices.map((c) => (
          <button
            key={c}
            onClick={() => toggle(c)}
            aria-pressed={picked.includes(c)}
            className={`rounded-full border px-2.5 py-0.5 text-xs ${
              picked.includes(c)
                ? "border-sky-500 bg-sky-500/15 text-sky-200"
                : "border-edge text-slate-400 hover:border-slate-500"
            }`}
          >
            {c}
          </button>
        ))}
      </div>
    );
  }
  if (opt.kind === "int" || opt.kind === "float") {
    return (
      <span className="flex items-center gap-1.5">
        <input
          id={id}
          type="number"
          inputMode={opt.kind === "int" ? "numeric" : "decimal"}
          step={opt.kind === "int" ? 1 : "any"}
          min={opt.min ?? undefined}
          max={opt.max ?? undefined}
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={invalid}
          className={`w-28 text-right tabular-nums ${box}`}
        />
        <span className="w-20 text-xs text-slate-500">{opt.unit}</span>
      </span>
    );
  }
  return (
    <input
      id={id}
      value={String(value)}
      onChange={(e) => onChange(e.target.value)}
      aria-invalid={invalid}
      placeholder="empty"
      className={`w-full sm:w-56 ${box}`}
    />
  );
}
