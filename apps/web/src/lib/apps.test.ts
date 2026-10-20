import { describe, expect, it } from "vitest";
import { appActions, appAudience, appState } from "./apps";
import type { AppItem } from "./types";

const base: AppItem = {
  id: "a1",
  name: "2048",
  slug: "2048",
  owner_id: "u1",
  owner: "Shivam",
  state: "running",
  live_url: "http://2048.apps.localhost:8080",
  repo_url: null,
  stack: "static",
  deployed_at: "2026-10-21T10:00:00Z",
  deploy_error: null,
  internet: false,
  public: false,
  screenshot_file_id: null,
};

describe("apps", () => {
  it("label each state", () => {
    expect(appState(base).label).toBe("Running");
    expect(appState({ ...base, state: "building" }).label).toBe("Deploying");
    expect(appState({ ...base, state: null }).label).toBe("Not deployed");
  });

  it("say who can open them", () => {
    expect(appAudience(base)).toMatch(/^Private/);
    expect(appAudience({ ...base, public: true })).toMatch(/^Public/);
  });

  it("offer only the buttons that make sense", () => {
    expect(appActions(base)).toEqual({ stop: true, restart: true, redeploy: true });
    expect(appActions({ ...base, state: "stopped" })).toEqual({ stop: false, restart: true, redeploy: true });
    expect(appActions({ ...base, state: "failed" })).toEqual({ stop: false, restart: false, redeploy: true });
    expect(appActions({ ...base, state: "building" })).toEqual({ stop: false, restart: false, redeploy: false });
  });
});
