import { History, Loader2, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { formatDate } from "../lib/format";
import type { ChatImportItem } from "../lib/types";
import { confirmAction, toast } from "./Feedback";

const SOURCE = { chatgpt: "ChatGPT", claude: "Claude" } as const;

/** Upload an export without waiting on it: the request carries on when the page that started
 * it moves on (onboarding goes ahead while a big export is still going up), and a toast says
 * when it's in. */
export function uploadInBackground(file: File): Promise<boolean> {
  return api.uploadImport(file).then(
    () => {
      toast("Your chat history is in. It's being read in the background");
      return true;
    },
    (e) => {
      toast((e as Error).message, "error");
      return false;
    },
  );
}

/** Upload a ChatGPT or Claude export so the graph starts from years of your own conversations.
 * Only your own messages are kept, read on this machine by the local model in the background
 * as fast as it goes; deleting the import takes back what it taught. */
export function ImportHistory({ assistant }: { assistant: string }) {
  const [items, setItems] = useState<ChatImportItem[]>([]);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const load = useCallback(() => api.imports().then(setItems).catch(() => {}), []);
  useEffect(() => {
    load();
  }, [load]);
  // While one is being read, look again now and then.
  const working = items.some((i) => i.status === "reading" || (i.status === "ready" && i.read < i.kept));
  useEffect(() => {
    if (!working) return;
    const t = window.setInterval(load, 30_000);
    return () => window.clearInterval(t);
  }, [working, load]);

  const upload = async (file: File) => {
    setBusy(true);
    try {
      await api.uploadImport(file);
      toast("Uploaded. Reading it now");
      await load();
      window.setTimeout(load, 8000);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  const remove = async (i: ChatImportItem) => {
    const ok = await confirmAction({
      title: `Delete ${i.name}?`,
      body: `Everything ${assistant} learned only from it leaves the graph. Things you've also said elsewhere stay.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteImport(i.id);
      await load();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  return (
    <section className="mb-4 max-w-xl rounded-lg border border-edge bg-panel p-3 text-sm">
      <div className="flex items-center gap-2">
        <History size={15} className="text-slate-400" />
        <h2 className="font-medium">Bring your chat history</h2>
        <button
          disabled={busy}
          onClick={() => input.current?.click()}
          className="ml-auto flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-xs hover:bg-white/5 disabled:opacity-40"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />} Upload export
        </button>
        <input
          ref={input}
          type="file"
          accept=".zip,.json"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])}
        />
      </div>
      <p className="mt-1 text-xs text-slate-400">
        Upload the data export from ChatGPT (Settings, Data controls, Export) or Claude (Settings, Privacy, Export),
        either the zip or the conversations.json inside it. Only your own messages are kept, with pasted code and
        logs left out; the export itself is deleted once read. A model on this machine reads it into what{" "}
        {assistant} knows in the background, newest first, as fast as this machine goes: a few hundred
        conversations take a couple of hours while it's awake. Nothing goes to a cloud model.
      </p>
      {items.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {items.map((i) => (
            <li key={i.id} className="flex items-center gap-2 rounded-md bg-ink/60 px-2 py-1.5 text-xs">
              <span className="min-w-0 flex-1">
                <span className="text-slate-200">{i.source ? SOURCE[i.source] : i.name}</span>
                <span className="text-slate-500"> · {formatDate(i.created_at)} · </span>
                {i.status === "reading" ? (
                  <span className="text-slate-400">reading the export…</span>
                ) : i.status === "failed" ? (
                  <span className="text-rose-300">{i.error || "couldn't read it"}</span>
                ) : (
                  <span className="text-slate-400">
                    {i.read} of {i.kept} conversations read
                    {i.conversations > i.kept && <> ({i.conversations - i.kept} skipped as empty, very short or with nothing about you)</>}
                  </span>
                )}
                {i.status === "ready" && i.kept > 0 && (
                  <span className="mt-1 block h-1 rounded bg-white/5">
                    <span
                      className="block h-1 rounded bg-sky-500/70"
                      style={{ width: `${Math.round((i.read / i.kept) * 100)}%` }}
                    />
                  </span>
                )}
              </span>
              <button
                onClick={() => void remove(i)}
                className="rounded p-1 text-slate-500 hover:text-rose-300"
                title="Delete this import and what it taught"
                aria-label={`Delete ${i.name}`}
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
