import type { FindKind, FindResult } from "./types";

/** Group headings, in the order groups show when they score the same. */
export const KIND_LABELS: Record<FindKind, string> = {
  chat: "Chats",
  file: "Files",
  memory: "Memories",
  person: "People",
  thing: "Knowledge",
  link: "Links",
  routine: "Routines",
  app: "Apps",
  system: "Agent teams",
  skill: "Skills",
};

export const KIND_ORDER = Object.keys(KIND_LABELS) as FindKind[];

/** A snippet split into plain and highlighted runs (the server marks matches with \u0002…\u0003). */
export function snippetParts(snippet: string): { text: string; hit: boolean }[] {
  const parts: { text: string; hit: boolean }[] = [];
  const re = /\u0002([^\u0003]*)\u0003/g;
  let last = 0;
  for (let m = re.exec(snippet); m; m = re.exec(snippet)) {
    if (m.index > last) parts.push({ text: snippet.slice(last, m.index), hit: false });
    if (m[1]) parts.push({ text: m[1], hit: true });
    last = m.index + m[0].length;
  }
  const rest = snippet.slice(last).replace(/[\u0002\u0003]/g, "");
  if (rest) parts.push({ text: rest, hit: false });
  return parts;
}

/** Results grouped by kind; groups ordered by their best hit. */
export function groupResults(results: FindResult[]): { kind: FindKind; items: FindResult[] }[] {
  const groups = new Map<FindKind, FindResult[]>();
  for (const r of results) {
    if (!groups.has(r.kind)) groups.set(r.kind, []);
    groups.get(r.kind)!.push(r);
  }
  const best = (items: FindResult[]) => Math.max(...items.map((r) => r.score));
  return [...groups.entries()]
    .map(([kind, items]) => ({ kind, items }))
    .sort(
      (a, b) =>
        best(b.items) - best(a.items) || KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind),
    );
}

/** A page or action in the bar, matched on the device as you type. */
export interface Command {
  id: string;
  label: string;
  /** Other words it answers to. */
  keywords?: string;
  hint?: string;
  section: "Go to" | "Actions";
  /** Where it goes; actions without one run `run` instead. */
  to?: string;
  owner?: boolean;
  admin?: boolean;
}

export const COMMANDS: Command[] = [
  { id: "new-chat", label: "New chat", keywords: "ask talk message start", section: "Actions", to: "/" },
  { id: "upload", label: "Upload a file", keywords: "add document pdf library", section: "Actions", to: "/library?upload=1" },
  { id: "new-routine", label: "New routine", keywords: "schedule brief daily remind", section: "Actions", to: "/routines?new=1" },
  { id: "home", label: "Home", keywords: "universe orb", section: "Go to", to: "/" },
  { id: "search", label: "Search", keywords: "find everything", section: "Go to", to: "/search" },
  { id: "library", label: "Library", keywords: "files documents links folders", section: "Go to", to: "/library" },
  { id: "apps", label: "Apps", keywords: "deployed projects", section: "Go to", to: "/apps" },
  { id: "routines", label: "Routines", keywords: "schedule automation", section: "Go to", to: "/routines" },
  { id: "mind", label: "Mind", keywords: "memory knowledge graph people", section: "Go to", to: "/mind" },
  { id: "memories", label: "Memories", keywords: "remember facts preferences", section: "Go to", to: "/mind?tab=memories" },
  { id: "agents", label: "Agents", keywords: "teams runs approvals", section: "Go to", to: "/agents" },
  { id: "skills", label: "Skills", keywords: "instructions library", section: "Go to", to: "/skills" },
  { id: "trash", label: "Trash", keywords: "deleted restore", section: "Go to", to: "/trash" },
  { id: "settings", label: "Settings", keywords: "connections google theme name push", section: "Go to", to: "/settings" },
  { id: "people", label: "People", keywords: "invite users access", section: "Go to", to: "/people", admin: true },
  { id: "admin", label: "Admin", keywords: "monitoring health usage models backups", section: "Go to", to: "/admin", owner: true },
];

/** Commands matching what was typed: every word must start a word of the label or keywords. */
export function matchCommands(query: string, commands: Command[]): Command[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return commands;
  return commands.filter((c) => {
    const hay = `${c.label} ${c.keywords ?? ""}`.toLowerCase().split(/[^a-z0-9]+/);
    return words.every((w) => hay.some((h) => h.startsWith(w)));
  });
}
