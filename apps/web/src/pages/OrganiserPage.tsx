import {
  Archive,
  CalendarCheck2,
  CheckSquare,
  ChevronLeft,
  ChevronRight,
  Download,
  FolderKanban,
  Inbox,
  Loader2,
  NotebookPen,
  Plus,
  RotateCcw,
  Search,
  Sun,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router";
import { toast } from "../components/Feedback";
import { Guide, GuideHeading } from "../components/Guide";
import { ItemEditor } from "../components/organiser/Editor";
import { InboxCard, useContainers } from "../components/organiser/Inbox";
import { CaptureBar, Empty, ItemCard, Section, TaskRow, useOrgOverview } from "../components/organiser/parts";
import { ReviewFlow } from "../components/organiser/Review";
import { SHORTCUT } from "../components/CommandBar";
import { Select } from "../components/Select";
import { api } from "../lib/api";
import { formatAgo } from "../lib/format";
import { addDays, groupByDue, shortDay, STATUS_LABEL, TYPE_LABEL, weekday } from "../lib/organiser";
import type { OrgItem, OrgOverview, OrgToday, OrgType } from "../lib/types";
import { useStore } from "../store/chat";

/** Today's date for the person (from the server, in their time zone), with a device fallback. */
function useToday(overview: OrgOverview | null): string {
  return overview?.today ?? new Date().toLocaleDateString("en-CA");
}

/** The life organiser: Today, Inbox, Tasks, Projects and areas, Notes, Archive, the weekly
 * review, and one page per item. */
export function OrganiserPage() {
  const overview = useOrgOverview();
  const today = useToday(overview);
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 py-5 sm:py-6">
        <Header overview={overview} />
        <Routes>
          <Route index element={<TodayView today={today} />} />
          <Route path="inbox" element={<InboxView today={today} />} />
          <Route path="tasks" element={<TasksView today={today} />} />
          <Route path="projects" element={<ProjectsView />} />
          <Route path="notes" element={<NotesView />} />
          <Route path="archive" element={<ArchiveView />} />
          <Route path="review" element={<ReviewFlow today={today} />} />
          <Route path="item/:id" element={<ItemView today={today} />} />
          <Route path="daily/:day" element={<DailyView today={today} />} />
          <Route path="new" element={<NewView />} />
          <Route path="*" element={<Navigate to="/organiser" replace />} />
        </Routes>
      </div>
    </div>
  );
}

const TABS: { to: string; label: string; icon: typeof Sun; count?: (o: OrgOverview) => number | null; tone?: string }[] = [
  { to: "/organiser", label: "Today", icon: Sun, count: (o) => o.overdue + o.due_today || null },
  { to: "/organiser/inbox", label: "Inbox", icon: Inbox, count: (o) => o.inbox || null, tone: "bg-sky-500/20 text-sky-200" },
  { to: "/organiser/tasks", label: "Tasks", icon: CheckSquare, count: (o) => o.open_tasks || null },
  { to: "/organiser/projects", label: "Projects and areas", icon: FolderKanban },
  { to: "/organiser/notes", label: "Notes", icon: NotebookPen },
  { to: "/organiser/review", label: "Weekly review", icon: CalendarCheck2 },
  { to: "/organiser/archive", label: "Archive", icon: Archive },
];

