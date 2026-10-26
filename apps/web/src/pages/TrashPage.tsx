import { ArchiveRestore, Clock3, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import type { TrashItem } from "../lib/types";
import { confirmAction, toast } from "../components/Feedback";
import { useStore } from "../store/chat";

const LABELS: Record<string, string> = {
  app: "App",
  chat: "Chat",
  entity: "Knowledge entity",
  file: "File",
  folder: "Folder",
  link: "Library link",
  memories_all: "All memories",
  memory: "Memory",
  project: "Software project",
  routine: "Routine",
  skill: "Skill",
  system: "Agent system",
};

function remaining(expiresAt: string): string {
  const days = Math.ceil((Date.parse(expiresAt) - Date.now()) / 86_400_000);
  if (days <= 0) return "Expired — permanent cleanup is pending";
  return `Permanently deleted in ${days} day${days === 1 ? "" : "s"}`;
}

export function TrashPage() {
  const [items, setItems] = useState<TrashItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const notifyChange = useStore((s) => s.handleEvent);

  const load = async () => {
    try {
      setItems(await api.trash());
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const restore = async (item: TrashItem) => {
    setBusy(item.id);
    try {
      await api.restoreTrash(item.id);
      notifyChange({ type: "trash.changed", data: { kind: item.kind } });
      toast(`Restored ${item.title}`);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const purge = async (item: TrashItem) => {
    const ok = await confirmAction({
      title: `Permanently delete ${item.title}?`,
      body: "This cannot be undone. Files and project resources will be removed permanently.",
      confirmLabel: "Delete permanently",
      danger: true,
    });
    if (!ok) return;
    setBusy(item.id);
    try {
      await api.permanentlyDeleteTrash(item.id);
      setItems((current) => current.filter((entry) => entry.id !== item.id));
      toast("Permanent deletion queued");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-4 py-6">
        <h1 className="mb-1 text-xl font-semibold">Trash</h1>
        <p className="mb-6 text-sm text-slate-400">
          Deleted items stay here for seven days. Restore them any time before they expire, or
          permanently delete them now.
        </p>
        {error && <p role="alert" className="mb-4 text-sm text-rose-400">{error}</p>}
        {items.length === 0 ? (
          <div className="rounded-xl border border-edge bg-panel p-8 text-center text-sm text-slate-400">
            Trash is empty.
          </div>
        ) : (
          <ul className="space-y-2">
            {items.map((item) => {
              const expired = Date.parse(item.expires_at) <= Date.now();
              return (
                <li
                  key={item.id}
                  className="flex flex-wrap items-center gap-3 rounded-lg border border-edge bg-panel p-3"
                >
                  <Clock3 size={16} className="shrink-0 text-slate-400" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{item.title}</div>
                    <div className="text-xs text-slate-500">
                      {LABELS[item.kind] ?? item.kind} · deleted {formatDate(item.created_at)} ·{" "}
                      {remaining(item.expires_at)}
                    </div>
                  </div>
                  {!expired && (
                    <button
                      disabled={busy !== null}
                      onClick={() => void restore(item)}
                      className="flex items-center gap-1 rounded-md border border-edge px-2 py-1.5 text-sm hover:bg-edge/60 disabled:opacity-50"
                    >
                      <ArchiveRestore size={14} />
                      {busy === item.id ? "Working…" : "Restore"}
                    </button>
                  )}
                  <button
                    disabled={busy !== null}
                    onClick={() => void purge(item)}
                    className="flex items-center gap-1 rounded-md border border-rose-500/30 px-2 py-1.5 text-sm text-rose-300 hover:bg-rose-500/10 disabled:opacity-50"
                  >
                    <Trash2 size={14} />
                    Delete permanently
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
