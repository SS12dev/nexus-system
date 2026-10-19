import type { Chat, FileItem, JarvisState, Message, ServerEvent } from "../lib/types";

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
}

export const initialState: ChatState = {
  chats: [],
  messages: {},
  jarvis: "idle",
  activity: null,
  files: {},
  memoryVersion: 0,
};

const ACTIVITY: Record<string, string> = {
  search_documents: "Searching your documents",
  read_document: "Reading a document",
  list_documents: "Looking through your library",
  search_memory: "Remembering",
  remember: "Saving to memory",
};

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
      return { ...state, activity: ACTIVITY[event.data.name] ?? "Working" };
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
      // Completion events carry no attachments; keep the ones already shown.
      const prev = state.messages[event.chat_id].find((m) => m.id === event.data.message.id);
      const message = { ...event.data.message, attachments: prev?.attachments ?? [] };
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
