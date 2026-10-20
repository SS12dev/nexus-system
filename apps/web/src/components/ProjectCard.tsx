import { ExternalLink, FolderGit2, GitBranch, Globe } from "lucide-react";
import { Link } from "react-router";
import { formatDate } from "../lib/format";
import type { Project } from "../lib/types";

/** The stacks a software team builds with, as people say them. */
export const STACK_LABEL: Record<string, string> = {
  static: "Static web app (Vite)",
  web_fastapi: "Vite + FastAPI",
  node: "Vite + Express",
};

/** Where the project's latest work is on GitHub, in a few words. */
export function projectWhere(p: Project): string {
  if (p.transferred_to) return `moved to ${p.transferred_to}`;
  if (!p.repo_url) return "not on GitHub yet";
  return p.merged ? "on main" : `on branch ${p.branch ?? ""}`;
}

/** A software project in the Library: its repo, stack and latest commit, never its files. */
export function ProjectCard({ project: p }: { project: Project }) {
  const where = projectWhere(p);
  return (
    <div className="rounded-xl border border-edge bg-panel p-3">
      <div className="flex items-center gap-2">
        <FolderGit2 size={18} className="shrink-0 text-sky-300" />
        <span className="truncate font-medium">{p.name}</span>
        {p.stack && <span className="shrink-0 text-xs text-slate-500">{STACK_LABEL[p.stack] ?? p.stack}</span>}
        {p.repo_url && (
          <a
            href={p.repo_url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto flex shrink-0 items-center gap-1 text-sm text-sky-300 hover:text-sky-200"
          >
            Repo <ExternalLink size={13} />
          </a>
        )}
      </div>
      {p.last_commit && (
        <p className="mt-2 flex items-center gap-1.5 truncate text-xs text-slate-400" title={p.last_commit_message ?? ""}>
          <GitBranch size={12} className="shrink-0" />
          <code className="text-slate-300">{p.last_commit.slice(0, 7)}</code>
          <span className="truncate">{p.last_commit_message}</span>
          {p.last_commit_at && <span className="shrink-0 text-slate-500">· {formatDate(p.last_commit_at)}</span>}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
        <span>{where}</span>
        <span className="flex items-center gap-1">
          <Globe size={12} />
          {p.live_url ? (
            <a href={p.live_url} target="_blank" rel="noreferrer" className="text-sky-300 hover:text-sky-200">
              {p.live_url.replace(/^https?:\/\//, "")}
            </a>
          ) : (
            "Live link coming once deploys are switched on"
          )}
        </span>
        {p.chat_id && (
          <Link to={`/c/${p.chat_id}`} className="ml-auto text-sky-300 hover:text-sky-200">
            Open chat
          </Link>
        )}
      </div>
    </div>
  );
}
