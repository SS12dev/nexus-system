export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Accept list for the file pickers; matches what the doc-worker can read. */
export const ACCEPT = [
  ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".txt", ".md", ".json", ".html", ".htm",
  ".epub", ".ipynb", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".yaml",
  ".yml", ".toml", ".log", ".py", ".js", ".ts", ".tsx", ".sql", ".sh", ".xml", ".markdown",
].join(",");
