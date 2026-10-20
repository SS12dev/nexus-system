---
name: docx
description: Create Word documents (.docx) from Markdown with real headings, lists, tables and images, and edit existing ones (replace text keeping formatting, inspect structure, append sections). Use whenever a task produces or changes a .docx file.
metadata:
  category: documents
---

# Word documents

Runs in the sandbox with `run_code`. This skill's folder is `skills/core/docx/`; inputs are
in `in/`; write results to `out/`.

## Make a document (most common)

Write Markdown, then convert. Headings become Word heading styles (so the navigation pane and
a table of contents work), lists become real Word lists, tables get a styled header row.

```bash
python skills/core/docx/scripts/md_to_docx.py report.md out/report.docx --title "Q3 review"
```

Supported: `#`-`####` headings, paragraphs with `**bold**`, `*italic*`, `` `code` `` and
`[links](https://…)`, bullet and numbered lists (nested with two spaces), tables, `> quotes`,
fenced code blocks, `![alt](image.png)` images (width fits the page), `---` page breaks.

- `--template in/brand.docx` uses an existing document's styles, page size, headers and
  footers (its body is replaced). Use this when the user gives a template or house style.
- `--toc` adds a table of contents field at the top (Word fills it in when the document is
  opened; tell the user to press F9 or accept the update prompt).

## Edit an existing document

```bash
python skills/core/docx/scripts/docx_tools.py inspect in/a.docx           # outline, styles, tables
python skills/core/docx/scripts/docx_tools.py replace in/a.docx out/a-v2.docx mapping.json
python skills/core/docx/scripts/docx_tools.py append in/a.docx out/a-v2.docx extra.md
```

- `replace` takes a JSON object of old text -> new text and keeps the formatting of the run
  where each match starts; it reports how many replacements it made in body, tables, headers
  and footers. A match split across differently formatted runs is still found.
- `append` adds Markdown (same syntax as above) at the end.
- For anything else, use python-docx directly: `from docx import Document`. Paragraph styles:
  `doc.add_paragraph(text, style="List Bullet")`; tables: `doc.add_table(rows, cols,
  style="Table Grid")`.

## Gotchas

- python-docx can't render or convert to PDF. For a PDF version, write the same Markdown to
  PDF with the `core/pdf` skill.
- Never overwrite an input; save as a new file in `out/` (e.g. `<name>-v2.docx`).
- Tracked changes and comments in an input are kept as they are but can't be created.
