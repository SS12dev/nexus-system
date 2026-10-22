import { Bot, Check, Lock, Pencil, ShieldCheck, Sparkles, User, Users } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import type { Instructions } from "../lib/types";
import { Markdown } from "./Markdown";

type LayerKey = "safety" | "shared" | "global" | "roles" | "user";

interface LayerInfo {
  key: LayerKey;
  title: string;
  icon: ReactNode;
  /** One line under the title in the stack. */
  summary: string;
  /** Who gets it in their prompt. */
  assistant: boolean;
  agents: boolean;
  /** Who can change it. */
  editedBy: "Built in" | "Admins" | "You";
}

/** Labels like fact_checker read better as "Fact checker". */
const roleLabel = (role: string) => {
  const s = role.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
};

/** The standing instructions, shown as the stack they form in every prompt: pick a layer to
 * read or edit it. Order and audiences mirror nexus_common.skills.instruction_layers and
 * how the orchestrator uses it (Nexus's own chat leaves out the safety rules and roles). */
export function InstructionsPanel({
  isAdmin,
  canWrite,
  onError,
}: {
  isAdmin: boolean;
  canWrite: boolean;
  onError: (msg: string) => void;
}) {
  const assistant = useAssistantName();
  const [data, setData] = useState<Instructions | null>(null);
  const [open, setOpen] = useState<LayerKey>("user");
  const [role, setRole] = useState<string | null>(null);

  useEffect(() => {
    api
      .instructions()
      .then(setData)
      .catch((e) => onError(e.message));
  }, [onError]);

  if (!data) return null;
  const currentRole = role ?? data.roles[0]?.role ?? null;

  const save = async (layer: Parameters<typeof api.setInstructions>[0], content: string) => {
    try {
      await api.setInstructions(layer, content);
      setData((d) => {
        if (!d) return d;
        if (layer === "user") return { ...d, user: content.trim() };
        if (layer === "global") return { ...d, global: content.trim() };
        const r = layer.slice("roles/".length);
        return {
          ...d,
          roles: d.roles.map((x) => (x.role === r ? { ...x, extra: content.trim() } : x)),
        };
      });
      return true;
    } catch (e) {
      onError((e as Error).message);
      return false;
    }
  };

  const layers: LayerInfo[] = [
    {
      key: "safety",
      title: "Safety rules",
      icon: <ShieldCheck size={16} />,
      summary: "Ground rules no other layer can override.",
      assistant: false,
      agents: true,
      editedBy: "Built in",
    },
    {
      key: "shared",
      title: "House conventions",
      icon: <Sparkles size={16} />,
      summary: "How dates, numbers, sources and files are written.",
      assistant: true,
      agents: true,
      editedBy: "Built in",
    },
    {
      key: "global",
      title: "Global instructions",
      icon: <Users size={16} />,
      summary: data.global
        ? "Extra rules for everyone on this server."
        : "Not set. Optional rules for everyone.",
      assistant: true,
      agents: true,
      editedBy: "Admins",
    },
    {
      key: "roles",
      title: "Agent roles",
      icon: <Bot size={16} />,
      summary: `${data.roles.length} job descriptions, one per kind of sub-agent.`,
      assistant: false,
      agents: true,
      editedBy: "Admins",
    },
    {
      key: "user",
      title: "Your instructions",
      icon: <User size={16} />,
      summary: data.user
        ? "Your personal style and preferences."
        : "Not set yet. Tell agents how you like things.",
      assistant: true,
      agents: true,
      editedBy: "You",
    },
  ];
  const info = layers.find((l) => l.key === open)!;

  return (
    <div className="space-y-5">
      <div className="max-w-3xl text-sm text-slate-400">
        <p>
          These are the standing instructions behind every answer. When {assistant} replies, or a sub-agent on
          one of its teams starts work, these layers are put together, top to bottom, into the instructions it
          reads first (its system prompt). Later layers add detail; none can override the safety rules.
        </p>
        <p className="mt-2">
          On top of these, {assistant} writes each sub-agent's task-specific instructions when it builds a
          team; you can read those on the Agents page.
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-[22rem_minmax(0,1fr)]">
        <ol className="space-y-2" aria-label="Instruction layers, in prompt order">
          {layers.map((l, i) => (
            <li key={l.key}>
              <button
                onClick={() => setOpen(l.key)}
                aria-current={open === l.key}
                className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${
                  open === l.key ? "border-sky-500/60 bg-edge" : "border-edge bg-panel hover:bg-edge/50"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="grid size-5 shrink-0 place-items-center rounded-full bg-ink text-[11px] text-slate-400">
                    {i + 1}
                  </span>
                  <span className="text-sky-300">{l.icon}</span>
                  <span className="font-medium">{l.title}</span>
                  <EditedBy who={l.editedBy} />
                </div>
                <p className="mt-1 pl-7 text-xs text-slate-400">{l.summary}</p>
                <div className="mt-1.5 flex gap-1.5 pl-7">
                  <Audience on={l.assistant} label={assistant} />
                  <Audience on={l.agents} label="Sub-agents" />
                </div>
              </button>
            </li>
          ))}
        </ol>

        <section className="min-w-0 rounded-lg border border-edge bg-panel p-4">
          <header className="mb-3 flex flex-wrap items-center gap-2">
            <span className="text-sky-300">{info.icon}</span>
            <h3 className="font-medium">{info.title}</h3>
            <EditedBy who={info.editedBy} />
          </header>

          {open === "safety" && (
            <>
              <Explain>
                Built into the app for every sub-agent, so a web page, email or document can't talk an agent
                into doing something you didn't ask. {assistant} follows similar rules in its own built-in
                instructions.
              </Explain>
              {data.safety ? (
                <Markdown size="sm">{data.safety}</Markdown>
              ) : (
                <p className="text-sm text-rose-400">The built-in safety rules couldn't be read.</p>
              )}
            </>
          )}

          {open === "shared" && (
            <>
              <Explain>
                The house style everyone shares: how to write dates, numbers and citations, how to name files,
                and how agents hand work to each other. Built in; your instructions can change the style, not
                these rules.
              </Explain>
              <Markdown size="sm">{data.shared || "None."}</Markdown>
            </>
          )}

          {open === "global" && (
            <>
              <Explain>
                Rules an admin adds for {assistant} and every sub-agent of every person on this server, you
                and anyone you invite. Leave it empty unless something should apply to everyone; personal
                preferences belong in Your instructions.
              </Explain>
              <Editor
                value={data.global}
                editable={isAdmin}
                placeholder={
                  isAdmin
                    ? "e.g. Never book or buy anything without asking first."
                    : "Nothing set. Only admins can add global instructions."
                }
                onSave={(v) => save("global", v)}
              />
            </>
          )}

          {open === "roles" && (
            <>
              <Explain>
                Every sub-agent has a role, chosen by {assistant} when it designs a team. The role's
                description is that agent's job description: how a researcher, writer or reviewer should work.
                Admins can add to a role; the built-in text stays.
              </Explain>
              <div className="mb-3 flex flex-wrap gap-1.5" role="tablist" aria-label="Roles">
                {data.roles.map((r) => (
                  <button
                    key={r.role}
                    role="tab"
                    aria-selected={currentRole === r.role}
                    onClick={() => setRole(r.role)}
                    className={`rounded-full border px-2.5 py-0.5 text-xs ${
                      currentRole === r.role
                        ? "border-sky-500/60 bg-edge text-white"
                        : "border-edge text-slate-400 hover:bg-edge/50"
                    }`}
                  >
                    {roleLabel(r.role)}
                    {r.extra && <span className="ml-1 text-amber-300">•</span>}
                  </button>
                ))}
              </div>
              {data.roles
                .filter((r) => r.role === currentRole)
                .map((r) => (
                  <div key={r.role} className="space-y-4">
                    <div>
                      <h4 className="mb-1 text-xs font-medium tracking-wide text-slate-500 uppercase">
                        Built in
                      </h4>
                      <Markdown size="sm">{r.shipped || "No built-in text for this role."}</Markdown>
                    </div>
                    <div>
                      <h4 className="mb-1 text-xs font-medium tracking-wide text-slate-500 uppercase">
                        Added by admins
                      </h4>
                      <Editor
                        key={r.role}
                        value={r.extra}
                        editable={isAdmin}
                        placeholder={
                          isAdmin
                            ? `Extra guidance for every ${roleLabel(r.role).toLowerCase()} agent.`
                            : "Nothing added."
                        }
                        onSave={(v) => save(`roles/${r.role}`, v)}
                      />
                    </div>
                  </div>
                ))}
            </>
          )}

          {open === "user" && (
            <>
              <Explain>
                Your own standing preferences, read by {assistant} and every sub-agent working for you: tone,
                language, how long answers should be, how you like reports laid out. Only you see them, and
                only your work uses them.
              </Explain>
              <Editor
                value={data.user}
                editable={canWrite}
                rows={10}
                placeholder="e.g. British English. Short answers first, detail after. Tables for comparisons. Prices in ₹."
                onSave={(v) => save("user", v)}
              />
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function EditedBy({ who }: { who: LayerInfo["editedBy"] }) {
  const style =
    who === "You"
      ? "bg-sky-500/15 text-sky-300"
      : who === "Admins"
        ? "bg-amber-500/15 text-amber-300"
        : "bg-slate-500/20 text-slate-400";
  return (
    <span className={`ml-auto flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] ${style}`}>
      {who === "Built in" ? <Lock size={10} /> : <Pencil size={10} />}
      {who === "Built in" ? "Built in" : `Edited by ${who.toLowerCase()}`}
    </span>
  );
}

function Audience({ on, label }: { on: boolean; label: string }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[10px] ${
        on ? "border-emerald-500/40 text-emerald-300" : "border-edge text-slate-600 line-through"
      }`}
      title={on ? `${label} reads this` : `${label} doesn't read this`}
    >
      {label}
    </span>
  );
}

function Explain({ children }: { children: ReactNode }) {
  return <p className="mb-4 rounded-md bg-ink/60 px-3 py-2 text-sm text-slate-400">{children}</p>;
}

function Editor({
  value,
  editable,
  placeholder,
  rows = 6,
  onSave,
}: {
  value: string;
  editable: boolean;
  placeholder: string;
  rows?: number;
  onSave: (v: string) => Promise<boolean>;
}) {
  const [text, setText] = useState(value);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setText(value);
  }, [value]);

  if (!editable)
    return value ? (
      <Markdown size="sm">{value}</Markdown>
    ) : (
      <p className="text-sm text-slate-500">{placeholder}</p>
    );
  return (
    <div>
      <textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setSaved(false);
        }}
        placeholder={placeholder}
        rows={rows}
        className="w-full rounded-md border border-edge bg-ink p-3 text-sm"
      />
      <div className="mt-2 flex items-center gap-3">
        <button
          onClick={async () => setSaved(await onSave(text))}
          disabled={text.trim() === value.trim()}
          className="rounded-md bg-sky-600 px-3 py-1.5 text-sm hover:bg-sky-500 disabled:bg-edge disabled:text-slate-500"
        >
          Save
        </button>
        {saved && (
          <span className="flex items-center gap-1 text-sm text-emerald-300">
            <Check size={14} /> Saved. New replies use it.
          </span>
        )}
      </div>
    </div>
  );
}
