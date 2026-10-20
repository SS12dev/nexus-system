import { describe, expect, it } from "vitest";
import { stepDetail, toolLabel } from "./tools";

describe("tool steps", () => {
  it("labels workbench tools and shows what they worked on", () => {
    expect(toolLabel("ws_run")).toBe("Running a command");
    expect(stepDetail({ command: "npm test" })).toBe("npm test");
    expect(stepDetail({ path: "src/App.tsx" })).toBe("src/App.tsx");
    expect(stepDetail({ message: "Add score board" })).toBe("Add score board");
  });

  it("falls back to a readable name for unknown tools", () => {
    expect(toolLabel("some_new_tool")).toBe("Some new tool");
  });
});
