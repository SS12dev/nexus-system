import { describe, expect, it } from "vitest";
import { feedFromEvents } from "./feed";
import type { Run } from "./types";

const run = (agents: Run["agents"]): Run => ({
  id: "r1",
  system_id: "s1",
  chat_id: "c1",
  status: "running",
  input: "",
  budget_usd: 1,
  cost_usd: 0,
  estimate_usd: null,
  input_tokens: 0,
  output_tokens: 0,
  steps: 0,
  agents,
  output: null,
  error: null,
  trace_id: null,
  created_at: "2026-10-20T00:00:00Z",
  started_at: null,
  finished_at: null,
});

describe("feedFromEvents", () => {
  it("turns a stored timeline into feed lines, with a busy agent's last call still running", () => {
    const at = "2026-10-20T00:00:01Z";
    const items = feedFromEvents(
      [
        { type: "agent.status", agent: "a", data: { status: "running" }, at },
        { type: "tool.call", agent: "a", data: { name: "web_search", call_id: "x", query: "q" }, at },
        { type: "approval.requested", agent: "a", data: {}, at },
        { type: "tool.call", agent: "a", data: { name: "web_fetch", url: "https://e.x" }, at },
      ],
      run({ a: { status: "working", tool: "web_fetch" } }),
    );
    expect(items.map((i) => [i.kind, i.name, i.status])).toEqual([
      ["agent", "running", "running"],
      ["tool", "web_search", "done"],
      ["tool", "web_fetch", "running"],
    ]);
    expect(items[1]).toMatchObject({ id: "x", args: { query: "q" } });
  });
});
