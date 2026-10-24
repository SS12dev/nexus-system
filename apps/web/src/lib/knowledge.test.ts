import { describe, expect, it } from "vitest";
import { egoLayout, filterEntities, linksOf } from "./knowledge";
import type { GraphEntity, GraphRelation } from "./types";

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

  it("lays neighbours out on a ring, marking ones whose links all ended", () => {
    const { nodes, more } = egoLayout("me", rels);
    expect(nodes.map((n) => n.id)).toEqual(["Nexus", "UST", "Acme"]);
    expect(nodes.find((n) => n.id === "Acme")!.past).toBe(true);
    expect(nodes.find((n) => n.id === "UST")!.past).toBe(false);
    for (const n of nodes) expect(Math.hypot(n.x - 0.5, n.y - 0.5)).toBeCloseTo(0.38);
    expect(more).toBe(0);
  });

  it("joins several links to one neighbour and caps how many are drawn", () => {
    const many = [
      rel("a", "me", "UST", { predicate: "works at" }),
      rel("b", "me", "UST", { predicate: "likes" }),
      ...Array.from({ length: 20 }, (_, i) => rel(`x${i}`, "me", `e${i}`)),
    ];
    const { nodes, more } = egoLayout("me", many, 14);
    expect(nodes).toHaveLength(14);
    expect(more).toBe(7);
    expect(nodes.find((n) => n.id === "UST")?.label).toBe("works at, likes");
  });
});