function Header({ overview }: { overview: OrgOverview | null }) {
  // One item or one day: straight to it, without the page's introduction.
  const focused = /^\/organiser\/(item|daily)\//.test(useLocation().pathname);
  return (
    <>
      <div className={`flex items-center gap-3 ${focused ? "mb-3" : "mb-1"}`}>
        <h1 className="text-xl font-semibold">Organiser</h1>
        <a
          href={api.orgExportUrl}
          download
          className="ml-auto flex items-center gap-1.5 rounded-md border border-edge px-2.5 py-1 text-sm text-slate-300 hover:bg-edge"
          title="Everything as Markdown files, ready to open as an Obsidian vault"
        >
          <Download size={14} /> <span className="hidden sm:inline">Export</span>
        </a>
      </div>
      {!focused && (
        <p className="mb-3 text-sm text-slate-400">
          Your notes, to-dos, projects and the parts of life you look after, in one place your
          assistant can read and help with.
        </p>
      )}
      {!focused && (
      <Guide title="How the Organiser works">
        <div>
          <GuideHeading>Four places for everything (PARA)</GuideHeading>
          <b className="text-slate-300">Projects</b> have a finish line (“Move flat”, “Renew passport”).{" "}
          <b className="text-slate-300">Areas</b> are parts of life you keep up with no end date
          (Health, Money, Home, Work). <b className="text-slate-300">Resources</b> are topics you
          collect notes on (Recipes, Robotics). Anything finished or parked goes to the{" "}
          <b className="text-slate-300">Archive</b>. Notes and tasks sit inside any of them.
        </div>
        <div>
          <GuideHeading>Capture first, sort later</GuideHeading>
          Anything you jot down lands in the Inbox: the box on Today, {SHORTCUT} then “Capture to
          Inbox”, <code>/in</code> on Telegram, or just telling your assistant. Start with{" "}
          <code>[ ]</code> to make it a to-do, add <code>#tags</code>, and end with a day (“tomorrow”,
          “friday”) to set a due date. Your assistant suggests where each one belongs; you accept with
          one tap.
        </div>
        <div>
          <GuideHeading>Today and the daily note</GuideHeading>
          Today shows what's overdue, what's due, what you're doing and your focus for the week, next to
          a fresh daily note for plans and a log. Checkboxes in any note can become real tasks with one
          button.
        </div>
        <div>
          <GuideHeading>The weekly review</GuideHeading>
          Ten minutes on Sunday evening: empty your head, clear the Inbox, sort overdue tasks, give every
          project a next step and choose three things to focus on. You get a reminder at 6 pm if it's
          not done.
        </div>
        <div>
          <GuideHeading>Links between notes</GuideHeading>
          Write <code>[[Title]]</code> to link to another item; it shows under “Linked from” on that
          item. Linking to something that doesn't exist yet offers to create it.
        </div>
        <div>
          <GuideHeading>Safe and yours</GuideHeading>
          Text saves as you type; if it changed on another device you choose which version to keep.
          Every item keeps its earlier versions, deleting goes to Trash for seven days, and Export gives
          you everything as Markdown files that open in Obsidian or any editor. A copy is also saved
          every night.
        </div>
      </Guide>
      )}
      <nav className="-mx-4 mb-5 flex gap-1 overflow-x-auto px-4 pb-1 [scrollbar-width:none]" aria-label="Organiser">
        {TABS.map((t) => {
          const n = overview && t.count ? t.count(overview) : null;
          const reviewDue = t.to.endsWith("review") && overview?.review.due;
          return (
            <NavLink
              key={t.to}
              to={t.to}
              end={t.to === "/organiser"}
              className={({ isActive }) =>
                `flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm ${
                  isActive ? "border-sky-500/60 bg-sky-500/15 text-sky-100" : "border-edge text-slate-300 hover:bg-edge/60"
                }`
              }
            >
              <t.icon size={14} />
              {t.label}
              {n != null && <span className={`rounded-full px-1.5 text-xs ${t.tone ?? "bg-edge text-slate-300"}`}>{n}</span>}
              {reviewDue && <span className="size-2 rounded-full bg-amber-400" title="This week's review is due" />}
            </NavLink>
          );
        })}
      </nav>
    </>
  );
}

function useOrgVersion() {
  return useStore((s) => s.organiserVersion);
}

// --- Today --------------------------------------------------------------------------------------

