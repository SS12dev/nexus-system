import { describe, expect, it } from "vitest";
import type { AgentSystem, Approval, FileItem, GraphEntity, KnowledgeGraph, Memory } from "../lib/types";
import {
  agentTargets,
  buildClusters,
  cameraDistance,
  clusterAnchor,
  crystals,
  inside,
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
  source: "assistant",
  source_chat_id: null,
  created_at: "2026-10-20T00:00:00Z",
  updated_at: "2026-10-20T00:00:00Z",
  reinforced_at: "2026-10-20T00:00:00Z",
  ...over,
});

const dist = (p: number[]) => Math.hypot(p[0], p[1], p[2]);

describe("liveSystems", () => {
  const run = (status: string) => ({ status }) as unknown as AgentSystem["run"];

  it("keeps teams with a run in progress, oldest first", () => {
    const list = liveSystems({
      b: sys({ id: "b", created_at: "2026-10-20T02:00:00Z", run: run("running") }),
      a: sys({ id: "a", created_at: "2026-10-20T01:00:00Z", run: run("awaiting_approval") }),
      p: sys({ id: "p", created_at: "2026-10-20T03:00:00Z", run: run("paused") }),
      z: sys({ id: "z", status: "archived", run: run("running") }),
    });
    expect(list.map((s) => s.id)).toEqual(["a", "b", "p"]);
  });

  it("leaves out saved teams whose run has finished, and teams that never ran", () => {
    const list = liveSystems({
      kept: sys({ id: "kept", lifetime: "persistent", status: "idle", run: run("completed") }),
      failed: sys({ id: "failed", lifetime: "template", status: "idle", run: run("failed") }),
      never: sys({ id: "never", lifetime: "template", status: "idle" }),
    });
    expect(list).toEqual([]);
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

  it("lays a team out as the chat card's diagram does, around its anchor", () => {
    const anchor = clusterAnchor(0, 1);
    const node = (id: string, stage: string) => ({ id, name: id, role: "r", tier: "mini" as const, lead: id === "re", stage });
    const t = agentTargets(
      anchor,
      [node("re", "Brief"), node("ar", "Plan"), node("fe", "Build"), node("be", "Build"), node("te", "Test"), node("rv", "Review")],
      "dev_project",
    );
    const d = (a: number[], b: number[]) => dist([0, 1, 2].map((k) => a[k] - b[k]));
    // The two builders share a column: equally far from the architect and the tester, apart
    // from each other; the stages run in a line through them.
    expect(d(t.fe, t.ar)).toBeCloseTo(d(t.be, t.ar));
    expect(d(t.fe, t.te)).toBeCloseTo(d(t.be, t.te));
    expect(d(t.fe, t.be)).toBeGreaterThan(0.4);
    expect(d(t.re, t.rv)).toBeCloseTo(d(t.re, t.ar) * 4);
    for (const p of Object.values(t)) expect(d(p, anchor)).toBeLessThan(2);
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
        edges: [
          { from: "a", to: "b", kind: "handoff" },
          { from: "b", to: "c", kind: "handoff" },
        ],
      }),
    ]);
    expect(c.nodes.map((n) => n.parent)).toEqual([null, "s1:a", "s1:b"]);
  });

  it("hangs a software team's agents from whoever hands them work", () => {
    const n = (id: string, stage: string) => ({ id, name: id, role: "r", tier: "mini" as const, lead: id === "researcher", stage });
    const [c] = buildClusters([
      sys({
        topology: "dev_project",
        nodes: [n("researcher", "Brief"), n("architect", "Plan"), n("frontend", "Build"),
          n("backend", "Build"), n("tester", "Test"), n("reviewer", "Review")],
        edges: [
          { from: "researcher", to: "architect", kind: "handoff" },
          { from: "architect", to: "frontend", kind: "handoff" },
          { from: "architect", to: "backend", kind: "handoff" },
          { from: "frontend", to: "tester", kind: "handoff" },
          { from: "backend", to: "tester", kind: "handoff" },
          { from: "tester", to: "reviewer", kind: "review" },
          { from: "reviewer", to: "frontend", kind: "feedback" },
          { from: "reviewer", to: "backend", kind: "feedback" },
        ],
      }),
    ]);
    const parent = Object.fromEntries(c.nodes.map((x) => [x.agentId, x.parent]));
    expect(parent).toEqual({
      researcher: null, architect: "s1:researcher", frontend: "s1:architect",
      backend: "s1:architect", tester: "s1:frontend", reviewer: "s1:tester",
    });
    expect(c.links).toHaveLength(8);
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

describe("the constellation", () => {
  const ent = (id: string, over: Partial<GraphEntity> = {}): GraphEntity => ({
    id,
    type: "project",
    name: id,
    aliases: [],
    summary: "",
    is_self: false,
    seen_at: "2026-10-20T00:00:00Z",
    memories: 1,
    past_chats: 0,
    files: 0,
    chats: 0,
    memory_ids: [],
    file_ids: [],
    ...over,
  });
  const file = (id: string, created_at = "2026-10-20T00:00:00Z") =>
    ({ id, name: id, content_type: "x", status: "ready", origin: "generated", created_at }) as FileItem;
  const limits = { stars: 40, memories: 600, crystals: 12 };
  const graph: KnowledgeGraph = {
    entities: [
      ent("me", { type: "person", name: "Shivam", is_self: true }),
      ent("nexus", { memories: 5, memory_ids: ["m1"], file_ids: ["f1"] }),
      ent("ana", { type: "person", memories: 1, memory_ids: ["m1", "m2"] }),
      ent("kyoto", { type: "place", memories: 0, chats: 1 }),
    ],
    relations: [
      { id: "r1", src: "me", dst: "nexus", predicate: "builds", fact: "", confidence: 1, valid_from: "", valid_to: null, source_chat_id: null },
      { id: "r2", src: "ana", dst: "nexus", predicate: "works on", fact: "", confidence: 1, valid_from: "", valid_to: "2026-10-21", source_chat_id: null },
    ],
  };

  it("falls back to memories by kind and an artifact ring before anything is connected", () => {
    const out = inside(null, [mem("m1")], [file("f1")], limits);
    expect(out.stars).toEqual([]);
    expect(out.memories).toEqual(memoryPoints([mem("m1")], 600));
    expect(out.crystals).toEqual(crystals([file("f1")], 12));
  });

  it("puts you at the centre with the most mentioned stars nearest, inside the orb", () => {
    const out = inside(graph, [], [], limits);
    expect(out.stars[0]).toMatchObject({ id: "me", self: true, pos: [0, 0, 0] });
    const at = Object.fromEntries(out.stars.map((s) => [s.id, dist(s.pos)]));
    expect(at.nexus).toBeLessThan(at.kyoto);
    for (const s of out.stars) expect(dist(s.pos)).toBeLessThan(0.8 * ORB_R);
    expect(inside(graph, [], [], limits)).toEqual(out);
  });

  it("draws relations as lines, fainter once they ended", () => {
    const out = inside(graph, [], [], limits);
    expect(out.lines).toEqual([
      { a: "me", b: "nexus", ended: false },
      { a: "ana", b: "nexus", ended: true },
    ]);
  });

  it("hangs a memory off the most mentioned thing it is about, and the rest round the centre", () => {
    const out = inside(graph, [mem("m1"), mem("m2"), mem("m3")], [], limits);
    const star = Object.fromEntries(out.memories.map((m) => [m.id, m.star]));
    expect(star).toEqual({ m1: "nexus", m2: "ana", m3: undefined });
    const nexus = out.stars.find((s) => s.id === "nexus")!;
    const m1 = out.memories.find((m) => m.id === "m1")!;
    expect(dist(m1.pos.map((v, i) => v - nexus.pos[i]))).toBeLessThan(0.13);
  });

  it("shows only the newest artifacts, each beside its project", () => {
    const files = [file("f1", "2026-10-22T00:00:00Z"), file("f2", "2026-10-21T00:00:00Z"), file("old", "2026-01-01T00:00:00Z")];
    const out = inside(graph, [], files, { ...limits, crystals: 2 });
    expect(out.crystals.map((c) => [c.id, c.star])).toEqual([
      ["f1", "nexus"],
      ["f2", undefined],
    ]);
  });

  it("always draws what a reply just recalled, even past the star limit", () => {
    const out = inside(graph, [], [], { ...limits, stars: 1 }, new Set(["kyoto"]));
    expect(out.stars.map((s) => s.id)).toEqual(["me", "kyoto"]);
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
