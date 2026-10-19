import type { Chat, Message, Role, User, UserStatus } from "./types";

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
    headers: init.body ? { "Content-Type": "application/json", ...init.headers } : init.headers,
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
  send: (chatId: string, content: string) =>
    request<{ chat: Chat; user_message: Message; assistant_message: Message }>(
      `/api/chats/${chatId}/messages`,
      { method: "POST", body: json({ content }) },
    ),

  users: () => request<User[]>("/api/admin/users"),
  invite: (email: string, role: Role, budget_usd?: string) =>
    request<User>("/api/admin/users", {
      method: "POST",
      body: json({ email, role, ...(budget_usd ? { budget_usd } : {}) }),
    }),
  updateUser: (id: string, changes: { role?: Role; status?: UserStatus; budget_usd?: string }) =>
    request<User>(`/api/admin/users/${id}`, { method: "PATCH", body: json(changes) }),
};
