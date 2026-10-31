import { describe, expect, it } from "vitest";
import type {
  AgentSystem,
  Approval,
  FileItem,
  Folder,
  GraphEntity,
  KnowledgeGraph,
  Memory,
  Project,
  Routine,
} from "../lib/types";
import {
  agentTargets,
  bandRadius,
  buildClusters,
  cameraDistance,
  clusterAnchor,
  inside,
  litAt,
  litSteps,
  liveSystems,
  memoryPoints,
  ORB_R,
  curve,
  concentric,
  DEFAULT_VIEW,
  SHELL_R,
  MOON_FAR,
  MOON_NEAR,
  orbitPos,
  SATELLITE_COLOR,
  satellites,
  shellFade,
  works,
  type StarPoint,
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
});

describe("the constellation", () => {
  const ent = (id: string, over: Partial<GraphEntity> = {}): GraphEntity => ({
    id,
    type: "topic",
    name: id,
    aliases: [],
    summary: "",
    is_self: false,
    created_at: "2026-10-20T00:00:00Z",
    seen_at: "2026-10-20T00:00:00Z",
    memories: 1,
    past_chats: 0,
    files: 0,
    chats: 0,
    memory_ids: [],
    file_ids: [],
    chat_ids: [],
    ...over,
  });
  const limits = { stars: 40, memories: 600 };
  const graph: KnowledgeGraph = {
    entities: [
      ent("me", { type: "person", name: "Shivam", is_self: true }),
      ent("nexus", { memories: 5, memory_ids: ["m1"], file_ids: ["f1"] }),
      ent("ana", { type: "person", memories: 1, memory_ids: ["m1", "m2"] }),
      ent("kyoto", { type: "place", memories: 1, chats: 1 }),
      ent("rocket", { type: "project", memories: 9, memory_ids: ["m3"], file_ids: ["f1"] }),
    ],
    relations: [
      { id: "r1", src: "me", dst: "nexus", predicate: "builds", fact: "", confidence: 1, valid_from: "", valid_to: null, source_chat_id: null },
      { id: "r2", src: "ana", dst: "nexus", predicate: "works on", fact: "", confidence: 1, valid_from: "", valid_to: "2026-10-21", source_chat_id: null },
    ],
  };

  it("falls back to memories by kind before anything is connected", () => {
    const out = inside(null, [mem("m1")], limits);
    // You are at the centre from the very start.
    expect(out.stars).toEqual([
      { id: "you", name: "You", type: "person", summary: "", self: true, weight: 0, pos: [0, 0, 0] },
    ]);
    expect(out.memories).toEqual(memoryPoints([mem("m1")], 600));
    expect(Object.keys(out)).toEqual(["stars", "lines", "memories"]);
  });

  it("puts you at the centre with the most mentioned stars nearest, inside the orb", () => {
    const out = inside(graph, [], limits);
    expect(out.stars[0]).toMatchObject({ id: "me", self: true, pos: [0, 0, 0] });
    const at = Object.fromEntries(out.stars.map((s) => [s.id, dist(s.pos)]));
    expect(at.nexus).toBeLessThan(at.kyoto);
    for (const s of out.stars) expect(dist(s.pos)).toBeLessThan(0.8 * ORB_R);
    expect(inside(graph, [], limits)).toEqual(out);
  });

  it("draws relations as lines, fainter once they ended", () => {
    const out = inside(graph, [], limits);
    expect(out.lines.filter((l) => !l.implied)).toEqual([
      { a: "me", b: "nexus", ended: false },
      { a: "ana", b: "nexus", ended: true },
    ]);
  });

  it("hangs a memory off the most mentioned thing it is about, and the rest round the centre", () => {
    const out = inside(graph, [mem("m1"), mem("m2"), mem("m3")], limits);
    const star = Object.fromEntries(out.memories.map((m) => [m.id, m.star]));
    expect(star).toEqual({ m1: "nexus", m2: "ana", m3: undefined });
    const nexus = out.stars.find((s) => s.id === "nexus")!;
    const m1 = out.memories.find((m) => m.id === "m1")!;
    expect(dist(m1.pos.map((v, i) => v - nexus.pos[i]))).toBeLessThan(0.13);
  });

  it("keeps projects out: inside is who you are, what was made is outside", () => {
    // A project is out, however much it is mentioned or recalled; its memory circles you.
    const out = inside(graph, [mem("m3")], limits, new Set(["rocket"]));
    expect(out.stars.map((s) => s.id)).not.toContain("rocket");
    expect(out.memories[0].star).toBeUndefined();
  });

  it("draws only your profile: what memories are about or what relates to you, never files or work-only things", () => {
    const work: KnowledgeGraph = {
      entities: [
        ...graph.entities,
        ent("tray", { type: "other", name: "out/tray.3mf", memories: 2 }),
        ent("script", { type: "tool", name: "rocket_model.py", memories: 1 }),
        ent("cad", { type: "tool", name: "build123d", memories: 0, chats: 3, files: 2 }),
        ent("node", { type: "tool", name: "Node.js", memories: 0 }),
      ],
      relations: [
        ...graph.relations,
        { id: "r3", src: "me", dst: "node", predicate: "uses", fact: "", confidence: 1, valid_from: "", valid_to: null, source_chat_id: null },
        { id: "r4", src: "rocket", dst: "cad", predicate: "built with", fact: "", confidence: 1, valid_from: "", valid_to: null, source_chat_id: null },
      ],
    };
    const ids = inside(work, [], limits).stars.map((s) => s.id).sort();
    expect(ids).toEqual(["ana", "kyoto", "me", "nexus", "node"]);
  });

  it("hangs a star with no drawn relation from you, faintly", () => {
    const out = inside(graph, [], limits);
    expect(out.lines).toContainEqual({ a: "me", b: "kyoto", ended: false, implied: true });
    expect(out.lines.filter((l) => l.implied).map((l) => l.b)).toEqual(["kyoto"]);
  });

  it("always draws what a reply just recalled, even past the star limit", () => {
    const out = inside(graph, [], { ...limits, stars: 1 }, new Set(["kyoto"]));
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

describe("shellFade", () => {
  it("draws the whole shell from the home view and only a faint outline zoomed right in", () => {
    expect(shellFade(cameraDistance(0, 1.6))).toBe(1);
    expect(shellFade(1.8)).toBeCloseTo(0.18);
    expect(shellFade(3)).toBeGreaterThan(shellFade(2.5));
  });
});


describe("moons outside the orb", () => {
  const routine = (id: string, extra: Partial<Routine> = {}): Routine => ({
    id,
    name: id,
    prompt: "",
    schedule: "FREQ=DAILY;BYHOUR=8;BYMINUTE=0",
    schedule_text: "Every day at 08:00",
    timezone: "UTC",
    delivery: [],
    only_if_new: false,
    enabled: true,
    source: "user",
    chat_id: null,
    next_run_at: null,
    last_run_at: null,
    failures: 0,
    created_at: `2026-10-0${id.slice(1)}T00:00:00Z`,
    last_status: null,
    ...extra,
  });
  const app = (id: string, name = id, app_state: string | null = null) =>
    ({ id, name, stack: "static", app_state, updated_at: "2026-10-01T00:00:00Z" }) as Project;
  const project = (id: string, name = id, app_state: string | null = null) => works([app(id, name, app_state)], []);
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
  const star = (id: string, name: string, self = false): StarPoint => ({
    id,
    name,
    type: "topic",
    summary: "",
    self,
    weight: 1,
    pos: [0.3, 0, 0],
  });

  it("puts each on its own orbit outside the shell, inside the teams' band", () => {
    const out = satellites([routine("r1"), routine("r2", { enabled: false })], project("p1", "p1", "running"));
    expect(out.map((s) => s.id)).toEqual(["r1", "r2", "p1"]);
    for (const s of out)
      for (const t of [0, 7, 40]) {
        const d = dist(orbitPos(s.orbit, t));
        expect(d).toBeGreaterThanOrEqual(MOON_NEAR - 1e-9);
        expect(d).toBeLessThanOrEqual(MOON_FAR + 1e-9);
        expect(d).toBeLessThan(bandRadius(1));
      }
    // Orbits differ, so moons don't line up on one ring.
    expect(new Set(out.map((s) => s.orbit.radius.toFixed(4))).size).toBe(3);
    expect(out[1].color).toBe(SATELLITE_COLOR.paused);
    expect(out[2]).toMatchObject({ kind: "project", active: true, color: SATELLITE_COLOR.project });
    expect(satellites([routine("r1")], [])).toEqual(satellites([routine("r1")], []));
  });

  it("threads routines to their topic or you, and projects only to what they are about", () => {
    const stars = [star("me", "You", true), star("t1", "Cooking"), star("t2", "3D printing")];
    const homes = (key: string) => (key === "pack:cooking" ? ["Cooking"] : key === "rocket" ? ["3D printing"] : []);
    const out = satellites(
      [routine("r1", { source: "pack:cooking" }), routine("r2", { source: "user" })],
      [...project("p1", "rocket"), ...project("p2", "my-app")],
      [...stars],
      homes,
    );
    expect(out.map((s) => s.home)).toEqual(["t1", "me", "t2", undefined]);
  });

  it("makes each folder of files one project moon, and an app's folders part of the app", () => {
    const folders = [
      folder("rocket", { updated_at: "2026-10-05T00:00:00Z" }),
      folder("empty"),
      folder("shots", { project_id: "solitaire" }),
    ];
    const files = [file("a", "rocket"), file("b", "rocket"), file("c", "rocket"), file("d", "shots"), file("e", null)];
    const list = works([app("solitaire", "Solitaire", "running")], folders, files);
    expect(list.map((w) => [w.id, w.files, w.folderIds])).toEqual([
      ["rocket", 3, ["rocket"]],
      ["solitaire", 1, ["shots"]],
    ]);
    const [rocket, solitaire] = satellites([], list);
    expect(rocket).toMatchObject({ kind: "project", app: false, detail: "3 files", color: SATELLITE_COLOR.files });
    expect(solitaire).toMatchObject({ app: true, active: true, color: SATELLITE_COLOR.project });
    // Before its files load, a folder counts what the folder list says.
    expect(works([], [folder("x", { files: 4 })])[0].files).toBe(4);
  });

  it("keeps to the limit", () => {
    const many = Array.from({ length: 9 }, (_, i) => routine(`r${i}`));
    expect(satellites(many, project("p1"), [], () => [], 6)).toHaveLength(6);
  });

  it("bows lines, each its own way, from end to end", () => {
    const a: [number, number, number] = [0, 0, 0];
    const b: [number, number, number] = [1, 0, 0];
    const p1 = curve(a, b, 1);
    expect(p1[0]).toEqual(a);
    expect(p1[p1.length - 1]).toEqual(b);
    const mid = p1[Math.floor(p1.length / 2)];
    expect(Math.hypot(mid[1], mid[2])).toBeGreaterThan(0.03);
    expect(curve(a, b, 2)[5]).not.toEqual(mid);
  });
});

describe("the concentric constellation", () => {
  const ent = (id: string, over: Partial<GraphEntity> = {}): GraphEntity => ({
    id,
    type: "topic",
    name: id,
    aliases: [],
    summary: "",
    is_self: false,
    created_at: "2026-10-20T00:00:00Z",
    seen_at: "2026-10-20T00:00:00Z",
    memories: 1,
    past_chats: 0,
    files: 0,
    chats: 0,
    memory_ids: [],
    file_ids: [],
    chat_ids: [],
    ...over,
  });
  const rel = (src: string, dst: string, family = "association", weight = 0.5): KnowledgeGraph["relations"][number] => ({
    id: `${src}-${dst}`,
    src,
    dst,
    predicate: "related to",
    fact: "",
    confidence: 1,
    valid_from: "",
    valid_to: null,
    source_chat_id: null,
    family,
    label: "relates to",
    weight,
  });
  const graph: KnowledgeGraph = {
    entities: [
      ent("me", { type: "person", name: "Shivam", is_self: true, shell: 0 }),
      ent("curious", { kind: "trait", shell: 1, domain: null, salience: 0.9 }),
      ent("ana", { type: "person", kind: "person", shell: 2, domain: "social", community: 1, salience: 0.2 }),
      ent("raj", { type: "person", kind: "person", shell: 2, domain: "social", community: 1 }),
      ent("ust", { type: "organisation", kind: "organisation", shell: 2, domain: "work", community: 0 }),
      ent("nexusapp", { kind: "app", shell: 3, domain: "work", community: 0 }),
      ent("langgraph", { kind: "tool", shell: 4, domain: "work", community: 0, memories: 7 }),
      ent("bonsai", { kind: "topic", shell: 4, domain: "hobbies" }),
    ],
    relations: [
      rel("me", "curious", "identity", 0.9),
      rel("me", "ana", "social", 0.8),
      rel("me", "raj", "social", 0.4),
      rel("ana", "raj", "social"),
      rel("me", "ust", "belonging", 0.9),
      rel("me", "nexusapp", "activity"),
      rel("nexusapp", "langgraph", "activity"),
      rel("ust", "nexusapp", "belonging"),
      rel("me", "bonsai", "identity", 0.3),
      rel("curious", "langgraph", "knowledge", 0.2),
    ],
  };
  const chosen = graph.entities.filter((e) => !e.is_self);
  const layout = (view = DEFAULT_VIEW) => concentric(chosen, graph.relations, "me", view);
  const byId = (stars: StarPoint[]) => Object.fromEntries(stars.map((s) => [s.id, s]));
  const lon = (s: StarPoint) => Math.atan2(s.pos[2], s.pos[0]);
  const gap = (a: number, b: number) => Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b)));

  it("puts each thing on its shell: who you are nearest, what you know furthest out", () => {
    const at = byId(layout());
    for (const s of Object.values(at)) expect(Math.abs(dist(s.pos) - SHELL_R[s.shell!])).toBeLessThan(0.07);
    expect(dist(at.curious.pos)).toBeLessThan(dist(at.ana.pos));
    expect(dist(at.ana.pos)).toBeLessThan(dist(at.nexusapp.pos));
    expect(dist(at.nexusapp.pos)).toBeLessThan(dist(at.bonsai.pos));
    expect(Math.max(...Object.values(at).map((s) => dist(s.pos)))).toBeLessThan(0.8 * ORB_R);
  });

  it("gives each part of life its own side, and a thing without one its strongest neighbour's", () => {
    const at = byId(layout());
    expect(at.curious.domain).toBe("work");
    expect(at.ana.domain).toBe("social");
    expect(gap(lon(at.ust), lon(at.langgraph))).toBeLessThan(gap(lon(at.ust), lon(at.ana)));
    expect(gap(lon(at.ana), lon(at.raj))).toBeLessThan(gap(lon(at.ana), lon(at.bonsai)));
  });

  it("pulls clusters together as gravity rises", () => {
    const spread = (g: number) => {
      const at = byId(layout({ size: "connections", gravity: g }));
      const work = [at.ust, at.nexusapp, at.langgraph].map(lon);
      return Math.max(gap(work[0], work[1]), gap(work[1], work[2]), gap(work[0], work[2]));
    };
    expect(spread(1)).toBeLessThan(spread(0));
  });

  it("sizes by connections, importance or mentions", () => {
    const size = (s: "connections" | "importance" | "mentions") =>
      byId(layout({ size: s, gravity: 0.5 }));
    expect(size("connections").nexusapp.weight).toBe(3);
    expect(size("importance").curious.weight).toBeCloseTo(9);
    expect(size("mentions").langgraph.weight).toBeGreaterThan(size("mentions").bonsai.weight);
  });

  it("draws the same every time, and through inside() with family-coloured lines", () => {
    expect(layout()).toEqual(layout());
    const out = inside(graph, [], { stars: 40, memories: 0 });
    expect(out.stars.find((s) => s.id === "ana")).toMatchObject({ shell: 2, domain: "social" });
    expect(out.lines.find((l) => l.a === "me" && l.b === "ana")).toMatchObject({ family: "social", weight: 0.8 });
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
