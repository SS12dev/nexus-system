import { Download, ExternalLink, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { formatSize } from "../lib/format";
import { useAssistantName } from "../lib/assistant";
import type { FileBrief } from "../lib/types";
import { FileIcon } from "./FileChip";
import { Markdown } from "./Markdown";

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];

/** How a file can be shown as itself in the browser, if at all. */
export function originalKind(file: Pick<FileBrief, "content_type">): "image" | "pdf" | null {
  if (IMAGE_TYPES.includes(file.content_type)) return "image";
  if (file.content_type === "application/pdf") return "pdf";
  return null;
}

/** A side panel showing a file: the original for PDFs and images, otherwise the text Jarvis
 * read from it, with a download button either way. */
export function FilePreview({ file, onClose }: { file: FileBrief; onClose: () => void }) {
  const assistant = useAssistantName();
  const kind = originalKind(file);
  const [view, setView] = useState<"original" | "text">(kind ? "original" : "text");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60" onClick={onClose}>
      <div
        role="dialog"
        aria-label={file.name}
        className="flex h-full w-full max-w-3xl flex-col border-l border-edge bg-ink"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-edge px-4 py-3">
          <FileIcon name={file.name} />
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium">{file.name}</div>
            <div className="text-xs text-slate-500">{formatSize(file.size)}</div>
          </div>
          {kind && (
            <a
              href={api.fileUrl(file.id, true)}
              target="_blank"
              rel="noreferrer"
              className="hidden items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60 sm:flex"
            >
              <ExternalLink size={14} /> Open
            </a>
          )}
          <a
            href={api.fileUrl(file.id)}
            download={file.name}
            className="flex items-center gap-1 rounded-md bg-sky-600 px-2.5 py-1 text-sm hover:bg-sky-500"
          >
            <Download size={14} /> Download
          </a>
          <button onClick={onClose} aria-label="Close" className="text-slate-400 hover:text-white">
            <X size={18} />
          </button>
        </div>
        {kind && (
          <div className="flex gap-1 border-b border-edge px-4 py-2 text-sm">
            {(["original", "text"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                aria-pressed={view === v}
                className={`rounded-md px-2.5 py-0.5 ${view === v ? "bg-edge text-white" : "text-slate-400 hover:bg-edge/50"}`}
              >
                {v === "original" ? "File" : `What ${assistant} read`}
              </button>
            ))}
          </div>
        )}
        {view === "original" && kind ? <Original file={file} kind={kind} /> : <ReadText file={file} />}
      </div>
    </div>
  );
}

function Original({ file, kind }: { file: FileBrief; kind: "image" | "pdf" }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  // The PDF goes in through a blob: URL; the download URL itself refuses to be framed.
  useEffect(() => {
    if (kind !== "pdf") return;
    let blobUrl: string | null = null;
    fetch(api.fileUrl(file.id, true), { credentials: "same-origin" })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => {
        blobUrl = URL.createObjectURL(new Blob([b], { type: "application/pdf" }));
        setUrl(blobUrl);
      })
      .catch(() => setFailed(true));
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [file.id, kind]);

  if (failed) return <p className="p-5 text-sm text-rose-400">Couldn't load this file. Try Download.</p>;
  if (kind === "image")
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-4">
        <img src={api.fileUrl(file.id, true)} alt={file.name} className="max-h-full max-w-full object-contain" onError={() => setFailed(true)} />
      </div>
    );
  return url ? (
    <iframe src={url} title={file.name} className="min-h-0 w-full flex-1 bg-white" />
  ) : (
    <p className="p-5 text-sm text-slate-500">Loading…</p>
  );
}

/** The text Jarvis extracted from the file, which is what it reads when answering. */
function ReadText({ file }: { file: FileBrief }) {
  const assistant = useAssistantName();
  const [text, setText] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const ready = file.status === "ready";

  useEffect(() => {
    if (!ready) return;
    setText(null);
    api
      .fileText(file.id)
      .then((r) => {
        setText(r.text);
        setTotal(r.total_chars);
      })
      .catch((e) => setError(e.message));
  }, [file.id, ready]);

  const more = () =>
    api.fileText(file.id, text?.length ?? 0).then((r) => setText((t) => (t ?? "") + r.text));

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      {!ready ? (
        <p className="text-sm text-slate-500">
          {file.status === "failed"
            ? `${assistant} couldn't read this file.`
            : `${assistant} is still reading this file…`}
        </p>
      ) : error ? (
        <p className="text-sm text-rose-400">{error}</p>
      ) : text == null ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : text.trim() ? (
        <Markdown size="sm">{text}</Markdown>
      ) : (
        <p className="text-sm text-slate-500">No text in this file.</p>
      )}
      {text != null && text.length < total && (
        <button onClick={more} className="mt-4 rounded-md border border-edge px-3 py-1.5 text-sm hover:bg-edge/60">
          Load more
        </button>
      )}
    </div>
  );
}
