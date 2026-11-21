import { describe, expect, it } from "vitest";
import { secretChange } from "./FormDialog";

describe("secretChange", () => {
  it("keeps a saved secret unless it was typed over or shown and emptied", () => {
    expect(secretChange({ account_no: "" }, "account_no")).toBeUndefined();
    expect(secretChange({ account_no: "  1234 5678 " }, "account_no")).toBe("1234 5678");
    expect(secretChange({ account_no: "", "account_no:shown": "1" }, "account_no")).toBe("");
  });
});
