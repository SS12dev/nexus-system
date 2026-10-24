import { describe, expect, it } from "vitest";
import { packsFor, routineTime, scheduleText } from "./onboarding";
import type { Pack } from "./types";

const pack = (id: string, kind: Pack["kind"], match: string[] = []): Pack => ({
  id,
  label: id,
  kind,
  blurb: "",
  match,
  prompts: [],
  routines: [],
});

describe("onboarding", () => {
  const packs = [
    pack("essentials", "base"),
    pack("teaching", "role"),
    pack("software", "role", ["coding"]),
    pack("cooking", "interest"),
    pack("learning", "interest", ["reading"]),
  ];

  it("picks packs like the server does", () => {
    expect(packsFor(packs, ["teaching"], ["reading"]).map((p) => p.id)).toEqual([
      "teaching",
      "learning",
      "essentials",
    ]);
    expect(packsFor(packs, [], ["coding"]).map((p) => p.id)).toEqual(["software", "essentials"]);
    expect(packsFor(packs, [], []).map((p) => p.id)).toEqual(["essentials"]);
  });

  it("works out times from the day start", () => {
    expect(routineTime({ at: "day_start", time: "08:00" }, "07:30")).toBe("07:30");
    expect(routineTime({ at: "day_start+45", time: "08:45" }, "07:30")).toBe("08:15");
    expect(routineTime({ at: "day_start+60", time: "09:00" }, "23:30")).toBe("00:30");
    expect(routineTime({ at: "18:00", time: "18:00" }, "07:30")).toBe("18:00");
    expect(scheduleText({ schedule_text: "Every day at 08:00" }, "07:30")).toBe(
      "Every day at 07:30",
    );
  });
});
