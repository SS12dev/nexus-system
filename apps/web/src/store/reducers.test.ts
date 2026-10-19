import { describe, expect, it } from "vitest";
import type { Message } from "../lib/types";
import { addMessages, applyEvent, initialState, mergeFetched } from "./reducers";

const msg = (over: Partial<Message> = {}): Message => ({
  id: "m1",
  chat_id: "c1",
  role: "assistant",
  content: "",
  status: "streaming",
  model: "jarvis-mini",
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

  it("tracks Jarvis's state", () => {
    const s = applyEvent(initialState, { type: "jarvis.state", chat_id: "c1", data: { state: "thinking" } });
    expect(s.jarvis).toBe("thinking");
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

  it("shows what Jarvis is doing until the reply finishes, and keeps attachments", () => {
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
});
