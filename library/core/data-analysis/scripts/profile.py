"""Profile a table: types, missing values, distributions, ids, dates, duplicates, correlations.

python profile.py data.csv [--sheet NAME] [--sep ';'] [--out profile.md] [--max-cols 60]
"""

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api import types


def read(path: str, sheet: str | None, sep: str | None) -> pd.DataFrame:
    suffix = Path(path).suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, sheet_name=sheet or 0)
    if suffix == ".json":
        return pd.read_json(path)
    if suffix == ".jsonl":
        return pd.read_json(path, lines=True)
    return pd.read_csv(path, sep=sep, engine="python" if sep is None else "c")


def is_text(s: pd.Series) -> bool:
    return types.is_object_dtype(s) or types.is_string_dtype(s)


def looks_like_dates(s: pd.Series) -> float:
    sample = s.dropna().astype(str).head(200)
    if sample.empty or not sample.str.contains(r"\d").all():
        return 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    return float(parsed.notna().mean())


def looks_numeric(s: pd.Series) -> float:
    sample = s.dropna().astype(str).head(500).str.replace(r"[,$€£₹%\s]", "", regex=True)
    if sample.empty:
        return 0.0
    return float(pd.to_numeric(sample, errors="coerce").notna().mean())


def fmt(v) -> str:
    if isinstance(v, float | np.floating):
        return f"{v:,.4g}" if abs(v) < 1e6 else f"{v:,.0f}"
    return str(v)


def profile(df: pd.DataFrame, max_cols: int) -> str:
    n = len(df)
    out = [f"# Profile: {n:,} rows x {df.shape[1]} columns", ""]
    dups = int(df.duplicated().sum())
    out.append(f"- Duplicate rows: {dups:,}" + (f" ({dups / n:.1%})" if n else ""))
    out.append(f"- Memory: {df.memory_usage(deep=True).sum() / 1e6:,.1f} MB")
    empty = [c for c in df.columns if df[c].isna().all()]
    if empty:
        out.append(f"- Entirely empty columns: {', '.join(map(str, empty))}")
    out += [
        "",
        "## Columns",
        "",
        "| Column | Type | Missing | Distinct | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    notes_by_col = {}
    for c in list(df.columns)[:max_cols]:
        s = df[c]
        miss = int(s.isna().sum())
        distinct = int(s.nunique(dropna=True))
        notes = []
        if distinct == n and n > 1 and not types.is_float_dtype(s):
            notes.append("unique: likely an id")
        elif distinct == 1:
            notes.append("constant")
        if is_text(s):
            if looks_numeric(s) > 0.95:
                notes.append("numbers stored as text")
            elif looks_like_dates(s) > 0.9:
                notes.append("dates stored as text")
            stripped = s.dropna().astype(str)
            if (stripped != stripped.str.strip()).any():
                notes.append("leading/trailing spaces")
            lower = stripped.str.strip().str.lower()
            if 0 < lower.nunique() < stripped.nunique():
                notes.append("same values in different case")
        notes_by_col[c] = notes
        miss_s = f"{miss:,} ({miss / n:.0%})" if n else "0"
        out.append(f"| {c} | {s.dtype} | {miss_s} | {distinct:,} | {'; '.join(notes)} |")
    if df.shape[1] > max_cols:
        out.append(f"\n({df.shape[1] - max_cols} more columns not shown; use --max-cols)")

    num = df.select_dtypes("number")
    if not num.empty:
        out += [
            "",
            "## Numeric columns",
            "",
            "| Column | Mean | Std | Min | 25% | Median | 75% | Max | Zeros | Outliers (IQR) |",
            "| --- |" + " --- |" * 9,
        ]
        for c in list(num.columns)[:max_cols]:
            s = num[c].dropna()
            if s.empty:
                continue
            q1, q3 = s.quantile([0.25, 0.75])
            iqr = q3 - q1
            outl = int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum())
            row = [s.mean(), s.std(), s.min(), q1, s.median(), q3, s.max()]
            out.append(
                f"| {c} | "
                + " | ".join(fmt(v) for v in row)
                + f" | {int((s == 0).sum()):,} | {outl:,} |"
            )

    cats = [c for c in df.columns[:max_cols] if is_text(df[c]) or str(df[c].dtype) == "category"]
    if cats:
        out += ["", "## Top values"]
        for c in cats:
            s = df[c].dropna().astype(str)
            if s.empty or s.nunique() == len(s):
                continue
            top = s.value_counts().head(5)
            out.append(f"- **{c}**: " + ", ".join(f"{k[:40]} ({v:,})" for k, v in top.items()))

    dates = [c for c in df.columns[:max_cols] if types.is_datetime64_any_dtype(df[c])]
    for c in dates:
        s = df[c].dropna()
        if not s.empty:
            out.append(f"- **{c}** spans {s.min()} to {s.max()}")

    if num.shape[1] >= 2:
        corr = num.corr(numeric_only=True)
        pairs = []
        cols = list(corr.columns)
        for i, a in enumerate(cols):
            for b in cols[i + 1 :]:
                v = corr.loc[a, b]
                if pd.notna(v) and abs(v) >= 0.7:
                    pairs.append((abs(v), a, b, v))
        if pairs:
            out += ["", "## Strong correlations (|r| >= 0.7)"]
            for _, a, b, v in sorted(pairs, reverse=True)[:15]:
                out.append(f"- {a} ~ {b}: r = {v:.2f}")

    out += ["", "## Sample rows", "", df.head(5).to_string(max_colwidth=40)]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--sheet")
    ap.add_argument("--sep")
    ap.add_argument("--out")
    ap.add_argument("--max-cols", type=int, default=60)
    a = ap.parse_args()
    text = profile(read(a.path, a.sheet, a.sep), a.max_cols)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, "utf-8")
    print(text)


if __name__ == "__main__":
    main()
