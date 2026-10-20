import { create } from "zustand";
import type {
  AgentSystem,
  Approval,
  Chat,
  FileItem,
  Message,
  ServerEvent,
  User,
} from "../lib/types";
import {
  addMessages,
  applyEvent,
  initialState,
  mergeFetched,
  upsertChat,
  upsertApprovals,
  upsertFile,
  upsertSystems,
  type ChatState,
} from "./reducers";

interface Store extends ChatState {
  user: User | null;
  connected: boolean;
  setUser: (user: User | null) => void;
  setConnected: (connected: boolean) => void;
  setChats: (chats: Chat[]) => void;
  upsertChat: (chat: Chat) => void;
  removeChat: (id: string) => void;
  setMessages: (chatId: string, messages: Message[]) => void;
  mergeFetched: (chatId: string, messages: Message[]) => void;
  addMessages: (chatId: string, messages: Message[]) => void;
  handleEvent: (event: ServerEvent) => void;
  upsertFile: (file: FileItem) => void;
  upsertSystems: (systems: AgentSystem[]) => void;
  upsertApprovals: (approvals: Approval[]) => void;
}

export const useStore = create<Store>((set) => ({
  ...initialState,
  user: null,
  connected: false,
  setUser: (user) => set({ user }),
  setConnected: (connected) => set({ connected }),
  setChats: (chats) => set({ chats }),
  upsertChat: (chat) => set((s) => ({ chats: upsertChat(s.chats, chat) })),
  removeChat: (id) =>
    set((s) => {
      const { [id]: _removed, ...messages } = s.messages;
      return { chats: s.chats.filter((c) => c.id !== id), messages };
    }),
  setMessages: (chatId, messages) =>
    set((s) => ({ messages: { ...s.messages, [chatId]: messages } })),
  mergeFetched: (chatId, messages) => set((s) => mergeFetched(s, chatId, messages)),
  addMessages: (chatId, messages) => set((s) => addMessages(s, chatId, messages)),
  handleEvent: (event) => set((s) => applyEvent(s, event)),
  upsertFile: (file) => set((s) => ({ files: upsertFile(s.files, file) })),
  upsertSystems: (systems) => set((s) => upsertSystems(s, systems)),
  upsertApprovals: (approvals) => set((s) => upsertApprovals(s, approvals)),
}));
