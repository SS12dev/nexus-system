"""Convert Markdown (or HTML) to a styled PDF with WeasyPrint.

python md_to_pdf.py input.md output.pdf [--title T] [--css extra.css] [--html]
"""

import argparse
import html
from pathlib import Path

import markdown
from weasyprint import CSS, HTML

BASE_CSS = """
@page { size: A4; margin: 22mm 18mm 20mm 18mm;
  @bottom-center { content: counter(page) " / " counter(pages); font-size: 9pt; color: #666; } }
html { font-family: "DejaVu Sans", "Liberation Sans", "Noto Sans", sans-serif; font-size: 11pt;
  line-height: 1.45; color: #1a1a1a; }
h1 { font-size: 22pt; margin: 0 0 12pt; color: #0f2a4a; }
h2 { font-size: 15pt; margin: 18pt 0 6pt; color: #0f2a4a; border-bottom: 1px solid #d0d7e2;
  padding-bottom: 2pt; }
h3 { font-size: 12.5pt; margin: 14pt 0 4pt; }
p, ul, ol { margin: 0 0 8pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0 12pt; font-size: 10pt; }
th, td { border: 1px solid #c8ced8; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #eef2f7; font-weight: bold; }
tr:nth-child(even) td { background: #fafbfd; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 9.5pt; background: #f3f4f6;
  padding: 0 2pt; }
pre { background: #f3f4f6; padding: 6pt 8pt; white-space: pre-wrap; font-size: 9pt; }
pre code { background: none; padding: 0; }
blockquote { margin: 0 0 8pt; padding-left: 10pt; border-left: 3px solid #c8ced8; color: #444; }
img { max-width: 100%; }
a { color: #1d4ed8; text-decoration: none; }
"""


def convert(source: Path, target: Path, title: str | None, css: Path | None, is_html: bool):
    text = source.read_text("utf-8")
    if is_html:
        body = text
    else:
        body = markdown.markdown(
            text, extensions=["tables", "fenced_code", "sane_lists", "toc", "attr_list"]
        )
    head_title = html.escape(title or source.stem)
    doc = f'<!doctype html><html><head><meta charset="utf-8"><title>{head_title}</title>'
    doc += f"</head><body>{body}</body></html>"
    sheets = [CSS(string=BASE_CSS)]
    if css:
        sheets.append(CSS(filename=str(css)))
    target.parent.mkdir(parents=True, exist_ok=True)
    # base_url lets relative image paths resolve next to the source file.
    HTML(string=doc, base_url=str(source.parent.resolve())).write_pdf(
        str(target), stylesheets=sheets
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--title")
    ap.add_argument("--css", type=Path)
    ap.add_argument("--html", action="store_true", help="input is HTML, not Markdown")
    a = ap.parse_args()
    convert(a.input, a.output, a.title, a.css, a.html)
    print(f"Wrote {a.output}")


if __name__ == "__main__":
    main()
