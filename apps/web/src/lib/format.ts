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

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "5 min ago", "3 h ago", "2 days ago". */
export function formatAgo(iso: string, now = Date.now()): string {
  const min = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/** A short name for a browser from its user agent, e.g. "Safari on iPhone". */
export function describeAgent(ua: string | null): string {
  if (!ua) return "Unknown browser";
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Firefox\//.test(ua)
      ? "Firefox"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : "Browser";
  const os = /iPhone/.test(ua)
    ? "iPhone"
    : /iPad/.test(ua)
      ? "iPad"
      : /Android/.test(ua)
        ? "Android"
        : /Mac OS X|Macintosh/.test(ua)
          ? "Mac"
          : /Windows/.test(ua)
            ? "Windows"
            : /Linux/.test(ua)
              ? "Linux"
              : "";
  return os ? `${browser} on ${os}` : browser;
}

/** Accept list for the file pickers: what the doc-worker can read, plus 3D models (kept as
 * they are for the viewer and for agents to open). */
export const ACCEPT = [
  ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".txt", ".md", ".json", ".html", ".htm",
  ".epub", ".ipynb", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".yaml",
  ".yml", ".toml", ".log", ".py", ".js", ".ts", ".tsx", ".sql", ".sh", ".xml", ".markdown",
  ".stl", ".step", ".stp", ".3mf", ".obj", ".glb", ".ply",
].join(",");

/** Pictures the assistant looks at itself (screenshots, photos), not only reads text from. */
export function isPicture(name: string): boolean {
  return ["png", "jpg", "jpeg", "webp"].includes(name.split(".").pop()?.toLowerCase() ?? "");
}

/** A name for a pasted screenshot, which the clipboard calls "image.png" every time. */
export function screenshotName(type: string, now = new Date()): string {
  const ext = type === "image/jpeg" ? "jpg" : (type.split("/")[1] ?? "png");
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `screenshot-${stamp}.${ext}`;
}
