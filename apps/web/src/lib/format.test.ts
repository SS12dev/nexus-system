import { describe, expect, it } from "vitest";
import { describeAgent, formatAgo, formatTokens, isPicture, screenshotName } from "./format";

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

describe("pictures", () => {
  it("knows which files the assistant can look at", () => {
    expect(isPicture("Screen Shot.PNG")).toBe(true);
    expect(isPicture("photo.jpeg")).toBe(true);
    expect(isPicture("scan.tiff")).toBe(false);
    expect(isPicture("notes.md")).toBe(false);
  });

  it("names a pasted screenshot by when it was taken", () => {
    const at = new Date(2026, 9, 26, 9, 5, 7);
    expect(screenshotName("image/png", at)).toBe("screenshot-2026-10-26-090507.png");
    expect(screenshotName("image/jpeg", at)).toBe("screenshot-2026-10-26-090507.jpg");
  });
});
