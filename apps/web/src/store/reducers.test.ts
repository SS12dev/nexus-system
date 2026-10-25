import { describe, expect, it } from "vitest";
import type { AgentSystem, FileItem, Message, Notice } from "../lib/types";
import {
  addMessages,
  applyEvent,
  initialState,
  markRead,
  mergeFetched,
  syncFiles,
  seedRunFeed,
  syncLiveSystems,
  upsertSystems,
} from "./reducers";

const msg = (over: Partial<Message> = {}): Message => ({
  id: "m1",
  chat_id: "c1",
  role: "assistant",
  content: "",
  status: "streaming",
  model: "nexus-mini",
  input_tokens: null,
  output_tokens: null,
  trace_id: null,
  error: null,
  created_at: "2026-10-19T00:00:00Z",
  ...over,
});

describe("applyEvent", () => {
  it("streams deltas into the open message and then replaces it when complete", () => {
    let s = addMessages(initialState, "c1", [msg()]);
    s = applyEvent(s, { type: "message.delta", chat_id: "c1", data: { message_id: "m1", delta: "Hel" } });
    s = applyEvent(s, { type: "message.delta", chat_id: "c1", data: { message_id: "m1", delta: "lo" } });
    expect(s.messages.c1[0].content).toBe("Hello");

    s = applyEvent(s, {
      type: "message.completed",
      chat_id: "c1",
      data: { message: msg({ content: "Hello!", status: "complete" }) },
    });
    expect(s.messages.c1).toHaveLength(1);
    expect(s.messages.c1[0]).toMatchObject({ content: "Hello!", status: "complete" });
  });

  it("ignores deltas for chats that are not loaded", () => {
    const s = applyEvent(initialState, {
      type: "message.delta",
      chat_id: "other",
      data: { message_id: "x", delta: "hi" },
    });
    expect(s).toBe(initialState);
  });

  it("tracks Nexus's state", () => {
    const s = applyEvent(initialState, { type: "assistant.state", chat_id: "c1", data: { state: "thinking" } });
    expect(s.assistantState).toBe("thinking");
  });
});

describe("mergeFetched", () => {
  it("keeps streamed text and locally added messages", () => {
    let s = addMessages(initialState, "c1", [msg({ content: "partial" }), msg({ id: "m2" })]);
    s = mergeFetched(s, "c1", [msg({ content: "" })]);
    expect(s.messages.c1.map((m) => [m.id, m.content])).toEqual([
      ["m1", "partial"],
      ["m2", ""],
    ]);
  });
});

describe("phase 2 events", () => {
  const file = {
    id: "f1",
    name: "sales.xlsx",
    content_type: "application/vnd.ms-excel",
    size: 10,
    status: "ready" as const,
    error: null,
    parser: "markitdown",
    chunk_count: 1,
    char_count: 10,
    chat_id: "c1",
    created_at: "2026-10-19T00:00:00Z",
  };

  it("updates a file's status everywhere it is shown", () => {
    let s = addMessages(initialState, "c1", [
      msg({ id: "u1", role: "user", attachments: [{ ...file, status: "processing" }] }),
    ]);
    s = applyEvent(s, { type: "file.updated", data: { file } });
    expect(s.files.f1.status).toBe("ready");
    expect(s.messages.c1[0].attachments?.[0].status).toBe("ready");
  });

  it("shows what Nexus is doing until the reply finishes, and keeps attachments", () => {
    let s = addMessages(initialState, "c1", [msg({ attachments: [file] })]);
    s = applyEvent(s, { type: "tool.call", chat_id: "c1", data: { name: "search_documents" } });
    expect(s.activity).toBe("Searching your documents");
    s = applyEvent(s, {
      type: "message.completed",
      chat_id: "c1",
      data: { message: msg({ content: "Done", status: "complete" }) },
    });
    expect(s.activity).toBeNull();
    expect(s.messages.c1[0].attachments).toHaveLength(1);
  });

  it("counts memory changes so the Memory page can refresh", () => {
    const s = applyEvent(initialState, {
      type: "memory.updated",
      data: { op: "add", memory: { id: "m1" } },
    });
    expect(s.memoryVersion).toBe(1);
  });

  it("puts what a reply remembered on it and lights it in the orb, through to the stored reply", () => {
    const recalled = { memories: ["m1"], entities: [{ id: "e1", name: "Nexus", type: "project" as const }] };
    let s = addMessages(initialState, "c1", [msg()]);
    s = applyEvent(s, { type: "memory.recalled", chat_id: "c1", data: recalled });
    expect(s.messages.c1[0].recalled).toEqual(recalled);
    expect(s.recall).toEqual({ ...recalled, seq: 1 });
    s = applyEvent(s, {
      type: "message.completed",
      chat_id: "c1",
      data: { message: msg({ content: "Done", status: "complete" }) },
    });
    expect(s.messages.c1[0].recalled).toEqual(recalled);
    // Lighting it again replays it.
    s = applyEvent(s, { type: "memory.recalled", chat_id: "c2", data: recalled });
    expect(s.recall?.seq).toBe(2);
  });
});

