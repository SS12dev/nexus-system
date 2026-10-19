"""Build a .pptx deck from a JSON outline. See SKILL.md for the format.

python build_deck.py deck.json out.pptx [--template brand.pptx]
"""

import argparse
import json
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

# Layout indexes in the default template (and most corporate ones).
TITLE, CONTENT, SECTION, TITLE_ONLY, BLANK = 0, 1, 2, 5, 6


def rgb(hex_color: str) -> RGBColor:
    h = hex_color.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def layout(prs, index: int):
    layouts = prs.slide_layouts
    return layouts[index] if index < len(layouts) else layouts[-1]


def set_title(slide, text: str, accent: RGBColor | None) -> None:
    if slide.shapes.title is None:
        return
    slide.shapes.title.text = text
    if accent is not None:
        for p in slide.shapes.title.text_frame.paragraphs:
            for r in p.runs:
                r.font.color.rgb = accent


def body_placeholder(slide):
    for ph in slide.placeholders:
        if ph.placeholder_format.idx not in (0,) and ph.has_text_frame:
            return ph
    return None


def fill_bullets(frame, bullets, size: int = 20) -> None:
    frame.clear()
    first = True

    def add(item, level):
        nonlocal first
        if isinstance(item, list):
            for sub in item:
                add(sub, level + 1)
            return
        p = frame.paragraphs[0] if first else frame.add_paragraph()
        first = False
        p.text = str(item)
        p.level = min(level, 4)
        for r in p.runs:
            r.font.size = Pt(size - 3 * min(level, 2))

    for b in bullets:
        add(b, 0)


def content_box(prs, slide):
    """Left, top, width, height of the area under the title."""
    width, height = prs.slide_width, prs.slide_height
    top = Inches(1.5)
    return Inches(0.6), top, width - Inches(1.2), height - top - Inches(0.5)


def add_notes(slide, notes: str | None) -> None:
    if notes:
        slide.notes_slide.notes_text_frame.text = notes


def build(outline: dict, base: Path, template: Path | None) -> Presentation:
    if template:
        prs = Presentation(str(template))
        # Drop the template's own slides, keep its layouts.
        ids = prs.slides._sldIdLst
        for sld in list(ids):
            prs.part.drop_rel(sld.rId)
            ids.remove(sld)
        accent = None
    else:
        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
        accent = rgb(outline.get("accent", "#0F2A4A"))
    if outline.get("title"):
        prs.core_properties.title = outline["title"]

    for i, s in enumerate(outline.get("slides", []), 1):
        kind = s.get("layout", "bullets")
        if kind == "title":
            slide = prs.slides.add_slide(layout(prs, TITLE))
            set_title(slide, s.get("title", ""), accent)
            sub = body_placeholder(slide)
            if sub is not None:
                sub.text = s.get("subtitle", "")
        elif kind == "section":
            slide = prs.slides.add_slide(layout(prs, SECTION))
            set_title(slide, s.get("title", ""), accent)
            sub = body_placeholder(slide)
            if sub is not None:
                sub.text = s.get("subtitle", "")
        elif kind == "bullets":
            slide = prs.slides.add_slide(layout(prs, CONTENT))
            set_title(slide, s.get("title", ""), accent)
            ph = body_placeholder(slide)
            if ph is not None:
                fill_bullets(ph.text_frame, s.get("bullets", []))
        elif kind in ("image", "table", "two_column"):
            slide = prs.slides.add_slide(layout(prs, TITLE_ONLY))
            set_title(slide, s.get("title", ""), accent)
            left, top, width, height = content_box(prs, slide)
            if kind == "image":
                path = base / s["image"]
                if not path.is_file():
                    raise SystemExit(f"Slide {i}: image not found: {path}")
                cap_h = Inches(0.4) if s.get("caption") else 0
                pic = slide.shapes.add_picture(str(path), left, top)
                # Fit inside the content box, keeping the aspect ratio, centred.
                scale = min(width / pic.width, (height - cap_h) / pic.height)
                pic.width, pic.height = int(pic.width * scale), int(pic.height * scale)
                pic.left = int(left + (width - pic.width) / 2)
                if s.get("caption"):
                    box = slide.shapes.add_textbox(
                        left, top + pic.height + Emu(50000), width, cap_h
                    )
                    box.text_frame.text = s["caption"]
                    for r in box.text_frame.paragraphs[0].runs:
                        r.font.size = Pt(12)
                        r.font.italic = True
            elif kind == "table":
                rows = s.get("rows") or [[""]]
                cols = max(len(r) for r in rows)
                row_h = min(Inches(0.5), int(height / len(rows)))
                shape = slide.shapes.add_table(len(rows), cols, left, top, width, row_h * len(rows))
                for r_i, row in enumerate(rows):
                    for c_i in range(cols):
                        cell = shape.table.cell(r_i, c_i)
                        cell.text = str(row[c_i]) if c_i < len(row) else ""
                        for p in cell.text_frame.paragraphs:
                            for r in p.runs:
                                r.font.size = Pt(14 if len(rows) < 8 else 11)
                                r.font.bold = r_i == 0
            else:
                half = int((width - Inches(0.4)) / 2)
                for j, key in enumerate(("left", "right")):
                    box = slide.shapes.add_textbox(
                        left + j * (half + Inches(0.4)), top, half, height
                    )
                    box.text_frame.word_wrap = True
                    heading = s.get(f"{key}_title")
                    items = ([f"{heading}"] if heading else []) + list(s.get(key, []))
                    fill_bullets(box.text_frame, items, size=18)
                    if heading:
                        box.text_frame.paragraphs[0].runs[0].font.bold = True
        else:
            raise SystemExit(f"Slide {i}: unknown layout {kind!r}")
        add_notes(slide, s.get("notes"))
    return prs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("outline", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--template", type=Path)
    a = ap.parse_args()
    outline = json.loads(a.outline.read_text("utf-8"))
    prs = build(outline, a.outline.parent, a.template)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(a.output))
    print(f"Wrote {a.output} ({len(prs.slides)} slides)")


if __name__ == "__main__":
    main()
