import {
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  Eye,
  FolderOpen,
  FolderPlus,
  Link2,
  Pencil,
  RotateCw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { FileIcon, StatusIcon } from "../components/FileChip";
import { FilePreview } from "../components/FilePreview";
import { LinkIcon, LinkPreview } from "../components/LinkChip";
import { ProjectCard } from "../components/ProjectCard";
import { api } from "../lib/api";
import { ACCEPT, formatDate, formatSize } from "../lib/format";
import { LINK_KIND_LABEL, linkSource, shortUrl } from "../lib/links";
import { canPreview } from "../lib/preview";
import { hasRole, type FileItem, type Folder, type LinkItem, type Project, type SearchHit } from "../lib/types";
import { Select } from "../components/Select";
import { useStore } from "../store/chat";
import { useAssistantName } from "../lib/assistant";
import { confirmAction } from "../components/Feedback";

/** Links shown before "Show all", so files stay near the top of the page. */
const LINKS_SHOWN = 5;

export function LibraryPage() {
  const assistant = useAssistantName();
  const user = useStore((s) => s.user)!;
  const live = useStore((s) => s.files);
  const upsertFile = useStore((s) => s.upsertFile);
  const [ids, setIds] = useState<string[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const foldersVersion = useStore((s) => s.foldersVersion);
  const projectsVersion = useStore((s) => s.projectsVersion);
  const projectDeletionErrors = useStore((s) => s.projectDeletionErrors);
  const [deletingProjects, setDeletingProjects] = useState<Set<string>>(() => new Set());
  const [params] = useSearchParams();
  const focus = params.get("folder");
  const [open, setOpen] = useState<Set<string>>(() => new Set(focus ? [focus] : []));
  const [links, setLinks] = useState<LinkItem[]>([]);
  const [linkHits, setLinkHits] = useState<LinkItem[]>([]);
  const [linkPreview, setLinkPreview] = useState<LinkItem | null>(null);
  const [adding, setAdding] = useState(false);
  const [allLinks, setAllLinks] = useState(false);
  const linksVersion = useStore((s) => s.linksVersion);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [preview, setPreview] = useState<FileItem | null>(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const canEdit = hasRole(user, "member");

  useEffect(() => {
    api
      .files()
      .then((list) => {
        list.forEach(upsertFile);
        setIds(list.map((f) => f.id));
      })
      .catch((e) => setError(e.message));
    api
      .projects()
      .then(setProjects)
      .catch(() => {});
  }, [upsertFile, projectsVersion]);

  // Links reload whenever one is added, changed or removed, here or by Nexus in a chat.
  useEffect(() => {
    api
      .links()
      .then(setLinks)
      .catch(() => {});
  }, [linksVersion]);

  // Folders reload when Nexus files something into a new one, or one is renamed or removed.
  useEffect(() => {
    api
      .folders()
      .then(setFolders)
      .catch(() => {});
  }, [foldersVersion]);

  // A link from the orb opens its project and scrolls to it.
  useEffect(() => {
    if (!focus) return;
    setOpen((o) => new Set(o).add(focus));
    const t = window.setTimeout(
      () => document.getElementById(`folder-${focus}`)?.scrollIntoView({ block: "start", behavior: "smooth" }),
      200,
    );
    return () => window.clearTimeout(t);
  }, [focus, folders.length]);

  const files = useMemo(() => ids.map((id) => live[id]).filter(Boolean), [ids, live]);
  // Files by project folder; files with no folder (uploads, mostly) are listed after them.
  const grouped = useMemo(() => {
    const by = new Map<string, FileItem[]>(folders.map((d) => [d.id, []]));
    const loose: FileItem[] = [];
    for (const f of files) {
      const list = f.folder_id ? by.get(f.folder_id) : undefined;
      if (list) list.push(f);
      else loose.push(f);
    }
    return { by, loose };
  }, [files, folders]);

  const run = async (fn: () => Promise<void>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const upload = (list: FileList | File[]) =>
    Array.from(list).forEach((file) =>
      run(async () => {
        const f = await api.uploadFile(file);
        upsertFile(f);
        setIds((xs) => [f.id, ...xs]);
      }),
    );

  const search = (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return setHits(null);
    run(async () => {
      const [found, foundLinks] = await Promise.all([
        api.searchFiles(query.trim()),
        api.links(query.trim()).catch(() => []),
      ]);
      setLinkHits(foundLinks);
      setHits(found);
    });
  };

  const rename = (f: FileItem) => {
    const name = prompt("Rename file", f.name);
    if (name && name !== f.name) run(async () => upsertFile(await api.renameFile(f.id, name)));
  };

  const move = (f: FileItem, to: string) =>
    run(async () => {
      let folderId: string | null = to || null;
      if (to === "new") {
        const name = prompt("New project's name", "")?.trim();
        if (!name) return;
        const made = await api.addFolder(name);
        setFolders((xs) => [made, ...xs]);
        folderId = made.id;
      }
      const moved = await api.moveFile(f.id, folderId);
      upsertFile(moved);
      // Its other versions moved with it.
      for (const other of Object.values(useStore.getState().files))
        if ((other.version_of_id ?? other.id) === (f.version_of_id ?? f.id) && other.id !== f.id)
          upsertFile({ ...other, folder_id: folderId });
      if (folderId) setOpen((o) => new Set(o).add(folderId!));
    });

  const renameFolder = (d: Folder) => {
    const name = prompt("Rename project", d.name)?.trim();
    if (name && name !== d.name)
      run(async () => {
        const saved = await api.renameFolder(d.id, name);
        setFolders((xs) => xs.map((x) => (x.id === saved.id ? saved : x)));
      });
  };

  const removeFolder = async (d: Folder) => {
    const folderFiles = Object.values(useStore.getState().files).filter((file) => file.folder_id === d.id);
    const ok = await confirmAction({
      title: `Permanently delete ${d.name} and its files?`,
      body: "This permanently deletes the folder and all files stored in it, including their stored contents. This does not delete a software project or its GitHub repository.",
      confirmLabel: "Delete folder and files",
      danger: true,
    });
    if (!ok) return;
    run(async () => {
      await api.deleteFolder(d.id);
      setFolders((xs) => xs.filter((x) => x.id !== d.id));
      setIds((xs) => xs.filter((id) => !folderFiles.some((file) => file.id === id)));
      for (const file of folderFiles)
        useStore.getState().handleEvent({ type: "file.deleted", data: { id: file.id } });
      useStore.getState().handleEvent({ type: "folder.deleted", data: { id: d.id } });
    });
  };

  const removeProject = async (p: Project) => {
    const remoteRepoMessage = p.transferred_to
      ? ` Its GitHub repository was transferred to ${p.transferred_to}, so Nexus cannot delete that repository.`
      : p.repo_url
        ? " Its managed GitHub repository will also be permanently deleted."
        : "";
    const ok = await confirmAction({
      title: `Permanently delete ${p.name}?`,
      body: `This permanently deletes the software project's workbench files, project record, associated Library files and any running app.${remoteRepoMessage} This cannot be undone.`,
      confirmLabel: "Delete project permanently",
      danger: true,
    });
    if (!ok) return;
    setError("");
    try {
      await api.deleteProject(p.id);
      setDeletingProjects((current) => new Set(current).add(p.id));
      useStore.getState().handleEvent({ type: "project.delete_requested", data: { id: p.id } });
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const toggle = (id: string) =>
    setOpen((o) => {
      const next = new Set(o);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const remove = async (f: FileItem) => {
    const ok = await confirmAction({
      title: `Delete ${f.name}?`,
      body: `${assistant} will no longer be able to read it.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    run(async () => {
      await api.deleteFile(f.id);
      setIds((xs) => xs.filter((x) => x !== f.id));
      useStore.getState().handleEvent({ type: "file.deleted", data: { id: f.id } });
    });
  };

  const editLink = (l: LinkItem) => {
    const title = prompt("Title", l.title);
    if (title === null) return;
    const note = prompt("What it is, in a sentence (optional)", l.note ?? "");
    if (note === null) return;
    run(async () => {
      const saved = await api.editLink(l.id, { title: title.trim() || l.title, note });
      setLinks((xs) => xs.map((x) => (x.id === saved.id ? saved : x)));
    });
  };

  const removeLink = async (l: LinkItem) => {
    const ok = await confirmAction({
      title: `Remove the link to ${l.title}?`,
      body: "What it points to stays where it is.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (!ok) return;
    run(async () => {
      await api.deleteLink(l.id);
      setLinks((xs) => xs.filter((x) => x.id !== l.id));
    });
  };

  const fileRow = (f: FileItem) => (
    <div key={f.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <FileIcon name={f.name} size={20} />
                <div className="min-w-40 flex-1">
                  <div className="flex items-center gap-2">
                    {canPreview(f) ? (
                      <button onClick={() => setPreview(f)} className="truncate text-left hover:text-sky-300">
                        {f.name}
                      </button>
                    ) : (
                      <span className="truncate">{f.name}</span>
                    )}
                    <StatusIcon status={f.status} />
                  </div>
                  <div className={`text-xs ${f.status === "failed" ? "text-rose-400" : "text-slate-500"}`}>
                    {f.status === "failed"
                      ? f.error
                      : f.status === "processing"
                        ? `${assistant} is reading it…`
                        : `${(f.version ?? 1) > 1 ? `v${f.version} · ` : ""}${formatSize(f.size)} · ${formatDate(f.created_at)}`}
                    {f.chat_id && f.status === "ready" && (
                      <>
                        {" · "}
                        <Link to={`/c/${f.chat_id}`} className="hover:text-sky-300">
                          from a chat
                        </Link>
                      </>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 text-slate-400">
                  {canPreview(f) && (
                    <IconButton label="Preview" onClick={() => setPreview(f)}>
                      <Eye size={16} />
                    </IconButton>
                  )}
                  <a href={api.fileUrl(f.id)} className="p-2 hover:text-white" aria-label="Download" title="Download">
                    <Download size={16} />
                  </a>
                  {canEdit && f.status === "failed" && (
                    <IconButton
                      label="Try again"
                      onClick={() => run(async () => upsertFile(await api.retryFile(f.id)))}
                    >
                      <RotateCw size={16} />
                    </IconButton>
                  )}
                  {canEdit && (
                    <>
                      <Select
                        size="xs"
                        value={f.folder_id ?? ""}
                        onChange={(e) => move(f, e.target.value)}
                        aria-label={`Project for ${f.name}`}
                        title="Move to a project"
                        className="mr-1 max-w-36"
                      >
                        <option value="">No project</option>
                        {folders.map((d) => (
                          <option key={d.id} value={d.id}>
                            {d.name}
                          </option>
                        ))}
                        <option value="new">New project…</option>
                      </Select>
                      <IconButton label="Rename" onClick={() => rename(f)}>
                        <Pencil size={16} />
                      </IconButton>
                      <IconButton label="Delete" onClick={() => remove(f)} danger>
                        <Trash2 size={16} />
                      </IconButton>
                    </>
                  )}
                </div>
              </div>
  );

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (canEdit && e.dataTransfer.files.length) upload(e.dataTransfer.files);
  };

  return (
    <div
      className="flex-1 overflow-y-auto"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="mx-auto max-w-4xl px-4 py-6">
        <div className="mb-1 flex items-center gap-3">
          <h1 className="text-xl font-semibold">Library</h1>
          {canEdit && (
            <div className="ml-auto flex items-center gap-2">
              <button
                onClick={() => setAdding((v) => !v)}
                aria-expanded={adding}
                className="flex items-center gap-2 rounded-md border border-edge px-3 py-1.5 text-sm hover:bg-edge/60"
              >
                <Link2 size={16} /> Add link
              </button>
              <button
                onClick={() => picker.current?.click()}
                className="flex items-center gap-2 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink"
              >
                <Upload size={16} /> Upload
              </button>
            </div>
          )}
          <input
            ref={picker}
            type="file"
            multiple
            hidden
            accept={ACCEPT}
            onChange={(e) => {
              if (e.target.files) upload(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
        <p className="mb-5 text-sm text-slate-400">
          Every file you upload, here or in a chat, plus links to things that live elsewhere. {assistant}{" "}
          reads them, so you can ask about them in any chat. Only you can see your library.
        </p>

        {adding && canEdit && (
          <AddLinkForm
            onCancel={() => setAdding(false)}
            onAdded={(l) => {
              setLinks((xs) => [l, ...xs.filter((x) => x.id !== l.id)]);
              setAdding(false);
            }}
          />
        )}

        {projects.length > 0 && (
          <section className="mb-6">
            <h2 className="mb-2 text-sm font-medium text-slate-300">Projects</h2>
            <p className="mb-3 text-xs text-slate-500">
              Software {assistant}'s teams built. The code lives in each project's GitHub repo, not here.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              {projects.map((p) => (
                <ProjectCard
                  key={p.id}
                  project={p}
                  onDelete={canEdit ? removeProject : undefined}
                  deleting={deletingProjects.has(p.id) && !projectDeletionErrors[p.id]}
                  deleteError={projectDeletionErrors[p.id]}
                />
              ))}
            </div>
          </section>
        )}

        {links.length > 0 && (
          <section className="mb-6">
            <h2 className="mb-2 text-sm font-medium text-slate-300">Links</h2>
            <p className="mb-3 text-xs text-slate-500">
              Pointers to things kept elsewhere: GitHub repos, files and pull requests, Google Docs, live apps and
              pages. {assistant} saves them as it works (a team's repo and app arrive here on their own); the library
              keeps the address and a preview, never a copy. Saving the same address again updates it.
            </p>
            <LinkList
              links={allLinks ? links : links.slice(0, LINKS_SHOWN)}
              canEdit={canEdit}
              onPreview={setLinkPreview}
              onEdit={editLink}
              onRemove={removeLink}
            />
            {links.length > LINKS_SHOWN && (
              <button onClick={() => setAllLinks((v) => !v)} className="text-sm text-sky-300 hover:text-sky-200">
                {allLinks ? "Show fewer" : `Show all ${links.length} links`}
              </button>
            )}
          </section>
        )}

        <div className="mb-2 flex items-center gap-2">
          <h2 className="text-sm font-medium text-slate-300">Files</h2>
          {canEdit && (
            <button
              onClick={() =>
                run(async () => {
                  const name = prompt("New project's name", "")?.trim();
                  if (!name) return;
                  const made = await api.addFolder(name);
                  setFolders((xs) => [made, ...xs]);
                  setOpen((o) => new Set(o).add(made.id));
                })
              }
              className="ml-auto flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-edge/60 hover:text-white"
            >
              <FolderPlus size={14} /> New project
            </button>
          )}
        </div>
        <p className="mb-3 text-xs text-slate-500">
          What {assistant} and its teams make is kept by project: a rocket model's .3mf, .step and .stl sit together in
          one, and a later fix or new version joins the same project. Pick a file's project to move it (its other
          versions go with it); your uploads stay outside projects unless you move them in. Uploading the same file
          twice keeps two files, as each may belong to a different piece of work. When {assistant} saves a file under a
          name it already used, it decides whether that is a new version of it or a separate file.
        </p>

        <form onSubmit={search} className="mb-5 flex gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-edge bg-panel px-3">
            <Search size={16} className="text-slate-500" />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                if (!e.target.value) setHits(null);
              }}
              placeholder="Search inside your documents and links"
              className="min-w-0 flex-1 bg-transparent py-2 outline-none"
            />
          </div>
          <button className="shrink-0 rounded-md border border-edge px-4 py-2 text-sm hover:bg-edge/60">
            Search
          </button>
        </form>

        {error && <p className="mb-4 text-sm text-rose-400">{error}</p>}

        {dragging && canEdit && (
          <div className="mb-4 rounded-lg border-2 border-dashed border-sky-400 p-6 text-center text-sky-300">
            Drop files to upload
          </div>
        )}

        {hits ? (
          <div className="space-y-2">
            {hits.length === 0 && linkHits.length === 0 && <p className="text-slate-400">Nothing found.</p>}
            {linkHits.length > 0 && (
              <LinkList
                links={linkHits}
                canEdit={false}
                onPreview={setLinkPreview}
                onEdit={editLink}
                onRemove={removeLink}
              />
            )}
            {hits.map((h, i) => (
              <button
                key={i}
                onClick={() => live[h.file.id] && setPreview(live[h.file.id])}
                className="block w-full rounded-lg border border-edge bg-panel px-4 py-3 text-left hover:border-sky-700"
              >
                <div className="mb-1 flex items-center gap-2 text-sm text-sky-300">
                  <FileIcon name={h.file.name} size={14} /> {h.file.name}
                  {h.heading && <span className="truncate text-slate-500">· {h.heading}</span>}
                </div>
                <p className="line-clamp-3 text-sm text-slate-300">{h.snippet}</p>
              </button>
            ))}
          </div>
        ) : files.length === 0 ? (
          <p className="text-slate-400">No files yet. Upload one, or attach one in a chat.</p>
        ) : (
          <div className="divide-y divide-edge rounded-lg border border-edge bg-panel">
            {folders.map((d) => {
              const list = grouped.by.get(d.id) ?? [];
              const isOpen = open.has(d.id);
              return (
                <div key={d.id} id={`folder-${d.id}`} className={focus === d.id ? "bg-sky-500/5" : ""}>
                  <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <button
                      onClick={() => toggle(d.id)}
                      aria-expanded={isOpen}
                      className="flex min-w-40 flex-1 items-center gap-3 text-left"
                    >
                      {isOpen ? <ChevronDown size={16} className="text-slate-500" /> : <ChevronRight size={16} className="text-slate-500" />}
                      <FolderOpen size={20} className="shrink-0 text-purple-300" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{d.name}</span>
                        <span className="block truncate text-xs text-slate-500">
                          {list.length} file{list.length === 1 ? "" : "s"} · {formatDate(d.updated_at)}
                          {!isOpen && list.length > 0 && ` · ${list.slice(0, 3).map((f) => f.name).join(", ")}`}
                        </span>
                      </span>
                    </button>
                    {canEdit && (
                      <div className="flex items-center gap-1 text-slate-400">
                        <IconButton label="Rename project" onClick={() => renameFolder(d)}>
                          <Pencil size={16} />
                        </IconButton>
                        <IconButton label="Remove project (keeps its files)" onClick={() => removeFolder(d)} danger>
                          <Trash2 size={16} />
                        </IconButton>
                      </div>
                    )}
                  </div>
                  {isOpen && (
                    <div className="divide-y divide-edge border-t border-edge bg-ink/40 pl-6">
                      {list.length === 0 && <p className="px-4 py-3 text-sm text-slate-500">No files in it yet.</p>}
                      {list.map(fileRow)}
                    </div>
                  )}
                </div>
              );
            })}
            {grouped.loose.length > 0 && folders.length > 0 && (
              <p className="bg-ink/40 px-4 py-1.5 text-xs font-medium uppercase tracking-wider text-slate-500">
                Not in a project
              </p>
            )}
            {grouped.loose.map(fileRow)}
          </div>
        )}
      </div>
      {preview && <FilePreview file={preview} onClose={() => setPreview(null)} />}
      {linkPreview && <LinkPreview link={linkPreview} onClose={() => setLinkPreview(null)} />}
    </div>
  );
}

function AddLinkForm({ onAdded, onCancel }: { onAdded: (l: LinkItem) => void; onCancel: () => void }) {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setError("");
    try {
      onAdded(await api.addLink(url.trim(), title.trim(), note.trim()));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const field = "w-full rounded-md border border-edge bg-ink px-3 py-2 text-sm outline-none focus:border-sky-600";
  return (
    <form onSubmit={submit} className="mb-6 space-y-2 rounded-lg border border-edge bg-panel p-4">
      <p className="text-sm text-slate-300">
        Add a link to a GitHub repo, file or pull request, a Google Doc, an app or any page. GitHub links get a
        preview.
      </p>
      <input
        autoFocus
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://github.com/you/project"
        aria-label="Address"
        className={field}
      />
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title (optional)"
          aria-label="Title"
          className={field}
        />
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What it is, in a sentence (optional)"
          aria-label="Note"
          className={field}
        />
      </div>
      {error && <p className="text-sm text-rose-400">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded-md px-3 py-1.5 text-sm text-slate-400 hover:text-white">
          Cancel
        </button>
        <button
          disabled={busy || !url.trim()}
          className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink disabled:opacity-50"
        >
          Add link
        </button>
      </div>
    </form>
  );
}

function LinkList({
  links,
  canEdit,
  onPreview,
  onEdit,
  onRemove,
}: {
  links: LinkItem[];
  canEdit: boolean;
  onPreview: (l: LinkItem) => void;
  onEdit: (l: LinkItem) => void;
  onRemove: (l: LinkItem) => void;
}) {
  const assistant = useAssistantName();
  return (
    <div className="mb-4 divide-y divide-edge rounded-lg border border-edge bg-panel">
      {links.map((l) => (
        <div key={l.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
          <span className="text-sky-300">
            <LinkIcon kind={l.kind} size={20} />
          </span>
          <div className="min-w-40 flex-1">
            <button onClick={() => onPreview(l)} className="block max-w-full truncate text-left hover:text-sky-300">
              {l.title}
            </button>
            {l.note && <p className="line-clamp-2 text-sm text-slate-400">{l.note}</p>}
            <div className="text-xs text-slate-500">
              {LINK_KIND_LABEL[l.kind] ?? "Link"} · <span title={l.url}>{shortUrl(l.url, 48)}</span> ·{" "}
              {linkSource(l, assistant)} · {formatDate(l.updated_at)}
              {l.chat_id && (
                <>
                  {" · "}
                  <Link to={`/c/${l.chat_id}`} className="hover:text-sky-300">
                    from a chat
                  </Link>
                </>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1 text-slate-400">
            <IconButton label="Preview" onClick={() => onPreview(l)}>
              <Eye size={16} />
            </IconButton>
            <a
              href={l.url}
              target="_blank"
              rel="noreferrer"
              className="p-2 hover:text-white"
              aria-label="Open"
              title="Open"
            >
              <ExternalLink size={16} />
            </a>
            {canEdit && (
              <>
                <IconButton label="Edit" onClick={() => onEdit(l)}>
                  <Pencil size={16} />
                </IconButton>
                <IconButton label="Remove link" onClick={() => onRemove(l)} danger>
                  <Trash2 size={16} />
                </IconButton>
              </>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function IconButton({
  label,
  onClick,
  danger,
  children,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`p-2 ${danger ? "hover:text-rose-400" : "hover:text-white"}`}
    >
      {children}
    </button>
  );
}
