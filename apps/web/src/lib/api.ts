import type { TuneId } from "./tunes";
import type {
  AccessRequest,
  AccessRequestList,
  AccessState,
  AuthConfig,
  AppItem,
  AgentSystem,
  Approval,
  AuditEntry,
  Backup,
  Chat,
  Channel,
  ConfigValue,
  ConfigView,
  FeedbackItem,
  FeedbackKind,
  FileItem,
  GoogleAccess,
  LinkItem,
  Grants,
  Instructions,
  Memory,
  MemoryEvent,
  MemoryKind,
  Message,
  Notice,
  BridgeCall,
  ScreenMode,
  ScreenState,
  Widget,
  WidgetPos,
  WidgetAction,
  WidgetInput,
  OnboardingAnswers,
  OpsStatus,
  Profile,
  Profiles,
  Project,
  Role,
  Routine,
  RoutineInput,
  RoutineRun,
  Run,
  SchedulePreview,
  RunDetail,
  SearchHit,
  SessionInfo,
  Skill,
  SkillDetail,
  SkillVersion,
  SystemDetail,
  ToolEntry,
  Alerts,
  Improvements,
  KnowledgeGraph,
  LlmQuery,
  LlmUsage,
  Monitoring,
  Proposal,
  RecallCheck,
  RecallSummary,
  Review,
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
      // Unexpected server errors name the request, so the owner can find it in the logs.
      if (typeof body.request_id === "string") detail += ` (ref ${body.request_id})`;
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.status === 204 ? (undefined as T) : resp.json();
}

const json = (body: unknown) => JSON.stringify(body);

/** Tell the server a page crashed, for the Admin page's recent errors. Best effort, silent. */
export function reportClientError(error: Error, page: string): void {
  fetch("/api/me/client-error", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: json({
      message: `${error.name}: ${error.message}`.slice(0, 1000),
      stack: (error.stack ?? "").slice(-4000),
      page: page.slice(0, 300),
    }),
  }).catch(() => {});
}

/** Admin stats narrowed to these people: one user_id per person, none for everyone. */
function forUsers(ids: string[]): URLSearchParams {
  const q = new URLSearchParams();
  for (const id of ids) q.append("user_id", id);
  return q;
}

