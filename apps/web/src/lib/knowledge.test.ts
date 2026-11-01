import { describe, expect, it } from "vitest";
import { areaCards, byKind, coreGroups, filterEntities, kindLabel, linksOf, sureness } from "./knowledge";
import type { GraphEntity, GraphRelation, Universe, UniverseStar } from "./types";

const ent = (id: string, over: Partial<GraphEntity> = {}): GraphEntity => ({
  id,
  type: "project",
  name: id,
  aliases: [],
  summary: "",
  is_self: false,
  created_at: "2026-10-01T00:00:00Z",
  seen_at: "2026-10-20T00:00:00Z",
  memories: 0,
  past_chats: 0,
  files: 0,
  chats: 0,
  memory_ids: [],
  file_ids: [],
  chat_ids: [],
  ...over,
});
const rel = (id: string, src: string, dst: string, over: Partial<GraphRelation> = {}): GraphRelation => ({
  id,
  src,
  dst,
  predicate: "uses",
  fact: "",
  confidence: 1,
  valid_from: "2026-10-01T00:00:00Z",
  valid_to: null,
  source_chat_id: null,
  ...over,
});

describe("the Knowledge list", () => {
  const all = [
    ent("me", { is_self: true, name: "Shivam" }),
    ent("Nexus", { memories: 3, summary: "Multi-agent system" }),
    ent("UST", { type: "organisation", aliases: ["ust global"], memories: 1, seen_at: "2026-10-23T00:00:00Z" }),
    ent("Asha", { type: "person", chats: 2 }),
  ];
  const ids = (es: GraphEntity[]) => es.map((e) => e.id);

  it("keeps you first, then the most mentioned", () => {
    expect(ids(filterEntities(all, { query: "", type: "all", sort: "mentions" }))).toEqual([
      "me",
      "Nexus",
      "Asha",
      "UST",
    ]);
  });

  it("sorts by name or by when it last came up", () => {
    expect(ids(filterEntities(all, { query: "", type: "all", sort: "name" }))).toEqual(["me", "Asha", "Nexus", "UST"]);
    expect(ids(filterEntities(all, { query: "", type: "all", sort: "recent" }))[1]).toBe("UST");
  });

  it("searches names, other names and summaries", () => {
    expect(ids(filterEntities(all, { query: "global", type: "all", sort: "name" }))).toEqual(["UST"]);
    expect(ids(filterEntities(all, { query: "agent", type: "all", sort: "name" }))).toEqual(["Nexus"]);
  });

  it("filters by type, leaving you out", () => {
    expect(ids(filterEntities(all, { query: "", type: "person", sort: "name" }))).toEqual(["Asha"]);
  });
});

describe("an entity's links", () => {
  const rels = [
    rel("r1", "me", "UST", { predicate: "works at", valid_from: "2026-09-01T00:00:00Z" }),
    rel("r2", "me", "Acme", { predicate: "works at", valid_to: "2026-08-31T00:00:00Z" }),
    rel("r3", "Nexus", "me", { predicate: "built by", valid_from: "2026-10-10T00:00:00Z" }),
    rel("r4", "Nexus", "UST"),
  ];

  it("splits current from ended, newest first, and says which way each goes", () => {
    const { current, past } = linksOf("me", rels);
    expect(current.map((l) => [l.other, l.outgoing])).toEqual([
      ["Nexus", false],
      ["UST", true],
    ]);
    expect(past.map((l) => l.other)).toEqual(["Acme"]);
  });
});

describe("the Mind page", () => {
  it("groups your core deepest first and leaves out what isn't in it", () => {
    const groups = coreGroups([
      ent("me", { is_self: true, kind: "person" }),
      ent("honesty", { kind: "value", memories: 1 }),
      ent("curious", { kind: "trait", memories: 6 }),
      ent("python", { kind: "skill", memories: 2 }),
      ent("nexus", { kind: "project" }),
    ]);
    expect(groups.map((g) => [g.id, g.items.map((e) => e.id)])).toEqual([
      ["values", ["honesty"]],
      ["traits", ["curious", "python"]],
    ]);
    expect(groups[1].items.map((e) => sureness(e).label)).toEqual(["Sure", "Fairly sure"]);
  });

  const star = (id: string, area: string | null, salience: number, kind = "project"): UniverseStar => ({
    id,
    name: id,
    kind,
    summary: "",
    area,
    pos: [0.5, 0, 0],
    salience,
    mentions: 1,
    seen_at: "",
    created_at: "",
    source: null,
  });
  const area = (id: string, born_at: string, activity: number, ended_at: string | null = null) => ({
    id,
    name: id,
    summary: "",
    domain: null,
    hue: 0,
    color: "#fff",
    angle: 0,
    tilt: 0,
    size: 1,
    activity,
    parent: null,
    merged_into: null,
    born_at,
    ended_at,
  });

  it("makes a card per live area, biggest first, with its most central things", () => {
    const u = {
      areas: [
        area("health", "2026-10-30T00:00:00Z", 0.05),
        area("work", "2026-09-01T00:00:00Z", 0.6),
        area("gone", "2026-09-01T00:00:00Z", 0, "2026-10-31T00:00:00Z"),
      ],
      stars: [star("gym", "health", 0.2), star("ust", "work", 0.9), star("rahul", "work", 0.3, "person"), star("x", null, 1)],
    } as unknown as Universe;
    const cards = areaCards(u, Date.parse("2026-11-01T00:00:00Z"));
    expect(cards.map((c) => [c.area.id, c.count, c.top.map((s) => s.id), c.isNew, c.fading])).toEqual([
      ["work", 2, ["ust", "rahul"], false, false],
      ["health", 1, ["gym"], true, true],
    ]);
    expect(byKind(u.stars.filter((s) => s.area === "work")).map(([k]) => k)).toEqual(["person", "project"]);
  });

  it("names kinds in plain words", () => {
    expect(["person", "communication_style", "activity", "tool"].map((k) => kindLabel(k))).toEqual([
      "People",
      "Communication styles",
      "Activities",
      "Tools",
    ]);
  });
});
