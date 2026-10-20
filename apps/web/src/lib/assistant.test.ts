import { describe, expect, it } from "vitest";
import { cleanAssistantName } from "./assistant";

describe("cleanAssistantName", () => {
  it("tidies spaces and keeps allowed names", () => {
    expect(cleanAssistantName("  Orion   Prime ")).toBe("Orion Prime");
    expect(cleanAssistantName("J.A.R.V.I.S")).toBe("J.A.R.V.I.S");
    expect(cleanAssistantName("Zoë")).toBe("Zoë");
  });

  it("refuses empty, too long or odd names", () => {
    for (const bad of ["", "   ", "<b>x</b>", "-dash", "x".repeat(25)]) {
      expect(cleanAssistantName(bad)).toBeNull();
    }
  });
});
