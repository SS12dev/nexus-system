import type { LinkItem, LinkKind } from "./types";

/** What each kind of link is, as people say it. */
export const LINK_KIND_LABEL: Record<LinkKind, string> = {
  github_repo: "GitHub repo",
  github_file: "GitHub file",
  github_folder: "GitHub folder",
  github_pr: "Pull request",
  github_issue: "GitHub issue",
  github_commit: "Commit",
  google_doc: "Google Doc",
  google_sheet: "Google Sheet",
  google_slides: "Google Slides",
  google_drive: "Google Drive",
  app: "Live app",
  video: "Video",
  design: "Design",
  web: "Web page",
};

export const isGitHub = (kind: LinkKind) => kind.startsWith("github_");

/** The address without its scheme, for showing under a title. */
export function shortUrl(url: string, max = 60): string {
  const bare = url.replace(/^https?:\/\//, "").replace(/\/$/, "");
  return bare.length > max ? `${bare.slice(0, max - 1)}…` : bare;
}

/** Who put the link in the library, in a few words. */
export function linkSource(link: Pick<LinkItem, "source">, assistant: string): string {
  if (link.source === "team") return `saved by ${assistant}'s team`;
  if (link.source === "assistant") return `saved by ${assistant}`;
  return "added by you";
}

/** The GitHub file path a file or folder link points to, for choosing a code language. */
export function linkPath(url: string): string {
  const m = url.match(/github\.com\/[^/]+\/[^/]+\/(?:blob|tree)\/[^/]+\/(.+)$/);
  return m ? decodeURIComponent(m[1].split(/[?#]/)[0]) : "";
}

/** GitHub hides HTML comments in READMEs and descriptions; Markdown here would show them. */
export const stripComments = (md: string) => md.replace(/<!--[\s\S]*?-->/g, "").trim();

/** A short status line for a pull request, issue or commit preview. */
export function changeSummary(p: { additions?: number | null; deletions?: number | null; changed_files?: number | null }) {
  const parts: string[] = [];
  if (p.changed_files != null) parts.push(`${p.changed_files} file${p.changed_files === 1 ? "" : "s"}`);
  if (p.additions != null) parts.push(`+${p.additions}`);
  if (p.deletions != null) parts.push(`−${p.deletions}`);
  return parts.join(" · ");
}