function TodayView({ today }: { today: string }) {
  const version = useOrgVersion();
  const [data, setData] = useState<OrgToday | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .orgToday()
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError((e as Error).message));
  }, [version]);
  const reload = () => api.orgToday().then(setData).catch(() => {});
  const containers = useContainers();
  const names = useMemo(() => new Map(containers.map((c) => [c.id, c.title])), [containers]);

  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <Spinner />;
  const nothing = !data.overdue.length && !data.due_today.length && !data.doing.length && !data.focus.length;
  const list = (items: OrgItem[]) => (
    <ul className="space-y-0.5">
      {items.map((t) => (
        <TaskRow key={t.id} task={t} today={data.day} parentTitle={t.parent_id ? names.get(t.parent_id) : null} onChanged={reload} />
      ))}
    </ul>
  );
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div className="min-w-0">
        <div className="mb-5">
          <CaptureBar onSaved={reload} />
        </div>
        <h2 className="mb-3 text-lg font-semibold">
          {weekday(data.day)} {shortDay(data.day, data.day)}
        </h2>
        {data.focus.length > 0 && <Section title="This week's focus">{list(data.focus)}</Section>}
        {data.overdue.length > 0 && (
          <Section title="Overdue" count={data.overdue.length}>
            {list(data.overdue)}
          </Section>
        )}
        {data.due_today.length > 0 && (
          <Section title="Due today" count={data.due_today.length}>
            {list(data.due_today)}
          </Section>
        )}
        {data.doing.length > 0 && <Section title="In progress">{list(data.doing)}</Section>}
        {nothing && (
          <Empty>
            Nothing due today. Add a to-do above (start with <code>[ ]</code>), or plan the week in the{" "}
            <Link to="/organiser/review" className="text-sky-300 hover:text-sky-200">
              weekly review
            </Link>
            .
          </Empty>
        )}
        {data.upcoming.length > 0 && <Section title="Coming up this week">{list(data.upcoming)}</Section>}
      </div>
      <div className="min-w-0">
        <div className="mb-2 flex items-center gap-2">
          <h2 className="text-sm font-medium text-slate-300">Daily note</h2>
          <Link to={`/organiser/daily/${addDays(data.day, -1)}`} className="ml-auto text-xs text-slate-400 hover:text-slate-200">
            Yesterday's
          </Link>
        </div>
        <ItemEditor id={data.daily.id} today={today} daily />
      </div>
    </div>
  );
}

// --- Inbox --------------------------------------------------------------------------------------

function InboxView({ today }: { today: string }) {
  const version = useOrgVersion();
  const containers = useContainers();
  const [items, setItems] = useState<OrgItem[] | null>(null);
  useEffect(() => {
    api.orgItems({ status: ["inbox"], order: "created", limit: 500 }).then(setItems).catch(() => setItems([]));
  }, [version]);
  return (
    <>
      <div className="mb-4">
        <CaptureBar autoFocus />
      </div>
      <p className="mb-3 text-sm text-slate-400">
        Things you captured but haven't sorted. Give each a home in a project, area or resource, or
        leave it loose. Aim to empty it in the weekly review.
      </p>
      {!items ? (
        <Spinner />
      ) : items.length ? (
        <ul className="grid gap-2 md:grid-cols-2">
          {items.map((i) => (
            <InboxCard key={i.id} item={i} today={today} containers={containers} />
          ))}
        </ul>
      ) : (
        <Empty>Inbox empty. Anything you capture lands here first.</Empty>
      )}
    </>
  );
}

// --- Tasks --------------------------------------------------------------------------------------

type TaskFilter = "open" | "doing" | "done";

