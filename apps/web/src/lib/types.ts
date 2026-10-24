export type Role = "owner" | "admin" | "member" | "viewer";
export type UserStatus = "invited" | "active" | "disabled";
export type AssistantState = "idle" | "thinking" | "speaking";

export interface Budget {
  max_usd: string;
  duration: string;
  spend_usd: number | null;
}

export interface User {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
  /** What the user calls their assistant; null until they choose (only on /api/me). */
  assistant_name?: string | null;
  role: Role;
  status: UserStatus;
  created_at: string;
  last_login_at: string | null;
  budget: Budget;
  /** Set while an admin has maintenance mode on (only on /api/me). */
  notice?: string | null;
  /** Owners and admins: access requests waiting for a decision (only on /api/me). */
  pending_access?: number | null;
}

export interface AuthConfig {
  oidc_enabled: boolean;
  dev_login_enabled: boolean;
  /** Someone refused for lack of an invite may ask for one. */
  access_requests: boolean;
}

/** Where someone refused at sign-in stands, for the address they just verified. */
export interface AccessState {
  email: string;
  name: string | null;
  avatar_url: string | null;
  status: "can_request" | "pending" | "approved" | "denied";
  requested_at: string | null;
  retry_after: string | null;
  email_on_approval: boolean;
}

export interface AccessRequest {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
  note: string | null;
  status: "pending" | "approved" | "denied";
  ip: string | null;
  user_agent: string | null;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
  decided_by_name: string | null;
  /** Earlier requests from the same address. */
  previous: number;
}

export interface AccessRequestList {
  requests: AccessRequest[];
  pending: number;
  email_notifications: boolean;
  enabled: boolean;
}

export interface GoogleAccess {
  available: boolean;
  requested: string[];
  granted: string[];
  connected_at: string | null;
}

