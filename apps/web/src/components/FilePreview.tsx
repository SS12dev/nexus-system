import { Download, ExternalLink, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { formatSize } from "../lib/format";
import { useAssistantName } from "../lib/assistant";
import { loadHighlighter } from "../lib/highlight";
import {
  TEXT_KINDS,
  codeLanguage,
  notebookCells,
  parseCsv,
  prettyJson,
  previewKind,
  readableText,
  type PreviewKind,
} from "../lib/preview";
import type { FileBrief } from "../lib/types";
import { FileIcon } from "./FileChip";
import { Markdown } from "./Markdown";

/** Text files bigger than this are cut short in the preview; Download has the rest. */
const MAX_TEXT = 400_000;

/** A side panel showing a file as itself (PDF, image, Word, code, JSON, CSV, Markdown, text…),
 * plus the text the assistant read from it, with a download button either way. */
export function FilePreview({ file, onClose }: { file: FileBrief; onClose: () => void }) {
  const assistant = useAssistantName();
  const kind = previewKind(file.name, file.content_type);
  const hasOriginal = kind !== "extracted";
  const [view, setView] = useState<"original" | "text">(hasOriginal ? "original" : "text");

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
        className="flex h-full w-full max-w-4xl flex-col border-l border-edge bg-ink"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-edge px-4 py-3">
          <FileIcon name={file.name} />
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium">{file.name}</div>
            <div className="text-xs text-slate-500">{formatSize(file.size)}</div>
          </div>
          {(kind === "pdf" || kind === "image") && (
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
        {hasOriginal && (
          <div className="flex gap-1 border-b border-edge px-4 py-2 text-sm">
            {(["original", "text"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                aria-pressed={view === v}
                className={`rounded-md px-2.5 py-0.5 ${view === v ? "bg-edge text-white" : "text-slate-400 hover:bg-edge/50"}`}
              >
                {v === "original" ? "Preview" : `What ${assistant} read`}
              </button>
            ))}
          </div>
        )}
        {view === "original" && hasOriginal ? <Original file={file} kind={kind} /> : <ReadText file={file} />}
      </div>
    </div>
  );
}

/** The file's bytes, fetched once. The download URL refuses to be framed or run, so binary
 * views (PDF, images, Word) get a blob: URL of their own. */
function useFileBody(file: FileBrief, kind: PreviewKind) {
  const [state, setState] = useState<{
    blob?: Blob;
    text?: string;
    truncated?: boolean;
    error?: boolean;
  }>({});
  useEffect(() => {
    let cancelled = false;
    setState({});
    fetch(api.fileUrl(file.id), { credentials: "same-origin" })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then(async (blob) => {
        if (!TEXT_KINDS.includes(kind)) return { blob };
        const truncated = blob.size > MAX_TEXT;
        return { text: await blob.slice(0, MAX_TEXT).text(), truncated };
      })
      .then((s) => {
        if (!cancelled) setState(s);
      })
      .catch(() => {
        if (!cancelled) setState({ error: true });
      });
    return () => {
      cancelled = true;
    };
  }, [file.id, kind]);
  return state;
}

function useBlobUrl(blob: Blob | undefined, type: string) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) return;
    const u = URL.createObjectURL(new Blob([blob], { type }));
    setUrl(u);
    return () => {
      URL.revokeObjectURL(u);
    };
  }, [blob, type]);
  return url;
}

const Note = ({ children, error }: { children: ReactNode; error?: boolean }) => (
  <p className={`p-5 text-sm ${error ? "text-rose-400" : "text-slate-500"}`}>{children}</p>
);

function Original({ file, kind }: { file: FileBrief; kind: PreviewKind }) {
  const body = useFileBody(file, kind);
  if (body.error) return <Note error>Couldn't load this file. Try Download.</Note>;
  if (!body.blob && body.text == null) return <Note>Loading…</Note>;

  const cut = body.truncated && (
    <p className="border-t border-edge px-5 py-2 text-xs text-amber-300">
      Showing the first {formatSize(MAX_TEXT)}. Download the file for the rest.
    </p>
  );
  const text = body.text ?? "";

  switch (kind) {
    case "pdf":
      return <BlobFrame blob={body.blob!} type="application/pdf" title={file.name} />;
    case "html":
      // Shown as a page with scripts off (sandbox without allow-scripts), never run.
      return <HtmlFrame html={text} title={file.name} />;
    case "image":
      return <ImageView blob={body.blob!} name={file.name} />;
    case "docx":
      return <DocxView blob={body.blob!} />;
    case "markdown":
      return (
        <Scroll>
          <div className="px-5 py-4">
            <Markdown size="sm">{text}</Markdown>
          </div>
          {cut}
        </Scroll>
      );
    case "json":
      return (
        <Scroll>
          <Code text={body.truncated ? text : prettyJson(text)} language="json" />
          {cut}
        </Scroll>
      );
    case "notebook":
      return <NotebookView text={text} truncated={!!body.truncated} />;
    case "csv":
      return (
        <Scroll>
          <CsvTable text={text} />
          {cut}
        </Scroll>
      );
    case "code":
      return (
        <Scroll>
          <Code text={text} language={codeLanguage(file.name)} />
          {cut}
        </Scroll>
      );
    default:
      return (
        <Scroll>
          <pre className="px-5 py-4 font-mono text-[13px] leading-relaxed break-words whitespace-pre-wrap text-slate-200">
            {text}
          </pre>
          {cut}
        </Scroll>
      );
  }
}

