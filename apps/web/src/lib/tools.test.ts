import { describe, expect, it } from "vitest";
import { currentStage, stepDetail, toolLabel } from "./tools";

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

describe("software team stages", () => {
  const nodes = [
    { id: "researcher", name: "Researcher", role: "researcher", tier: "mini" as const, lead: true, stage: "Brief" },
    { id: "frontend", name: "Frontend builder", role: "developer", tier: "mini" as const, lead: false, stage: "Build" },
    { id: "backend", name: "Backend builder", role: "developer", tier: "mini" as const, lead: false, stage: "Build" },
  ];

  it("names the stage of the busy agent, with its round", () => {
    expect(currentStage(nodes, { researcher: { status: "done" }, frontend: { status: "working", item: "round 2" } })).toBe(
      "Build · round 2",
    );
    expect(currentStage(nodes, { researcher: { status: "thinking" } })).toBe("Brief");
  });

  it("is empty when nobody is busy or the team has no stages", () => {
    expect(currentStage(nodes, { backend: { status: "skipped" } })).toBe("");
    expect(currentStage([{ ...nodes[0], stage: undefined }], { researcher: { status: "running" } })).toBe("");
  });
});