function TasksView({ today }: { today: string }) {
  const version = useOrgVersion();
  const containers = useContainers();
  const names = useMemo(() => new Map(containers.map((c) => [c.id, c.title])), [containers]);
  const [params, setParams] = useSearchParams();
  const filter = (params.get("show") as TaskFilter) || "open";
  const place = params.get("in") ?? "";
  const [items, setItems] = useState<OrgItem[] | null>(null);
  const load = () =>
    api
      .orgItems({
        type: ["task"],
        status: filter === "open" ? ["todo", "doing"] : filter === "doing" ? ["doing"] : ["done"],
        parent_id: place || undefined,
        order: filter === "done" ? "updated" : "due",
        limit: filter === "done" ? 100 : 1000,
      })
      .then(setItems)
      .catch(() => setItems([]));
  useEffect(() => void load(), [version, filter, place]);
  const set = (k: string, v: string) =>
    setParams((p) => {
      if (v) p.set(k, v);
      else p.delete(k);
      return p;
    });

  return (
    <>
      <div className="mb-4">
        <CaptureBar
          inbox={false}
          task
          parentId={place || undefined}
          placeholder="Add a task (end with a day, like “tomorrow” or “friday”, to date it)"
          onSaved={() => void load()}
        />
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {(["open", "doing", "done"] as const).map((f) => (
          <button
            key={f}
            onClick={() => set("show", f === "open" ? "" : f)}
            aria-pressed={filter === f}
            className={`rounded-full border px-3 py-1 text-sm ${filter === f ? "border-sky-500/60 bg-sky-500/15 text-sky-100" : "border-edge text-slate-300 hover:bg-edge/60"}`}
          >
            {f === "open" ? "To do" : f === "doing" ? "Doing" : "Done"}
          </button>
        ))}
        <Select value={place} onChange={(e) => set("in", e.target.value)} aria-label="Only in" className="ml-auto max-w-56">
          <option value="">Everywhere</option>
          {containers.map((c) => (
            <option key={c.id} value={c.id}>
              {TYPE_LABEL[c.type]}: {c.title}
            </option>
          ))}
        </Select>
      </div>
      {!items ? (
        <Spinner />
      ) : !items.length ? (
        <Empty>{filter === "done" ? "Nothing ticked off yet." : "No tasks here. Add one above."}</Empty>
      ) : filter === "done" ? (
        <ul className="space-y-0.5">
          {items.map((t) => (
            <TaskRow key={t.id} task={t} today={today} parentTitle={t.parent_id ? names.get(t.parent_id) : null} onChanged={() => void load()} extra={t.done_at ? <span>done {formatAgo(t.done_at)}</span> : null} />
          ))}
        </ul>
      ) : (
        groupByDue(items, today).map((g) => (
          <Section key={g.key} title={g.label} count={g.items.length}>
            <ul className="space-y-0.5">
              {g.items.map((t) => (
                <TaskRow key={t.id} task={t} today={today} parentTitle={t.parent_id ? names.get(t.parent_id) : null} onChanged={() => void load()} />
              ))}
            </ul>
          </Section>
        ))
      )}
    </>
  );
}

// --- Projects and areas -------------------------------------------------------------------------

