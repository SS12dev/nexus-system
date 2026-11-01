import { describe, expect, it } from "vitest";
import type { AgentSystem, Approval, FileItem, Folder, Project } from "../lib/types";
import { agentTargets, buildClusters, clusterAnchor, litAt, litSteps, liveSystems, works } from "./teams";

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
    for (const a of anchors) expect(dist(a)).toBeGreaterThan(2);
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


describe("projects", () => {
  const app = (id: string, name = id, app_state: string | null = null) =>
    ({ id, name, stack: "static", app_state, updated_at: "2026-10-01T00:00:00Z" }) as Project;
  const folder = (id: string, over: Partial<Folder> = {}): Folder => ({
    id,
    name: id,
    project_id: null,
    chat_id: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-02T00:00:00Z",
    ...over,
  });
  const file = (id: string, folder_id: string | null) =>
    ({ id, name: id, content_type: "x", status: "ready", origin: "generated", folder_id, created_at: "" }) as FileItem;

  it("makes each folder of files one project, and an app's folders part of the app", () => {
    const folders = [
      folder("rocket", { updated_at: "2026-10-05T00:00:00Z" }),
      folder("empty"),
      folder("shots", { project_id: "solitaire" }),
    ];
    const files = [file("a", "rocket"), file("b", "rocket"), file("c", "rocket"), file("d", "shots"), file("e", null)];
    const list = works([app("solitaire", "Solitaire", "running")], folders, files);
    expect(list.map((w) => [w.id, w.files, w.folderIds, w.live])).toEqual([
      ["rocket", 3, ["rocket"], false],
      ["solitaire", 1, ["shots"], true],
    ]);
    // Before its files load, a folder counts what the folder list says.
    expect(works([], [folder("x", { files: 4 })])[0].files).toBe(4);
  });
});

describe("lighting a recall", () => {
  const recall = {
    memories: ["m1"],
    entities: [
      { id: "nexus", name: "Nexus", type: "project" as const },
      { id: "langgraph", name: "LangGraph", type: "tool" as const },
      { id: "python", name: "Python", type: "tool" as const },
    ],
    path: [
      { id: "nexus", hop: 0, via: null },
      { id: "langgraph", hop: 1, via: "nexus" },
      { id: "python", hop: 2, via: "langgraph" },
    ],
  };

  it("lights what the message is about first, then a hop at a time, then the memories", () => {
    expect(litSteps(recall)).toBe(4);
    expect([...litAt(recall, 0)]).toEqual(["nexus"]);
    expect([...litAt(recall, 1)]).toEqual(["nexus", "langgraph"]);
    expect([...litAt(recall, 2)]).toEqual(["nexus", "langgraph", "python"]);
    expect([...litAt(recall, 3)]).toEqual(["nexus", "langgraph", "python", "m1"]);
  });

  it("lights an older reply's things together, then its memories", () => {
    const old = { memories: ["m1"], entities: recall.entities };
    expect(litSteps(old)).toBe(2);
    expect(litAt(old, 1).size).toBe(4);
    expect(litAt(null, 0).size).toBe(0);
  });
});

