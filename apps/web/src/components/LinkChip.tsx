import {
  AppWindow,
  CircleDot,
  ExternalLink,
  FileCode,
  FileText,
  Folder,
  FolderGit2,
  GitCommitHorizontal,
  GitPullRequest,
  Globe,
  HardDrive,
  Lock,
  PenTool,
  Presentation,
  RefreshCw,
  Sheet,
  Star,
  Video,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { formatDate } from "../lib/format";
import { changeSummary, isGitHub, LINK_KIND_LABEL, linkPath, linkSource, shortUrl, stripComments } from "../lib/links";
import { codeLanguage, previewKind } from "../lib/preview";
import type { LinkItem, LinkKind } from "../lib/types";
import { useStore } from "../store/chat";
import { Code } from "./FilePreview";
import { Markdown } from "./Markdown";

export function LinkIcon({ kind, size = 16 }: { kind: LinkKind; size?: number }) {
  const Icon = {
    github_repo: FolderGit2,
    github_file: FileCode,
    github_folder: Folder,
    github_pr: GitPullRequest,
    github_issue: CircleDot,
    github_commit: GitCommitHorizontal,
    google_doc: FileText,
    google_sheet: Sheet,
    google_slides: Presentation,
    google_drive: HardDrive,
    app: AppWindow,
    video: Video,
    design: PenTool,
    web: Globe,
  }[kind] ?? Globe;
  return <Icon size={size} className="shrink-0" />;
}

/** A link on a message: opens the preview, with a button straight to the real thing. */
export function LinkChip({ link }: { link: LinkItem }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="flex max-w-full items-center gap-2 rounded-lg border border-edge bg-panel px-2.5 py-1.5 text-sm"
      title={link.url}
    >
      <button
        onClick={() => setOpen(true)}
        className="flex min-w-0 items-center gap-2 text-left hover:text-sky-300"
        title={`Preview ${link.title}`}
      >
        <LinkIcon kind={link.kind} />
        <span className="min-w-0">
          <span className="block max-w-56 truncate">{link.title}</span>
          <span className="block text-xs text-slate-500">{LINK_KIND_LABEL[link.kind] ?? "Link"}</span>
        </span>
      </button>
      <a
        href={link.url}
        target="_blank"
        rel="noreferrer"
        className="ml-1 text-slate-500 hover:text-white"
        aria-label={`Open ${link.title}`}
        title="Open"
      >
        <ExternalLink size={14} />
      </a>
      {open && <LinkPreview link={link} onClose={() => setOpen(false)} />}
    </div>
  );
}

/** A side panel for a link, like a file's preview: what the library knows about it (a repo's
 * README, a file's code, a pull request's state), and a button to open the real thing. */
