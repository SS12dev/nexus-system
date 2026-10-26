import { Check, Clock, Copy, History, Inbox, Mail, Search, ShieldCheck, UserPlus, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { confirmAction, toast } from "../components/Feedback";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { APP_NAME } from "../lib/assistant";
import { describeAgent, formatAgo, formatDateTime } from "../lib/format";
import {
  ROLE_RANK,
  type AccessRequest,
  type AccessRequestList,
  type AccountRecoveryList,
  type AccountRecoveryRequest,
  type Role,
  type User,
  type UserStatus,
} from "../lib/types";
import { useStore } from "../store/chat";

const ROLES: Role[] = ["owner", "admin", "member", "viewer"];
const ROLE_LABEL: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};
const ROLE_HINT: Record<Role, string> = {
  owner: "Everything, including the Admin page",
  admin: "Manages people below them",
  member: "Chats, agents, library",
  viewer: "Reads their chats; can't send",
};
const STATUS_STYLE: Record<UserStatus, string> = {
  active: "border-emerald-800/60 bg-emerald-500/10 text-emerald-300",
  invited: "border-amber-800/60 bg-amber-500/10 text-amber-300",
  disabled: "border-slate-700 bg-slate-500/10 text-slate-400",
};
type Filter = "all" | UserStatus;

const personName = (u: { name: string | null; email: string }) => u.name || u.email;