describe("agent systems", () => {
  const run = {
    id: "r1",
    system_id: "s1",
    chat_id: "c1",
    status: "running" as const,
    input: "go",
    budget_usd: 0.5,
    cost_usd: 0,
    estimate_usd: 0.1,
    input_tokens: 0,
    output_tokens: 0,
    steps: 0,
    agents: { writer: { status: "idle" } },
    output: null,
    error: null,
    trace_id: null,
    created_at: "2026-10-19T00:00:00Z",
    started_at: null,
    finished_at: null,
  };
  const system = {
    id: "s1",
    name: "Team",
    goal: "g",
    topology: "single" as const,
    lifetime: "ephemeral" as const,
    status: "running" as const,
    chat_id: "c1",
    template_id: null,
    nodes: [{ id: "writer", name: "Writer", role: "writer", tier: "mini" as const, lead: true }],
    edges: [],
    created_at: "2026-10-19T00:00:00Z",
    updated_at: "2026-10-19T00:00:00Z",
    run,
  };

  it("follows a system, its run and each agent's live status", () => {
    let s = applyEvent(initialState, { type: "system.created", chat_id: "c1", data: { system } });
    s = applyEvent(s, {
      type: "agent.status",
      chat_id: "c1",
      data: { system_id: "s1", run_id: "r1", agent: "writer", status: "working", tool: "run_code" },
    });
    expect(s.systems.s1.run?.agents.writer).toMatchObject({ status: "working", tool: "run_code" });
    // A system update without a run keeps the run already known.
    const { run: _r, ...bare } = system;
    s = applyEvent(s, { type: "system.updated", chat_id: "c1", data: { system: bare } });
    expect(s.systems.s1.run?.id).toBe("r1");
    s = applyEvent(s, {
      type: "run.updated",
      chat_id: "c1",
      data: { run: { ...run, status: "completed", cost_usd: 0.04 } },
    });
    expect(s.systems.s1.run).toMatchObject({ status: "completed", cost_usd: 0.04 });
    s = applyEvent(s, { type: "system.deleted", data: { system: { id: "s1" } } });
    expect(s.systems.s1).toBeUndefined();
  });

  it("keeps agent tool calls out of Nexus's own activity and logs them on the run", () => {
    let s = applyEvent(initialState, { type: "system.created", chat_id: "c1", data: { system } });
    s = applyEvent(s, {
      type: "agent.status",
      chat_id: "c1",
      data: { system_id: "s1", run_id: "r1", agent: "writer", status: "running" },
    });
    s = applyEvent(s, {
      type: "tool.call",
      chat_id: "c1",
      data: { name: "web_search", call_id: "t1", run_id: "r1", agent: "writer", query: "evals" },
    });
    expect(s.activity).toBeNull();
    s = applyEvent(s, {
      type: "tool.done",
      chat_id: "c1",
      data: { name: "web_search", call_id: "t1", ok: true, ms: 800, summary: "5 results", run_id: "r1", agent: "writer" },
    });
    expect(s.runFeed.r1.map((f) => [f.kind, f.name, f.status])).toEqual([
      ["agent", "running", "running"],
      ["tool", "web_search", "done"],
    ]);
    expect(s.runFeed.r1[1]).toMatchObject({ agent: "writer", args: { query: "evals" }, ms: 800, summary: "5 results" });
    // Thinking and tool-use statuses are live status, not feed lines.
    s = applyEvent(s, {
      type: "agent.status",
      chat_id: "c1",
      data: { system_id: "s1", run_id: "r1", agent: "writer", status: "thinking" },
    });
    expect(s.runFeed.r1).toHaveLength(2);
    // The stored timeline fills in what happened before the live events, without repeats.
    const old = { id: "t0", kind: "tool" as const, agent: "writer", name: "web_fetch", status: "running" as const, at: "2000-01-01T00:00:00Z" };
    const seeded = seedRunFeed(s, "r1", [old, { ...s.runFeed.r1[1], at: old.at }]);
    expect(seeded.runFeed.r1.map((f) => f.id)).toEqual(["t0", ...s.runFeed.r1.map((f) => f.id)]);
    // The writer has called a tool since, so the stored call is over.
    expect(seeded.runFeed.r1[0].status).toBe("done");
    expect(seedRunFeed(s, "r1", [])).toBe(s);
  });

  it("uses attachments carried by a completed message", () => {
    let s = addMessages(initialState, "c1", [msg()]);
    const file = { id: "f1", name: "report.docx", content_type: "x", size: 1, status: "ready" as const };
    s = applyEvent(s, {
      type: "message.completed",
      chat_id: "c1",
      data: { message: msg({ status: "complete", attachments: [file] }) },
    });
    expect(s.messages.c1[0].attachments).toEqual([file]);
  });
});

