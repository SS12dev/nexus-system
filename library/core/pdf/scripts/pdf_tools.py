"""Everyday PDF operations with pypdf (and pdfplumber for tables).

python pdf_tools.py info IN
python pdf_tools.py merge OUT IN [IN ...]
python pdf_tools.py pages IN OUT 1-3,7,5
python pdf_tools.py split IN OUT_DIR
python pdf_tools.py rotate IN OUT DEGREES [--pages 1,3]
python pdf_tools.py watermark IN OUT TEXT
python pdf_tools.py encrypt IN OUT PASSWORD
python pdf_tools.py text IN [--pages 1-2]
python pdf_tools.py tables IN OUT.xlsx [--pages 1-2]
"""

import argparse
import io
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def page_list(spec: str | None, count: int) -> list[int]:
    """'1-3,7,5' -> [0, 1, 2, 6, 4]; None -> every page."""
    if not spec:
        return list(range(count))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a) - 1, int(b)))
        elif part:
            out.append(int(part) - 1)
    bad = [p + 1 for p in out if not 0 <= p < count]
    if bad:
        raise SystemExit(f"Pages out of range (document has {count}): {bad}")
    return out


def info(a):
    r = PdfReader(a.input)
    fields = r.get_fields() or {}
    meta = {k.lstrip("/"): str(v) for k, v in (r.metadata or {}).items()}
    print(
        json.dumps(
            {
                "pages": len(r.pages),
                "encrypted": r.is_encrypted,
                "metadata": meta,
                "form_fields": sorted(fields),
                "page_size_pt": [
                    float(r.pages[0].mediabox.width),
                    float(r.pages[0].mediabox.height),
                ]
                if r.pages
                else None,
            },
            indent=2,
        )
    )


def merge(a):
    w = PdfWriter()
    for f in a.inputs:
        w.append(f)
    _write(w, a.output)


def pages(a):
    r = PdfReader(a.input)
    w = PdfWriter()
    for i in page_list(a.spec, len(r.pages)):
        w.add_page(r.pages[i])
    _write(w, a.output)


def split(a):
    r = PdfReader(a.input)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(a.input).stem
    for i, page in enumerate(r.pages, 1):
        w = PdfWriter()
        w.add_page(page)
        _write(w, out / f"{stem}-p{i:03d}.pdf")


def rotate(a):
    r = PdfReader(a.input)
    w = PdfWriter(clone_from=r)
    for i in page_list(a.pages, len(r.pages)):
        w.pages[i].rotate(a.degrees)
    _write(w, a.output)


def watermark(a):
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    r = PdfReader(a.input)
    w = PdfWriter(clone_from=r)
    for page in w.pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(width, height))
        c.setFillColor(Color(0.5, 0.5, 0.5, alpha=0.25))
        c.setFont("Helvetica-Bold", max(24, min(width, height) / 8))
        c.translate(width / 2, height / 2)
        c.rotate(45)
        c.drawCentredString(0, 0, a.text)
        c.save()
        buf.seek(0)
        page.merge_page(PdfReader(buf).pages[0])
    _write(w, a.output)


def encrypt(a):
    w = PdfWriter(clone_from=PdfReader(a.input))
    w.encrypt(a.password, algorithm="AES-256")
    _write(w, a.output)


def text(a):
    r = PdfReader(a.input)
    for i in page_list(a.pages, len(r.pages)):
        print(f"--- page {i + 1} ---")
        print(r.pages[i].extract_text() or "[no text layer]")


def tables(a):
    import pdfplumber
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    found = 0
    with pdfplumber.open(a.input) as pdf:
        for i in page_list(a.pages, len(pdf.pages)):
            for j, table in enumerate(pdf.pages[i].extract_tables(), 1):
                ws = wb.create_sheet(f"p{i + 1}-t{j}"[:31])
                for row in table:
                    ws.append([c if c is not None else "" for c in row])
                found += 1
    if not found:
        print("No tables found.")
        return
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    wb.save(a.output)
    print(f"Wrote {found} tables to {a.output}")


def _write(w: PdfWriter, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        w.write(f)
    print(f"Wrote {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("input")
    p.set_defaults(fn=info)
    p = sub.add_parser("merge")
    p.add_argument("output")
    p.add_argument("inputs", nargs="+")
    p.set_defaults(fn=merge)
    p = sub.add_parser("pages")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("spec")
    p.set_defaults(fn=pages)
    p = sub.add_parser("split")
    p.add_argument("input")
    p.add_argument("out_dir")
    p.set_defaults(fn=split)
    p = sub.add_parser("rotate")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("degrees", type=int, choices=[90, 180, 270])
    p.add_argument("--pages")
    p.set_defaults(fn=rotate)
    p = sub.add_parser("watermark")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("text")
    p.set_defaults(fn=watermark)
    p = sub.add_parser("encrypt")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("password")
    p.set_defaults(fn=encrypt)
    p = sub.add_parser("text")
    p.add_argument("input")
    p.add_argument("--pages")
    p.set_defaults(fn=text)
    p = sub.add_parser("tables")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--pages")
    p.set_defaults(fn=tables)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
