import { describe, expect, it } from "vitest";
import { DEFAULT_SCHEDULE, fromRule, toRule } from "./schedule";

describe("schedule", () => {
  it("builds rules the server accepts", () => {
    expect(toRule({ ...DEFAULT_SCHEDULE, time: "07:30" })).toBe("FREQ=DAILY;BYHOUR=7;BYMINUTE=30");
    expect(toRule({ ...DEFAULT_SCHEDULE, repeat: "weekdays" })).toBe(
      "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=8;BYMINUTE=0",
    );
    expect(toRule({ ...DEFAULT_SCHEDULE, repeat: "weekly", days: ["SU", "MO"] })).toBe(
      "FREQ=WEEKLY;BYDAY=MO,SU;BYHOUR=8;BYMINUTE=0",
    );
    expect(toRule({ ...DEFAULT_SCHEDULE, repeat: "monthly", monthday: 15 })).toBe(
      "FREQ=MONTHLY;BYMONTHDAY=15;BYHOUR=8;BYMINUTE=0",
    );
  });

  it("reads back what it builds, and gives up on anything else", () => {
    for (const s of [
      { ...DEFAULT_SCHEDULE, time: "18:05" },
      { ...DEFAULT_SCHEDULE, repeat: "weekdays" as const },
      { ...DEFAULT_SCHEDULE, repeat: "weekly" as const, days: ["TU" as const, "TH" as const] },
      { ...DEFAULT_SCHEDULE, repeat: "monthly" as const, monthday: 3 },
    ]) {
      expect(fromRule(toRule(s))).toEqual(s);
    }
    expect(fromRule("FREQ=HOURLY")).toBeNull();
    expect(fromRule("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;BYHOUR=9;BYMINUTE=0")).toBeNull();
    expect(fromRule("FREQ=MONTHLY;BYMONTHDAY=-1;BYHOUR=9;BYMINUTE=0")).toBeNull();
  });
});
