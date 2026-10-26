import { describe, expect, it } from "vitest";
import { teamLayout } from "./teamLayout";
import type { AgentNode } from "./types";

const node = (id: string, level: number): AgentNode => ({
  id,
  name: id,
  role: "assistant",
  tier: "mini",
  lead: false,
  level,
});

describe("teamLayout for a designed flow", () => {
  it("puts each step in its own column and side-by-side agents in one", () => {
    // Listed out of order: the columns come from the levels, not the list.
    const nodes = [node("modeller", 1), node("looks", 0), node("checker", 2), node("sizes", 0)];
    const { pos } = teamLayout(nodes, "flow");
    expect(pos.looks.x).toBe(pos.sizes.x);
    expect(pos.looks.y).not.toBe(pos.sizes.y);
    expect(pos.looks.x).toBeLessThan(pos.modeller.x);
    expect(pos.modeller.x).toBeLessThan(pos.checker.x);
  });

  it("keeps three agents side by side inside the diagram", () => {
    const { pos, height } = teamLayout([node("a", 0), node("b", 0), node("c", 0), node("d", 1)], "flow");
    for (const id of ["a", "b", "c"]) expect(pos[id].y).toBeLessThanOrEqual(height - 30);
  });
});
