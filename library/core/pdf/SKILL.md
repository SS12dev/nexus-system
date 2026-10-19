---
name: pdf
description: Create PDFs from Markdown or HTML (reports, letters, invoices), and merge, split, rotate, watermark, fill forms in, or extract text and tables from existing PDFs. Use whenever a task produces or changes a .pdf file.
---

# PDF

Everything runs in the sandbox with `run_code`. This skill's folder is
`skills/core/pdf/`; inputs you were given are in `in/`; write results to `out/`.

## Make a PDF (most common)

Write the content as Markdown (headings, lists, tables, code blocks all work), then convert:

```bash
python skills/core/pdf/scripts/md_to_pdf.py report.md out/report.pdf --title "EV battery suppliers"
```

- `--title` sets the document title (and the PDF metadata).
- `--css extra.css` adds your own styles; `--html` treats the input as HTML instead.
- Images: reference files by relative path (`![chart](chart.png)`); make charts first with
  the `core/charts` skill and save them next to the Markdown file.
- Page breaks: put `<div style="page-break-after: always"></div>` on its own line.
- The default style is A4, 11pt, with page numbers in the footer. Tables get borders and a
  shaded header row.

For pixel-exact layouts (forms, certificates), use ReportLab directly
(`from reportlab.pdfgen import canvas`); coordinates are in points from the bottom left.

## Change an existing PDF

`scripts/pdf_tools.py` wraps pypdf:

| Task | Command |
| --- | --- |
| Page count, metadata, form fields | `python skills/core/pdf/scripts/pdf_tools.py info in/a.pdf` |
| Merge | `... merge out/all.pdf in/a.pdf in/b.pdf` |
| Pick or reorder pages (1-based) | `... pages in/a.pdf out/part.pdf 1-3,7,5` |
| Split into one file per page | `... split in/a.pdf out/pages/` |
| Rotate pages | `... rotate in/a.pdf out/r.pdf 90 --pages 2,4` |
| Text watermark on every page | `... watermark in/a.pdf out/w.pdf "CONFIDENTIAL"` |
| Encrypt with a password | `... encrypt in/a.pdf out/locked.pdf secret` |

## Read a PDF

The user's uploaded PDFs are usually already readable through `read_document`, which is
cheaper than code. Use code when you need exact layout or tables:

```bash
python skills/core/pdf/scripts/pdf_tools.py text in/a.pdf --pages 1-2
python skills/core/pdf/scripts/pdf_tools.py tables in/a.pdf out/tables.xlsx
```

`tables` uses pdfplumber and writes one sheet per table found. Scanned PDFs (images only)
have no text layer; say so rather than guessing their contents.

## Fill a form

```bash
python skills/core/pdf/scripts/fill_form.py fields in/form.pdf           # list field names
python skills/core/pdf/scripts/fill_form.py fill in/form.pdf out/filled.pdf values.json
```

`values.json` maps field names to values; checkboxes take `true`/`false`. Check the result
with `fields out/filled.pdf`.

## Gotchas

- Always write to `out/`; anything else is thrown away when the call ends.
- Don't overwrite the input; write a new file such as `out/<name>-v2.pdf`.
- WeasyPrint has no JavaScript and only uses fonts installed in the sandbox (DejaVu, Liberation, Noto).
