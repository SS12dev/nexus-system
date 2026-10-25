import { create } from "zustand";
import type {
  AgentSystem,
  Approval,
  Chat,
  FeedItem,
  FileBrief,
  FileItem,
  Message,
  Notice,
  Recalled,
  ServerEvent,
  User,
} from "../lib/types";
import {
  addMessages,
  applyEvent,
  initialState,
  markRead,
  mergeFetched,
  seedRunFeed,
  upsertChat,
  upsertApprovals,
  upsertFile,
  upsertSystems,
  syncFiles,
  syncLiveSystems,
  type ChatState,
} from "./reducers";

interface Store extends ChatState {
  user: User | null;
  connected: boolean;
  setUser: (user: User | null) => void;
  setConnected: (connected: boolean) => void;
  setPendingAccess: (count: number) => void;
  setChats: (chats: Chat[]) => void;
  upsertChat: (chat: Chat) => void;
  removeChat: (id: string) => void;
  setMessages: (chatId: string, messages: Message[]) => void;
  mergeFetched: (chatId: string, messages: Message[]) => void;
  addMessages: (chatId: string, messages: Message[]) => void;
  handleEvent: (event: ServerEvent) => void;
  upsertFile: (file: FileItem) => void;
  /** Take a fresh file list as the truth, keeping uploads still being processed. */
  syncFiles: (files: FileItem[]) => void;
  upsertSystems: (systems: AgentSystem[]) => void;
  syncLiveSystems: (systems: AgentSystem[]) => void;
  upsertApprovals: (approvals: Approval[]) => void;
  seedRunFeed: (runId: string, items: FeedItem[]) => void;
  /** Light up a reply's recall in the orb again (its chips were tapped). */
  showRecall: (recalled: Recalled) => void;
  /** The tray as loaded from the server. */
  setNotices: (notices: Notice[], unread: number) => void;
  dismissToast: (id: string) => void;
  markNoticesRead: (ids: string[] | null) => void;
  /** A result open on the main screen, in the orb's place (a sheet on phones). */
  viewer: { file: FileBrief; chatId: string | null } | null;
  openViewer: (file: FileBrief, chatId: string | null) => void;
  closeViewer: () => void;
}

export const useStore = create<Store>((set) => ({
  ...initialState,
  user: null,
  connected: false,
  setUser: (user) => set({ user, pendingAccess: user?.pending_access ?? null }),
  setConnected: (connected) => set({ connected }),
  setPendingAccess: (pendingAccess) => set({ pendingAccess }),
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
  syncFiles: (files) => set((s) => ({ files: syncFiles(s.files, files) })),
  upsertSystems: (systems) => set((s) => upsertSystems(s, systems)),
  syncLiveSystems: (systems) => set((s) => syncLiveSystems(s, systems)),
  upsertApprovals: (approvals) => set((s) => upsertApprovals(s, approvals)),
  seedRunFeed: (runId, items) => set((s) => seedRunFeed(s, runId, items)),
  showRecall: (recalled) => set((s) => ({ recall: { ...recalled, seq: (s.recall?.seq ?? 0) + 1 } })),
  setNotices: (notices, unread) => set({ notices, unread }),
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t !== id) })),
  markNoticesRead: (ids) => set((s) => markRead(s, ids)),
  viewer: null,
  openViewer: (file, chatId) => set({ viewer: { file, chatId } }),
  closeViewer: () => set({ viewer: null }),
}));
