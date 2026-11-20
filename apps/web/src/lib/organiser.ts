import type { OrgItem, OrgRef, OrgStatus, OrgType } from "./types";

/** Mirrors nexus_common/organiser.py STATUSES. */
export const STATUSES: Record<OrgType, OrgStatus[]> = {
  note: ["inbox", "active", "archived"],
  task: ["inbox", "todo", "doing", "done", "dropped"],
  project: ["active", "paused", "done", "archived"],
  area: ["active", "archived"],
  resource: ["active", "archived"],
  daily: ["active"],
};

export const DEFAULT_STATUS: Record<OrgType, OrgStatus> = {
  note: "active",
  task: "todo",
  project: "active",
  area: "active",
  resource: "active",
  daily: "active",
};

export const TYPE_LABEL: Record<OrgType, string> = {
  note: "Note",
  task: "Task",
  project: "Project",
  area: "Area",
  resource: "Resource",
  daily: "Daily note",
};

export const STATUS_LABEL: Record<OrgStatus, string> = {
  inbox: "In Inbox",
  todo: "To do",
  doing: "Doing",
  done: "Done",
  dropped: "Dropped",
  active: "Active",
  paused: "Paused",
  archived: "Archived",
};

export const PRIORITY_LABEL = ["No priority", "Low", "Medium", "High"];

/** Containers a note or task can go in; a project only in an area (as the server checks). */
export function canContain(parent: OrgType, child: OrgType): boolean {
  if (child === "project") return parent === "area";
  if (child === "note" || child === "task") return ["project", "area", "resource"].includes(parent);
  return false;
}

export function isClosed(item: Pick<OrgItem, "status">): boolean {
  return ["done", "dropped", "archived"].includes(item.status);
}

/** Dates are plain YYYY-MM-DD days in the person's own time zone; do the sums in UTC so no
 * local offset can move them a day. */
export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

const WEEKDAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function weekday(day: string): string {
  return WEEKDAY[new Date(`${day}T00:00:00Z`).getUTCDay()];
}

/** "3 Dec", or "3 Dec 2027" outside this year. */
export function shortDay(day: string, today: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  const base = `${d.getUTCDate()} ${MONTH[d.getUTCMonth()]}`;
  return day.slice(0, 4) === today.slice(0, 4) ? base : `${base} ${day.slice(0, 4)}`;
}

/** A due date the way people say it: "Today", "Tomorrow", "Friday", "3 days late". */
export function dueLabel(due: string, today: string): { text: string; tone: "late" | "today" | "soon" | "later" } {
  const n = daysBetween(today, due);
  if (n < 0) return { text: n === -1 ? "Yesterday" : `${-n} days late`, tone: "late" };
  if (n === 0) return { text: "Today", tone: "today" };
  if (n === 1) return { text: "Tomorrow", tone: "soon" };
  if (n < 7) return { text: weekday(due), tone: "soon" };
  return { text: shortDay(due, today), tone: "later" };
}

export interface TaskGroup {
  key: string;
  label: string;
  items: OrgItem[];
}

/** Open tasks by when they're due: Overdue, Today, Next 7 days, Later, No date. Empty groups
 * are left out. Each group keeps the order it was given (the server sorts by due date). */
export function groupByDue(tasks: OrgItem[], today: string): TaskGroup[] {
  const groups: TaskGroup[] = [
    { key: "overdue", label: "Overdue", items: [] },
    { key: "today", label: "Today", items: [] },
    { key: "week", label: "Next 7 days", items: [] },
    { key: "later", label: "Later", items: [] },
    { key: "none", label: "No date", items: [] },
  ];
  const by = Object.fromEntries(groups.map((g) => [g.key, g]));
  for (const t of tasks) {
    if (!t.due_on) by.none.items.push(t);
    else {
      const n = daysBetween(today, t.due_on);
      by[n < 0 ? "overdue" : n === 0 ? "today" : n <= 7 ? "week" : "later"].items.push(t);
    }
  }
  return groups.filter((g) => g.items.length);
}

const WIKILINK = /\[\[([^[\]|#\n]{1,300})(#[^[\]|\n]*)?(?:\|([^[\]\n]*))?\]\]/g;

/** Every [[link]] title in a text, once each, as the server reads them. */
export function wikilinks(text: string): string[] {
  const seen = new Set<string>();
  for (const m of text.matchAll(WIKILINK)) {
    const t = m[1].trim();
    if (t) seen.add(t);
  }
  return [...seen];
}

const escapeLabel = (s: string) => s.replace(/([[\]\\])/g, "\\$1");

/** Turn [[Title]] and [[Title|label]] into Markdown links for the preview: to the item when
 * one has that title, otherwise to /organiser/new?title=… (the page offers to make it). */
export function linkWikilinks(text: string, links: OrgRef[]): string {
  const byTitle = new Map(links.map((l) => [l.title.toLowerCase(), l]));
  return text.replace(WIKILINK, (_m, title: string, _heading, label?: string) => {
    const t = title.trim();
    const shown = escapeLabel((label ?? t).trim() || t);
    const hit = byTitle.get(t.toLowerCase());
    return hit
      ? `[${shown}](/organiser/item/${hit.id})`
      : `[${shown}](/organiser/new?title=${encodeURIComponent(t)})`;
  });
}

/** Unticked "- [ ] something" lines that would become tasks (the server's rule). */
export function openCheckboxes(text: string): number {
  return (text.match(/^\s*[-*+]\s+\[ \]\s+(?!\[\[)\S.*$/gm) ?? []).length;
}

/** The status a task or note moves to when filed out of the Inbox. */
export function filedStatus(type: OrgType): OrgStatus {
  return DEFAULT_STATUS[type];
}
