import { describe, expect, it } from "vitest";
import { describeAgent, formatAgo, formatTokens } from "./format";

describe("format", () => {
  it("names browsers", () => {
    expect(
      describeAgent(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
      ),
    ).toBe("Safari on iPhone");
    expect(
      describeAgent(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
      ),
    ).toBe("Chrome on Mac");
    expect(describeAgent(null)).toBe("Unknown browser");
  });

  it("formats ages and token counts", () => {
    const now = Date.parse("2026-10-20T12:00:00Z");
    expect(formatAgo("2026-10-20T11:55:00Z", now)).toBe("5 min ago");
    expect(formatAgo("2026-10-20T09:00:00Z", now)).toBe("3 h ago");
    expect(formatAgo("2026-10-17T12:00:00Z", now)).toBe("3 days ago");
    expect(formatTokens(1234)).toBe("1.2k");
  });
});