export interface Chat {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export type FileStatus = "processing" | "ready" | "failed";

export interface FileBrief {
  id: string;
  name: string;
  content_type: string;
  size: number;
  status: FileStatus;
  origin?: "upload" | "generated";
  version?: number;
}

/** A running app a software team built, on the Apps page. */
export interface AppItem {
  id: string;
  name: string;
  slug: string | null;
  owner_id: string;
  owner: string;
  state: string | null;
  live_url: string | null;
  repo_url: string | null;
  stack: string | null;
  deployed_at: string | null;
  deploy_error: string | null;
  internet: boolean;
  public: boolean;
  screenshot_file_id: string | null;
}

/** A software project a dev team built: one Library card linking to its GitHub repo. */
export interface Project {
  id: string;
  name: string;
  stack: string | null;
  repo_url: string | null;
  branch: string | null;
  merged: boolean;
  transferred_to: string | null;
  last_commit: string | null;
  last_commit_message: string | null;
  last_commit_at: string | null;
  live_url: string | null;
  app_state: string | null;
  public: boolean;
  screenshot_file_id: string | null;
  chat_id: string | null;
  updated_at: string;
}

/** What a link points to; see nexus_common.links. */
export type LinkKind =
  | "github_repo"
  | "github_file"
  | "github_folder"
  | "github_pr"
  | "github_issue"
  | "github_commit"
  | "google_doc"
  | "google_sheet"
  | "google_slides"
  | "google_drive"
  | "app"
  | "video"
  | "design"
  | "web";

/** A link in the library: a pointer to something that lives elsewhere, with a preview snapshot. */
export interface LinkItem {
  id: string;
  url: string;
  kind: LinkKind;
  title: string;
  note: string | null;
  source: "user" | "assistant" | "team";
  chat_id: string | null;
  run_id: string | null;
  project_id: string | null;
  /** Fetched from GitHub when saved or refreshed; null for other kinds. */
  preview: LinkPreviewData | null;
  preview_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface LinkPreviewData {
  error?: string;
  // Repos
  description?: string | null;
  default_branch?: string | null;
  language?: string | null;
  stars?: number | null;
  private?: boolean | null;
  pushed_at?: string | null;
  topics?: string[];
  readme?: string;
  readme_truncated?: boolean;
  // Files and folders
  name?: string;
  size?: number;
  text?: string;
  truncated?: boolean;
  note?: string;
  entries?: { name: string; type: string; size: number }[];
  entries_total?: number;
  // Pull requests, issues and commits
  title?: string;
  state?: string;
  draft?: boolean;
  author?: string | null;
  head?: string | null;
  base?: string | null;
  additions?: number | null;
  deletions?: number | null;
  changed_files?: number | null;
  comments?: number | null;
  labels?: string[];
  body?: string;
  body_truncated?: boolean;
  message?: string;
  date?: string;
  files?: { name: string; status: string; additions: number; deletions: number }[];
}

export interface FileItem extends FileBrief {
  error: string | null;
  parser: string | null;
  chunk_count: number;
  char_count: number;
  chat_id: string | null;
  created_at: string;
}

export interface SearchHit {
  file: FileBrief;
  heading: string | null;
  snippet: string;
  score: number;
}

export type MemoryKind = "preference" | "fact" | "goal" | "instruction";

export interface Memory {
  id: string;
  kind: MemoryKind;
  content: string;
  confidence: number;
  pinned: boolean;
  source: "assistant" | "user";
  source_chat_id: string | null;
  created_at: string;
  updated_at: string;
  reinforced_at: string;
}

export interface MemoryEvent {
  op: string;
  actor: string;
  content: string | null;
  message_id: string | null;
  created_at: string;
}

export interface Profile {
  content: string;
  memory_enabled: boolean;
  built_at: string | null;
  stale: boolean;
  memory_count: number;
}

export interface Message {
  id: string;
  chat_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status: "streaming" | "complete" | "failed";
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  trace_id: string | null;
  error: string | null;
  created_at: string;
  attachments?: FileBrief[];
  /** Links Nexus or a team saved for this reply (a repo, an app, a Google Doc). */
  links?: LinkItem[];
  /** Tools Nexus used for this reply, in order. */
  steps?: ToolStep[] | null;
}

export interface ToolStep {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  status: "running" | "done" | "failed";
  ms?: number | null;
  summary?: string;
}

/** One line of a team run's live activity: an agent starting or finishing, or a tool call. */
export interface FeedItem extends ToolStep {
  kind: "tool" | "agent";
  agent: string | null;
  at: string;
  /** Read from the stored timeline, which records calls but not their end. */
  stored?: boolean;
}

// --- Skills -----------------------------------------------------------------------------

export type SkillScope = "core" | "domain" | "personal" | "learned";
export type SkillStatus = "draft" | "active" | "disabled";

export interface Skill {
  id: string;
  slug: string;
  scope: SkillScope;
  name: string;
  description: string;
  category: string;
  version: number;
  status: SkillStatus;
  source: string | null;
  owner_id: string | null;
  uses: number;
  successes: number;
  failures: number;
  flagged: boolean;
  files: string[];
  last_used_at: string | null;
  updated_at: string;
  can_edit: boolean;
}

export interface SkillDetail extends Skill {
  content: string;
  file_contents: Record<string, string>;
  recent_uses?: { run_id: string | null; agent: string; outcome: string | null; at: string }[];
}

export interface SkillVersion {
  version: number;
  note: string | null;
  content: string;
  files: string[];
  created_at: string;
  created_by: string | null;
}

export interface Instructions {
  safety: string;
  shared: string;
  global: string;
  roles: { role: string; shipped: string; extra: string }[];
  user: string;
}

// --- Agent systems ------------------------------------------------------------------------

export type Topology =
  | "single"
  | "pipeline"
  | "supervisor_workers"
  | "parallel_map_reduce"
  | "critic_loop"
  | "dev_project";
export type Tier = "nano" | "mini" | "pro" | "max";
export type SystemStatus = "idle" | "running" | "awaiting_approval" | "paused" | "archived";
export type RunStatus =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export interface AgentNode {
  id: string;
  name: string;
  role: string;
  tier: Tier;
  lead: boolean;
  /** dev_project only: Brief, Plan, Build, Test or Review. */
  stage?: string;
}

export interface AgentEdge {
  from: string;
  to: string;
  kind: "handoff" | "delegate" | "map" | "reduce" | "review" | "feedback";
}

export interface AgentLive {
  status: string;
  tier?: Tier;
  steps?: number;
  tool?: string;
  item?: string;
}

export interface Run {
  id: string;
  system_id: string;
  chat_id: string | null;
  status: RunStatus;
  input: string;
  budget_usd: number;
  cost_usd: number;
  estimate_usd: number | null;
  input_tokens: number;
  output_tokens: number;
  steps: number;
  agents: Record<string, AgentLive>;
  output: string | null;
  error: string | null;
  trace_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AgentSystem {
  id: string;
  name: string;
  goal: string;
  topology: Topology;
  lifetime: "ephemeral" | "persistent" | "template";
  status: SystemStatus;
  chat_id: string | null;
  template_id: string | null;
  nodes: AgentNode[];
  edges: AgentEdge[];
  created_at: string;
  updated_at: string;
  run?: Run;
}

export interface AgentSpecJson {
  id: string;
  name: string;
  role: string;
  instructions: string;
  tier: Tier;
  tools: string[];
  skills: string[];
  max_steps: number | null;
}

export interface SystemDetail extends AgentSystem {
  spec: {
    name: string;
    goal: string;
    topology: Topology;
    agents: AgentSpecJson[];
    deliverables: string[];
    max_rounds: number;
    approval: { required_for: string[] };
    budget_usd: number | null;
  };
  runs: Run[];
}

export interface Approval {
  id: string;
  run_id: string;
  system_id: string;
  chat_id: string | null;
  agent: string;
  tool: string;
  args: Record<string, unknown>;
  reason: string | null;
  status: "pending" | "approved" | "rejected";
  decided_args: Record<string, unknown> | null;
  note: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface RunEventItem {
  type: string;
  agent: string | null;
  data: Record<string, unknown>;
  at: string;
}

export interface RunDetail {
  run: Run;
  system: AgentSystem;
  events: RunEventItem[];
  approvals: Approval[];
  files: { id: string; name: string; content_type: string; size: number; version: number }[];
}

export interface ToolEntry {
  name: string;
  title: string;
  risk: "read" | "write" | "exec" | "outbound" | "destructive";
  description: string;
  allowed: boolean;
  asks_first: boolean;
}

export interface Grants {
  granted: string[];
  effective: string[];
}

type RunScoped = { system_id: string; run_id: string; agent?: string };

export type ServerEvent =
  | { type: "assistant.state"; chat_id: string | null; data: { state: AssistantState } }
  | { type: "message.delta"; chat_id: string; data: { message_id: string; delta: string } }
  | { type: "message.completed"; chat_id: string; data: { message: Message } }
  | { type: "message.failed"; chat_id: string; data: { message: Message } }
  | { type: "chat.updated"; chat_id: string; data: { chat: Chat } }
  | {
      type: "tool.call";
      chat_id: string | null;
      data: { name: string; call_id?: string | null; agent?: string; run_id?: string; [arg: string]: unknown };
    }
  | {
      type: "tool.done";
      chat_id: string | null;
      data: { name: string; call_id: string; ok: boolean; ms: number; summary: string; agent?: string; run_id?: string };
    }
  | { type: "file.updated"; chat_id?: null; data: { file: FileItem } }
  | { type: "memory.updated"; chat_id?: null; data: { op: string; memory: Memory | { id: string } } }
  | { type: "profile.updated"; chat_id?: null; data: { content: string } }
  | { type: "system.created" | "system.updated"; chat_id?: string | null; data: { system: AgentSystem } }
  | { type: "system.deleted"; chat_id?: string | null; data: { system: { id: string } } }
  | { type: "run.updated"; chat_id?: string | null; data: { run: Run } }
  | { type: "agent.status"; chat_id?: string | null; data: RunScoped & AgentLive & { agent: string } }
  | { type: "approval.requested"; chat_id?: string | null; data: RunScoped & { approval: Approval } }
  | { type: "approval.resolved"; chat_id?: string | null; data: { approval: Approval; run_continues: boolean } }
  | { type: "artifact.created"; chat_id?: string | null; data: { file: FileItem } }
  | { type: "skill.updated"; chat_id?: null; data: { skill: Skill } }
  | { type: "link.updated"; chat_id?: null; data: { link: LinkItem } }
  | { type: "link.deleted"; chat_id?: null; data: { id: string } }
  | { type: "file.deleted"; chat_id?: null; data: { id: string } }
  | { type: "access.pending"; chat_id?: null; data: { count: number } }
  | { type: "pong"; chat_id?: null; data?: undefined };

export const ROLE_RANK: Record<Role, number> = { viewer: 10, member: 20, admin: 30, owner: 40 };

export function hasRole(user: Pick<User, "role">, minimum: Role): boolean {
  return ROLE_RANK[user.role] >= ROLE_RANK[minimum];
}

/** The Admin page is the owner's alone; admins manage people but don't see it. */
export function isOwner(user: Pick<User, "role">): boolean {
  return user.role === "owner";
}

export interface SessionInfo {
  id: string;
  created_at: string;
  expires_at: string;
  user_agent: string | null;
  ip: string | null;
  current: boolean;
}

export interface ServiceHealth {
  name: string;
  ok: boolean;
  detail: string;
}

export interface Backup {
  id: string;
  kind: "scheduled" | "manual";
  status: "requested" | "running" | "ok" | "failed";
  name: string | null;
  size_bytes: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface TrafficMinute {
  minute: string;
  requests: number;
  errors: number;
  limited: number;
  avg_ms: number | null;
}

export interface OpsStatus {
  services: ServiceHealth[];
  queues: { orchestrator: number | null; documents: number | null };
  traffic: TrafficMinute[];
  people: { active: number; invited: number; disabled: number; signed_in_24h: number };
  last_24h: {
    turns: number;
    failed_turns: number;
    input_tokens: number;
    output_tokens: number;
    failed_runs: number;
    run_cost_usd: number;
  };
  runs_live: number;
  files: { processing: number; failed: number };
  spend: { period_usd: number; budgets_usd: number; unknown: number };
  backup: { last_ok: Backup | null; latest: Backup | null; stale: boolean };
  maintenance: string | null;
  failures: { kind: "turn" | "run" | "file"; at: string; error: string; user: string }[];
  links: { langfuse: string };
}

export interface UsagePerson {
  user_id: string;
  email: string;
  name: string | null;
  turns: number;
  failed_turns: number;
  input_tokens: number;
  output_tokens: number;
  last_active: string | null;
  runs: number;
  run_cost_usd: number;
  files: number;
}

export interface Usage {
  days: number;
  people: UsagePerson[];
  series: { day: string; turns: number; failed_turns: number; tokens: number; run_cost_usd: number }[];
}

export interface AuditEntry {
  id: string;
  at: string;
  action: string;
  actor: string | null;
  target: string | null;
  target_user?: string | null;
  data: Record<string, unknown>;
}

/** One route or job on the Admin Monitoring tab. Latencies are histogram edges (p95 at or
 * under that many ms); null means slower than the last edge, or no calls. */
export interface MonitorRow {
  name: string;
  count: number;
  failed: number;
  avg_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  avg_wait_ms?: number | null;
}

export interface AlertItem {
  key: string;
  title: string;
  detail?: string;
  since?: string;
  state?: "raised" | "resolved";
  at?: string;
}

export interface LoggedError {
  ts: string;
  service: string;
  level: string;
  logger: string;
  msg: string;
  error?: string;
  stack?: string;
  page?: string;
  request_id?: string;
  user_id?: string;
  chat_id?: string;
  run_id?: string;
  job?: string;
  trace_id?: string;
}

export interface Monitoring {
  minutes: number;
  alerts: { active: AlertItem[]; recent: AlertItem[] };
  routes: MonitorRow[];
  jobs: MonitorRow[];
  errors: LoggedError[];
}

export type ConfigValue = string | number | boolean;

/** One runtime setting on the Admin page's Configuration tab. */
export interface ConfigOption {
  name: string;
  env: string;
  group: string;
  label: string;
  help: string;
  kind: "bool" | "int" | "float" | "text" | "choice" | "list";
  choices: string[];
  min: number | null;
  max: number | null;
  unit: string;
  /** Services that read it only at start-up. */
  restart: string[];
  value: ConfigValue;
  /** The value from .env, used when not overridden. */
  default: ConfigValue;
  overridden: boolean;
  waiting_for_restart: string[];
  restart_command: string | null;
}

export interface ConfigService {
  service: string;
  instance: string;
  version: number;
  pending_restart: string[];
  applied_at: string | null;
}

export interface ConfigView {
  groups: string[];
  options: ConfigOption[];
  version: number;
  updated_at: string | null;
  updated_by: string | null;
  services: ConfigService[];
}

/** Model-call figures for one group (or the whole window) on the Admin Models tab. */
export interface LlmFigures {
  calls: number;
  failed: number;
  cache_hits: number;
  cache_hit_rate: number | null;
  spend_usd: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cached_share: number | null;
  avg_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p50_first_token_ms: number | null;
  saved_usd: number;
}

export interface LlmGroup extends LlmFigures {
  key: string | null;
  email?: string | null;
  name?: string | null;
}

export interface ValkeyCache {
  hits: number;
  misses: number;
  hit_rate: number | null;
  evicted: number;
  memory: string | null;
  llm_cached_responses: number;
}

export interface LlmUsage {
  available: boolean;
  reason: string | null;
  since: string;
  until: string;
  valkey: ValkeyCache | null;
  failures_tracked?: boolean;
  unit?: "hour" | "day";
  totals?: LlmFigures;
  by_model?: LlmGroup[];
  by_user?: LlmGroup[];
  by_purpose?: LlmGroup[];
  series?: {
    at: string;
    calls: number;
    failed: number;
    cache_hits: number;
    spend_usd: number;
    tokens: number;
    p95_ms: number | null;
  }[];
  failures?: {
    at: string;
    model: string;
    user_id: string | null;
    user: string | null;
    purpose: string;
    error: string;
  }[];
  models?: string[];
  purposes?: string[];
  purpose_labels?: Record<string, string>;
}

export interface LlmQuery {
  since?: string;
  until?: string;
  userIds: string[];
  models: string[];
  purposes: string[];
}

export interface AlertRule {
  name: string;
  what: string;
  setting: string | null;
  threshold: number | null;
  on: boolean;
}

export interface Alerts {
  active: AlertItem[];
  recent: AlertItem[];
  rules: AlertRule[];
  interval_s: number;
  email: boolean;
}

export type ProposalKind = "skill" | "instruction" | "setting" | "tool" | "code";
export type ProposalStatus = "pending" | "applying" | "applied" | "denied" | "failed" | "reverted";

export interface Proposal {
  id: string;
  review_id: string | null;
  kind: ProposalKind;
  status: ProposalStatus;
  title: string;
  summary: string;
  evidence: string[];
  impact: "low" | "medium" | "high";
  effort: "low" | "medium" | "high";
  payload: {
    name?: string;
    description?: string;
    content?: string;
    layer?: "global" | "role";
    role?: string;
    text?: string;
    label?: string;
    value?: string | number | boolean;
    spec?: string;
    files?: string[];
  };
  note: string | null;
  result: {
    skill_id?: string;
    skill_slug?: string;
    pr_url?: string;
    branch?: string;
    spec_only?: boolean;
    before?: unknown;
  };
  error: string | null;
  created_at: string;
  decided_at: string | null;
}

/** What a self-review saw: counts and anonymous topics, never names or chat text. */
export interface ReviewFindings {
  activity?: {
    turns: number;
    failed_turns: number;
    people: number;
    team_runs: number;
    failed_team_runs: number;
    team_cost_usd: number;
    teams_designed: number;
  };
  tools?: { tool: string; calls: number; failed: number; avg_ms: number | null }[];
  failures?: { pattern: string; count: number }[];
  teams?: {
    roles: { role: string; count: number }[];
    tools: { tool: string; count: number }[];
    topologies: { topology: string; count: number }[];
  };
  skills?: {
    used: { skill: string; uses: number; failed: number }[];
    unused: string[];
    learned_drafts_waiting: number;
  };
  topics?: { topic: string; count: number }[];
  gaps?: { gap: string; count: number }[];
  requests_read?: number;
}

export interface Review {
  id: string;
  status: "running" | "done" | "failed";
  trigger: "schedule" | "manual";
  window_start: string;
  window_end: string;
  findings: ReviewFindings;
  proposals: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Improvements {
  proposals: Proposal[];
  counts: Record<ProposalStatus, number>;
  reviews: Review[];
  schedule: {
    every_days: number;
    next_at: string | null;
    read_requests: boolean;
    min_count: number;
    model: string;
    topic_model: string;
  };
  pull_requests: { repo: string | null; ready: boolean };
}

/** One run of the recall check on the Admin page's Recall tab. */
export interface RecallScore {
  n: number;
  hits: number;
  hit_rate: number;
  mrr: number;
}

export interface RecallMiss {
  question: string;
  expected: string;
  target: "memory" | "past chat";
  /** The other modes that did find it. */
  found_by: string[];
}

export interface RecallCheck {
  id: string;
  status: "running" | "done" | "failed";
  scores: Record<string, RecallScore>;
  misses: RecallMiss[];
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface RecallSummary {
  questions: number;
  memories: number;
  past_chats: number;
  checks: RecallCheck[];
}
