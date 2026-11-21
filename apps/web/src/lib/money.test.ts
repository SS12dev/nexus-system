import { describe, expect, it } from "vitest";
import { addMonths, balanceShares, budgetState, mergeScreenshotRows, compact, daysUntil, emiFor, monthLabel, parsePercent, parseRupees, percent, rupees, shiftMonth, whenLabel } from "./money";

describe("money helpers", () => {
  it("formats rupees the Indian way", () => {
    expect(rupees(12345678)).toBe("₹1,23,456.78");
    expect(rupees(99)).toBe("₹0.99");
    expect(rupees(-100000)).toBe("−₹1,000.00");
    expect(rupees(1000000000, { whole: true })).toBe("₹1,00,00,000");
    expect(rupees(5000, { sign: true })).toBe("+₹50.00");
  });

  it("shortens amounts for charts", () => {
    expect(compact(95000)).toBe("₹950");
    expect(compact(1240000)).toBe("₹12.4k");
    expect(compact(12000000)).toBe("₹1.2L");
    expect(compact(3400000000)).toBe("₹3.4Cr");
  });

  it("moves between months", () => {
    expect(shiftMonth("2026-11", 1)).toBe("2026-12");
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(monthLabel("2026-11")).toBe("November 2026");
    expect(monthLabel("2026-11", true)).toBe("Nov");
  });

  it("reads typed rupees", () => {
    expect(parseRupees("1,250.5")).toBe(125050);
    expect(parseRupees("₹ 99")).toBe(9900);
    expect(parseRupees("abc")).toBeNull();
    expect(parseRupees("1.234")).toBeNull();
  });

  it("says whether a budget is on pace", () => {
    const b = { category: "Food", monthly_paise: 1000, spent_paise: 500 };
    expect(budgetState(b, 0.5)).toBe("ok");
    expect(budgetState({ ...b, spent_paise: 700 }, 0.5)).toBe("ahead");
    expect(budgetState({ ...b, spent_paise: 1100 }, 0.9)).toBe("over");
  });
});

describe("planning helpers", () => {
  it("works out an EMI the same way as the server", () => {
    // The PS5: ₹54,990 over 12 months at 15%.
    expect(Math.abs(emiFor(5499000, 1500, 12) - 496330)).toBeLessThan(50);
    expect(emiFor(4999000, 0, 6)).toBe(833167);
  });
  it("adds months, keeping to the end of shorter months", () => {
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-28");
    expect(addMonths("2026-11-05", 2)).toBe("2027-01-05");
  });
  it("reads percentages and says when a date is", () => {
    expect(parsePercent("7.1")).toBe(710);
    expect(parsePercent("8.25%")).toBe(825);
    expect(parsePercent("abc")).toBeNull();
    expect(percent(710)).toBe("7.1%");
    const now = new Date(2026, 10, 21);
    expect(whenLabel("2026-11-21", now)).toBe("today");
    expect(whenLabel("2026-11-22", now)).toBe("tomorrow");
    expect(whenLabel("2026-11-26", now)).toBe("in 5 days");
    expect(daysUntil("2026-11-18", now)).toBe(-3);
  });
});

describe("balanceShares", () => {
  it("splits a month into shares of income", () => {
    const b = { income_paise: 10000000, essentials_paise: 5000000, lifestyle_paise: 3500000, saved_paise: 1500000, guide: { essentials: 0.5, lifestyle: 0.3, saved: 0.2 } };
    expect(balanceShares(b)).toEqual({ essentials: 0.5, lifestyle: 0.35, saved: 0.15 });
  });
  it("is empty without income, and saved can go below zero", () => {
    const guide = { essentials: 0.5, lifestyle: 0.3, saved: 0.2 };
    expect(balanceShares({ income_paise: 0, essentials_paise: 1, lifestyle_paise: 1, saved_paise: -2, guide })).toBeNull();
    expect(balanceShares({ income_paise: 100, essentials_paise: 80, lifestyle_paise: 40, saved_paise: -20, guide })?.saved).toBeCloseTo(-0.2);
  });
});

describe("mergeScreenshotRows", () => {
  const row = (occurred_on: string, merchant: string, amount_paise: number) => ({ occurred_on, merchant, amount_paise, direction: "debit" as const });
  it("keeps overlapping screenshots' rows once, newest first", () => {
    const a = [row("2026-11-20", "Swiggy", 34900), row("2026-11-19", "Zepto", 81240)];
    const b = [row("2026-11-19", "zepto", 81240), row("2026-11-18", "Uber", 25000)];
    expect(mergeScreenshotRows([a, b]).map((r) => r.merchant)).toEqual(["Swiggy", "Zepto", "Uber"]);
  });
  it("keeps two identical rows on one screenshot", () => {
    const a = [row("2026-11-20", "Chai Point", 2000), row("2026-11-20", "Chai Point", 2000)];
    expect(mergeScreenshotRows([a, a.slice(0, 1)])).toHaveLength(2);
  });
});
