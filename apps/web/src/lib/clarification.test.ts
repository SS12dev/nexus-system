import { describe, expect, it } from "vitest";
import { parseClarification } from "./clarification";

describe("parseClarification", () => {
  it("extracts bounded selectable questions and removes the hidden block", () => {
    const answer = parseClarification(
      `Before I start, who is this for?\n<!-- nexus-clarification\n${JSON.stringify({
        questions: [
          {
            question: "Who is the audience?",
            options: ["Customers", "Internal team"],
            recommended: 1,
          },
        ],
      })}\n-->`,
    );
    expect(answer).toEqual({
      content: "Before I start, who is this for?",
      questions: [
        {
          question: "Who is the audience?",
          options: ["Customers", "Internal team"],
          recommended: 1,
        },
      ],
    });
  });

  it("returns null for ordinary answers and safely ignores malformed card data", () => {
    expect(parseClarification("A direct answer.")).toBeNull();
    expect(parseClarification("Which format?\n<!-- nexus-clarification nope -->")).toEqual({
      content: "Which format?",
      questions: [],
    });
  });
});
