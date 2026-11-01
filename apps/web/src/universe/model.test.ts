import { describe, expect, it } from "vitest";
import type { UniverseArea, UniverseStar } from "../lib/types";
import {
  CORE_R,
  crewRole,
  deckActivity,
  isEmpty,
  placeGalaxies,
  shipType,
  starWorld,
  teamTarget,
  tiersBusy,
} from "./model";
import type { OrbCluster, OrbNode } from "./teams";

const area = (id: string, over: Partial<UniverseArea> = {}): UniverseArea => ({
  id,
  name: id,
  summary: "",
  domain: null,
  hue: 200,
  color: "#38bdf8",
  angle: 0,
  tilt: 0,
  size: 10,
  activity: 0.3,
  parent: null,
  merged_into: null,
  born_at: "2026-09-01T00:00:00Z",
  ended_at: null,
  ...over,
});

const star = (id: string, name: string, over: Partial<UniverseStar> = {}): UniverseStar => ({
  id,
  name,
  kind: "project",
  summary: "",
  area: "work",
  pos: [0.5, 1, 0],
  salience: 0.5,
  mentions: 1,
  seen_at: "2026-10-30T00:00:00Z",
  created_at: "2026-10-01T00:00:00Z",
  source: null,
  ...over,
});

const node = (role: string, over: Partial<OrbNode> = {}): OrbNode => ({
  key: `t:${role}`,
  systemId: "t",
  agentId: role,
  name: role,
  role,
  tier: "mini",
  lead: false,
  status: "idle",
  parent: null,
  target: [0, 0, 0],
  ...over,
});

const team = (name: string, nodes: OrbNode[], status = "running"): OrbCluster => ({
  id: name,
  name,
  status,
  lifetime: "ephemeral",
  anchor: [0, 0, 0],
  nodes,
  links: [],
});

const dist = (a: number[], b: number[]) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

describe("galaxies", () => {
  const now = Date.parse("2026-11-01T00:00:00Z");

  it("rings the areas well outside the core, bigger areas bigger, each with its share of dust", () => {
    const g = placeGalaxies(
      [area("work", { size: 40 }), area("health", { size: 4, angle: Math.PI })],
      12000,
      now,
    );
    for (const x of g) expect(dist(x.center, [0, 0, 0])).toBeGreaterThan(CORE_R * 3);
    expect(g[0].radius).toBeGreaterThan(g[1].radius);
    expect(g[0].particles).toBeGreaterThan(g[1].particles);
    // Never so few that a small area vanishes.
    expect(g[1].particles).toBeGreaterThanOrEqual(1800);
  });

  it("marks a new area for a couple of days, and one that ended", () => {
    const [born, old, gone] = placeGalaxies(
      [area("a", { born_at: "2026-10-31T00:00:00Z" }), area("b"), area("c", { ended_at: "2026-10-31T00:00:00Z" })],
      9000,
      now,
    );
    expect([born.newborn, old.newborn, gone.newborn]).toEqual([true, false, false]);
    expect(gone.ending).toBe(true);
  });

  it("puts a galaxy's centre star at its centre and its edge stars on the rim", () => {
    const [g] = placeGalaxies([area("work", { tilt: 0.3, angle: 1 })], 6000, now);
    expect(dist(starWorld(g, [0, 0, 0]), g.center)).toBeCloseTo(0);
    expect(dist(starWorld(g, [1, 2, 0]), g.center)).toBeCloseTo(g.radius);
  });
});

describe("teams as ships", () => {
  it("dresses each agent for its role", () => {
    expect(["Researcher", "Architect", "UI designer", "Backend developer", "Reviewer", "Tester", "Deployer", "Writer"].map(crewRole)).toEqual([
      "research",
      "architect",
      "designer",
      "builder",
      "reviewer",
      "tester",
      "deployer",
      "scribe",
    ]);
  });

  it("picks the ship from the crew", () => {
    expect(shipType(team("t", [node("Researcher")]))).toBe("scout");
    expect(shipType(team("t", [node("Architect"), node("Backend developer")]))).toBe("freighter");
    expect(shipType(team("t", [node("Reviewer"), node("Tester")]))).toBe("cutter");
    expect(shipType(team("t", [node("Writer")]))).toBe("courier");
    expect(shipType(team("t", ["a", "b", "c", "d", "e", "f"].map((r) => node(r))))).toBe("carrier");
  });

  it("flies a team to the star it works on, by name or most of the words", () => {
    const stars = [star("n", "Nexus"), star("s", "Solitaire game", { source: { kind: "project", id: "p" } }), star("x", "Loose", { area: null })];
    expect(teamTarget(team("Nexus", []), stars)?.id).toBe("n");
    expect(teamTarget(team("Fix solitaire game deploy", []), stars)?.id).toBe("s");
    expect(teamTarget(team("Loose", []), stars)).toBeNull();
    expect(teamTarget(team("Morning brief", []), stars)).toBeNull();
  });
});

describe("the hub", () => {
  it("lights the decks for what is going on", () => {
    const quiet = deckActivity({ assistant: "idle", teams: [], recalling: false, routinesDue: false });
    expect(quiet.bridge).toBeLessThan(0.5);
    expect(quiet.hangar).toBeLessThan(0.5);
    const busy = deckActivity({
      assistant: "thinking",
      teams: [team("t", [node("Builder", { status: "working", tool: "run_code" })])],
      recalling: true,
      routinesDue: false,
    });
    expect([busy.bridge, busy.workshop, busy.archive, busy.reactor]).toEqual([1, 1, 1, 1]);
    expect(busy.hangar).toBeGreaterThan(0.5);
  });

  it("lights a reactor cell per tier at work, and pro while the assistant answers", () => {
    expect(tiersBusy([team("t", [node("a", { tier: "nano", status: "working" })])], true)).toEqual([1, 0.25, 1, 0.25]);
  });

  it("knows when there is nothing to draw yet", () => {
    expect(isEmpty(null)).toBe(true);
  });
});
