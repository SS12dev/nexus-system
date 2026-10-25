import { describe, expect, it } from "vitest";
import type { Widget } from "./types";
import { clock, elapsed, frameHeight, isRinging, pendingAsks, repeatText, timerLeft, zoneCity } from "./widgets";

const T0 = Date.parse("2026-10-25T18:00:00Z");
const widget = (kind: Widget["kind"], state: Widget["state"]): Widget => ({
  id: "w1",
  kind,
  title: "",
  state,
  slot: 1,
  chat_id: null,
  fires_at: null,
  created_at: "",
  updated_at: "",
});

describe("widgets", () => {
  it("formats durations like a clock", () => {
    expect(clock(75)).toBe("1:15");
    expect(clock(3725)).toBe("1:02:05");
    expect(clock(-3)).toBe("0:00");
  });

  it("counts a timer down from ends_at and keeps a paused one still", () => {
    const running = widget("timer", { status: "running", ends_at: "2026-10-25T18:05:00Z", duration_s: 300 });
    expect(timerLeft(running, T0 + 60_000)).toBe(240);
    expect(isRinging(running, T0 + 60_000)).toBe(false);
    // Rings here at zero, before the server's next pass says so.
    expect(isRinging(running, T0 + 300_000)).toBe(true);
    expect(timerLeft(widget("timer", { status: "paused", remaining_s: 90 }), T0)).toBe(90);
  });

  it("stops ringing on screen after half an hour", () => {
    const w = widget("alarm", { status: "ringing", rang_at: "2026-10-25T18:00:00Z" });
    expect(isRinging(w, T0 + 60_000)).toBe(true);
    expect(isRinging(w, T0 + 31 * 60_000)).toBe(false);
  });

  it("adds a running stopwatch's time to what it had", () => {
    const w = widget("stopwatch", { status: "running", started_at: "2026-10-25T18:00:00Z", elapsed_s: 10 });
    expect(elapsed(w, T0 + 5000)).toBe(15);
  });

  it("says how an alarm repeats", () => {
    expect(repeatText(null)).toBe("Once");
    expect(repeatText("FREQ=DAILY;BYHOUR=7;BYMINUTE=0")).toBe("Every day");
    expect(repeatText("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=7;BYMINUTE=0")).toBe("Weekdays");
    expect(repeatText("FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=7;BYMINUTE=0")).toBe("Mon, Thu");
    expect(zoneCity("America/New_York")).toBe("New York");
  });
});

describe("custom widgets", () => {
  it("keeps the frame near its size class", () => {
    expect(frameHeight("m")).toBe(200);
    expect(frameHeight("s", 1000)).toBe(180);
    expect(frameHeight("l", 10)).toBe(160);
    expect(frameHeight("m", 260)).toBe(260);
  });

  it("lists only what isn't allowed yet", () => {
    const w = widget("custom", {
      manifest: { size: "m", hosts: ["api.open-meteo.com", "x.io"], data: ["calendar.today"], about: "" },
      approved: { hosts: ["x.io"], data: [] },
    });
    expect(pendingAsks(w)).toEqual({ hosts: ["api.open-meteo.com"], data: ["calendar.today"] });
  });
});