describe("orb data", () => {
  const system = (id: string, status: AgentSystem["status"] = "idle") =>
    ({ id, status, nodes: [], edges: [] }) as unknown as AgentSystem;
  const file = (id: string, status: FileItem["status"] = "ready") =>
    ({ id, name: id, status, origin: "generated" }) as FileItem;

  it("drops live systems a fresh list no longer has, keeping archived ones for chat cards", () => {
    let s = upsertSystems(initialState, [system("a"), system("b"), system("old", "archived")]);
    s = syncLiveSystems(s, [system("a")]);
    expect(Object.keys(s.systems).sort()).toEqual(["a", "old"]);
  });

  it("adds artifacts as they are made and removes deleted files", () => {
    let s = applyEvent(initialState, { type: "artifact.created", data: { file: file("f1") } });
    expect(s.files.f1).toBeDefined();
    s = applyEvent(s, { type: "file.deleted", data: { id: "f1" } });
    expect(s.files.f1).toBeUndefined();
  });

  it("syncs files but keeps uploads still being processed", () => {
    const current = { up: file("up", "processing"), gone: file("gone") };
    expect(Object.keys(syncFiles(current, [file("new")])).sort()).toEqual(["new", "up"]);
  });

  it("shows the tools Nexus uses on the reply it is writing, and keeps them when it completes", () => {
    let s = addMessages(initialState, "c1", [msg({ id: "u1", role: "user", status: "complete" }), msg()]);
    s = applyEvent(s, {
      type: "tool.call",
      chat_id: "c1",
      data: { name: "search_documents", call_id: "a", query: "Q3 target" },
    });
    expect(s.activity).toBe("Searching your documents");
    expect(s.messages.c1[1].steps).toEqual([
      { id: "a", name: "search_documents", args: { query: "Q3 target" }, status: "running" },
    ]);
    s = applyEvent(s, {
      type: "tool.done",
      chat_id: "c1",
      data: { name: "search_documents", call_id: "a", ok: false, ms: 12, summary: "boom" },
    });
    expect(s.messages.c1[1].steps?.[0]).toMatchObject({ status: "failed", ms: 12, args: { query: "Q3 target" } });
    // An older server that sends no steps keeps the ones already shown.
    s = applyEvent(s, { type: "message.completed", chat_id: "c1", data: { message: msg({ status: "complete" }) } });
    expect(s.messages.c1[1].steps).toHaveLength(1);
  });
});

describe("access requests", () => {
  it("keeps the waiting count from the live event", () => {
    const state = applyEvent(initialState, { type: "access.pending", data: { count: 3 } });
    expect(state.pendingAccess).toBe(3);
    expect(applyEvent(state, { type: "access.pending", data: { count: 0 } }).pendingAccess).toBe(0);
  });
});

describe("notices", () => {
  const notice = (id: string, over: Partial<Notice> = {}): Notice => ({
    id,
    kind: "result",
    title: "Rocket team finished",
    body: "",
    chat_id: "c1",
    run_id: "r1",
    url: "/c/c1",
    file: null,
    link: null,
    read_at: null,
    created_at: "2026-10-25T00:00:00Z",
    ...over,
  });

  it("adds a live notice to the tray and the toasts once, and marks it fresh", () => {
    let s = applyEvent(initialState, { type: "notification.created", data: { notification: notice("n1") } });
    s = applyEvent(s, { type: "notification.created", data: { notification: notice("n1") } });
    expect(s.notices.map((n) => n.id)).toEqual(["n1"]);
    expect(s.unread).toBe(1);
    expect(s.toasts).toEqual(["n1"]);
    expect(s.freshNotice?.id).toBe("n1");
  });

  it("marks some or all read, here or from another device", () => {
    let s = initialState;
    for (const id of ["n1", "n2", "n3"])
      s = applyEvent(s, { type: "notification.created", data: { notification: notice(id) } });
    s = markRead(s, ["n2"]);
    expect(s.unread).toBe(2);
    expect(s.toasts).toEqual(["n1", "n3"]);
    s = markRead(s, ["n2"]);
    expect(s.unread).toBe(2);
    s = applyEvent(s, { type: "notification.read", data: { ids: null } });
    expect(s.unread).toBe(0);
    expect(s.toasts).toEqual([]);
    expect(s.notices.every((n) => n.read_at)).toBe(true);
  });
});
