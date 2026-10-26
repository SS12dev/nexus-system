import { describe, expect, it } from "vitest";
import { swell, tuneOf, TUNES } from "./tunes";

describe("tunes", () => {
  it("has one of each, every one with notes and room between plays", () => {
    expect(new Set(TUNES.map((t) => t.id)).size).toBe(TUNES.length);
    for (const t of TUNES) {
      expect(t.notes.length).toBeGreaterThan(0);
      const end = Math.max(...t.notes.map((n) => n.at + n.len));
      expect(end).toBeLessThan(t.every);
    }
  });

  it("falls back to the chime and lets an alarm grow louder", () => {
    expect(tuneOf(undefined).id).toBe("chime");
    expect(tuneOf("kazoo").id).toBe("chime");
    expect(swell(0)).toBeLessThan(swell(10));
    expect(swell(120)).toBe(1);
  });
});
