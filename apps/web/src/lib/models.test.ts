import { describe, expect, it } from "vitest";
import { modelLabel } from "./models";

const models = {
  "nexus-pro": { model: "gpt-5.4", label: "GPT-5.4", provider: "OpenAI" },
};

describe("modelLabel", () => {
  it("names the real model for a tier or alias", () => {
    expect(modelLabel(models, "pro")).toBe("GPT-5.4");
    expect(modelLabel(models, "nexus-pro")).toBe("GPT-5.4");
  });
  it("falls back to what it was given", () => {
    expect(modelLabel(models, "max")).toBe("max");
    expect(modelLabel(undefined, "nexus-mini")).toBe("nexus-mini");
    expect(modelLabel(models, null)).toBe("");
  });
});
