import type {
  Chat,
  FileItem,
  Memory,
  MemoryEvent,
  MemoryKind,
  Message,
  Profile,
  Role,
  SearchHit,
  User,
  UserStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, {
    credentials: "same-origin",
    ...init,
    // FormData bodies (uploads) set their own multipart Content-Type.
    headers:
      typeof init.body === "string"
        ? { "Content-Type": "application/json", ...init.headers }
        : init.headers,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.status === 204 ? (undefined as T) : resp.json();
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  authConfig: () =>
    request<{ oidc_enabled: boolean; dev_login_enabled: boolean }>("/auth/config"),
  devLogin: (email: string, token: string) =>
    request<User>("/auth/dev-login", { method: "POST", body: json({ email, token }) }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/api/me"),

  chats: () => request<Chat[]>("/api/chats"),
  createChat: () => request<Chat>("/api/chats", { method: "POST", body: json({}) }),
  renameChat: (id: string, title: string) =>
    request<Chat>(`/api/chats/${id}`, { method: "PATCH", body: json({ title }) }),
  deleteChat: (id: string) => request<void>(`/api/chats/${id}`, { method: "DELETE" }),
  messages: (chatId: string) => request<Message[]>(`/api/chats/${chatId}/messages`),
  send: (chatId: string, content: string, file_ids: string[] = []) =>
    request<{ chat: Chat; user_message: Message; assistant_message: Message }>(
      `/api/chats/${chatId}/messages`,
      { method: "POST", body: json({ content, file_ids }) },
    ),

  files: () => request<FileItem[]>("/api/files"),
  uploadFile: (file: File, chatId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (chatId) form.append("chat_id", chatId);
    return request<FileItem>("/api/files", { method: "POST", body: form });
  },
  fileText: (id: string, offset = 0) =>
    request<{ text: string; offset: number; total_chars: number }>(
      `/api/files/${id}/text?offset=${offset}`,
    ),
  searchFiles: (q: string) => request<SearchHit[]>(`/api/files/search?q=${encodeURIComponent(q)}`),
  renameFile: (id: string, name: string) =>
    request<FileItem>(`/api/files/${id}`, { method: "PATCH", body: json({ name }) }),
  retryFile: (id: string) => request<FileItem>(`/api/files/${id}/retry`, { method: "POST" }),
  deleteFile: (id: string) => request<void>(`/api/files/${id}`, { method: "DELETE" }),
  fileUrl: (id: string, inline = false) => `/api/files/${id}/content${inline ? "?inline=true" : ""}`,

  memories: (q?: string) =>
    request<Memory[]>(`/api/memories${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  addMemory: (content: string, kind: MemoryKind) =>
    request<Memory>("/api/memories", { method: "POST", body: json({ content, kind }) }),
  updateMemory: (id: string, changes: { content?: string; kind?: MemoryKind; pinned?: boolean }) =>
    request<Memory>(`/api/memories/${id}`, { method: "PATCH", body: json(changes) }),
  deleteMemory: (id: string, rebuild = false) =>
    request<void>(`/api/memories/${id}${rebuild ? "?rebuild=true" : ""}`, { method: "DELETE" }),
  memoryEvents: (id: string) => request<MemoryEvent[]>(`/api/memories/${id}/events`),
  forgetEverything: () => request<void>("/api/memories", { method: "DELETE" }),
  profile: () => request<Profile>("/api/memories/profile"),
  setMemoryEnabled: (memory_enabled: boolean) =>
    request<Profile>("/api/memories/profile", { method: "PATCH", body: json({ memory_enabled }) }),
  rebuildProfile: () => request<void>("/api/memories/profile/rebuild", { method: "POST" }),

  users: () => request<User[]>("/api/admin/users"),
  invite: (email: string, role: Role, budget_usd?: string) =>
    request<User>("/api/admin/users", {
      method: "POST",
      body: json({ email, role, ...(budget_usd ? { budget_usd } : {}) }),
    }),
  updateUser: (id: string, changes: { role?: Role; status?: UserStatus; budget_usd?: string }) =>
    request<User>(`/api/admin/users/${id}`, { method: "PATCH", body: json(changes) }),
};