const Scroll = ({ children }: { children: ReactNode }) => (
  <div className="min-h-0 flex-1 overflow-auto">{children}</div>
);

function BlobFrame({ blob, type, title }: { blob: Blob; type: string; title: string }) {
  const url = useBlobUrl(blob, type);
  return url ? (
    <iframe src={url} title={title} className="min-h-0 w-full flex-1 bg-white" />
  ) : (
    <Note>Loading…</Note>
  );
}

function HtmlFrame({ html, title }: { html: string; title: string }) {
  return <iframe srcDoc={html} sandbox="" title={title} className="min-h-0 w-full flex-1 bg-white" />;
}

function ImageView({ blob, name }: { blob: Blob; name: string }) {
  const url = useBlobUrl(blob, blob.type || "image/png");
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-4">
      {url && <img src={url} alt={name} className="max-h-full max-w-full object-contain" />}
    </div>
  );
}

/** Word documents laid out as pages by docx-preview, loaded only when one is opened. */
function DocxView({ blob }: { blob: Blob }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const el = ref.current;
    if (!el) return;
    import("docx-preview")
      .then(({ renderAsync }) => {
        if (cancelled) return;
        el.replaceChildren();
        return renderAsync(blob, el, undefined, {
          className: "docx",
          inWrapper: true,
          ignoreLastRenderedPageBreak: true,
          renderComments: false,
        });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [blob]);
  if (failed)
    return <Note error>Couldn't lay out this document. The other tab shows its text, or use Download.</Note>;
  return <div ref={ref} className="docx-host min-h-0 flex-1 overflow-auto bg-slate-200" />;
}

/** Code with syntax colours and line numbers; plain until highlight.js has loaded. */
function Code({ text, language }: { text: string; language: string }) {
  const [html, setHtml] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setHtml(null);
    // Very long files stay plain: colouring them would freeze the page for little gain.
    if (text.length > 200_000) return;
    loadHighlighter().then((hljs) => {
      if (cancelled) return;
      const lang = hljs.getLanguage(language) ? language : "plaintext";
      setHtml(hljs.highlight(text, { language: lang }).value);
    });
    return () => {
      cancelled = true;
    };
  }, [text, language]);

  const lines = text.split("\n").length;
  return (
    <div className="flex font-mono text-[13px] leading-relaxed">
      <pre
        aria-hidden
        className="shrink-0 border-r border-edge px-3 py-4 text-right text-slate-600 select-none"
      >
        {Array.from({ length: lines }, (_, i) => i + 1).join("\n")}
      </pre>
      {html != null ? (
        <pre
          className="hljs min-w-0 flex-1 overflow-x-auto px-4 py-4"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="min-w-0 flex-1 overflow-x-auto px-4 py-4 text-slate-200">{text}</pre>
      )}
    </div>
  );
}

function CsvTable({ text }: { text: string }) {
  const rows = parseCsv(text);
  if (!rows.length) return <Note>Empty file.</Note>;
  const [head, ...body] = rows;
  return (
    <div className="p-4">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="sticky top-0 bg-panel">
          <tr>
            {head.map((h, i) => (
              <th key={i} className="border border-edge px-2 py-1.5 font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((r, i) => (
            <tr key={i} className="odd:bg-panel/40">
              {r.map((c, j) => (
                <td key={j} className="border border-edge px-2 py-1 align-top text-slate-300">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length >= 500 && (
        <p className="mt-2 text-xs text-amber-300">
          Showing the first 500 rows. Download the file for the rest.
        </p>
      )}
    </div>
  );
}

function NotebookView({ text, truncated }: { text: string; truncated: boolean }) {
  const cells = truncated ? null : notebookCells(text);
  if (!cells)
    return (
      <Scroll>
        <Code text={text} language="json" />
      </Scroll>
    );
  return (
    <Scroll>
      <div className="space-y-3 p-4">
        {cells.map((c, i) =>
          c.kind === "markdown" ? (
            <div key={i} className="px-1">
              <Markdown size="sm">{c.source}</Markdown>
            </div>
          ) : (
            <div key={i} className="overflow-hidden rounded-md border border-edge">
              <Code text={c.source} language="python" />
              {c.outputs.length > 0 && (
                <pre className="border-t border-edge bg-panel px-4 py-2 font-mono text-xs whitespace-pre-wrap text-slate-400">
                  {c.outputs.join("")}
                </pre>
              )}
            </div>
          ),
        )}
      </div>
    </Scroll>
  );
}

/** The text the assistant extracted from the file, which is what it reads when answering. */
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

  const more = () => api.fileText(file.id, text?.length ?? 0).then((r) => setText((t) => (t ?? "") + r.text));

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
        <Markdown size="sm">{readableText(text)}</Markdown>
      ) : (
        <p className="text-sm text-slate-500">No text in this file.</p>
      )}
      {text != null && text.length < total && (
        <button
          onClick={more}
          className="mt-4 rounded-md border border-edge px-3 py-1.5 text-sm hover:bg-edge/60"
        >
          Load more
        </button>
      )}
    </div>
  );
}
