---
name: file-conversion
description: Convert between file formats - CSV, Excel, JSON and Markdown tables; Word, PowerPoint and PDF to Markdown text; Markdown to Word or PDF; images to PDF - and pick the right skill for each. Use when a task says convert, export, turn this into, or needs a file in a different format.
metadata:
  category: documents
---

# Converting files

Runs in the sandbox with `run_code`; inputs are in `in/`, write results to `out/`.

## Tables and text: one script

```bash
C=skills/core/file-conversion/scripts/convert.py
python $C in/data.csv out/data.xlsx            # CSV -> Excel (one sheet)
python $C in/book.xlsx out/book.json           # every sheet -> {"Sheet": [rows]}
python $C in/book.xlsx out/orders.csv --sheet Orders
python $C in/data.json out/data.csv            # list of objects -> CSV
python $C in/data.csv out/table.md             # Markdown table (first 200 rows; --limit N)
python $C in/report.docx out/report.md         # headings, lists and tables as Markdown
python $C in/deck.pptx out/deck.md             # one section per slide, with notes
python $C in/paper.pdf out/paper.md            # text per page (no OCR)
```

The format comes from the file extensions. CSV encoding and delimiter are detected
(UTF-8 with or without BOM, Latin-1; comma, semicolon, tab).

## Other conversions: use the matching skill

| From -> to | Use |
| --- | --- |
| Markdown -> Word | `core/docx` (`md_to_docx.py`) |
| Markdown or HTML -> PDF | `core/pdf` (`md_to_pdf.py`) |
| JSON outline -> PowerPoint | `core/pptx` (`build_deck.py`) |
| JSON -> styled Excel with formulas, charts | `core/xlsx` (`build_workbook.py`) |
| Images -> PDF, PNG <-> JPEG/WebP | `core/images` (`image_tools.py convert`) |
| PDF tables -> Excel | `core/pdf` (`pdf_tools.py tables`) |

Word or PowerPoint -> PDF needs LibreOffice, which the sandbox doesn't have: convert to
Markdown with this script, then to PDF with `core/pdf`, and tell the user the layout is
rebuilt rather than identical.

## Check the result

Open the output and print a summary (row counts, sheet names, first lines) before saying
it's done, and say what didn't carry over (formulas become values in CSV, images are
dropped from Markdown, merged cells are unmerged).
