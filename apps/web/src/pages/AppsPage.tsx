import { ExternalLink, FolderGit2, Globe, Play, RefreshCw, RotateCw, Square, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { STACK_LABEL } from "../components/ProjectCard";
import { api } from "../lib/api";
import { appActions, appState } from "../lib/apps";
import { formatDateTime } from "../lib/format";
import { isOwner, type AppItem } from "../lib/types";
import { useAssistantName } from "../lib/assistant";
import { useStore } from "../store/chat";

/** The apps software teams built and run: the owner sees everyone's, members their own. */
export function AppsPage() {
  const assistant = useAssistantName();
  const user = useStore((s) => s.user)!;
  const owner = isOwner(user);
  const [apps, setApps] = useState<AppItem[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .apps()
      .then(setApps)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);
  // While something deploys, check back now and then.
  useEffect(() => {
    if (!apps?.some((a) => a.state === "building")) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [apps, load]);

  const act = async (a: AppItem, fn: () => Promise<unknown>) => {
    setError("");
    setBusy(a.id);
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const remove = (a: AppItem) => {
    if (!confirm(`Delete the running ${a.name}? Its container and image go; the project and its repo stay.`)) return;
    act(a, () => api.deleteApp(a.id));
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-6">
        <h1 className="mb-1 text-xl font-semibold">Apps</h1>
        <p className="mb-5 text-sm text-slate-400">
          Software {assistant}'s teams built, running on this machine.{" "}
          {owner ? "You see everyone's apps." : "Members can have 3 running at once."} Ask {assistant} in a chat to
          build one.
        </p>
        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}
        {apps === null ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : apps.length === 0 ? (
          <p className="rounded-xl border border-edge bg-panel p-4 text-sm text-slate-400">
            No apps yet. Try "build me a 2048 game".
          </p>
        ) : (
          <ul className="space-y-3">
            {apps.map((a) => {
              const st = appState(a);
              const can = appActions(a);
              const off = busy === a.id;
              return (
                <li key={a.id} className="rounded-xl border border-edge bg-panel p-3">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="truncate font-medium">{a.name}</span>
                    <span className={`text-xs ${st.style}`}>{st.label}</span>
                    {a.stack && <span className="text-xs text-slate-500">{STACK_LABEL[a.stack] ?? a.stack}</span>}
                    {owner && a.owner_id !== user.id && <span className="text-xs text-slate-500">· {a.owner}</span>}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                    {a.live_url && (
                      <a href={a.live_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-sky-300 hover:text-sky-200">
                        <Globe size={12} /> {a.live_url.replace(/^https?:\/\//, "")}
                      </a>
                    )}
                    {a.repo_url && (
                      <a href={a.repo_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-sky-300 hover:text-sky-200">
                        <FolderGit2 size={12} /> Repo <ExternalLink size={11} />
                      </a>
                    )}
                    {a.deployed_at && <span>Deployed {formatDateTime(a.deployed_at)}</span>}
                    {owner && (
                      <label className="flex items-center gap-1" title="Applies from the next deploy">
                        <input
                          type="checkbox"
                          checked={a.internet}
                          disabled={off}
                          onChange={(e) => act(a, () => api.setAppInternet(a.id, e.target.checked))}
                        />
                        Internet access
                      </label>
                    )}
                  </div>
                  {a.deploy_error && a.state === "failed" && (
                    <p className="mt-2 text-xs text-rose-400">{a.deploy_error}</p>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2 text-sm">
                    {can.stop && (
                      <button disabled={off} onClick={() => act(a, () => api.appAction(a.id, "stop"))} className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1 hover:bg-edge/60 disabled:opacity-50">
                        <Square size={13} /> Stop
                      </button>
                    )}
                    {can.restart && (
                      <button disabled={off} onClick={() => act(a, () => api.appAction(a.id, "restart"))} className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1 hover:bg-edge/60 disabled:opacity-50">
                        {a.state === "stopped" ? <Play size={13} /> : <RotateCw size={13} />}
                        {a.state === "stopped" ? "Start" : "Restart"}
                      </button>
                    )}
                    {can.redeploy && (
                      <button disabled={off} onClick={() => act(a, () => api.appAction(a.id, "redeploy"))} className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1 hover:bg-edge/60 disabled:opacity-50">
                        <RefreshCw size={13} /> Redeploy
                      </button>
                    )}
                    <button disabled={off} onClick={() => remove(a)} className="ml-auto flex items-center gap-1.5 rounded-md border border-edge px-3 py-1 text-rose-300 hover:bg-edge/60 disabled:opacity-50">
                      <Trash2 size={13} /> Delete
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
