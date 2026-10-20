import { describe, expect, it } from "vitest";
import type { Project } from "../lib/types";
import { projectWhere, STACK_LABEL } from "./ProjectCard";

const base: Project = {
  id: "p1",
  name: "2048",
  stack: "static",
  repo_url: "https://github.com/servhub-apps/2048",
  branch: "jarvis/r1",
  merged: true,
  transferred_to: null,
  last_commit: "abc1234def",
  last_commit_message: "Add the score",
  last_commit_at: "2026-10-21T10:00:00Z",
  live_url: null,
  app_state: null,
  public: false,
  screenshot_file_id: null,
  chat_id: "c1",
  updated_at: "2026-10-21T10:00:00Z",
};

describe("project cards", () => {
  it("say where the latest work is", () => {
    expect(projectWhere(base)).toBe("on main");
    expect(projectWhere({ ...base, merged: false })).toBe("on branch jarvis/r1");
    expect(projectWhere({ ...base, transferred_to: "SS12dev" })).toBe("moved to SS12dev");
    expect(projectWhere({ ...base, repo_url: null, merged: false })).toBe("not on GitHub yet");
  });

  it("name every stack a team builds with", () => {
    expect(Object.keys(STACK_LABEL).sort()).toEqual(["node", "static", "web_fastapi"]);
  });
});
