import { toolLabel } from "../lib/tools";
import type {
  AgentSystem,
  Approval,
  Chat,
  FeedItem,
  FileItem,
  AssistantState,
  Message,
  ServerEvent,
  ToolStep,
} from "../lib/types";

export interface ChatState {
  chats: Chat[];
  messages: Record<string, Message[]>;
  assistantState: AssistantState;
  /** What Nexus is doing right now, such as searching documents, while it works. */
  activity: string | null;
  /** Files whose live status the UI shows, keyed by id. */
  files: Record<string, FileItem>;
  /** Bumped whenever Nexus changes a memory or the profile, so open pages can refresh. */
  memoryVersion: number;
  /** Agent systems seen this session, with their latest run, kept live by events. */
  systems: Record<string, AgentSystem>;
  /** Approval requests seen this session, pending or decided. */
  approvals: Record<string, Approval>;
  /** Bumped on any agent-system change, so the Agents page can refresh its details. */
  agentsVersion: number;
  /** Bumped when a skill changes (Nexus drafted one, or someone edited one). */
  skillsVersion: number;
  /** Bumped when a link is added, changed or deleted, so the Library reloads its links. */
  linksVersion: number;
  /** Live activity of team runs, keyed by run id: agents starting and finishing, tool calls. */
  runFeed: Record<string, FeedItem[]>;
}

export const initialState: ChatState = {
  chats: [],
  messages: {},
  assistantState: "idle",
  activity: null,
  files: {},
  memoryVersion: 0,
  systems: {},
  approvals: {},
  agentsVersion: 0,
  skillsVersion: 0,
  linksVersion: 0,
  runFeed: {},
};

/** Most feed lines kept per run; older ones drop off the top. */
export const FEED_MAX = 60;
const META_KEYS = new Set(["name", "call_id", "agent", "run_id", "system_id"]);

function argsOf(data: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(data).filter(([k]) => !META_KEYS.has(k)));
}

/** Add a step, or merge into the one with the same id. */
export function upsertStep<T extends ToolStep>(list: T[], step: T): T[] {
  const i = list.findIndex((s) => s.id === step.id);
  if (i === -1) return [...list, step];
  const next = list.slice();
  next[i] = { ...next[i], ...step, args: { ...next[i].args, ...step.args } };
  return next;
}

/** Add a step to the reply Nexus is writing in a chat. */
function stepOnReply(state: ChatState, chatId: string | null, step: ToolStep): ChatState {
  const list = chatId ? state.messages[chatId] : undefined;
  if (!chatId || !list) return state;
  let i = -1;
  for (let j = list.length - 1; j >= 0; j--)
    if (list[j].role === "assistant" && list[j].status === "streaming") {
      i = j;
      break;
    }
  if (i === -1) return state;
  const next = list.slice();
  next[i] = { ...list[i], steps: upsertStep(list[i].steps ?? [], step) };
  return { ...state, messages: { ...state.messages, [chatId]: next } };
}

function feedOn(state: ChatState, runId: string, item: FeedItem): ChatState {
  let list = state.runFeed[runId] ?? [];
  // A new call from an agent means its last stored call (whose end wasn't recorded) is over.
  if (item.kind === "tool" && item.agent)
    list = list.map((f) =>
      f.stored && f.status === "running" && f.agent === item.agent ? { ...f, status: "done" as const } : f,
    );
  list = upsertStep(list, item);
  return { ...state, runFeed: { ...state.runFeed, [runId]: list.slice(-FEED_MAX) } };
}

/** Fill in a run's feed from its stored timeline, for a card opened after the events went by.
 * Lines that live events already brought stay; stored ones from before them go first. */
export function seedRunFeed(state: ChatState, runId: string, items: FeedItem[]): ChatState {
  const live = state.runFeed[runId] ?? [];
  const ids = new Set(live.map((f) => f.id));
  const first = live[0]?.at;
  // An agent's stored call counts as finished once a later live one from that agent arrived.
  const busy = new Set(live.filter((f) => f.kind === "tool").map((f) => f.agent));
  const earlier = items
    .filter((f) => !ids.has(f.id) && (!first || f.at < first))
    .map((f) => (f.kind === "tool" && f.status === "running" && busy.has(f.agent) ? { ...f, status: "done" as const } : f));
  if (!earlier.length) return state;
  return { ...state, runFeed: { ...state.runFeed, [runId]: [...earlier, ...live].slice(-FEED_MAX) } };
}


