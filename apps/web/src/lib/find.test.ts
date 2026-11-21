import { describe, expect, it } from "vitest";
import { COMMANDS, groupResults, matchCommands, snippetParts } from "./find";
import type { FindResult } from "./types";

const hit = (kind: FindResult["kind"], id: string, score: number): FindResult => ({
  kind,
  id,
  title: id,
  url: `/${id}`,
  snippet: "",
  detail: "",
  at: null,
  score,
  matched: ["words"],
});

describe("snippetParts", () => {
  it("splits marked matches from plain text", () => {
    expect(snippetParts("the \u0002invoice\u0003 for \u0002March\u0003.")).toEqual([
      { text: "the ", hit: false },
      { text: "invoice", hit: true },
      { text: " for ", hit: false },
      { text: "March", hit: true },
      { text: ".", hit: false },
    ]);
  });
  it("drops a stray marker", () => {
    expect(snippetParts("half \u0002open")).toEqual([{ text: "half open", hit: false }]);
  });
});

describe("groupResults", () => {
  it("orders groups by their best hit and keeps order inside", () => {
    const groups = groupResults([hit("file", "f1", 0.5), hit("chat", "c1", 0.9), hit("file", "f2", 0.4)]);
    expect(groups.map((g) => g.kind)).toEqual(["chat", "file"]);
    expect(groups[1]!.items.map((r) => r.id)).toEqual(["f1", "f2"]);
  });
});

describe("matchCommands", () => {
  it("matches word starts in labels and keywords", () => {
    expect(matchCommands("lib", COMMANDS).map((c) => c.id)).toContain("library");
    expect(matchCommands("new rou", COMMANDS).map((c) => c.id)).toEqual(["new-routine"]);
    expect(matchCommands("pdf", COMMANDS).map((c) => c.id)).toContain("upload");
    expect(matchCommands("zzz", COMMANDS)).toEqual([]);
    expect(matchCommands("", COMMANDS)).toHaveLength(COMMANDS.length);
  });
});
