"""Inspect and edit existing PowerPoint decks.

python pptx_tools.py inspect IN
python pptx_tools.py replace IN OUT mapping.json
python pptx_tools.py delete IN OUT 3,5
"""

import argparse
import json
import sys
from pathlib import Path

from pptx import Presentation


def frames(slide):
    for shape in slide.shapes:
        if shape.has_text_frame:
            yield shape.text_frame
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    yield cell.text_frame
    if slide.has_notes_slide:
        yield slide.notes_slide.notes_text_frame


def inspect(a):
    prs = Presentation(a.input)
    slides = []
    for i, s in enumerate(prs.slides, 1):
        texts = []
        for shape in s.shapes:
            if shape.has_text_frame and shape != s.shapes.title and shape.text_frame.text.strip():
                texts.append(shape.text_frame.text.strip()[:300])
            if getattr(shape, "has_table", False) and shape.has_table:
                texts.append("table: " + " | ".join(c.text for c in shape.table.rows[0].cells))
            if shape.shape_type == 13:
                texts.append("[picture]")
            if getattr(shape, "has_chart", False) and shape.has_chart:
                texts.append(f"[chart: {shape.chart.chart_type}]")
        slides.append(
            {
                "slide": i,
                "layout": s.slide_layout.name,
                "title": s.shapes.title.text if s.shapes.title is not None else None,
                "text": texts,
                "notes": s.notes_slide.notes_text_frame.text if s.has_notes_slide else "",
            }
        )
    print(
        json.dumps(
            {"slides": slides, "size_in": [prs.slide_width / 914400, prs.slide_height / 914400]},
            indent=2,
        )
    )


def replace_in_paragraph(p, old: str, new: str) -> int:
    count = 0
    while old and old in "".join(r.text for r in p.runs):
        full = "".join(r.text for r in p.runs)
        idx, end, pos, first = full.find(old), full.find(old) + len(old), 0, True
        for r in p.runs:
            s, e = pos, pos + len(r.text)
            pos = e
            if e <= idx or s >= end:
                continue
            a_, b_ = max(idx, s) - s, min(end, e) - s
            if first:
                r.text = r.text[:a_] + new + r.text[b_:]
                first = False
            else:
                r.text = r.text[:a_] + r.text[b_:]
        count += 1
    return count


def replace(a):
    mapping = json.loads(Path(a.mapping).read_text("utf-8"))
    prs = Presentation(a.input)
    counts = {k: 0 for k in mapping}
    for slide in prs.slides:
        for frame in frames(slide):
            for p in frame.paragraphs:
                for old, new in mapping.items():
                    counts[old] += replace_in_paragraph(p, old, str(new))
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    prs.save(a.output)
    print(json.dumps({"replacements": counts, "output": a.output}, indent=2))
    missing = [k for k, v in counts.items() if not v]
    if missing:
        print(f"Not found: {missing}", file=sys.stderr)


def delete(a):
    prs = Presentation(a.input)
    drop = sorted({int(x) for x in a.slides.split(",") if x.strip()}, reverse=True)
    ids = prs.slides._sldIdLst
    slides = list(ids)
    for n in drop:
        if not 1 <= n <= len(slides):
            raise SystemExit(f"No slide {n} (deck has {len(slides)})")
        prs.part.drop_rel(slides[n - 1].rId)
        ids.remove(slides[n - 1])
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    prs.save(a.output)
    print(f"Deleted slides {sorted(drop)}; wrote {a.output} ({len(prs.slides)} slides)")


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
    p = sub.add_parser("delete")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("slides")
    p.set_defaults(fn=delete)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