export function upsertSystems(state: ChatState, systems: AgentSystem[]): ChatState {
  const next = { ...state.systems };
  for (const s of systems) next[s.id] = { ...next[s.id], ...s, run: s.run ?? next[s.id]?.run };
  return { ...state, systems: next };
}

/** Take a fresh list of the user's non-archived systems as the truth: any system the store
 * still has as live that the list leaves out was deleted or archived while the socket was down. */
export function syncLiveSystems(state: ChatState, systems: AgentSystem[]): ChatState {
  const ids = new Set(systems.map((s) => s.id));
  const next = upsertSystems(state, systems);
  const kept = Object.fromEntries(
    Object.entries(next.systems).filter(([id, s]) => ids.has(id) || s.status === "archived"),
  );
  return { ...next, systems: kept };
}

export function upsertApprovals(state: ChatState, approvals: Approval[]): ChatState {
  const next = { ...state.approvals };
  for (const a of approvals) next[a.id] = a;
  return { ...state, approvals: next };
}

function upsertMessage(list: Message[] = [], message: Message): Message[] {
  const i = list.findIndex((m) => m.id === message.id);
  if (i === -1) return [...list, message];
  const next = list.slice();
  next[i] = message;
  return next;
}

export function upsertFile(files: Record<string, FileItem>, file: FileItem) {
  return { ...files, [file.id]: file };
}

export function syncFiles(current: Record<string, FileItem>, fetched: FileItem[]) {
  const next: Record<string, FileItem> = {};
  for (const f of Object.values(current)) if (f.status === "processing") next[f.id] = f;
  for (const f of fetched) next[f.id] = f;
  return next;
}

