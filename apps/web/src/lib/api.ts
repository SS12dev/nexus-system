import type {
  AgentSystem,
  Approval,
  AuditEntry,
  Backup,
  Chat,
  FileItem,
  GoogleAccess,
  Grants,
  Instructions,
  Memory,
  MemoryEvent,
  MemoryKind,
  Message,
  OpsStatus,
  Profile,
  Project,
  Role,
  Run,
  RunDetail,
  SearchHit,
  SessionInfo,
  Skill,
  SkillDetail,
  SkillVersion,
  SystemDetail,
  ToolEntry,
  Usage,
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

/** Admin stats narrowed to these people: one user_id per person, none for everyone. */
function forUsers(ids: string[]): URLSearchParams {
  const q = new URLSearchParams();
  for (const id of ids) q.append("user_id", id);
  return q;
}

export const api = {
  authConfig: () =>
    request<{ oidc_enabled: boolean; dev_login_enabled: boolean }>("/auth/config"),
  devLogin: (email: string, token: string) =>
    request<User>("/auth/dev-login", { method: "POST", body: json({ email, token }) }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/api/me"),
  nameAssistant: (assistant_name: string) =>
    request<User>("/api/me", { method: "PATCH", body: json({ assistant_name }) }),
  googleAccess: () => request<GoogleAccess>("/api/me/google"),
  disconnectGoogle: () => request<void>("/api/me/google", { method: "DELETE" }),
  sessions: () => request<SessionInfo[]>("/api/me/sessions"),
  signOutSession: (id: string) => request<void>(`/api/me/sessions/${id}`, { method: "DELETE" }),
  signOutOthers: () => request<void>("/api/me/sessions/sign-out-others", { method: "POST" }),

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
  projects: () => request<Project[]>("/api/projects"),
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

  skills: (q?: string) =>
    request<Skill[]>(`/api/skills${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  skill: (id: string) => request<SkillDetail>(`/api/skills/${id}`),
  createSkill: (content: string, scope: "personal" | "domain" = "personal") =>
    request<SkillDetail>("/api/skills", { method: "POST", body: json({ content, scope }) }),
  importSkill: (file: File, scope: "personal" | "domain" = "personal") => {
    const form = new FormData();
    form.append("file", file);
    form.append("scope", scope);
    return request<SkillDetail>("/api/skills/import", { method: "POST", body: form });
  },
  editSkill: (id: string, content: string, files?: Record<string, string>, note?: string) =>
    request<SkillDetail>(`/api/skills/${id}`, {
      method: "PUT",
      body: json({ content, ...(files ? { files } : {}), ...(note ? { note } : {}) }),
    }),
  setSkillStatus: (id: string, status: "active" | "disabled") =>
    request<Skill>(`/api/skills/${id}/status`, { method: "POST", body: json({ status }) }),
  skillVersions: (id: string) => request<SkillVersion[]>(`/api/skills/${id}/versions`),
  rollbackSkill: (id: string, version: number) =>
    request<SkillDetail>(`/api/skills/${id}/rollback`, {
      method: "POST",
      body: json({ version }),
    }),
  shareSkill: (id: string) => request<SkillDetail>(`/api/skills/${id}/share`, { method: "POST" }),
  deleteSkill: (id: string) => request<void>(`/api/skills/${id}`, { method: "DELETE" }),

  instructions: () => request<Instructions>("/api/instructions"),
  setInstructions: (layer: "user" | "global" | `roles/${string}`, content: string) =>
    request<void>(`/api/instructions/${layer}`, { method: "PUT", body: json({ content }) }),

  tools: () => request<ToolEntry[]>("/api/tools"),
  systems: (params: { chat_id?: string; archived?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (params.chat_id) q.set("chat_id", params.chat_id);
    if (params.archived) q.set("archived", "true");
    return request<AgentSystem[]>(`/api/systems${q.size ? `?${q}` : ""}`);
  },
  system: (id: string) => request<SystemDetail>(`/api/systems/${id}`),
  runSystem: (id: string, body: { request: string; chat_id?: string; budget_usd?: number }) =>
    request<AgentSystem>(`/api/systems/${id}/runs`, { method: "POST", body: json(body) }),
  keepAsTemplate: (id: string, name?: string) =>
    request<AgentSystem>(`/api/systems/${id}/template`, {
      method: "POST",
      body: json(name ? { name } : {}),
    }),
  deleteSystem: (id: string) => request<void>(`/api/systems/${id}`, { method: "DELETE" }),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  controlRun: (id: string, action: "pause" | "resume" | "cancel") =>
    request<Run>(`/api/runs/${id}/${action}`, { method: "POST" }),
  approvals: (chatId?: string) =>
    request<Approval[]>(`/api/approvals${chatId ? `?chat_id=${chatId}` : ""}`),
  decide: (
    id: string,
    decision: "approve" | "reject",
    extra: { args?: Record<string, unknown>; note?: string } = {},
  ) =>
    request<Approval>(`/api/approvals/${id}`, {
      method: "POST",
      body: json({ decision, ...extra }),
    }),

  users: () => request<User[]>("/api/admin/users"),
  grants: (userId: string) => request<Grants>(`/api/admin/users/${userId}/grants`),
  setGrants: (userId: string, capabilities: string[]) =>
    request<Grants>(`/api/admin/users/${userId}/grants`, {
      method: "PUT",
      body: json({ capabilities }),
    }),
  invite: (email: string, role: Role, budget_usd?: string) =>
    request<User>("/api/admin/users", {
      method: "POST",
      body: json({ email, role, ...(budget_usd ? { budget_usd } : {}) }),
    }),
  signOutUser: (id: string) =>
    request<void>(`/api/admin/users/${id}/sign-out`, { method: "POST" }),
  systemStatus: (userIds: string[] = []) =>
    request<OpsStatus>(`/api/admin/status${userIds.length ? `?${forUsers(userIds)}` : ""}`),
  usage: (days = 7, userIds: string[] = []) => {
    const q = forUsers(userIds);
    q.set("days", String(days));
    return request<Usage>(`/api/admin/usage?${q}`);
  },
  audit: (params: { action?: string; before?: string; userIds?: string[] } = {}) => {
    const q = forUsers(params.userIds ?? []);
    if (params.action) q.set("action", params.action);
    if (params.before) q.set("before", params.before);
    return request<{ entries: AuditEntry[]; actions: string[] }>(
      `/api/admin/audit${q.size ? `?${q}` : ""}`,
    );
  },
  backups: () => request<Backup[]>("/api/admin/backups"),
  backupNow: () => request<Backup>("/api/admin/backups", { method: "POST" }),
  setMaintenance: (enabled: boolean, message?: string) =>
    request<{ enabled: boolean; message: string | null }>("/api/admin/maintenance", {
      method: "PUT",
      body: json({ enabled, message }),
    }),
  updateUser: (id: string, changes: { role?: Role; status?: UserStatus; budget_usd?: string }) =>
    request<User>(`/api/admin/users/${id}`, { method: "PATCH", body: json(changes) }),
};
