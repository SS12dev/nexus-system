import { CalendarDays, CheckSquare, HardDrive, Mail, Monitor, Users } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { AssistantNameForm } from "../components/AssistantNameForm";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { describeAgent, formatAgo, formatDate } from "../lib/format";
import type { GoogleAccess, SessionInfo } from "../lib/types";

const SERVICES: Record<string, { label: string; what: string; icon: ReactNode }> = {
  gmail: { label: "Gmail", what: "Read, label, draft and send mail", icon: <Mail size={16} /> },
  calendar: {
    label: "Calendar",
    what: "See and manage your events",
    icon: <CalendarDays size={16} />,
  },
  contacts: { label: "Contacts", what: "Look up people you know", icon: <Users size={16} /> },
  tasks: { label: "Tasks", what: "See and manage your tasks", icon: <CheckSquare size={16} /> },
  drive: { label: "Drive", what: "Read your files", icon: <HardDrive size={16} /> },
};

const ERRORS: Record<string, string> = {
  google_cancelled: "Google access wasn't changed.",
  google_other_account: "Choose the same Google account you sign in with.",
};

export function SettingsPage() {
  const assistant = useAssistantName();
  const [params] = useSearchParams();
  const [access, setAccess] = useState<GoogleAccess | null>(null);
  const [error, setError] = useState(ERRORS[params.get("error") ?? ""] ?? "");

  useEffect(() => {
    api.googleAccess().then(setAccess).catch((e) => setError(e.message));
  }, []);

  const disconnect = async () => {
    if (!confirm(`Stop ${assistant} from using your Google account? Agents lose access right away.`))
      return;
    try {
      await api.disconnectGoogle();
      setAccess(await api.googleAccess());
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const missing = access ? access.requested.filter((s) => !access.granted.includes(s)) : [];
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-2xl px-4 py-6">
        <h1 className="mb-1 text-xl font-semibold">Settings</h1>
        <p className="mb-6 text-sm text-slate-400">
          Your assistant's name, the Google services you let {assistant} and its agents use, and
          the devices you're signed in on. Only you can see this.
        </p>
        <section className="mb-6 rounded-lg border border-edge bg-panel p-4">
          <h2 className="mb-1 font-medium">Your assistant</h2>
          <p className="mb-3 text-xs text-slate-500">
            What you call it in chats, and later by voice. Other people name their own.
          </p>
          <AssistantNameForm />
        </section>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
        {access && !access.available && (
          <p className="text-sm text-slate-400">
            Google services aren't set up on this server. They need Google sign-in and
            GOOGLE_SERVICES in .env.
          </p>
        )}
        {access?.available && (
          <section className="rounded-lg border border-edge bg-panel p-4">
            <div className="mb-3 flex items-center gap-3">
              <h2 className="font-medium">Google</h2>
              <span className="text-xs text-slate-500">
                {access.connected_at
                  ? `Updated ${formatDate(access.connected_at)}`
                  : "Not connected"}
              </span>
            </div>
            <ul className="space-y-2">
              {access.requested.map((s) => {
                const info = SERVICES[s] ?? { label: s, what: "", icon: null };
                const on = access.granted.includes(s);
                return (
                  <li key={s} className="flex items-center gap-3 text-sm">
                    <span className="text-slate-400">{info.icon}</span>
                    <span className="w-20">{info.label}</span>
                    <span className="flex-1 text-slate-400">{info.what}</span>
                    <span className={on ? "text-emerald-400" : "text-slate-500"}>
                      {on ? "Allowed" : "Not allowed"}
                    </span>
                  </li>
                );
              })}
            </ul>
            <div className="mt-4 flex flex-wrap gap-2 border-t border-edge pt-3">
              {missing.length > 0 && (
                <a
                  href="/auth/google/connect"
                  className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400"
                >
                  {access.granted.length ? "Allow more" : "Connect Google"}
                </a>
              )}
              {access.granted.length > 0 && (
                <button
                  onClick={disconnect}
                  className="rounded-md border border-edge px-3 py-1.5 text-sm hover:bg-edge/60"
                >
                  Disconnect
                </button>
              )}
            </div>
          </section>
        )}
        <Devices onError={setError} />
      </div>
    </div>
  );
}

/** Browsers signed in as you. Sign out one you don't recognise, or all but this one. */
function Devices({ onError }: { onError: (msg: string) => void }) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const load = () => api.sessions().then(setSessions).catch((e) => onError(e.message));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const act = async (fn: () => Promise<void>) => {
    try {
      await fn();
      await load();
    } catch (e) {
      onError((e as Error).message);
    }
  };
  return (
    <section className="mt-6 rounded-lg border border-edge bg-panel p-4">
      <h2 className="mb-1 font-medium">Signed-in devices</h2>
      <p className="mb-3 text-xs text-slate-500">Each stays signed in for 30 days.</p>
      <ul className="space-y-2">
        {sessions.map((s) => (
          <li key={s.id} className="flex items-center gap-3 text-sm">
            <Monitor size={16} className="text-slate-400" />
            <div className="min-w-0 flex-1">
              <div className="truncate">
                {describeAgent(s.user_agent)}
                {s.current && <span className="ml-2 text-xs text-emerald-400">this device</span>}
              </div>
              <div className="text-xs text-slate-500">
                Signed in {formatAgo(s.created_at)}
                {s.ip ? ` from ${s.ip}` : ""}
              </div>
            </div>
            {!s.current && (
              <button
                onClick={() => act(() => api.signOutSession(s.id))}
                className="rounded-md border border-edge px-2 py-1 text-xs hover:bg-edge/60"
              >
                Sign out
              </button>
            )}
          </li>
        ))}
      </ul>
      {sessions.filter((s) => !s.current).length > 1 && (
        <button
          onClick={() => act(() => api.signOutOthers())}
          className="mt-3 rounded-md border border-edge px-3 py-1.5 text-sm hover:bg-edge/60"
        >
          Sign out all other devices
        </button>
      )}
    </section>
  );
}