export function upsertChat(chats: Chat[], chat: Chat): Chat[] {
  const rest = chats.filter((c) => c.id !== chat.id);
  return [chat, ...rest].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

export function addMessages(state: ChatState, chatId: string, messages: Message[]): ChatState {
  let list = state.messages[chatId] ?? [];
  for (const m of messages) list = upsertMessage(list, m);
  return { ...state, messages: { ...state.messages, [chatId]: list } };
}

/** Merge a freshly fetched message list. The server stores a reply only once it is finished,
 * so for a reply still streaming keep the text that has already arrived over the socket. */
export function mergeFetched(state: ChatState, chatId: string, fetched: Message[]): ChatState {
  const local = new Map((state.messages[chatId] ?? []).map((m) => [m.id, m]));
  const merged = fetched.map((m) => {
    const mine = local.get(m.id);
    if (m.status !== "streaming" || !mine) return m;
    return {
      ...m,
      content: mine.content.length > m.content.length ? mine.content : m.content,
      steps: mine.steps ?? m.steps,
    };
  });
  const fetchedIds = new Set(fetched.map((m) => m.id));
  // Keep messages added locally after the fetch started.
  for (const m of state.messages[chatId] ?? []) if (!fetchedIds.has(m.id)) merged.push(m);
  return { ...state, messages: { ...state.messages, [chatId]: merged } };
}

/** Apply one WebSocket event. Pure, so it is easy to test. */
export function applyEvent(state: ChatState, event: ServerEvent): ChatState {
  switch (event.type) {
    case "assistant.state":
      return {
        ...state,
        assistantState: event.data.state,
        activity: event.data.state === "idle" ? null : state.activity,
      };
    case "tool.call": {
      const { name, call_id, agent, run_id } = event.data;
      const id = call_id ?? `${name}-${Date.now()}`;
      const step: ToolStep = { id, name, args: argsOf(event.data), status: "running" };
      // Tools that agents in a run call show on the run's card, not as Nexus's activity.
      if (run_id)
        return feedOn(state, run_id, { ...step, kind: "tool", agent: agent ?? null, at: new Date().toISOString() });
      return stepOnReply({ ...state, activity: toolLabel(name) }, event.chat_id, step);
    }
    case "tool.done": {
      const { name, call_id, ok, ms, summary, agent, run_id } = event.data;
      const step: ToolStep = { id: call_id, name, status: ok ? "done" : "failed", ms, summary };
      if (run_id) {
        const known = state.runFeed[run_id]?.some((f) => f.id === call_id);
        const item = known ? step : { ...step, kind: "tool", agent: agent ?? null, at: new Date().toISOString() };
        return feedOn(state, run_id, item as FeedItem);
      }
      return stepOnReply(state, event.chat_id, step);
    }
    case "system.created":
    case "system.updated":
      return { ...upsertSystems(state, [event.data.system]), agentsVersion: state.agentsVersion + 1 };
    case "system.deleted": {
      const { [event.data.system.id]: _gone, ...systems } = state.systems;
      return { ...state, systems, agentsVersion: state.agentsVersion + 1 };
    }
    case "run.updated": {
      const run = event.data.run;
      const sys = state.systems[run.system_id];
      const bumped = { ...state, agentsVersion: state.agentsVersion + 1 };
      if (!sys) return bumped;
      return { ...bumped, systems: { ...state.systems, [sys.id]: { ...sys, run } } };
    }
    case "agent.status": {
      const { system_id, run_id, agent, ...live } = event.data;
      const sys = state.systems[system_id];
      if (!sys?.run || sys.run.id !== run_id) return state;
      const agents = { ...sys.run.agents, [agent]: { ...sys.run.agents[agent], ...live } };
      const run = { ...sys.run, agents };
      const next = { ...state, systems: { ...state.systems, [sys.id]: { ...sys, run } } };
      // Starting, finishing and failing are worth a line in the feed; the rest is live status.
      if (!["running", "done", "failed"].includes(live.status)) return next;
      const at = new Date().toISOString();
      return feedOn(next, run_id, {
        id: `${agent}:${live.status}:${at}`,
        kind: "agent",
        agent,
        name: live.status,
        args: live.item ? { item: live.item } : {},
        status: live.status === "running" ? "running" : live.status === "failed" ? "failed" : "done",
        at,
      });
    }
    case "approval.requested":
    case "approval.resolved":
      return {
        ...upsertApprovals(state, [event.data.approval]),
        agentsVersion: state.agentsVersion + 1,
      };
    case "skill.updated":
      return { ...state, skillsVersion: state.skillsVersion + 1 };
    case "link.updated":
    case "link.deleted":
      return { ...state, linksVersion: state.linksVersion + 1 };
    case "artifact.created":
      return { ...state, files: upsertFile(state.files, event.data.file) };
    case "file.deleted": {
      const { [event.data.id]: _gone, ...files } = state.files;
      return { ...state, files };
    }
    case "file.updated": {
      const file = event.data.file;
      // Attachment chips in loaded chats follow the file's status too.
      const messages: Record<string, Message[]> = {};
      for (const [chatId, list] of Object.entries(state.messages)) {
        messages[chatId] = list.some((m) => m.attachments?.some((a) => a.id === file.id))
          ? list.map((m) => ({
              ...m,
              attachments: m.attachments?.map((a) =>
                a.id === file.id ? { ...a, status: file.status, name: file.name } : a,
              ),
            }))
          : list;
      }
      return { ...state, messages, files: { ...state.files, [file.id]: file } };
    }
    case "message.delta": {
      const list = state.messages[event.chat_id];
      if (!list) return state; // Chat not open; it loads in full when opened.
      const next = list.map((m) =>
        m.id === event.data.message_id ? { ...m, content: m.content + event.data.delta } : m,
      );
      return { ...state, messages: { ...state.messages, [event.chat_id]: next } };
    }
    case "message.completed":
    case "message.failed": {
      const next = { ...state, activity: null };
      if (!state.messages[event.chat_id]) return next;
      // Replies with files Nexus or a team made carry them; otherwise keep the ones shown.
      const prev = state.messages[event.chat_id].find((m) => m.id === event.data.message.id);
      const message = {
        ...event.data.message,
        attachments: event.data.message.attachments ?? prev?.attachments ?? [],
        links: event.data.message.links ?? prev?.links ?? [],
        steps: event.data.message.steps ?? prev?.steps ?? null,
      };
      return addMessages(next, event.chat_id, [message]);
    }
    case "memory.updated":
    case "profile.updated":
      return { ...state, memoryVersion: state.memoryVersion + 1 };
    case "chat.updated":
      return { ...state, chats: upsertChat(state.chats, event.data.chat) };
    default:
      return state;
  }
}
