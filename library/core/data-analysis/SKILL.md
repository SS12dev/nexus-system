---
name: data-analysis
description: Explore, clean and analyse tabular data (CSV, Excel, JSON) with pandas - a one-command profile of every column, a cleaning checklist, group-by summaries, and turning results into tables and charts. Use whenever a task involves a dataset, a spreadsheet of numbers, or questions like "what does this data show".
metadata:
  category: data
---

# Data analysis

Runs in the sandbox with `run_code` (pandas, numpy, scipy, scikit-learn, matplotlib).
The user's files are in `in/`; write results to `out/`. Pair with `core/charts` for figures,
`core/xlsx` for a styled workbook, and `core/statistics` for significance tests.

## 1. Profile first (always)

```bash
python skills/core/data-analysis/scripts/profile.py in/sales.csv
python skills/core/data-analysis/scripts/profile.py in/book.xlsx --sheet Orders --out out/profile.md
```

Prints shape, each column's type, missing values, distinct values, numeric summaries,
top categories, likely ids and dates, duplicate rows, and strong correlations. Read it
before writing analysis code: it tells you what to clean.

## 2. Clean, and log every change

```python
import pandas as pd

df = pd.read_csv("in/sales.csv")
log = []
before = len(df)
df = df.drop_duplicates()
log.append(f"dropped {before - len(df)} duplicate rows")
df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)  # check the format first
df["amount"] = pd.to_numeric(
    df["amount"].astype(str).str.replace(r"[,$€₹ ]", "", regex=True), errors="coerce"
)
df["country"] = df["country"].str.strip().str.title()
```

- Decide per column what missing means (unknown, zero, not applicable) instead of a
  blanket `fillna(0)`.
- Never silently drop rows: report how many and why.
- Check units and currencies before summing; check date formats (day-first in India and
  Europe).
- Keep the raw file untouched; save cleaned data as `out/<name>-clean.csv` if it's useful.

## 3. Answer the question

- Restate the question as a computation ("monthly revenue by region, 2025, in INR").
- Prefer `groupby(...).agg(...)` tables over prose; sort by what matters.
- Sanity-check: totals match the source, percentages add to 100, row counts are what you
  expect after each join (`validate="one_to_one"` / `"many_to_one"` in `merge`).
- Large files: read only needed columns (`usecols=`), use `chunksize=` for > 1 GB.

## 4. Report

Lead with the answer and the 2 to 3 numbers that support it, then a small table, then a
chart if a trend or comparison matters, then caveats (missing data, cleaning steps,
assumptions). Print tables with `df.to_markdown(index=False)` (needs no extra library:
use `df.to_string()` if `tabulate` is missing).