export function PeoplePage() {
  const me = useStore((s) => s.user)!;
  const [users, setUsers] = useState<User[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    api
      .users()
      .then(setUsers)
      .catch((e) => setError(e.message))
      .finally(() => setLoaded(true));
  }, []);

  const grantable = ROLES.filter((r) => me.role === "owner" || ROLE_RANK[r] < ROLE_RANK[me.role]);
  const manageable = (u: User) => me.role === "owner" || ROLE_RANK[u.role] < ROLE_RANK[me.role];

  const replace = (u: User) => setUsers((list) => list.map((x) => (x.id === u.id ? u : x)));
  const add = useCallback(
    (u: User) => setUsers((list) => [...list.filter((x) => x.id !== u.id), u]),
    [],
  );
  const run = async (fn: () => Promise<void>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: users.length, active: 0, invited: 0, disabled: 0 };
    for (const u of users) c[u.status]++;
    return c;
  }, [users]);
  const q = query.trim().toLowerCase();
  const shown = users.filter(
    (u) =>
      (filter === "all" || u.status === filter) &&
      (!q || `${u.name ?? ""} ${u.email}`.toLowerCase().includes(q)),
  );

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-6">
        <h1 className="mb-1 text-xl font-semibold">People</h1>
        <p className="mb-6 text-sm text-slate-400">
          Who can use {APP_NAME}, with what role and budget. Each person's chats stay private to
          them. Admins manage people below them; only owners see the Admin page.
        </p>

        <AccessRequests grantable={grantable} onApproved={add} />
        <RecoveryRequests
          grantable={grantable}
          onChanged={() => api.users().then(setUsers).catch((e) => toast(e.message, "error"))}
        />

        <InviteForm grantable={grantable} onInvited={add} />

        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

        <section aria-labelledby="people-heading">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h2 id="people-heading" className="mr-auto font-medium">
              Everyone <span className="text-slate-500">({users.length})</span>
            </h2>
            <div className="flex rounded-md border border-edge bg-panel p-0.5 text-sm" role="tablist" aria-label="Filter by status">
              {(["all", "active", "invited", "disabled"] as Filter[]).map((f) => (
                <button
                  key={f}
                  role="tab"
                  aria-selected={filter === f}
                  onClick={() => setFilter(f)}
                  className={`rounded px-2.5 py-1 capitalize ${filter === f ? "bg-edge text-white" : "text-slate-400 hover:text-slate-200"}`}
                >
                  {f} <span className="text-xs text-slate-500">{counts[f]}</span>
                </button>
              ))}
            </div>
            {users.length > 5 && (
              <label className="relative w-full sm:w-56">
                <span className="sr-only">Search people</span>
                <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search name or email"
                  className="w-full rounded-md border border-edge bg-panel py-1.5 pl-8 pr-2 text-base sm:text-sm"
                />
              </label>
            )}
          </div>

          {!loaded && <div className="h-16 animate-pulse rounded-lg bg-panel" aria-label="Loading people" />}
          {loaded && shown.length === 0 && (
            <p className="rounded-lg border border-dashed border-edge px-4 py-6 text-center text-sm text-slate-500">
              {users.length ? "Nobody matches." : "No one yet."}
            </p>
          )}
          <ul className="space-y-2">
            {shown.map((u) => (
              <PersonRow
                key={u.id}
                user={u}
                isMe={u.id === me.id}
                canManage={manageable(u)}
                grantable={grantable}
                onChange={replace}
                onRemoved={() => setUsers((list) => list.filter((x) => x.id !== u.id))}
                run={run}
              />
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

function Avatar({ person, size = 36 }: { person: { name: string | null; email: string; avatar_url: string | null }; size?: number }) {
  const [broken, setBroken] = useState(false);
  const initials = personName(person)
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
  if (person.avatar_url && !broken)
    return (
      <img
        src={person.avatar_url}
        alt=""
        width={size}
        height={size}
        referrerPolicy="no-referrer"
        onError={() => setBroken(true)}
        className="shrink-0 rounded-full bg-edge"
        style={{ width: size, height: size }}
      />
    );
  return (
    <span
      aria-hidden
      className="grid shrink-0 place-items-center rounded-full bg-edge text-xs font-medium text-slate-300"
      style={{ width: size, height: size }}
    >
      {initials}
    </span>
  );
}

function InviteForm({ grantable, onInvited }: { grantable: Role[]; onInvited: (u: User) => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);
  const [retryId, setRetryId] = useState<string | null>(null);
  const [retryBusy, setRetryBusy] = useState(false);

  const invite = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const u = await api.invite(email, role, budget || undefined);
      onInvited(u);
      setRetryId(!u.invite_email_queued && u.invite_email_enabled ? u.id : null);
      toast(
        u.invite_email_queued
          ? `Invited ${u.email}; the invitation email was queued.`
          : u.invite_email_enabled
            ? `Invited ${u.email}, but the email could not be queued. Retry it below.`
            : `Invited ${u.email}, but SMTP email is not configured.`,
        u.invite_email_queued ? "info" : "error",
      );
      setEmail("");
      setBudget("");
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const retryEmail = async () => {
    if (!retryId) return;
    setRetryBusy(true);
    try {
      await api.retryInviteNotice(retryId);
      setRetryId(null);
      toast("Invitation email queued.");
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setRetryBusy(false);
    }
  };

  return (
    <form onSubmit={invite} className="mb-8 rounded-lg border border-edge bg-panel p-4">
      <h2 className="mb-1 flex items-center gap-2 font-medium">
        <UserPlus size={16} className="text-sky-400" aria-hidden /> Invite someone
      </h2>
      <p className="mb-3 text-xs text-slate-500">
        Use the Google address they'll sign in with. If SMTP is configured, we'll email them a
        sign-in link; otherwise, tell them to open {location.origin} and sign in.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-56 flex-1 flex-col gap-1 text-xs text-slate-400">
          Email
          <input
            type="email"
            required
            placeholder="name@gmail.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-edge bg-ink px-3 py-2 text-base text-slate-100"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Role
          <Select size="md" value={role} onChange={(e) => setRole(e.target.value as Role)} className="w-32" title={ROLE_HINT[role]}>
            {grantable.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Budget (USD)
          <input
            inputMode="decimal"
            placeholder="Default"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            className="w-28 rounded-md border border-edge bg-ink px-3 py-2 text-base text-slate-100"
          />
        </label>
        <button disabled={busy} className="rounded-md bg-sky-500 px-4 py-2 font-medium text-ink hover:bg-sky-400 disabled:opacity-60">
          {busy ? "Inviting…" : "Invite"}
        </button>
      </div>
      {retryId && (
        <div className="mt-3 flex items-center gap-2 text-sm text-amber-300">
          <span className="mr-auto">The account was created, but its invitation email is still pending.</span>
          <button
            type="button"
            onClick={retryEmail}
            disabled={retryBusy}
            className="rounded-md border border-amber-800/60 px-3 py-1.5 hover:bg-amber-500/10 disabled:opacity-50"
          >
            {retryBusy ? "Retrying…" : "Retry email"}
          </button>
        </div>
      )}
    </form>
  );
}

/** People who asked to join from the login page, waiting for an owner or admin. */
function AccessRequests({ grantable, onApproved }: { grantable: Role[]; onApproved: (u: User) => void }) {
  const pendingCount = useStore((s) => s.pendingAccess);
  const setPendingAccess = useStore((s) => s.setPendingAccess);
  const [list, setList] = useState<AccessRequestList | null>(null);
  const [history, setHistory] = useState<AccessRequest[] | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const load = useCallback(() => {
    api
      .accessRequests()
      .then((l) => {
        setList(l);
        setPendingAccess(l.pending);
      })
      .catch((e) => toast(e.message, "error"));
  }, [setPendingAccess]);

  // Load once, then again whenever the live count moves away from what's shown, e.g. someone
  // asked, or another admin decided, while this page is open.
  const shownPending = list?.pending;
  useEffect(() => {
    if (shownPending === undefined || pendingCount !== shownPending) load();
  }, [pendingCount, shownPending, load]);

  useEffect(() => {
    if (showHistory) api.accessRequests("decided").then((l) => setHistory(l.requests)).catch(() => setHistory([]));
  }, [showHistory, list?.pending]);

  if (!list) return null;
  // Nothing waiting and requests are off: don't take up room.
  if (!list.enabled && list.requests.length === 0) return null;

  const done = (id: string) => {
    const pending = Math.max(0, list.pending - 1);
    setList({ ...list, requests: list.requests.filter((r) => r.id !== id), pending });
    setPendingAccess(pending);
  };

  return (
    <section aria-labelledby="requests-heading" className="mb-8">
      <div className="mb-3 flex items-center gap-2">
        <h2 id="requests-heading" className="flex items-center gap-2 font-medium">
          <Inbox size={16} className="text-sky-400" aria-hidden /> Access requests
          {list.pending > 0 && <span className="rounded-full bg-rose-500 px-1.5 text-xs text-white">{list.pending}</span>}
        </h2>
        <button
          onClick={() => setShowHistory(!showHistory)}
          className="ml-auto flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-slate-400 hover:bg-edge/50 hover:text-slate-200"
          aria-expanded={showHistory}
        >
          <History size={14} /> {showHistory ? "Hide decided" : "Decided"}
        </button>
      </div>

      {list.requests.length === 0 ? (
        <p className="rounded-lg border border-dashed border-edge px-4 py-5 text-center text-sm text-slate-500">
          No one is waiting. People without an invite can ask from the sign-in page.
        </p>
      ) : (
        <ul className="space-y-2">
          {list.requests.map((r) => (
            <RequestCard key={r.id} req={r} grantable={grantable} onDone={done} onApproved={onApproved} />
          ))}
        </ul>
      )}

      <p className="mt-2 flex items-start gap-1.5 text-xs text-slate-500">
        <Mail size={12} className="mt-0.5 shrink-0" aria-hidden />
        {!list.enabled
          ? "Requests are turned off (ACCESS_REQUESTS_ENABLED, or SIGNUP_POLICY is open)."
          : list.email_notifications
            ? "Owners and admins are also emailed about new requests, and approved people get an email."
            : "New requests show here and as a badge on People. Set the SMTP_* settings in .env to also get them by email."}
      </p>

      {showHistory && (
        <ul className="mt-3 divide-y divide-edge rounded-lg border border-edge bg-panel/60 text-sm">
          {history === null && <li className="px-4 py-3 text-slate-500">Loading…</li>}
          {history?.length === 0 && <li className="px-4 py-3 text-slate-500">Nothing decided yet.</li>}
          {history?.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
              <span className="min-w-0 flex-1 truncate">{personName(r)}</span>
              <span className={r.status === "approved" ? "text-emerald-400" : "text-rose-400"}>{r.status}</span>
              <span className="text-xs text-slate-500" title={r.decided_at ? formatDateTime(r.decided_at) : undefined}>
                {r.decided_by_name ? `by ${r.decided_by_name}, ` : ""}
                {r.decided_at ? formatAgo(r.decided_at) : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Deleted accounts stay recoverable for 30 days; only a person who can manage the old role
 * can approve restoring them. */
function RecoveryRequests({
  grantable,
  onChanged,
}: {
  grantable: Role[];
  onChanged: () => void;
}) {
  const pendingCount = useStore((s) => s.pendingRecovery);
  const setPendingRecovery = useStore((s) => s.setPendingRecovery);
  const [list, setList] = useState<AccountRecoveryList | null>(null);
  const [history, setHistory] = useState<AccountRecoveryRequest[] | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const load = useCallback(() => {
    api
      .accountRecoveryRequests()
      .then((value) => {
        setList(value);
        setPendingRecovery(value.pending);
      })
      .catch((e) => toast(e.message, "error"));
  }, [setPendingRecovery]);

  const shownPending = list?.pending;
  useEffect(() => {
    if (shownPending === undefined || pendingCount !== shownPending) load();
  }, [pendingCount, shownPending, load]);

  useEffect(() => {
    if (showHistory)
      api
        .accountRecoveryRequests("decided")
        .then((value) => setHistory(value.requests))
        .catch((e) => toast(e.message, "error"));
  }, [showHistory, list?.pending]);

  if (!list) return null;
  const done = (id: string) => {
    const pending = Math.max(0, list.pending - 1);
    setList({ ...list, requests: list.requests.filter((request) => request.id !== id), pending });
    setPendingRecovery(pending);
  };

  return (
    <section aria-labelledby="recovery-heading" className="mb-8">
      <div className="mb-3 flex items-center gap-2">
        <h2 id="recovery-heading" className="flex items-center gap-2 font-medium">
          <ShieldCheck size={16} className="text-sky-400" aria-hidden /> Account recovery
          {list.pending > 0 && <span className="rounded-full bg-rose-500 px-1.5 text-xs text-white">{list.pending}</span>}
        </h2>
        <button
          onClick={() => setShowHistory(!showHistory)}
          className="ml-auto flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-slate-400 hover:bg-edge/50 hover:text-slate-200"
          aria-expanded={showHistory}
        >
          <History size={14} /> {showHistory ? "Hide decided" : "Decided"}
        </button>
      </div>
      {list.requests.length === 0 ? (
        <p className="rounded-lg border border-dashed border-edge px-4 py-5 text-center text-sm text-slate-500">
          No account recovery requests are waiting.
        </p>
      ) : (
        <ul className="space-y-2">
          {list.requests.map((request) => (
            <RecoveryCard
              key={request.id}
              request={request}
              canDecide={grantable.includes(request.role)}
              onDone={done}
              onChanged={onChanged}
            />
          ))}
        </ul>
      )}
      <p className="mt-2 flex items-start gap-1.5 text-xs text-slate-500">
        <Mail size={12} className="mt-0.5 shrink-0" aria-hidden />
        {list.email_notifications
          ? "People requesting recovery are emailed to owners and admins; approved people are emailed."
          : "Recovery requests appear here and as a notification. Configure SMTP to also email people."}
      </p>
      {showHistory && (
        <ul className="mt-3 divide-y divide-edge rounded-lg border border-edge bg-panel/60 text-sm">
          {history === null && <li className="px-4 py-3 text-slate-500">Loading…</li>}
          {history?.length === 0 && <li className="px-4 py-3 text-slate-500">Nothing decided yet.</li>}
          {history?.map((request) => (
            <li key={request.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
              <span className="min-w-0 flex-1 truncate">{request.name || request.email}</span>
              <span className={request.status === "approved" ? "text-emerald-400" : "text-rose-400"}>{request.status}</span>
              <span className="text-xs text-slate-500">
                {request.decided_by_name ? `by ${request.decided_by_name}, ` : ""}
                {request.decided_at ? formatAgo(request.decided_at) : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RecoveryCard({
  request,
  canDecide,
  onDone,
  onChanged,
}: {
  request: AccountRecoveryRequest;
  canDecide: boolean;
  onDone: (id: string) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);

  const approve = async () => {
    setBusy("approve");
    try {
      await api.approveAccountRecovery(request.id);
      onDone(request.id);
      onChanged();
      toast(`Restored ${request.name || request.email}'s account.`);
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(null);
    }
  };
  const deny = async () => {
    const ok = await confirmAction({
      title: `Deny recovery for ${request.name || request.email}?`,
      body: "They will not be able to restore this account, but can still choose to permanently remove it and start fresh.",
      confirmLabel: "Deny recovery",
      danger: true,
    });
    if (!ok) return;
    setBusy("deny");
    try {
      await api.denyAccountRecovery(request.id);
      onDone(request.id);
      toast("Recovery request denied.", "info");
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(null);
    }
  };

  return (
    <li className="flex flex-wrap items-center gap-3 rounded-lg border border-edge bg-panel px-4 py-3">
      <Avatar person={request} size={36} />
      <div className="min-w-48 flex-1">
        <div className="font-medium">{personName(request)}</div>
        <div className="text-xs text-slate-500">
          {ROLE_LABEL[request.role]} · requested {formatAgo(request.created_at)} · expires {formatDateTime(request.expires_at)}
        </div>
      </div>
      {!canDecide ? (
        <span className="text-xs text-slate-500">You can't manage this role.</span>
      ) : (
        <div className="flex gap-2">
          <button
            onClick={approve}
            disabled={busy !== null}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {busy === "approve" ? "Restoring…" : "Approve"}
          </button>
          <button
            onClick={deny}
            disabled={busy !== null}
            className="rounded-md border border-rose-900/60 px-3 py-1.5 text-sm text-rose-300 hover:bg-rose-500/10 disabled:opacity-50"
          >
            {busy === "deny" ? "Denying…" : "Deny"}
          </button>
        </div>
      )}
    </li>
  );
}

function RequestCard({
  req,
  grantable,
  onDone,
  onApproved,
}: {
  req: AccessRequest;
  grantable: Role[];
  onDone: (id: string) => void;
  onApproved: (u: User) => void;
}) {
  const [role, setRole] = useState<Role>(grantable.includes("member") ? "member" : grantable[grantable.length - 1]);
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);

  const approve = async () => {
    setBusy("approve");
    try {
      const { user } = await api.approveAccess(req.id, role);
      onApproved(user);
      onDone(req.id);
      toast(`Approved ${personName(req)} as ${ROLE_LABEL[role].toLowerCase()}. They can sign in now.`);
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(null);
    }
  };
  const deny = async () => {
    const ok = await confirmAction({
      title: `Deny ${personName(req)}?`,
      body: "They aren't emailed. If they come back to the sign-in page it says the request wasn't approved, and they can ask again later.",
      confirmLabel: "Deny",
      danger: true,
    });
    if (!ok) return;
    setBusy("deny");
    try {
      await api.denyAccess(req.id);
      onDone(req.id);
      toast(`Denied ${personName(req)}.`, "info");
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(null);
    }
  };

  return (
    <li className="rounded-lg border border-sky-900/60 bg-sky-950/20 p-4">
      <div className="flex flex-wrap items-start gap-3">
        <Avatar person={req} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{req.name || req.email}</div>
          <div className="truncate text-xs text-slate-400">{req.name ? req.email : null}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
            <span className="flex items-center gap-1" title={formatDateTime(req.created_at)}>
              <Clock size={11} aria-hidden /> asked {formatAgo(req.created_at)}
            </span>
            {req.previous > 0 && (
              <span className="text-amber-400/90">
                asked {req.previous} time{req.previous > 1 ? "s" : ""} before
              </span>
            )}
            {req.user_agent && <span>· {describeAgent(req.user_agent)}</span>}
          </div>
        </div>
      </div>
      {req.note && (
        <blockquote className="mt-3 whitespace-pre-wrap break-words border-l-2 border-sky-700/60 pl-3 text-sm text-slate-300">
          {req.note}
        </blockquote>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-xs text-slate-400">
          Join as
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)} aria-label={`Role for ${personName(req)}`} className="w-28" title={ROLE_HINT[role]}>
            {grantable.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
        </label>
        <div className="ml-auto flex gap-2">
          <button
            onClick={deny}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 text-sm hover:border-rose-800 hover:text-rose-300 disabled:opacity-50"
          >
            <X size={14} /> {busy === "deny" ? "Denying…" : "Deny"}
          </button>
          <button
            onClick={approve}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-md bg-emerald-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-emerald-400 disabled:opacity-50"
          >
            <Check size={14} /> {busy === "approve" ? "Approving…" : "Approve"}
          </button>
        </div>
      </div>
    </li>
  );
}

function PersonRow({
  user: u,
  isMe,
  canManage,
  grantable,
  onChange,
  onRemoved,
  run,
}: {
  user: User;
  isMe: boolean;
  canManage: boolean;
  grantable: Role[];
  onChange: (u: User) => void;
  onRemoved: () => void;
  run: (fn: () => Promise<void>) => Promise<void>;
}) {
  const name = personName(u);
  const seen =
    u.status === "invited"
      ? `invited ${formatAgo(u.created_at)}, hasn't signed in yet`
      : u.last_login_at
        ? `last signed in ${formatAgo(u.last_login_at)}`
        : null;

  const signOut = async () => {
    const ok = await confirmAction({
      title: `Sign ${name} out everywhere?`,
      body: "Their sessions on every device end. They can sign in again.",
      confirmLabel: "Sign out",
    });
    if (ok)
      run(async () => {
        await api.signOutUser(u.id);
        toast(`Signed ${name} out on every device.`);
      });
  };
  const toggleDisabled = async () => {
    const disabling = u.status !== "disabled";
    if (disabling) {
      const ok = await confirmAction({
        title: `Disable ${name}?`,
        body: "They're signed out everywhere and can't sign in or use their model budget until you enable them again. Their data stays.",
        confirmLabel: "Disable",
        danger: true,
      });
      if (!ok) return;
    }
    run(async () => {
      onChange(await api.updateUser(u.id, { status: disabling ? "disabled" : "active" }));
      toast(disabling ? `Disabled ${name}.` : `Enabled ${name}.`, disabling ? "info" : "success");
    });
  };
  const revoke = async () => {
    const ok = await confirmAction({
      title: `Remove the invite for ${u.email}?`,
      body: "They won't be able to sign in. You can invite them again later.",
      confirmLabel: "Remove invite",
      danger: true,
    });
    if (ok)
      run(async () => {
        await api.revokeInvite(u.id);
        onRemoved();
        toast(`Removed the invite for ${u.email}.`, "info");
      });
  };
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(`${location.origin}/login`);
      toast(`Sign-in link copied. Send it to ${u.email}.`, "info");
    } catch {
      toast(`Copy failed. The link is ${location.origin}/login`, "error");
    }
  };

  const btn = "rounded-md border border-edge px-2 py-1 text-sm hover:bg-edge/60";
  return (
    <li className={`flex flex-wrap items-center gap-3 rounded-lg border border-edge bg-panel px-4 py-3 ${u.status === "disabled" ? "opacity-70" : ""}`}>
      <Avatar person={u} />
      <div className="min-w-48 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate">{name}</span>
          {isMe && <span className="rounded bg-edge px-1.5 text-xs text-slate-300">You</span>}
          <span className={`rounded-full border px-2 text-xs ${STATUS_STYLE[u.status]}`}>{u.status}</span>
        </div>
        <div className="truncate text-xs text-slate-500">
          {u.name ? `${u.email} · ` : ""}
          {seen}
        </div>
      </div>
      <Select
        value={u.role}
        disabled={!canManage || isMe}
        onChange={(e) => run(async () => onChange(await api.updateUser(u.id, { role: e.target.value as Role })))}
        aria-label={`Role for ${name}`}
        className="w-28 shrink-0"
        title={ROLE_HINT[u.role]}
      >
        {(canManage ? grantable : [u.role]).map((r) => (
          <option key={r} value={r}>
            {ROLE_LABEL[r]}
          </option>
        ))}
      </Select>
      {u.role === "member" && canManage && <GrantsField user={u} />}
      <BudgetField
        user={u}
        disabled={!canManage}
        onSave={(v) =>
          run(async () => {
            onChange(await api.updateUser(u.id, { budget_usd: v }));
            toast(`Budget for ${name} is now $${v}.`);
          })
        }
      />
      {u.status === "invited" && canManage && (
        <>
          <button onClick={copyLink} className={`${btn} flex items-center gap-1`} title="Copy the sign-in link to send them">
            <Copy size={13} /> Link
          </button>
          <button onClick={revoke} className={`${btn} hover:border-rose-800 hover:text-rose-300`}>
            Remove
          </button>
        </>
      )}
      {u.status === "active" && !isMe && canManage && (
        <button onClick={signOut} className={btn} title="Ends their sessions; they can sign in again">
          Sign out
        </button>
      )}
      {u.status !== "invited" && !isMe && canManage && (
        <button onClick={toggleDisabled} className={btn}>
          {u.status === "disabled" ? "Enable" : "Disable"}
        </button>
      )}
    </li>
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
  const save = () => value !== user.budget.max_usd && onSave(value);
  return (
    <label className="flex items-center gap-1 text-sm text-slate-400" title="Model spend this period, of the budget">
      {spend != null ? `$${spend.toFixed(2)} of $` : "$"}
      <input
        value={value}
        disabled={disabled}
        inputMode="decimal"
        aria-label={`Budget for ${personName(user)}`}
        onChange={(e) => setValue(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLInputElement).blur()}
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
function GrantsField({ user }: { user: User }) {
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
      toast((e as Error).message, "error");
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
