import { describe, expect, it } from "vitest";
import { changeSummary, isGitHub, linkPath, linkSource, shortUrl, stripComments } from "./links";

describe("links", () => {
  it("shortens addresses", () => {
    expect(shortUrl("https://github.com/SS12dev/nexus-system/")).toBe("github.com/SS12dev/nexus-system");
    expect(shortUrl("https://example.com/" + "a".repeat(80), 20)).toHaveLength(20);
  });

  it("knows GitHub kinds", () => {
    expect(isGitHub("github_pr")).toBe(true);
    expect(isGitHub("google_doc")).toBe(false);
  });

  it("finds the path of a file link", () => {
    expect(linkPath("https://github.com/a/b/blob/main/src/app%20x.py?plain=1")).toBe("src/app x.py");
    expect(linkPath("https://github.com/a/b")).toBe("");
  });

  it("says who saved it", () => {
    expect(linkSource({ source: "team" }, "Jarvis")).toBe("saved by Jarvis's team");
    expect(linkSource({ source: "user" }, "Jarvis")).toBe("added by you");
  });

  it("hides HTML comments", () => {
    expect(stripComments("<!-- marker -->\nHello <!--x-->there")).toBe("Hello there");
  });

  it("summarises a change", () => {
    expect(changeSummary({ changed_files: 1, additions: 10, deletions: 2 })).toBe("1 file · +10 · −2");
    expect(changeSummary({})).toBe("");
  });
});
