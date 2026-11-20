import { describe, expect, it } from "vitest";
import { budgetState, compact, monthLabel, parseRupees, rupees, shiftMonth } from "./money";

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
