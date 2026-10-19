import {
  AlertCircle,
  FileImage,
  FileSpreadsheet,
  FileText,
  LoaderCircle,
  Presentation,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import { formatSize } from "../lib/format";
import type { FileBrief, FileStatus } from "../lib/types";

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

/** A file in a message or the composer. Ready files link to their download. */
export function FileChip({
  file,
  status,
  error,
  onRemove,
}: {
  file: Pick<FileBrief, "name" | "size"> & { id?: string };
  status: FileStatus | "uploading";
  error?: string | null;
  onRemove?: () => void;
}) {
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
      {file.id && status === "ready" && !onRemove ? (
        <a href={api.fileUrl(file.id)} className="flex min-w-0 items-center gap-2 hover:text-sky-300">
          {body}
        </a>
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