function ProjectsView() {
  const version = useOrgVersion();
  const [items, setItems] = useState<OrgItem[] | null>(null);
  const [openTasks, setOpenTasks] = useState<OrgItem[]>([]);
  useEffect(() => {
    api
      .orgItems({ type: ["project", "area", "resource"], status: ["active", "paused"], order: "title", limit: 1000 })
      .then(setItems)
      .catch(() => setItems([]));
    api.orgItems({ type: ["task"], status: ["todo", "doing"], limit: 1000 }).then(setOpenTasks).catch(() => {});
  }, [version]);
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const t of openTasks) if (t.parent_id) m.set(t.parent_id, (m.get(t.parent_id) ?? 0) + 1);
    return m;
  }, [openTasks]);
  if (!items) return <Spinner />;
  const areaName = new Map(items.filter((i) => i.type === "area").map((a) => [a.id, a.title]));
  const groups: { type: OrgType; title: string; blurb: string; example: string }[] = [
    { type: "project", title: "Projects", blurb: "Things with a finish line.", example: "Renew passport" },
    { type: "area", title: "Areas", blurb: "Parts of life you keep up with, no end date.", example: "Health" },
    { type: "resource", title: "Resources", blurb: "Topics you collect notes about.", example: "Recipes" },
  ];
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      {groups.map((g) => {
        const list = items.filter((i) => i.type === g.type);
        return (
          <section key={g.type} className="min-w-0">
            <h2 className="flex items-center gap-2 text-sm font-medium text-slate-300">
              {g.title} <span className="rounded-full bg-edge px-1.5 text-xs text-slate-400">{list.length}</span>
            </h2>
            <p className="mb-2 text-xs text-slate-500">{g.blurb}</p>
            <NewContainer type={g.type} example={g.example} />
            <div className="mt-2 space-y-2">
              {list.map((i) => {
                const n = counts.get(i.id) ?? 0;
                const bits = [
                  i.status === "paused" ? "Paused" : null,
                  i.type === "project" && i.parent_id ? areaName.get(i.parent_id) : null,
                  n ? `${n} open` : i.type === "project" ? "no next step" : null,
                ].filter(Boolean);
                return <ItemCard key={i.id} item={i} meta={bits.join(" · ")} />;
              })}
              {!list.length && <p className="text-sm text-slate-500">None yet.</p>}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function NewContainer({ type, example }: { type: OrgType; example: string }) {
  const [title, setTitle] = useState("");
  const navigate = useNavigate();
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (!title.trim()) return;
        try {
          const made = await api.orgCreate({ type: type as Exclude<OrgType, "daily">, title: title.trim() });
          setTitle("");
          navigate(`/organiser/item/${made.id}`);
        } catch (err) {
          toast((err as Error).message, "error");
        }
      }}
      className="flex items-center gap-2 rounded-lg border border-dashed border-edge px-2.5 py-1.5 focus-within:border-sky-500/50"
    >
      <Plus size={14} className="shrink-0 text-slate-500" />
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder={`New ${TYPE_LABEL[type].toLowerCase()}, e.g. ${example}`}
        className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-slate-500 sm:text-sm"
        style={{ outline: "none" }}
        maxLength={300}
        aria-label={`New ${TYPE_LABEL[type].toLowerCase()}`}
      />
    </form>
  );
}

// --- Notes --------------------------------------------------------------------------------------

function NotesView() {
  const version = useOrgVersion();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const tag = params.get("tag") ?? "";
  const [draft, setDraft] = useState(q);
  const [items, setItems] = useState<OrgItem[] | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      api
        .orgItems({ type: ["note"], status: ["active"], q: q || undefined, tag: tag || undefined, limit: 500 })
        .then(setItems)
        .catch(() => setItems([]));
    }, 150);
    return () => window.clearTimeout(timer);
  }, [version, q, tag]);
  const tags = useMemo(() => [...new Set((items ?? []).flatMap((i) => i.tags))].sort(), [items]);
  const set = (k: string, v: string) =>
    setParams((p) => {
      if (v) p.set(k, v);
      else p.delete(k);
      return p;
    }, { replace: true });

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-edge bg-panel px-3 py-1.5 focus-within:border-sky-500/60">
          <Search size={15} className="shrink-0 text-slate-400" />
          <input
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              set("q", e.target.value.trim());
            }}
            placeholder="Filter notes"
            className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-slate-500 sm:text-sm"
            style={{ outline: "none" }}
            aria-label="Filter notes"
          />
        </div>
        <button
          onClick={async () => {
            try {
              const made = await api.orgCreate({ type: "note", title: "Untitled note", tags: tag ? [tag] : [] });
              navigate(`/organiser/item/${made.id}`);
            } catch (e) {
              toast((e as Error).message, "error");
            }
          }}
          className="flex items-center gap-1.5 rounded-md bg-sky-500 px-3 py-1.5 text-sm font-medium text-ink hover:bg-sky-400"
        >
          <Plus size={15} /> New note
        </button>
      </div>
      {(tags.length > 0 || tag) && (
        <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="Tags">
          {[...new Set([...(tag ? [tag] : []), ...tags])].map((t) => (
            <button
              key={t}
              onClick={() => set("tag", t === tag ? "" : t)}
              aria-pressed={t === tag}
              className={`rounded-full border px-2.5 py-0.5 text-xs ${t === tag ? "border-sky-500/60 bg-sky-500/15 text-sky-100" : "border-edge text-slate-400 hover:bg-edge/60"}`}
            >
              #{t}
            </button>
          ))}
        </div>
      )}
      {!items ? (
        <Spinner />
      ) : items.length ? (
        <div className="grid gap-2 md:grid-cols-2">
          {items.map((i) => (
            <ItemCard key={i.id} item={i} meta={formatAgo(i.updated_at)} />
          ))}
        </div>
      ) : (
        <Empty>{q || tag ? "No notes match." : "No notes yet. Notes inside projects and areas show here too."}</Empty>
      )}
    </>
  );
}

// --- Archive ------------------------------------------------------------------------------------

