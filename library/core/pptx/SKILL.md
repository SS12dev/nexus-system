---
name: pptx
description: Build PowerPoint decks (.pptx) from a JSON outline (title, bullet, image, chart, table, two-column and section slides with speaker notes), optionally on the user's own template, and edit existing decks (inspect, replace text, delete slides). Use whenever a task produces or changes a .pptx file.
---

# PowerPoint decks

Runs in the sandbox with `run_code`. This skill's folder is `skills/core/pptx/`; inputs are
in `in/`; write results to `out/`.

## Build a deck (most common)

1. Plan the story first: one message per slide, a title that states the takeaway ("Sales up
   18% in Q3", not "Sales"), at most 5 bullets of under 12 words each. Put detail in notes.
2. Make any charts as PNGs with the `core/charts` skill.
3. Write the outline as JSON and build:

```bash
python skills/core/pptx/scripts/build_deck.py deck.json out/deck.pptx
python skills/core/pptx/scripts/build_deck.py deck.json out/deck.pptx --template in/brand.pptx
```

```json
{
  "title": "Q3 board update",
  "accent": "#0F2A4A",
  "slides": [
    {"layout": "title", "title": "Q3 board update", "subtitle": "October 2026"},
    {"layout": "section", "title": "Results"},
    {"layout": "bullets", "title": "Sales up 18% in Q3",
     "bullets": ["EMEA led growth", ["Germany +31%", "UK +12%"], "APAC flat"],
     "notes": "Mention the one-off order in Germany."},
    {"layout": "image", "title": "Revenue by month", "image": "revenue.png",
     "caption": "Source: Q3-sales.xlsx"},
    {"layout": "table", "title": "Top accounts",
     "rows": [["Account", "Revenue"], ["Acme", "$1.2M"], ["Globex", "$0.9M"]]},
    {"layout": "two_column", "title": "Risks and actions",
     "left": ["Supply delays"], "right": ["Second supplier signed"]}
  ]
}
```

A nested list in `bullets` makes sub-bullets. Every slide can have `notes`. Image paths are
relative to the JSON file. With `--template`, slides use the template's own layouts, fonts
and colours (title = layout 0, content = layout 1, section = layout 2, title only = layout 5),
and the template's existing slides are dropped.

## Edit an existing deck

```bash
python skills/core/pptx/scripts/pptx_tools.py inspect in/deck.pptx        # slides, titles, text, notes
python skills/core/pptx/scripts/pptx_tools.py replace in/deck.pptx out/deck-v2.pptx mapping.json
python skills/core/pptx/scripts/pptx_tools.py delete in/deck.pptx out/deck-v2.pptx 3,5
```

`replace` takes old -> new text (JSON) and searches every text box, table cell and the speaker
notes, keeping run formatting. To update numbers in a deck from a spreadsheet: read the sheet
(`read_document` or openpyxl), inspect the deck, build the mapping, replace, then inspect the
result to check. For changes beyond text, use python-pptx directly (`from pptx import
Presentation`); positions are in EMU (`from pptx.util import Inches, Pt`).

## Gotchas

- python-pptx can't render slides to images or PDF; check your work with `inspect`.
- Charts made in PowerPoint itself (native charts) can be edited through
  `shape.chart.replace_data(...)`; images of charts must be regenerated.
- Never overwrite an input; write `out/<name>-v2.pptx`.
