import {
  AlertCircle,
  Download,
  FileImage,
  FileSpreadsheet,
  FileText,
  LoaderCircle,
  Presentation,
  X,
} from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { formatSize } from "../lib/format";
import type { FileBrief, FileStatus } from "../lib/types";
import { FilePreview } from "./FilePreview";

export function FileIcon({ name, size = 16 }: { name: string; size?: number }) {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (["xlsx", "xls", "csv"].includes(ext)) return <FileSpreadsheet size={size} />;
  if (ext === "pptx") return <Presentation size={size} />;
  if (["png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp"].includes(ext))
    return <FileImage size={size} />;
  return <FileText size={size} />;
}

export function StatusIcon({ status }: { status: FileStatus | "uploading" }) {
  if (status === "uploading" || status === "processing")
    return <LoaderCircle size={14} className="animate-spin text-sky-400" />;
  if (status === "failed") return <AlertCircle size={14} className="text-rose-400" />;
  return null;
}

/** A file in a message or the composer. Ready files open a preview and have a download button. */
export function FileChip({
  file,
  status,
  error,
  onRemove,
}: {
  file: Pick<FileBrief, "name" | "size"> & Partial<FileBrief>;
  status: FileStatus | "uploading";
  error?: string | null;
  onRemove?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const label =
    status === "uploading"
      ? "Uploading"
      : status === "processing"
        ? "Reading"
        : status === "failed"
          ? (error ?? "Couldn't read this file")
          : formatSize(file.size);
  const body = (
    <>
      <FileIcon name={file.name} />
      <span className="min-w-0">
        <span className="block max-w-48 truncate">{file.name}</span>
        <span className={`block text-xs ${status === "failed" ? "text-rose-400" : "text-slate-500"}`}>
          {label}
        </span>
      </span>
      <StatusIcon status={status} />
    </>
  );
  return (
    <div
      className="flex max-w-full items-center gap-2 rounded-lg border border-edge bg-panel px-2.5 py-1.5 text-sm"
      title={error ?? file.name}
    >
      {file.id && file.content_type && status === "ready" && !onRemove ? (
        <>
          <button
            onClick={() => setOpen(true)}
            className="flex min-w-0 items-center gap-2 text-left hover:text-sky-300"
            title={`Preview ${file.name}`}
          >
            {body}
          </button>
          <a
            href={api.fileUrl(file.id)}
            download={file.name}
            className="ml-1 text-slate-500 hover:text-white"
            aria-label={`Download ${file.name}`}
            title="Download"
          >
            <Download size={14} />
          </a>
          {open && <FilePreview file={file as FileBrief} onClose={() => setOpen(false)} />}
        </>
      ) : (
        <span className="flex min-w-0 items-center gap-2">{body}</span>
      )}
      {onRemove && (
        <button onClick={onRemove} className="text-slate-500 hover:text-white" aria-label="Remove">
          <X size={14} />
        </button>
      )}
    </div>
  );
}
