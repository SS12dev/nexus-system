export type Role = "owner" | "admin" | "member" | "viewer";
export type UserStatus = "invited" | "active" | "disabled";
export type JarvisState = "idle" | "thinking" | "speaking";

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
  chat_id: string | null;
  updated_at: string;
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
  source: "jarvis" | "user";
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
  /** Tools Jarvis used for this reply, in order. */
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
  | { type: "jarvis.state"; chat_id: string | null; data: { state: JarvisState } }
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
  | { type: "file.deleted"; chat_id?: null; data: { id: string } }
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
