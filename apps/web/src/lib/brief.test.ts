import { describe, expect, it } from "vitest";
import { clockText, nextSend, type BriefSettings } from "./brief";

const base: BriefSettings = {
  enabled: true,
  send_at: "07:30",
  days: "every",
  push: true,
  telegram: true,
  telegram_money: false,
  place: null,
  lat: null,
  lon: null,
  sections: ["weather", "calendar", "tasks", "money"],
  last_sent_on: null,
};

describe("brief", () => {
  it("writes times the way the brief does", () => {
    expect(clockText("07:30")).toBe("7:30 am");
    expect(clockText("12:00")).toBe("12 pm");
    expect(clockText("00:05")).toBe("12:05 am");
    expect(clockText("bad")).toBe("bad");
  });

  it("says when the next brief goes", () => {
    // Friday 20 November 2026, 6 am local.
    const friday = new Date(2026, 10, 20, 6, 0);
    expect(nextSend(base, friday)).toBe("Today at 7:30 am");
    expect(nextSend({ ...base, last_sent_on: "2026-11-20" }, friday)).toBe("Tomorrow at 7:30 am");
    const late = new Date(2026, 10, 20, 9, 0);
    expect(nextSend({ ...base, days: "weekdays" }, late)).toBe("Monday at 7:30 am");
    expect(nextSend({ ...base, enabled: false }, friday)).toBeNull();
  });
});