export function LinkPreview({ link: initial, onClose }: { link: LinkItem; onClose: () => void }) {
  const assistant = useAssistantName();
  const version = useStore((s) => s.linksVersion);
  const user = useStore((s) => s.user);
  const [link, setLink] = useState(initial);
  const [refreshing, setRefreshing] = useState(false);
  const canEdit = user?.role !== "viewer";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // A refreshed preview arrives as a link event; pick it up.
  useEffect(() => {
    if (!version) return;
    api
      .links()
      .then((all) => {
        const now = all.find((l) => l.id === initial.id);
        if (now) {
          setLink(now);
          if (now.preview_at !== link.preview_at) setRefreshing(false);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, initial.id]);

  const refresh = () => {
    setRefreshing(true);
    api.refreshLink(link.id).catch(() => setRefreshing(false));
    // Stop the spinner even if GitHub never answers.
    setTimeout(() => setRefreshing(false), 15000);
  };

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60" onClick={onClose}>
      <div
        role="dialog"
        aria-label={link.title}
        className="flex h-full w-full max-w-4xl flex-col border-l border-edge bg-ink"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-edge px-4 py-3">
          <LinkIcon kind={link.kind} />
          <div className="min-w-0 flex-1">
            <div className="truncate font-medium">{link.title}</div>
            <div className="truncate text-xs text-slate-500">
              {LINK_KIND_LABEL[link.kind] ?? "Link"} · {shortUrl(link.url)}
            </div>
          </div>
          {isGitHub(link.kind) && canEdit && (
            <button
              onClick={refresh}
              disabled={refreshing}
              className="hidden items-center gap-1 rounded-md border border-edge px-2.5 py-1 text-sm hover:bg-edge/60 disabled:opacity-60 sm:flex"
              title="Fetch the preview again from GitHub"
            >
              <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} /> Refresh
            </button>
          )}
          <a
            href={link.url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 rounded-md bg-sky-600 px-2.5 py-1 text-sm hover:bg-sky-500"
          >
            <ExternalLink size={14} /> Open
          </a>
          <button onClick={onClose} aria-label="Close" className="text-slate-400 hover:text-white">
            <X size={18} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="border-b border-edge px-5 py-3 text-sm text-slate-400">
            {link.note && <p className="mb-1 text-slate-200">{link.note}</p>}
            <p className="text-xs text-slate-500">
              A link, not a copy: the library keeps the address{isGitHub(link.kind) ? " and this preview" : ""}, and
              the real thing stays where it is.
              <br />
              {linkSource(link, assistant).replace(/^./, (c) => c.toUpperCase())} · {formatDate(link.created_at)}
              {link.preview_at && ` · preview from ${formatDate(link.preview_at)}`}
            </p>
          </div>
          <PreviewBody link={link} />
        </div>
      </div>
    </div>
  );
}

const Note = ({ children, error }: { children: ReactNode; error?: boolean }) => (
  <p className={`p-5 text-sm ${error ? "text-rose-400" : "text-slate-500"}`}>{children}</p>
);

const Fact = ({ children }: { children: ReactNode }) => (
  <span className="flex items-center gap-1 rounded-md bg-edge/60 px-2 py-0.5 text-xs text-slate-300">{children}</span>
);

function PreviewBody({ link }: { link: LinkItem }) {
  const assistant = useAssistantName();
  const p = link.preview;
  if (!isGitHub(link.kind)) {
    return (
      <Note>
        {link.kind === "app"
          ? `An app ${assistant}'s team built and deployed. Open it to use it; the Apps page starts, stops and shares it.`
          : `${LINK_KIND_LABEL[link.kind] ?? "This page"} opens in a new tab. ${assistant} keeps the link so you, and it, can find it again.`}
      </Note>
    );
  }
  if (!p) return <Note>Fetching the preview from GitHub…</Note>;
  if (p.error) return <Note error>{p.error}</Note>;
  const facts = (
    <div className="flex flex-wrap gap-1.5 px-5 pt-4">
      {p.private && (
        <Fact>
          <Lock size={11} /> private
        </Fact>
      )}
      {p.state && <Fact>{p.draft ? "draft" : p.state}</Fact>}
      {p.default_branch && <Fact>{p.default_branch}</Fact>}
      {p.head && p.base && (
        <Fact>
          {p.head} → {p.base}
        </Fact>
      )}
      {p.language && <Fact>{p.language}</Fact>}
      {p.stars != null && p.stars > 0 && (
        <Fact>
          <Star size={11} /> {p.stars}
        </Fact>
      )}
      {p.author && <Fact>by {p.author}</Fact>}
      {changeSummary(p) && <Fact>{changeSummary(p)}</Fact>}
      {p.labels?.map((l) => <Fact key={l}>{l}</Fact>)}
      {(p.pushed_at || p.date) && <Fact>updated {formatDate((p.pushed_at || p.date)!)}</Fact>}
    </div>
  );
  if (link.kind === "github_repo") {
    return (
      <>
        {facts}
        {p.description && <p className="px-5 pt-3 text-slate-300">{p.description}</p>}
        {p.readme ? (
          <div className="p-5">
            <Markdown>{stripComments(p.readme)}</Markdown>
            {p.readme_truncated && <Note>The README is longer; open the repo for the rest.</Note>}
          </div>
        ) : (
          <Note>This repo has no README yet.</Note>
        )}
      </>
    );
  }
  if (link.kind === "github_folder" || p.entries) {
    return (
      <div className="p-5">
        <ul className="divide-y divide-edge rounded-lg border border-edge bg-panel text-sm">
          {(p.entries ?? []).map((e) => (
            <li key={e.name} className="flex items-center gap-2 px-3 py-2">
              {e.type === "dir" ? <Folder size={14} className="text-sky-300" /> : <FileCode size={14} />}
              <span className="truncate">{e.name}</span>
            </li>
          ))}
        </ul>
        {(p.entries_total ?? 0) > (p.entries?.length ?? 0) && (
          <Note>Showing {p.entries?.length} of {p.entries_total}; open it on GitHub for the rest.</Note>
        )}
      </div>
    );
  }
  if (link.kind === "github_file") {
    if (p.text == null) return <Note>{p.note ?? "No preview for this file; open it on GitHub."}</Note>;
    const path = linkPath(link.url) || p.name || "";
    return (
      <>
        {previewKind(path, "") === "markdown" ? (
          <div className="p-5">
            <Markdown>{stripComments(p.text)}</Markdown>
          </div>
        ) : (
          <Code text={p.text} language={codeLanguage(path)} />
        )}
        {p.truncated && <Note>The file is longer; open it on GitHub for the rest.</Note>}
      </>
    );
  }
  if (link.kind === "github_commit") {
    return (
      <>
        {facts}
        <pre className="px-5 pt-3 text-sm whitespace-pre-wrap text-slate-200">{p.message}</pre>
        {!!p.files?.length && (
          <ul className="m-5 divide-y divide-edge rounded-lg border border-edge bg-panel text-sm">
            {p.files.map((f) => (
              <li key={f.name} className="flex items-center gap-2 px-3 py-2">
                <span className="truncate">{f.name}</span>
                <span className="ml-auto shrink-0 text-xs text-slate-500">
                  {f.status} · <span className="text-emerald-400">+{f.additions}</span>{" "}
                  <span className="text-rose-400">−{f.deletions}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </>
    );
  }
  // Pull requests and issues.
  return (
    <>
      {facts}
      {p.title && <h2 className="px-5 pt-3 text-lg font-medium">{p.title}</h2>}
      <div className="p-5">
        {p.body ? <Markdown>{stripComments(p.body)}</Markdown> : <p className="text-sm text-slate-500">No description.</p>}
        {p.body_truncated && <Note>The description is longer; open it on GitHub for the rest.</Note>}
      </div>
    </>
  );
}
