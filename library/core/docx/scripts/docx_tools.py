"""Inspect and edit existing Word documents.

python docx_tools.py inspect IN
python docx_tools.py replace IN OUT mapping.json
python docx_tools.py append IN OUT extra.md
"""

import argparse
import json
import sys
from pathlib import Path

from docx import Document

sys.path.insert(0, str(Path(__file__).parent))


def inspect(a):
    doc = Document(a.input)
    outline = [
        {"level": p.style.name, "text": p.text[:120]}
        for p in doc.paragraphs
        if p.style.name.startswith(("Heading", "Title"))
    ]
    print(
        json.dumps(
            {
                "paragraphs": len(doc.paragraphs),
                "tables": [
                    {
                        "rows": len(t.rows),
                        "cols": len(t.columns),
                        "header": [c.text for c in t.rows[0].cells] if t.rows else [],
                    }
                    for t in doc.tables
                ],
                "outline": outline,
                "styles_used": sorted({p.style.name for p in doc.paragraphs}),
                "sections": len(doc.sections),
                "images": len(doc.inline_shapes),
                "title": doc.core_properties.title,
            },
            indent=2,
        )
    )


def replace_in_paragraph(p, old: str, new: str) -> int:
    """Replace across runs, keeping the formatting of the run where each match starts."""
    count = 0
    while True:
        full = "".join(r.text for r in p.runs)
        idx = full.find(old)
        if idx < 0 or not old:
            return count
        # Locate the runs covering [idx, idx + len(old)).
        pos = 0
        spans = []
        for r in p.runs:
            spans.append((pos, pos + len(r.text), r))
            pos += len(r.text)
        end = idx + len(old)
        first = True
        for s, e, r in spans:
            if e <= idx or s >= end:
                continue
            a, b = max(idx, s) - s, min(end, e) - s
            if first:
                r.text = r.text[:a] + new + r.text[b:]
                first = False
            else:
                r.text = r.text[:a] + r.text[b:]
        count += 1


def all_paragraphs(doc):
    yield from doc.paragraphs
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for s in doc.sections:
        for part in (s.header, s.footer):
            yield from part.paragraphs
            for t in part.tables:
                for row in t.rows:
                    for cell in row.cells:
                        yield from cell.paragraphs


def replace(a):
    mapping = json.loads(Path(a.mapping).read_text("utf-8"))
    doc = Document(a.input)
    counts = {k: 0 for k in mapping}
    for p in all_paragraphs(doc):
        for old, new in mapping.items():
            if old in p.text:
                counts[old] += replace_in_paragraph(p, old, str(new))
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    doc.save(a.output)
    print(json.dumps({"replacements": counts, "output": a.output}, indent=2))
    missing = [k for k, v in counts.items() if not v]
    if missing:
        print(f"Not found: {missing}", file=sys.stderr)


def append(a):
    from md_to_docx import render

    doc = Document(a.input)
    src = Path(a.markdown)
    render(doc, src.read_text("utf-8"), src.parent)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    doc.save(a.output)
    print(f"Wrote {a.output}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("input")
    p.set_defaults(fn=inspect)
    p = sub.add_parser("replace")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("mapping")
    p.set_defaults(fn=replace)
    p = sub.add_parser("append")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("markdown")
    p.set_defaults(fn=append)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
