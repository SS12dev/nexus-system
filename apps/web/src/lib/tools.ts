import type { AgentLive, AgentNode } from "./types";

/** How tools read in the UI: what Nexus or an agent is doing while one runs. */
export const TOOL_LABEL: Record<string, string> = {
  search_documents: "Searching your documents",
  read_document: "Reading a document",
  list_documents: "Looking through your library",
  delete_document: "Deleting a document",
  search_memory: "Remembering",
  remember: "Saving to memory",
  web_search: "Searching the web",
  web_fetch: "Reading a web page",
  run_code: "Running code",
  ws_tree: "Looking through the project",
  ws_read: "Reading a project file",
  ws_write: "Writing a project file",
  ws_edit: "Editing a project file",
  ws_run: "Running a command",
  ws_diff: "Checking the changes",
  ws_commit: "Committing the changes",
  save_file: "Saving a file",
  save_link: "Saving a link",
  send_email: "Sending an email",
  gmail_search: "Searching your email",
  gmail_read: "Reading an email",
  gmail_draft: "Drafting an email",
  gmail_update_labels: "Tidying your inbox",
  gmail_send: "Sending an email",
  calendar_list_events: "Checking your calendar",
  calendar_create_event: "Adding to your calendar",
  calendar_update_event: "Updating your calendar",
  calendar_invite: "Sending an invitation",
  calendar_delete_event: "Removing a calendar event",
  contacts_search: "Looking up a contact",
  tasks_list: "Checking your tasks",
  tasks_add: "Adding a task",
  tasks_complete: "Ticking off a task",
  skills_load: "Loading a skill",
  skills_read_file: "Reading a skill",
  skills_search: "Looking for a skill",
  present_result: "Picking the result to show you",
  view_image: "Looking at the picture",
  start_timer: "Starting a timer",
  set_alarm: "Setting an alarm",
  remind_me: "Setting a reminder",
  add_widget: "Adding a widget",
  list_widgets: "Checking your widgets",
  control_widget: "Changing a widget",
  save_widget: "Saving a widget",
  screen: "Changing your screen",
  build_agent_system: "Designing a team of agents",
  update_agent_system: "Redesigning the team",
  run_agent_system: "Starting the team",
  list_agent_systems: "Checking your teams",
  save_as_template: "Saving the team as a template",
  delete_agent_system: "Removing a team",
  control_agent_run: "Steering a team",
};

export function toolLabel(name: string): string {
  return TOOL_LABEL[name] ?? name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

/** Argument keys worth showing next to a step, best first. */
const DETAIL_KEYS = [
  "query", "file", "url", "skill", "name", "path", "command", "message", "subject", "to", "title", "item",
];

/** The one argument that says what a step was about, such as the search query. */
export function stepDetail(args: Record<string, unknown> | undefined): string {
  if (!args) return "";
  for (const k of DETAIL_KEYS) {
    const v = args[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return "";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

/** What an agent in a team is doing, by its live status. */
export const AGENT_LABEL: Record<string, string> = {
  idle: "Idle",
  queued: "Starting",
  running: "Working",
  thinking: "Thinking",
  working: "Using a tool",
  waiting: "Waiting for your approval",
  paused: "Paused",
  done: "Done",
  failed: "Failed",
  skipped: "Not needed",
};

const BUSY = ["running", "thinking", "working", "waiting"];

/** A software team's current stage, from whichever agent is busy, e.g. "Build · round 2". */
export function currentStage(nodes: AgentNode[], live: Record<string, AgentLive> = {}): string {
  const busy = nodes.find((n) => n.stage && BUSY.includes(live[n.id]?.status ?? ""));
  if (!busy) return "";
  const item = live[busy.id]?.item;
  return item ? `${busy.stage} · ${item}` : (busy.stage ?? "");
}