function ArchiveView() {
  const version = useOrgVersion();
  const [items, setItems] = useState<OrgItem[] | null>(null);
  const [kind, setKind] = useState<"" | OrgType>("");
  useEffect(() => {
    Promise.all([
      api.orgItems({ status: ["archived", "dropped"], limit: 500 }),
      api.orgItems({ type: ["project"], status: ["done"], limit: 200 }),
    ])
      .then(([a, b]) => setItems([...a, ...b].sort((x, y) => y.updated_at.localeCompare(x.updated_at))))
      .catch(() => setItems([]));
  }, [version]);
  const shown = (items ?? []).filter((i) => !kind || i.type === kind);
  const restore = async (i: OrgItem) => {
    try {
      await api.orgUpdate(i.id, { status: i.type === "task" ? "todo" : "active" });
      toast(`${i.title} is back`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <>
      <div className="mb-4 flex items-center gap-2">
        <p className="text-sm text-slate-400">Finished projects, dropped tasks and anything archived. Kept, but out of the way.</p>
        <Select value={kind} onChange={(e) => setKind(e.target.value as OrgType | "")} aria-label="Show" className="ml-auto">
          <option value="">Everything</option>
          {(["project", "area", "resource", "note", "task"] as const).map((t) => (
            <option key={t} value={t}>
              {TYPE_LABEL[t]}s
            </option>
          ))}
        </Select>
      </div>
      {!items ? (
        <Spinner />
      ) : shown.length ? (
        <ul className="space-y-1.5">
          {shown.map((i) => (
            <li key={i.id} className="flex items-center gap-3 rounded-lg border border-edge bg-panel px-3 py-2">
              <Link to={`/organiser/item/${i.id}`} className="min-w-0 flex-1 truncate hover:text-sky-200">
                {i.title}
              </Link>
              <span className="shrink-0 text-xs text-slate-500">
                {TYPE_LABEL[i.type]} · {STATUS_LABEL[i.status]} · {formatAgo(i.updated_at)}
              </span>
              <button onClick={() => restore(i)} className="flex shrink-0 items-center gap-1 text-xs text-sky-300 hover:text-sky-200" title="Make it active again">
                <RotateCcw size={12} /> Restore
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>The Archive is empty.</Empty>
      )}
    </>
  );
}

// --- One item, a day's note, and "create from a link" ------------------------------------------

function ItemView({ today }: { today: string }) {
  const { id = "" } = useParams();
  return (
    <div className="mx-auto max-w-3xl">
      <BackLink />
      <ItemEditor id={id} today={today} />
    </div>
  );
}

function DailyView({ today }: { today: string }) {
  const { day = today } = useParams();
  const navigate = useNavigate();
  const [id, setId] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setId(null);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) {
      setError("That isn't a date.");
      return;
    }
    api
      .orgDaily(day)
      .then((d) => {
        setId(d.id);
        setError("");
      })
      .catch((e) => setError((e as Error).message));
  }, [day]);
  const go = (d: string) => navigate(d === today ? "/organiser" : `/organiser/daily/${d}`);
  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-3 flex items-center gap-2">
        <button onClick={() => go(addDays(day, -1))} className="rounded-md p-1.5 text-slate-400 hover:bg-edge hover:text-white" aria-label="Day before">
          <ChevronLeft size={18} />
        </button>
        <h2 className="text-lg font-semibold">
          {/^\d{4}-\d{2}-\d{2}$/.test(day) ? `${weekday(day)} ${shortDay(day, today)}` : day}
        </h2>
        <button onClick={() => go(addDays(day, 1))} className="rounded-md p-1.5 text-slate-400 hover:bg-edge hover:text-white" aria-label="Day after">
          <ChevronRight size={18} />
        </button>
        {day !== today && (
          <Link to="/organiser" className="ml-auto text-sm text-sky-300 hover:text-sky-200">
            Today
          </Link>
        )}
      </div>
      {error ? <p className="text-sm text-rose-400">{error}</p> : id ? <ItemEditor id={id} today={today} daily /> : <Spinner />}
    </div>
  );
}

/** /organiser/new?title=… (a link to a note that doesn't exist yet): make it and open it. */
function NewView() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const title = params.get("title") ?? "Untitled note";
  const made = useRef(false);
  useEffect(() => {
    // Once, even when React runs the effect twice in development.
    if (made.current) return;
    made.current = true;
    api
      .orgCreate({ type: "note", title })
      .then((made) => navigate(`/organiser/item/${made.id}`, { replace: true }))
      .catch((e) => {
        toast((e as Error).message, "error");
        navigate("/organiser/notes", { replace: true });
      });
  }, [title, navigate]);
  return <Spinner />;
}

function BackLink() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate(-1)} className="mb-3 flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
      <ChevronLeft size={15} /> Back
    </button>
  );
}

function Spinner(): ReactNode {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-slate-400">
      <Loader2 size={16} className="animate-spin" /> Loading…
    </div>
  );
}
