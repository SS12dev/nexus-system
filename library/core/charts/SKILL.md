---
name: charts
description: Make clean, readable chart images (bar, horizontal bar, stacked bar, line, area, pie/donut, scatter) as PNG or SVG from a small JSON spec, for reports, decks and spreadsheets. Use when a task needs a chart or graph.
---

# Charts

Runs in the sandbox with `run_code`; the script is `skills/core/charts/scripts/chart.py`.

```bash
python skills/core/charts/scripts/chart.py chart.json out/revenue.png
```

```json
{
  "type": "bar",
  "title": "Revenue by region, Q3 2026",
  "x": ["EMEA", "Americas", "APAC"],
  "series": [{"name": "Q2", "values": [1.1, 2.0, 0.8]}, {"name": "Q3", "values": [1.4, 2.1, 0.8]}],
  "y_label": "USD millions",
  "source": "Q3-sales.xlsx",
  "value_labels": true
}
```

- `type`: `bar`, `barh`, `stacked_bar`, `line`, `area`, `pie`, `donut`, `scatter`. For
  `scatter`, each series has `x` and `values` (y) lists; for `pie`/`donut` use one series.
- Optional: `y_label`, `x_label`, `source` (a footnote), `value_labels`, `y_format`
  (`"percent"`, `"thousands"`, `"millions"`, `"currency"`), `width`/`height` in inches
  (default 10 x 5.6, which fits a 16:9 slide), `dpi` (default 200), `colors` (hex list).
- The output format follows the extension (`.png` or `.svg`).

## Choosing a chart

- Compare categories: `bar` (or `barh` when labels are long or there are more than 8).
- Change over time: `line` (many points) or `bar` (few periods).
- Parts of a whole: `stacked_bar`; use `pie`/`donut` only for 2 to 5 parts.
- Relationship between two measures: `scatter`.

Title the chart with the takeaway when there is one ("APAC flat while EMEA grew"), label
units, and always give the `source`. For anything this script doesn't cover, use matplotlib
directly with `import matplotlib; matplotlib.use("Agg")`.
