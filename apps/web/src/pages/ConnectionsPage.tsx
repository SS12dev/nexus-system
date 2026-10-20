import { CalendarDays, CheckSquare, HardDrive, Mail, Users } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import type { GoogleAccess } from "../lib/types";

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
  google_other_account: "Choose the same Google account you sign in to Jarvis with.",
};

export function ConnectionsPage() {
  const [params] = useSearchParams();
  const [access, setAccess] = useState<GoogleAccess | null>(null);
  const [error, setError] = useState(ERRORS[params.get("error") ?? ""] ?? "");

  useEffect(() => {
    api.googleAccess().then(setAccess).catch((e) => setError(e.message));
  }, []);

  const disconnect = async () => {
    if (!confirm("Stop Jarvis from using your Google account? Agents lose access right away."))
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
        <h1 className="mb-1 text-xl font-semibold">Connections</h1>
        <p className="mb-6 text-sm text-slate-400">
          Google services you let Jarvis and its agents use. You chose these when you signed in;
          change them here at any time. Only you can see this.
        </p>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
        {access && !access.available && (
          <p className="text-sm text-slate-400">
            Google services aren't set up on this Jarvis. They need Google sign-in and
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
      </div>
    </div>
  );
}
