"""Convert Markdown to a Word document with python-docx.

python md_to_docx.py input.md output.docx [--title T] [--template brand.docx] [--toc]
"""

import argparse
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

sys.path.insert(0, str(Path(__file__).parent))
from mdlite import inline, parse  # noqa: E402


def add_hyperlink(paragraph, text: str, url: str) -> None:
    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1D4ED8")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(color)
    props.append(underline)
    run.append(props)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def add_inline(paragraph, text: str) -> None:
    for chunk, fmt in inline(text):
        if "link" in fmt:
            add_hyperlink(paragraph, chunk, fmt["link"])
            continue
        run = paragraph.add_run(chunk)
        run.bold = fmt.get("bold")
        run.italic = fmt.get("italic")
        if fmt.get("code"):
            run.font.name = "Consolas"
            run.font.size = Pt(9.5)


def style_or(doc, name: str, fallback: str = "Normal") -> str:
    return name if name in [s.name for s in doc.styles] else fallback


def add_toc(doc) -> None:
    p = doc.add_paragraph()
    run = p.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "Table of contents: right-click and choose Update Field."
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for el in (begin, instr, sep, placeholder, end):
        run._r.append(el)
    # Ask Word to refresh fields when the file opens.
    settings = doc.settings.element
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    settings.append(update)


def render(doc, md: str, base: Path) -> None:
    usable = (
        doc.sections[-1].page_width - doc.sections[-1].left_margin - doc.sections[-1].right_margin
    )
    for b in parse(md):
        if b.kind == "heading":
            doc.add_heading(b.text, level=min(b.level, 4) if b.level > 1 else 1)
        elif b.kind == "para":
            add_inline(doc.add_paragraph(), b.text)
        elif b.kind in ("bullet", "number"):
            base_style = "List Bullet" if b.kind == "bullet" else "List Number"
            name = base_style if b.level == 0 else f"{base_style} {min(b.level + 1, 3)}"
            add_inline(
                doc.add_paragraph(style=style_or(doc, name, style_or(doc, base_style))), b.text
            )
        elif b.kind == "quote":
            p = doc.add_paragraph(style=style_or(doc, "Quote"))
            add_inline(p, b.text)
        elif b.kind == "code":
            p = doc.add_paragraph()
            run = p.add_run(b.text)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif b.kind == "image":
            path = (base / b.src) if not Path(b.src).is_absolute() else Path(b.src)
            if path.is_file():
                doc.add_picture(str(path), width=min(usable, Inches(6.3)))
                if b.text:
                    cap = doc.add_paragraph(style=style_or(doc, "Caption"))
                    cap.add_run(b.text).italic = True
            else:
                doc.add_paragraph(f"[missing image: {b.src}]")
        elif b.kind == "table":
            cols = max(len(r) for r in b.rows)
            table = doc.add_table(rows=0, cols=cols)
            table.style = style_or(doc, "Table Grid", None) or table.style
            for r_i, row in enumerate(b.rows):
                cells = table.add_row().cells
                for c_i in range(cols):
                    cell = cells[c_i]
                    cell.text = ""
                    add_inline(cell.paragraphs[0], row[c_i] if c_i < len(row) else "")
                    if r_i == 0:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
                        shade = OxmlElement("w:shd")
                        shade.set(qn("w:val"), "clear")
                        shade.set(qn("w:fill"), "EEF2F7")
                        cell._tc.get_or_add_tcPr().append(shade)
            doc.add_paragraph()
        elif b.kind == "pagebreak":
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def convert(source: Path, target: Path, title: str | None, template: Path | None, toc: bool):
    if template:
        doc = Document(str(template))
        body = doc.element.body
        for el in list(body):
            if el.tag != qn("w:sectPr"):
                body.remove(el)
    else:
        doc = Document()
        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)
        for level, size in ((1, 18), (2, 14), (3, 12)):
            h = doc.styles[f"Heading {level}"]
            h.font.size = Pt(size)
            h.font.color.rgb = RGBColor(0x0F, 0x2A, 0x4A)
    if title:
        doc.core_properties.title = title
    if toc:
        add_toc(doc)
    render(doc, source.read_text("utf-8"), source.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(target))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--title")
    ap.add_argument("--template", type=Path)
    ap.add_argument("--toc", action="store_true")
    a = ap.parse_args()
    convert(a.input, a.output, a.title, a.template, a.toc)
    print(f"Wrote {a.output}")


if __name__ == "__main__":
    main()
