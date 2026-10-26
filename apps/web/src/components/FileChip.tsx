import {
  AlertCircle,
  Box,
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
import { canPreview } from "../lib/preview";
import type { FileBrief, FileStatus } from "../lib/types";
import { FilePreview } from "./FilePreview";

export function FileIcon({ name, size = 16 }: { name: string; size?: number }) {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (["xlsx", "xls", "csv"].includes(ext)) return <FileSpreadsheet size={size} />;
  if (ext === "pptx") return <Presentation size={size} />;
  if (["stl", "step", "stp", "3mf", "obj", "glb", "ply"].includes(ext)) return <Box size={size} />;
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
  const viewable = canPreview({ ...file, status });
  // Failed to read but still viewable: the file is fine, only searching its text isn't.
  const failed = status === "failed" && !viewable;
  const label =
    status === "uploading"
      ? "Uploading"
      : status === "processing"
        ? "Reading"
        : failed
          ? (error ?? "Couldn't read this file")
          : formatSize(file.size);
  const body = (
    <>
      <FileIcon name={file.name} />
      <span className="min-w-0">
        <span className="block max-w-48 truncate">{file.name}</span>
        <span className={`block text-xs ${failed ? "text-rose-400" : "text-slate-500"}`}>
          {label}
        </span>
      </span>
      {(failed || status !== "failed") && <StatusIcon status={status} />}
    </>
  );
  return (
    <div
      className="flex max-w-full items-center gap-2 rounded-lg border border-edge bg-panel px-2.5 py-1.5 text-sm"
      title={error ?? file.name}
    >
      {file.id && file.content_type && viewable && !onRemove ? (
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

/** A picture in a message or the composer, shown as a thumbnail; it opens the preview. src is a
 * local object URL while the composer holds it, the stored file once it is sent. */
export function PictureThumb({
  file,
  src,
  status,
  error,
  onRemove,
}: {
  file: Pick<FileBrief, "name" | "size"> & Partial<FileBrief>;
  src?: string;
  status: FileStatus | "uploading";
  error?: string | null;
  onRemove?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const url = src ?? (file.id ? api.fileUrl(file.id, true) : undefined);
  const uploading = status === "uploading";
  // Only a failed upload matters; a picture whose text couldn't be read is still seen.
  const failed = !!error && !file.id;
  const canOpen = !!file.id && !!file.content_type && !onRemove;
  const img = url ? (
    <img src={url} alt={file.name} className="size-full object-cover" loading="lazy" />
  ) : (
    <span className="flex size-full items-center justify-center text-slate-500">
      <FileImage size={20} />
    </span>
  );
  return (
    <div
      className={`group relative size-20 shrink-0 overflow-hidden rounded-lg border bg-panel ${
        failed ? "border-rose-500" : "border-edge"
      }`}
      title={failed ? (error ?? file.name) : file.name}
    >
      {canOpen ? (
        <button
          onClick={() => setOpen(true)}
          className="size-full"
          aria-label={`Preview ${file.name}`}
        >
          {img}
        </button>
      ) : (
        img
      )}
      {(uploading || failed) && (
        <span className="absolute inset-0 flex items-center justify-center bg-ink/60">
          {uploading ? (
            <LoaderCircle size={18} className="animate-spin text-sky-400" />
          ) : (
            <AlertCircle size={18} className="text-rose-400" />
          )}
        </span>
      )}
      {onRemove && (
        <button
          onClick={onRemove}
          className="absolute right-1 top-1 rounded-full bg-ink/80 p-0.5 text-slate-300 hover:text-white"
          aria-label={`Remove ${file.name}`}
        >
          <X size={12} />
        </button>
      )}
      {open && <FilePreview file={file as FileBrief} onClose={() => setOpen(false)} />}
    </div>
  );
}