export const api = {
  authConfig: () => request<AuthConfig>("/auth/config"),
  accessState: () => request<AccessState>("/auth/access-request"),
  requestAccess: (note: string) =>
    request<AccessState>("/auth/access-request", {
      method: "POST",
      body: json({ note: note.trim() || null }),
    }),
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
  updateMe: (changes: {
    timezone?: string;
    phone?: string;
    share_usage?: boolean;
    tour_done?: boolean;
  }) =>
    request<User>("/api/me", { method: "PATCH", body: json(changes) }),
  profiles: () => request<Profiles>("/api/profiles"),
  onboard: (answers: Partial<OnboardingAnswers>) =>
    request<{ me: User; chat_id: string | null; routines: number }>("/api/me/onboarding", {
      method: "POST",
      body: json(answers),
    }),
  suggestions: () => request<string[]>("/api/me/suggestions"),
  notifications: () => request<{ items: Notice[]; unread: number }>("/api/notifications"),
  widgets: () => request<Widget[]>("/api/widgets"),
  addWidget: (body: WidgetInput) => request<Widget>("/api/widgets", { method: "POST", body: json(body) }),
  widgetAction: (id: string, action: WidgetAction, minutes?: number) =>
    request<Widget | null>(`/api/widgets/${id}/action`, { method: "POST", body: json({ action, minutes }) }),
  editWidget: (
    id: string,
    body: {
      title?: string;
      text?: string;
      slot?: number;
      pos?: WidgetPos;
      dock?: boolean;
      tune?: TuneId;
      time?: string;
      days?: string | string[] | null;
    },
  ) =>
    request<Widget>(`/api/widgets/${id}`, { method: "PATCH", body: json(body) }),
  /** Mark these notices read, or every one when ids is left out. */
  screen: () => request<ScreenState>("/api/screen"),
  setScreenMode: (mode: ScreenMode) =>
    request<ScreenState>("/api/screen/mode", { method: "PUT", body: json({ mode }) }),
  closeOnScreen: (kind: "file" | "widget", id: string) =>
    request<ScreenState>("/api/screen/close", { method: "POST", body: json({ kind, id }) }),
  widgetBridge: (id: string, call: BridgeCall, args: Record<string, unknown>) =>
    request<{ result: unknown }>(`/api/widgets/${id}/bridge`, { method: "POST", body: json({ call, args }) }),
  approveWidget: (id: string, hosts: string[], data: string[]) =>
    request<Widget>(`/api/widgets/${id}/approve`, { method: "POST", body: json({ hosts, data }) }),
  readNotifications: (ids?: string[]) =>
    request<void>("/api/notifications/read", { method: "POST", body: json({ ids: ids ?? null }) }),
  acceptTerms: (version: string) =>
    request<User>("/api/me/terms", { method: "POST", body: json({ version }) }),
  deleteAccount: (confirm: string) =>
    request<void>("/api/me/delete", { method: "POST", body: json({ confirm }) }),

  pushState: () =>
    request<{ enabled: boolean; public_key: string | null; devices: number }>("/api/push"),
  pushSubscribe: (sub: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    request<void>("/api/push/subscriptions", { method: "POST", body: json(sub) }),
  pushUnsubscribe: (endpoint: string) =>
    request<void>("/api/push/unsubscribe", { method: "POST", body: json({ endpoint }) }),
  pushTest: () => request<{ sent: number }>("/api/push/test", { method: "POST" }),
  sendFeedback: (body: { kind: FeedbackKind; message: string; page: string; screenshots?: File[] }) => {
    const form = new FormData();
    form.append("kind", body.kind);
    form.append("message", body.message);
    form.append("page", body.page);
    for (const shot of body.screenshots ?? []) form.append("screenshots", shot);
    return request<FeedbackItem>("/api/feedback", { method: "POST", body: form });
  },
  feedbackShotUrl: (id: string, n: number) => `/api/admin/feedback/${id}/screenshots/${n}`,
  feedbackList: (openOnly = false) =>
    request<FeedbackItem[]>(`/api/admin/feedback${openOnly ? "?open_only=true" : ""}`),
  markFeedback: (id: string, done: boolean) =>
    request<FeedbackItem>(`/api/admin/feedback/${id}/done?done=${done}`, { method: "POST" }),

  routines: () => request<Routine[]>("/api/routines"),
  createRoutine: (body: RoutineInput) =>
    request<Routine>("/api/routines", { method: "POST", body: json(body) }),
  updateRoutine: (id: string, changes: Partial<RoutineInput> & { delivery?: Channel[] }) =>
    request<Routine>(`/api/routines/${id}`, { method: "PATCH", body: json(changes) }),
  deleteRoutine: (id: string) => request<void>(`/api/routines/${id}`, { method: "DELETE" }),
  runRoutine: (id: string) =>
    request<{ queued: boolean; chat_id: string | null }>(`/api/routines/${id}/run`, {
      method: "POST",
    }),
  routineRuns: (id: string) => request<RoutineRun[]>(`/api/routines/${id}/runs`),
  previewSchedule: (schedule: string, timezone?: string) =>
    request<SchedulePreview>("/api/routines/preview", {
      method: "POST",
      body: json({ schedule, timezone }),
    }),

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
  links: (q?: string) => request<LinkItem[]>(`/api/links${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  addLink: (url: string, title?: string, note?: string) =>
    request<LinkItem>("/api/links", { method: "POST", body: json({ url, title: title || null, note: note || null }) }),
  editLink: (id: string, changes: { title?: string; note?: string }) =>
    request<LinkItem>(`/api/links/${id}`, { method: "PATCH", body: json(changes) }),
  refreshLink: (id: string) => request<LinkItem>(`/api/links/${id}/refresh`, { method: "POST" }),
  deleteLink: (id: string) => request<void>(`/api/links/${id}`, { method: "DELETE" }),
  apps: () => request<AppItem[]>("/api/apps"),
  appAction: (id: string, action: "stop" | "restart" | "redeploy") =>
    request<AppItem>(`/api/apps/${id}/${action}`, { method: "POST" }),
  setAppInternet: (id: string, internet: boolean) =>
    request<AppItem>(`/api/apps/${id}`, { method: "PATCH", body: json({ internet }) }),
  setAppPublic: (id: string, isPublic: boolean) =>
    request<AppItem>(`/api/apps/${id}/visibility`, { method: "POST", body: json({ public: isPublic }) }),
  deleteApp: (id: string) => request<void>(`/api/apps/${id}`, { method: "DELETE" }),
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
  knowledgeGraph: () => request<KnowledgeGraph>("/api/memories/graph"),
  deleteEntity: (id: string) => request<void>(`/api/memories/entities/${id}`, { method: "DELETE" }),
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
  revokeInvite: (id: string) => request<void>(`/api/admin/users/${id}`, { method: "DELETE" }),
  accessRequests: (status: "pending" | "decided" = "pending") =>
    request<AccessRequestList>(`/api/admin/access-requests?status=${status}`),
  approveAccess: (id: string, role: Role, budget_usd?: string) =>
    request<{ request: AccessRequest; user: User }>(`/api/admin/access-requests/${id}/approve`, {
      method: "POST",
      body: json({ role, ...(budget_usd ? { budget_usd } : {}) }),
    }),
  denyAccess: (id: string) =>
    request<AccessRequest>(`/api/admin/access-requests/${id}/deny`, { method: "POST" }),
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
  monitoring: (minutes: number) => request<Monitoring>(`/api/admin/monitoring?minutes=${minutes}`),
  adminSummary: () => request<{ alerts: number; proposals: number }>("/api/admin/summary"),
  alerts: () => request<Alerts>("/api/admin/alerts"),
  llmUsage: (query: LlmQuery) => {
    const q = forUsers(query.userIds);
    if (query.since) q.set("since", query.since);
    if (query.until) q.set("until", query.until);
    for (const m of query.models) q.append("model", m);
    for (const p of query.purposes) q.append("purpose", p);
    return request<LlmUsage>(`/api/admin/llm?${q}`);
  },
  improvements: (status?: string) =>
    request<Improvements>(`/api/admin/improvements${status ? `?status=${status}` : ""}`),
  reviewNow: () => request<Review>("/api/admin/improvements/review", { method: "POST" }),
  recall: () => request<RecallSummary>("/api/admin/recall"),
  runRecallCheck: () => request<RecallCheck>("/api/admin/recall/run", { method: "POST" }),
  resetRecallQuestions: () => request<void>("/api/admin/recall/questions", { method: "DELETE" }),
  approveProposal: (id: string, note?: string) =>
    request<Proposal>(`/api/admin/improvements/${id}/approve`, { method: "POST", body: json({ note: note || null }) }),
  denyProposal: (id: string, note?: string) =>
    request<Proposal>(`/api/admin/improvements/${id}/deny`, { method: "POST", body: json({ note: note || null }) }),
  revertProposal: (id: string) =>
    request<Proposal>(`/api/admin/improvements/${id}/revert`, { method: "POST" }),
  backupNow: () => request<Backup>("/api/admin/backups", { method: "POST" }),
  config: () => request<ConfigView>("/api/admin/config"),
  /** A value per setting, or null to go back to the .env value. */
  saveConfig: (values: Record<string, ConfigValue | null>) =>
    request<ConfigView>("/api/admin/config", { method: "PUT", body: json({ values }) }),
  /** Sends the signed-in owner a test email with the saved Email settings. */
  testEmail: () => request<{ to: string; from: string }>("/api/admin/config/test-email", { method: "POST" }),
  setMaintenance: (enabled: boolean, message?: string) =>
    request<{ enabled: boolean; message: string | null }>("/api/admin/maintenance", {
      method: "PUT",
      body: json({ enabled, message }),
    }),
  updateUser: (id: string, changes: { role?: Role; status?: UserStatus; budget_usd?: string }) =>
    request<User>(`/api/admin/users/${id}`, { method: "PATCH", body: json(changes) }),
};
