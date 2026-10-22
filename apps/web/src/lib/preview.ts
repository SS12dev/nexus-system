/** How the file preview shows each kind of file. */
export type PreviewKind =
  | "image"
  | "pdf"
  | "docx"
  | "markdown"
  | "json"
  | "notebook"
  | "csv"
  | "html"
  | "code"
  | "text"
  /** No browser view of the file itself: show the text Nexus extracted from it. */
  | "extracted";

const CODE_LANGUAGES: Record<string, string> = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  ts: "typescript",
  tsx: "typescript",
  jsx: "javascript",
  sh: "bash",
  bash: "bash",
  sql: "sql",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  ini: "ini",
  xml: "xml",
  css: "css",
};

export const extension = (name: string) => name.split(".").pop()?.toLowerCase() ?? "";

export function previewKind(name: string, contentType = ""): PreviewKind {
  const ext = extension(name);
  if (["png", "jpg", "jpeg", "webp", "gif", "bmp"].includes(ext)) return "image";
  if (ext === "pdf" || contentType === "application/pdf") return "pdf";
  if (ext === "docx") return "docx";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "json") return "json";
  if (ext === "ipynb") return "notebook";
  if (ext === "csv") return "csv";
  if (ext === "html" || ext === "htm") return "html";
  if (ext in CODE_LANGUAGES) return "code";
  if (ext === "txt" || ext === "log" || contentType.startsWith("text/")) return "text";
  return "extracted";
}

export const codeLanguage = (name: string) => CODE_LANGUAGES[extension(name)] ?? "plaintext";

/** Kinds read as text in the browser (the others are binary or shown as extracted text). */
export const TEXT_KINDS: PreviewKind[] = ["markdown", "json", "notebook", "csv", "html", "code", "text"];

/** A small CSV parser: quoted fields, escaped quotes and newlines inside quotes. */
export function parseCsv(text: string, maxRows = 500): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      if (rows.length >= maxRows) return rows;
    } else field += c;
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

/** Pretty-prints JSON, or returns it unchanged when it doesn't parse. */
export function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export interface NotebookCell {
  kind: "markdown" | "code";
  source: string;
  outputs: string[];
}

/** The cells of a Jupyter notebook, with text outputs only. */
export function notebookCells(text: string): NotebookCell[] | null {
  try {
    const nb = JSON.parse(text) as { cells?: Record<string, unknown>[] };
    const join = (v: unknown) => (Array.isArray(v) ? v.join("") : typeof v === "string" ? v : "");
    return (nb.cells ?? []).map((c) => ({
      kind: c.cell_type === "markdown" ? "markdown" : "code",
      source: join(c.source),
      outputs: ((c.outputs as Record<string, unknown>[] | undefined) ?? [])
        .map((o) => join(o.text) || join((o.data as Record<string, unknown> | undefined)?.["text/plain"]))
        .filter(Boolean),
    }));
  } catch {
    return null;
  }
}

/** The text Nexus read from a converted file, ready for Markdown. Raw HTML never renders, so
 * the comments MarkItDown leaves would show as text; each slide's marker becomes a heading. */
export function readableText(text: string): string {
  return text
    .replace(/<!--\s*Slide number:\s*(\d+)\s*-->/gi, "\n\n**Slide $1**\n\n")
    .replace(/<!--[\s\S]*?-->/g, "");
}
