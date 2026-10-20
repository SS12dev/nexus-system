---
name: xlsx
description: Create Excel workbooks (.xlsx) from JSON with styled headers, number formats, formulas, totals, frozen panes, filters, column widths and native charts, and read or edit existing workbooks with openpyxl while keeping formulas. Use whenever a task produces or changes a .xlsx file or needs a spreadsheet of results.
---

# Excel workbooks

Runs in the sandbox with `run_code`. This skill's folder is `skills/core/xlsx/`; inputs are
in `in/`; write results to `out/`.

## Build a workbook (most common)

```bash
python skills/core/xlsx/scripts/build_workbook.py book.json out/data.xlsx
```

```json
{
  "sheets": [
    {
      "name": "Suppliers",
      "columns": [
        {"header": "Supplier", "width": 28},
        {"header": "Country"},
        {"header": "Capacity (GWh)", "format": "#,##0.0"},
        {"header": "Share", "format": "0.0%"},
        {"header": "Source", "width": 50}
      ],
      "rows": [["CATL", "China", 400, 0.37, "https://..."]],
      "totals": {"Capacity (GWh)": "sum", "Share": "sum"},
      "chart": {"type": "bar", "title": "Capacity by supplier", "categories": "Supplier",
                "values": ["Capacity (GWh)"]}
    },
    {"name": "Notes", "rows": [["Method", "Figures from company reports, 2025."]]}
  ]
}
```

- Headers are bold on a shaded row, panes freeze below them and autofilter is on.
- `format` is any Excel number format (`#,##0`, `0.0%`, `$#,##0.00`, `yyyy-mm-dd`).
- Pass numbers as JSON numbers. Strings that are plainly numbers (`"45,990"`, `"-3.5"`) are
  stored as numbers anyway, and ISO dates (`"2026-01-31"`) as dates; text like `"007"` stays text.
- A cell value starting with `=` is a formula (`"=C2*D2"`); `totals` adds a total row with
  `sum`, `average`, `min`, `max` or `count` formulas, so the sheet stays live.
- `chart` adds a native Excel chart (`bar`, `col`, `line`, `pie`) next to the data;
  `categories` and `values` name columns.
- Without `columns`, the first row of `rows` is used as plain data (no styling).
- ISO dates (`"2026-10-19"`) are stored as real dates.

## Read or edit a workbook

The user's uploaded spreadsheets are usually already readable with `read_document`. For exact
values, or to edit:

```bash
python skills/core/xlsx/scripts/xlsx_tools.py inspect in/sales.xlsx      # sheets, sizes, headers, formulas
```

```python
from openpyxl import load_workbook

wb = load_workbook("in/sales.xlsx")  # formulas as text, e.g. "=SUM(B2:B9)"
vals = load_workbook("in/sales.xlsx", data_only=True)  # cached values Excel last computed
ws = wb["Q3"]
ws["B10"] = 1250
wb.save("out/sales-v2.xlsx")
```

- Saving a workbook opened with `data_only=True` throws its formulas away; edit the one
  opened normally.
- openpyxl doesn't calculate formulas. Values of new formulas appear when the user opens the
  file; if you need the numbers yourself, compute them in Python (pandas is installed:
  `pd.read_excel("in/sales.xlsx", sheet_name=None)`).
- Never overwrite an input; save `out/<name>-v2.xlsx`.
