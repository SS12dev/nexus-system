import { describe, expect, it } from "vitest";
import {
  addDays,
  canContain,
  dueLabel,
  groupByDue,
  linkWikilinks,
  openCheckboxes,
  wikilinks,
} from "./organiser";
import type { OrgItem } from "./types";

const task = (id: string, due_on: string | null) => ({ id, due_on }) as OrgItem;

describe("organiser helpers", () => {
  it("does date sums without time-zone drift", () => {
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("says due dates the way people do", () => {
    const today = "2026-11-20"; // a Friday
    expect(dueLabel("2026-11-20", today)).toEqual({ text: "Today", tone: "today" });
    expect(dueLabel("2026-11-21", today).text).toBe("Tomorrow");
    expect(dueLabel("2026-11-24", today).text).toBe("Tuesday");
    expect(dueLabel("2026-12-03", today).text).toBe("3 Dec");
    expect(dueLabel("2027-01-03", today).text).toBe("3 Jan 2027");
    expect(dueLabel("2026-11-19", today)).toEqual({ text: "Yesterday", tone: "late" });
    expect(dueLabel("2026-11-16", today).text).toBe("4 days late");
  });

  it("groups tasks by when they're due", () => {
    const groups = groupByDue(
      [task("a", "2026-11-01"), task("b", "2026-11-20"), task("c", "2026-11-27"), task("d", "2027-02-01"), task("e", null)],
      "2026-11-20",
    );
    expect(groups.map((g) => [g.key, g.items.map((i) => i.id)])).toEqual([
      ["overdue", ["a"]],
      ["today", ["b"]],
      ["week", ["c"]],
      ["later", ["d"]],
      ["none", ["e"]],
    ]);
  });

  it("reads and links [[wikilinks]]", () => {
    const text = "See [[Flat move]], [[flat move|the move]] and [[Nothing yet]].";
    expect(wikilinks(text)).toEqual(["Flat move", "flat move", "Nothing yet"]);
    expect(linkWikilinks(text, [{ id: "p1", type: "project", title: "Flat Move", status: "active" }])).toBe(
      "See [Flat move](/organiser/item/p1), [the move](/organiser/item/p1) and [Nothing yet](/organiser/new?title=Nothing%20yet).",
    );
  });

  it("counts checkboxes that would become tasks", () => {
    expect(openCheckboxes("- [ ] one\n- [x] done\n* [ ] two\n- [ ] [[Linked]]\n- [ ] ")).toBe(2);
  });

  it("knows what can go inside what", () => {
    expect(canContain("area", "project")).toBe(true);
    expect(canContain("project", "project")).toBe(false);
    expect(canContain("resource", "note")).toBe(true);
    expect(canContain("task", "note")).toBe(false);
  });
});
