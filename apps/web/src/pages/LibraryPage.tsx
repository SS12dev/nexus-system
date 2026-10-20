import { Download, Eye, Pencil, RotateCw, Search, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link } from "react-router";
import { FileIcon, StatusIcon } from "../components/FileChip";
import { FilePreview } from "../components/FilePreview";
import { api } from "../lib/api";
import { ACCEPT, formatDate, formatSize } from "../lib/format";
import { hasRole, type FileItem, type SearchHit } from "../lib/types";
import { useStore } from "../store/chat";
import { useAssistantName } from "../lib/assistant";

export function LibraryPage() {
  const assistant = useAssistantName();
  const user = useStore((s) => s.user)!;
  const live = useStore((s) => s.files);
  const upsertFile = useStore((s) => s.upsertFile);
  const [ids, setIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [preview, setPreview] = useState<FileItem | null>(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const canEdit = hasRole(user, "member");

  useEffect(() => {
    api
      .files()
      .then((list) => {
        list.forEach(upsertFile);
        setIds(list.map((f) => f.id));
      })
      .catch((e) => setError(e.message));
  }, [upsertFile]);

  const files = useMemo(() => ids.map((id) => live[id]).filter(Boolean), [ids, live]);

  const run = async (fn: () => Promise<void>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const upload = (list: FileList | File[]) =>
    Array.from(list).forEach((file) =>
      run(async () => {
        const f = await api.uploadFile(file);
        upsertFile(f);
        setIds((xs) => [f.id, ...xs]);
      }),
    );

  const search = (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return setHits(null);
    run(async () => setHits(await api.searchFiles(query.trim())));
  };

  const rename = (f: FileItem) => {
    const name = prompt("Rename file", f.name);
    if (name && name !== f.name) run(async () => upsertFile(await api.renameFile(f.id, name)));
  };

  const remove = (f: FileItem) => {
    if (!confirm(`Delete ${f.name}? ${assistant} will no longer be able to read it.`)) return;
    run(async () => {
      await api.deleteFile(f.id);
      setIds((xs) => xs.filter((x) => x !== f.id));
      useStore.getState().handleEvent({ type: "file.deleted", data: { id: f.id } });
    });
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (canEdit && e.dataTransfer.files.length) upload(e.dataTransfer.files);
  };

  return (
    <div
      className="flex-1 overflow-y-auto"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="mx-auto max-w-4xl px-4 py-6">
        <div className="mb-1 flex items-center gap-3">
          <h1 className="text-xl font-semibold">Library</h1>
          {canEdit && (
            <button
              onClick={() => picker.current?.click()}
              className="ml-auto flex items-center gap-2 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink"
            >
              <Upload size={16} /> Upload
            </button>
          )}
          <input
            ref={picker}
            type="file"
            multiple
            hidden
            accept={ACCEPT}
            onChange={(e) => {
              if (e.target.files) upload(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
        <p className="mb-5 text-sm text-slate-400">
          Every file you upload, here or in a chat. {assistant} reads them, so you can ask about them in
          any chat. Only you can see your files.
        </p>

        <form onSubmit={search} className="mb-5 flex gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-md border border-edge bg-panel px-3">
            <Search size={16} className="text-slate-500" />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                if (!e.target.value) setHits(null);
              }}
              placeholder="Search inside your documents"
              className="flex-1 bg-transparent py-2 outline-none"
            />
          </div>
          <button className="rounded-md border border-edge px-4 py-2 text-sm hover:bg-edge/60">
            Search
          </button>
        </form>

        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

        {dragging && canEdit && (
          <div className="mb-4 rounded-lg border-2 border-dashed border-sky-400 p-6 text-center text-sky-300">
            Drop files to upload
          </div>
        )}

        {hits ? (
          <div className="space-y-2">
            {hits.length === 0 && <p className="text-slate-400">Nothing found.</p>}
            {hits.map((h, i) => (
              <button
                key={i}
                onClick={() => live[h.file.id] && setPreview(live[h.file.id])}
                className="block w-full rounded-lg border border-edge bg-panel px-4 py-3 text-left hover:border-sky-700"
              >
                <div className="mb-1 flex items-center gap-2 text-sm text-sky-300">
                  <FileIcon name={h.file.name} size={14} /> {h.file.name}
                  {h.heading && <span className="truncate text-slate-500">· {h.heading}</span>}
                </div>
                <p className="line-clamp-3 text-sm text-slate-300">{h.snippet}</p>
              </button>
            ))}
          </div>
        ) : files.length === 0 ? (
          <p className="text-slate-400">No files yet. Upload one, or attach one in a chat.</p>
        ) : (
          <div className="divide-y divide-edge rounded-lg border border-edge bg-panel">
            {files.map((f) => (
              <div key={f.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <FileIcon name={f.name} size={20} />
                <div className="min-w-40 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate">{f.name}</span>
                    <StatusIcon status={f.status} />
                  </div>
                  <div className={`text-xs ${f.status === "failed" ? "text-rose-400" : "text-slate-500"}`}>
                    {f.status === "failed"
                      ? f.error
                      : f.status === "processing"
                        ? `${assistant} is reading it…`
                        : `${formatSize(f.size)} · ${formatDate(f.created_at)}`}
                    {f.chat_id && f.status === "ready" && (
                      <>
                        {" · "}
                        <Link to={`/c/${f.chat_id}`} className="hover:text-sky-300">
                          from a chat
                        </Link>
                      </>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 text-slate-400">
                  {f.status === "ready" && (
                    <IconButton label="Preview" onClick={() => setPreview(f)}>
                      <Eye size={16} />
                    </IconButton>
                  )}
                  <a href={api.fileUrl(f.id)} className="p-2 hover:text-white" aria-label="Download" title="Download">
                    <Download size={16} />
                  </a>
                  {canEdit && f.status === "failed" && (
                    <IconButton
                      label="Try again"
                      onClick={() => run(async () => upsertFile(await api.retryFile(f.id)))}
                    >
                      <RotateCw size={16} />
                    </IconButton>
                  )}
                  {canEdit && (
                    <>
                      <IconButton label="Rename" onClick={() => rename(f)}>
                        <Pencil size={16} />
                      </IconButton>
                      <IconButton label="Delete" onClick={() => remove(f)} danger>
                        <Trash2 size={16} />
                      </IconButton>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {preview && <FilePreview file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

function IconButton({
  label,
  onClick,
  danger,
  children,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`p-2 ${danger ? "hover:text-rose-400" : "hover:text-white"}`}
    >
      {children}
    </button>
  );
}
