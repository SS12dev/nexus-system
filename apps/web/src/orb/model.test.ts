import { describe, expect, it } from "vitest";
import type { AgentSystem, Approval, FileItem, Memory } from "../lib/types";
import {
  agentTargets,
  buildClusters,
  cameraDistance,
  clusterAnchor,
  crystals,
  liveSystems,
  memoryPoints,
  ORB_R,
} from "./model";

const sys = (over: Partial<AgentSystem> = {}): AgentSystem => ({
  id: "s1",
  name: "Research team",
  goal: "g",
  topology: "supervisor_workers",
  lifetime: "ephemeral",
  status: "running",
  chat_id: null,
  template_id: null,
  nodes: [
    { id: "w1", name: "Searcher", role: "researcher", tier: "mini", lead: false },
    { id: "lead", name: "Lead", role: "planner", tier: "pro", lead: true },
    { id: "w2", name: "Writer", role: "writer", tier: "mini", lead: false },
  ],
  edges: [
    { from: "lead", to: "w1", kind: "delegate" },
    { from: "lead", to: "w2", kind: "delegate" },
  ],
  created_at: "2026-10-20T00:00:00Z",
  updated_at: "2026-10-20T00:00:00Z",
  ...over,
});

const mem = (id: string, over: Partial<Memory> = {}): Memory => ({
  id,
  kind: "preference",
  content: `memory ${id}`,
  confidence: 0.9,
  pinned: false,
  source: "jarvis",
  source_chat_id: null,
  created_at: "2026-10-20T00:00:00Z",
  updated_at: "2026-10-20T00:00:00Z",
  reinforced_at: "2026-10-20T00:00:00Z",
  ...over,
});

const dist = (p: number[]) => Math.hypot(p[0], p[1], p[2]);

describe("liveSystems", () => {
  it("leaves archived systems out and keeps the oldest first", () => {
    const list = liveSystems({
      b: sys({ id: "b", created_at: "2026-10-20T02:00:00Z" }),
      a: sys({ id: "a", created_at: "2026-10-20T01:00:00Z" }),
      z: sys({ id: "z", status: "archived" }),
    });
    expect(list.map((s) => s.id)).toEqual(["a", "b"]);
  });
});

describe("layout", () => {
  it("puts every team outside the orb, apart from each other", () => {
    const anchors = Array.from({ length: 8 }, (_, i) => clusterAnchor(i, 8));
    for (const a of anchors) expect(dist(a)).toBeGreaterThan(ORB_R * 2);
    for (let i = 0; i < anchors.length; i++)
      for (let j = i + 1; j < anchors.length; j++) {
        const d = dist([0, 1, 2].map((k) => anchors[i][k] - anchors[j][k]));
        expect(d).toBeGreaterThan(0.9);
      }
  });

  it("places the lead at the anchor and workers around it", () => {
    const anchor = clusterAnchor(0, 1);
    const t = agentTargets(anchor, 4, "supervisor_workers");
    expect(t[0]).toEqual(anchor);
    expect(t).toHaveLength(4);
    for (const p of t.slice(1)) expect(dist([0, 1, 2].map((k) => p[k] - anchor[k]))).toBeGreaterThan(0.4);
  });
});

describe("buildClusters", () => {
  it("hangs workers from the lead and the lead from the orb", () => {
    const [c] = buildClusters([sys()]);
    expect(c.nodes[0]).toMatchObject({ agentId: "lead", lead: true, parent: null });
    expect(c.nodes.slice(1).every((n) => n.parent === "s1:lead")).toBe(true);
    expect(c.links).toHaveLength(2);
  });

  it("chains a pipeline's agents one after another", () => {
    const [c] = buildClusters([
      sys({
        topology: "pipeline",
        nodes: [
          { id: "a", name: "A", role: "r", tier: "mini", lead: true },
          { id: "b", name: "B", role: "r", tier: "mini", lead: false },
          { id: "c", name: "C", role: "r", tier: "mini", lead: false },
        ],
      }),
    ]);
    expect(c.nodes.map((n) => n.parent)).toEqual([null, "s1:a", "s1:b"]);
  });

  it("colours agents from the live run, and a pending approval wins", () => {
    const run = {
      id: "r1",
      system_id: "s1",
      status: "awaiting_approval",
      agents: { lead: { status: "thinking" }, w1: { status: "working", tool: "web_search" } },
    } as unknown as AgentSystem["run"];
    const approval = { system_id: "s1", agent: "w2", status: "pending" } as Approval;
    const [c] = buildClusters([sys({ run })], [approval]);
    const by = Object.fromEntries(c.nodes.map((n) => [n.agentId, n]));
    expect(by.lead.status).toBe("thinking");
    expect(by.w1).toMatchObject({ status: "working", tool: "web_search" });
    expect(by.w2.status).toBe("waiting");
  });

  it("shows agents of a finished run as done or idle, not still working", () => {
    const run = {
      status: "completed",
      agents: { lead: { status: "done" }, w1: { status: "working" } },
    } as unknown as AgentSystem["run"];
    const [c] = buildClusters([sys({ run })]);
    const by = Object.fromEntries(c.nodes.map((n) => [n.agentId, n.status]));
    expect(by).toEqual({ lead: "done", w1: "idle", w2: "idle" });
  });
});

describe("inside the orb", () => {
  it("keeps memories inside the shell, in the same place every time", () => {
    const list = Array.from({ length: 200 }, (_, i) => mem(`m${i}`, { kind: (["preference", "fact", "goal", "instruction"] as const)[i % 4] }));
    const a = memoryPoints(list, 600);
    const b = memoryPoints(list, 600);
    expect(a).toEqual(b);
    for (const p of a) expect(dist(p.pos)).toBeLessThanOrEqual(0.82 * ORB_R + 1e-9);
  });

  it("keeps pinned memories first when over the device's limit", () => {
    const list = [mem("a"), mem("b", { pinned: true }), mem("c")];
    expect(memoryPoints(list, 1).map((p) => p.id)).toEqual(["b"]);
  });

  it("shows only ready generated files as crystals", () => {
    const f = (id: string, over: Partial<FileItem>) =>
      ({ id, name: id, content_type: "x", status: "ready", origin: "generated", created_at: "2026-10-20T00:00:00Z", ...over }) as FileItem;
    const out = crystals([f("a", {}), f("b", { origin: "upload" }), f("c", { status: "processing" })], 10);
    expect(out.map((c) => c.id)).toEqual(["a"]);
    expect(dist(out[0].pos)).toBeLessThan(ORB_R);
  });
});

describe("cameraDistance", () => {
  it("steps back as teams are added and further on a portrait phone", () => {
    expect(cameraDistance(4, 1.6)).toBeGreaterThan(cameraDistance(0, 1.6));
    expect(cameraDistance(4, 0.5)).toBeGreaterThan(cameraDistance(4, 1.6));
    // A tighter band on portrait screens keeps the phone camera closer.
    expect(cameraDistance(4, 0.5, true)).toBeLessThan(cameraDistance(4, 0.5));
  });
});
