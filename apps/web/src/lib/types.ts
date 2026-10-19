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
  role: Role;
  status: UserStatus;
  created_at: string;
  last_login_at: string | null;
  budget: Budget;
}

export interface Chat {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
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
}

export type ServerEvent =
  | { type: "jarvis.state"; chat_id: string | null; data: { state: JarvisState } }
  | { type: "message.delta"; chat_id: string; data: { message_id: string; delta: string } }
  | { type: "message.completed"; chat_id: string; data: { message: Message } }
  | { type: "message.failed"; chat_id: string; data: { message: Message } }
  | { type: "chat.updated"; chat_id: string; data: { chat: Chat } }
  | { type: "pong"; chat_id?: null; data?: undefined };

export const ROLE_RANK: Record<Role, number> = { viewer: 10, member: 20, admin: 30, owner: 40 };

export function hasRole(user: Pick<User, "role">, minimum: Role): boolean {
  return ROLE_RANK[user.role] >= ROLE_RANK[minimum];
}
