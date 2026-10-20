import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { ROLE_RANK, type Role, type User } from "../lib/types";
import { useStore } from "../store/chat";

const ROLES: Role[] = ["owner", "admin", "member", "viewer"];

export function PeoplePage() {
  const me = useStore((s) => s.user)!;
  const [users, setUsers] = useState<User[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [budget, setBudget] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.users().then(setUsers).catch((e) => setError(e.message));
  }, []);

  const grantable = ROLES.filter((r) => me.role === "owner" || ROLE_RANK[r] < ROLE_RANK[me.role]);
  const manageable = (u: User) => me.role === "owner" || ROLE_RANK[u.role] < ROLE_RANK[me.role];

  const replace = (u: User) => setUsers((list) => list.map((x) => (x.id === u.id ? u : x)));
  const run = async (fn: () => Promise<void>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const invite = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const u = await api.invite(email, role, budget || undefined);
      setUsers((list) => [...list, u]);
      setEmail("");
      setBudget("");
    });
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-6">
        <h1 className="mb-1 text-xl font-semibold">People</h1>
        <p className="mb-6 text-sm text-slate-400">
          Invite people by email. They join the first time they sign in with that address. Each
          person's chats are private to them; budgets cap their model spend per period.
        </p>

        <form onSubmit={invite} className="mb-8 flex flex-wrap gap-2">
          <input
            type="email"
            required
            placeholder="email@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="min-w-56 flex-1 rounded-md border border-edge bg-panel px-3 py-2"
          />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            className="rounded-md border border-edge bg-panel px-3 py-2"
          >
            {grantable.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          <input
            inputMode="decimal"
            placeholder="Budget $ (default)"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            className="w-40 rounded-md border border-edge bg-panel px-3 py-2"
          />
          <button className="rounded-md bg-sky-500 px-4 py-2 font-medium text-ink">Invite</button>
        </form>

        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

        <div className="space-y-2">
          {users.map((u) => (
            <div
              key={u.id}
              className="flex flex-wrap items-center gap-3 rounded-lg border border-edge bg-panel px-4 py-3"
            >
              <div className="min-w-48 flex-1">
                <div className="truncate">{u.name || u.email}</div>
                <div className="text-xs text-slate-500">
                  {u.email} · {u.status}
                </div>
              </div>
              <select
                value={u.role}
                disabled={!manageable(u) || u.id === me.id}
                onChange={(e) =>
                  run(async () => replace(await api.updateUser(u.id, { role: e.target.value as Role })))
                }
                className="rounded-md border border-edge bg-ink px-2 py-1 text-sm disabled:opacity-50"
              >
                {(manageable(u) ? grantable : [u.role]).map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
              {u.role === "member" && manageable(u) && <GrantsField user={u} onError={setError} />}
              <BudgetField
                user={u}
                disabled={!manageable(u)}
                onSave={(v) => run(async () => replace(await api.updateUser(u.id, { budget_usd: v })))}
              />
              {u.status === "active" && u.id !== me.id && manageable(u) && (
                <button
                  onClick={() =>
                    confirm(`Sign ${u.name || u.email} out on every device?`) &&
                    run(() => api.signOutUser(u.id))
                  }
                  className="rounded-md border border-edge px-2 py-1 text-sm hover:bg-edge/60"
                  title="Ends their sessions; they can sign in again"
                >
                  Sign out
                </button>
              )}
              {u.status !== "invited" && u.id !== me.id && manageable(u) && (
                <button
                  onClick={() =>
                    run(async () =>
                      replace(
                        await api.updateUser(u.id, {
                          status: u.status === "disabled" ? "active" : "disabled",
                        }),
                      ),
                    )
                  }
                  className="rounded-md border border-edge px-2 py-1 text-sm hover:bg-edge/60"
                >
                  {u.status === "disabled" ? "Enable" : "Disable"}
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function BudgetField({
  user,
  disabled,
  onSave,
}: {
  user: User;
  disabled: boolean;
  onSave: (v: string) => void;
}) {
  const [value, setValue] = useState(user.budget.max_usd);
  useEffect(() => setValue(user.budget.max_usd), [user.budget.max_usd]);
  const spend = user.budget.spend_usd;
  return (
    <label className="flex items-center gap-1 text-sm text-slate-400">
      {spend != null ? `$${spend.toFixed(2)} of $` : "$"}
      <input
        value={value}
        disabled={disabled}
        inputMode="decimal"
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => value !== user.budget.max_usd && onSave(value)}
        className="w-20 rounded-md border border-edge bg-ink px-2 py-1 text-slate-100 disabled:opacity-50"
      />
      <span className="text-xs">/ {user.budget.duration}</span>
    </label>
  );
}

const CAPABILITIES = [
  { key: "exec", label: "Run code", hint: "Agents may run code in the sandbox" },
  { key: "outbound", label: "Send", hint: "Agents may send email (each send asks first)" },
];

/** What a member's agents may do beyond reading and writing their own library. */
function GrantsField({ user, onError }: { user: User; onError: (msg: string) => void }) {
  const [granted, setGranted] = useState<string[] | null>(null);
  useEffect(() => {
    api.grants(user.id).then((g) => setGranted(g.granted)).catch(() => setGranted([]));
  }, [user.id]);
  if (granted == null) return null;
  const toggle = async (key: string) => {
    const next = granted.includes(key) ? granted.filter((k) => k !== key) : [...granted, key];
    try {
      setGranted((await api.setGrants(user.id, next)).granted);
    } catch (e) {
      onError((e as Error).message);
    }
  };
  return (
    <div className="flex gap-3 text-xs text-slate-400">
      {CAPABILITIES.map((c) => (
        <label key={c.key} className="flex items-center gap-1" title={c.hint}>
          <input type="checkbox" checked={granted.includes(c.key)} onChange={() => toggle(c.key)} />
          {c.label}
        </label>
      ))}
    </div>
  );
}
