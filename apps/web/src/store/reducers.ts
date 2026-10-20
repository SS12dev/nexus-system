import type {
  AgentSystem,
  Approval,
  Chat,
  FileItem,
  JarvisState,
  Message,
  ServerEvent,
} from "../lib/types";

export interface ChatState {
  chats: Chat[];
  messages: Record<string, Message[]>;
  jarvis: JarvisState;
  /** What Jarvis is doing right now, such as searching documents, while it works. */
  activity: string | null;
  /** Files whose live status the UI shows, keyed by id. */
  files: Record<string, FileItem>;
  /** Bumped whenever Jarvis changes a memory or the profile, so open pages can refresh. */
  memoryVersion: number;
  /** Agent systems seen this session, with their latest run, kept live by events. */
  systems: Record<string, AgentSystem>;
  /** Approval requests seen this session, pending or decided. */
  approvals: Record<string, Approval>;
  /** Bumped on any agent-system change, so the Agents page can refresh its details. */
  agentsVersion: number;
  /** Bumped when a skill changes (Jarvis drafted one, or someone edited one). */
  skillsVersion: number;
}

export const initialState: ChatState = {
  chats: [],
  messages: {},
  jarvis: "idle",
  activity: null,
  files: {},
  memoryVersion: 0,
  systems: {},
  approvals: {},
  agentsVersion: 0,
  skillsVersion: 0,
};

const ACTIVITY: Record<string, string> = {
  search_documents: "Searching your documents",
  read_document: "Reading a document",
  list_documents: "Looking through your library",
  search_memory: "Remembering",
  remember: "Saving to memory",
  web_search: "Searching the web",
  web_fetch: "Reading a web page",
  run_code: "Running code",
  save_file: "Saving a file",
  skills_load: "Loading a skill",
  skills_read_file: "Reading a skill",
  skills_search: "Looking for a skill",
  build_agent_system: "Designing a team of agents",
  update_agent_system: "Redesigning the team",
  run_agent_system: "Starting the team",
};

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
    return m.status === "streaming" && mine && mine.content.length > m.content.length
      ? { ...m, content: mine.content }
      : m;
  });
  const fetchedIds = new Set(fetched.map((m) => m.id));
  // Keep messages added locally after the fetch started.
  for (const m of state.messages[chatId] ?? []) if (!fetchedIds.has(m.id)) merged.push(m);
  return { ...state, messages: { ...state.messages, [chatId]: merged } };
}

/** Apply one WebSocket event. Pure, so it is easy to test. */
export function applyEvent(state: ChatState, event: ServerEvent): ChatState {
  switch (event.type) {
    case "jarvis.state":
      return {
        ...state,
        jarvis: event.data.state,
        activity: event.data.state === "idle" ? null : state.activity,
      };
    case "tool.call":
      // Tools that agents in a run call show on the run's card, not as Jarvis's activity.
      if (event.data.run_id) return state;
      return { ...state, activity: ACTIVITY[event.data.name] ?? "Working" };
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
      return { ...state, systems: { ...state.systems, [sys.id]: { ...sys, run } } };
    }
    case "approval.requested":
    case "approval.resolved":
      return {
        ...upsertApprovals(state, [event.data.approval]),
        agentsVersion: state.agentsVersion + 1,
      };
    case "skill.updated":
      return { ...state, skillsVersion: state.skillsVersion + 1 };
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
      // Replies with files Jarvis or a team made carry them; otherwise keep the ones shown.
      const prev = state.messages[event.chat_id].find((m) => m.id === event.data.message.id);
      const message = {
        ...event.data.message,
        attachments: event.data.message.attachments ?? prev?.attachments ?? [],
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
