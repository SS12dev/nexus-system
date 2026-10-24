import { describe, expect, it } from "vitest";
import { codeLanguage, modelFormat, notebookCells, parseCsv, prettyJson, previewKind, readableText } from "./preview";

describe("file previews", () => {
  it("picks a view from the file name", () => {
    expect(previewKind("game.py")).toBe("code");
    expect(codeLanguage("game.py")).toBe("python");
    expect(previewKind("report.pdf")).toBe("pdf");
    expect(previewKind("Notes.MD")).toBe("markdown");
    expect(previewKind("data.json")).toBe("json");
    expect(previewKind("plan.docx")).toBe("docx");
    expect(previewKind("deck.pptx")).toBe("extracted");
    expect(previewKind("server.log")).toBe("text");
    expect(previewKind("bracket.STL")).toBe("model3d");
    expect(previewKind("box.3mf")).toBe("model3d");
    expect(modelFormat("scene.glb")).toBe("glb");
    expect(previewKind("box.step")).toBe("cad");
    expect(modelFormat("box.step")).toBeNull();
  });

  it("turns slide markers into headings and drops other comments", () => {
    expect(readableText("<!-- Slide number: 2 -->\n# Plan<!-- note -->")).toBe("\n\n**Slide 2**\n\n\n# Plan");
  });

  it("parses quoted CSV fields", () => {
    expect(parseCsv('a,b\n"x, y","say ""hi"""\r\n1,2')).toEqual([
      ["a", "b"],
      ["x, y", 'say "hi"'],
      ["1", "2"],
    ]);
  });

  it("pretty-prints JSON and leaves broken JSON alone", () => {
    expect(prettyJson('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(prettyJson("{oops")).toBe("{oops");
  });

  it("reads notebook cells and text outputs", () => {
    const nb = JSON.stringify({
      cells: [
        { cell_type: "markdown", source: ["# Title"] },
        { cell_type: "code", source: "print(1)", outputs: [{ text: ["1\n"] }] },
      ],
    });
    expect(notebookCells(nb)).toEqual([
      { kind: "markdown", source: "# Title", outputs: [] },
      { kind: "code", source: "print(1)", outputs: ["1\n"] },
    ]);
  });
});
