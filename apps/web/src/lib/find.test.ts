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

describe("isSearchKey", () => {
  const key = (k: Partial<KeyboardEvent>) => ({ metaKey: false, ctrlKey: false, altKey: false, key: "", code: "", ...k });
  it("is Cmd-K or Ctrl-K on any keyboard layout", async () => {
    const { isSearchKey } = await import("../components/CommandBar");
    expect(isSearchKey(key({ metaKey: true, key: "k", code: "KeyK" }))).toBe(true);
    expect(isSearchKey(key({ ctrlKey: true, key: "K", code: "KeyK" }))).toBe(true);
    // A Russian layout gives "л" for the same key.
    expect(isSearchKey(key({ metaKey: true, key: "л", code: "KeyK" }))).toBe(true);
    expect(isSearchKey(key({ key: "k", code: "KeyK" }))).toBe(false);
    expect(isSearchKey(key({ metaKey: true, altKey: true, key: "k", code: "KeyK" }))).toBe(false);
  });
});

describe("Go to", () => {
  it("lists the Brief and Money's tabs", () => {
    expect(matchCommands("brief", COMMANDS).map((c) => c.id)).toContain("brief");
    expect(matchCommands("subscriptions", COMMANDS).map((c) => c.id)).toContain("money-subs");
  });
});
