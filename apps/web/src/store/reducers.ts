import type { Chat, JarvisState, Message, ServerEvent } from "../lib/types";

export interface ChatState {
  chats: Chat[];
  messages: Record<string, Message[]>;
  jarvis: JarvisState;
}

export const initialState: ChatState = { chats: [], messages: {}, jarvis: "idle" };

function upsertMessage(list: Message[] = [], message: Message): Message[] {
  const i = list.findIndex((m) => m.id === message.id);
  if (i === -1) return [...list, message];
  const next = list.slice();
  next[i] = message;
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
      return { ...state, jarvis: event.data.state };
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
      if (!state.messages[event.chat_id]) return state;
      return addMessages(state, event.chat_id, [event.data.message]);
    }
    case "chat.updated":
      return { ...state, chats: upsertChat(state.chats, event.data.chat) };
    default:
      return state;
  }
}
