"""Compare scored LLM/agent results with bootstrap CIs and paired tests. See SKILL.md.

python eval_report.py summary results.csv [--by category]
python eval_report.py compare results.csv --a SYSTEM_A --b SYSTEM_B
python eval_report.py agreement labels.csv --rater1 human --rater2 judge
Input: CSV or JSONL, one row per (system, item_id) with a numeric `score`.
"""

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

rng = np.random.default_rng(0)
N_BOOT = 10_000


def read(path: str) -> pd.DataFrame:
    if Path(path).suffix.lower() in (".jsonl", ".ndjson"):
        return pd.read_json(path, lines=True)
    if Path(path).suffix.lower() == ".json":
        return pd.read_json(path)
    return pd.read_csv(path)


def ci(x: np.ndarray) -> tuple[float, float]:
    if len(x) < 2:
        return float("nan"), float("nan")
    means = x[rng.integers(0, len(x), size=(N_BOOT, len(x)))].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def summary(df: pd.DataFrame, by: str | None) -> None:
    extras = [c for c in ("cost_usd", "latency_s", "tokens") if c in df.columns]
    head = "| System | n | Mean score | 95% CI |" + "".join(f" mean {c} |" for c in extras)
    print(head)
    print("| --- |" + " --- |" * (3 + len(extras)))
    for system, part in df.groupby("system"):
        x = part["score"].to_numpy(dtype=float)
        lo, hi = ci(x)
        row = f"| {system} | {len(x)} | {x.mean():.3f} | {lo:.3f} to {hi:.3f} |"
        row += "".join(f" {part[c].mean():.4g} |" for c in extras)
        print(row)
    by = by or ("category" if "category" in df.columns else None)
    if by:
        print(f"\nBy {by} (mean score, n):")
        table = df.pivot_table(
            index=by, columns="system", values="score", aggfunc=["mean", "count"]
        )
        systems = sorted(df["system"].unique(), key=str)
        print(f"| {by} | " + " | ".join(map(str, systems)) + " |")
        print("| --- |" + " --- |" * len(systems))
        for cat in table.index:
            cells = [
                f"{table.loc[cat, ('mean', s)]:.3f} ({int(table.loc[cat, ('count', s)])})"
                if pd.notna(table.loc[cat, ("mean", s)])
                else "·"
                for s in systems
            ]
            print(f"| {cat} | " + " | ".join(cells) + " |")
    if "response_chars" in df.columns or "length" in df.columns:
        col = "response_chars" if "response_chars" in df.columns else "length"
        r, pv = stats.spearmanr(df[col], df["score"])
        print(f"\nLength bias check: Spearman rho(score, {col}) = {r:.3f} (p = {pv:.3g})")


def compare(df: pd.DataFrame, a: str, b: str) -> None:
    wide = df[df["system"].isin([a, b])].pivot_table(
        index="item_id", columns="system", values="score", aggfunc="mean"
    )
    missing = [s for s in (a, b) if s not in wide.columns]
    if missing:
        raise SystemExit(f"no rows for system(s): {', '.join(missing)}")
    wide = wide.dropna()
    xa, xb = wide[a].to_numpy(dtype=float), wide[b].to_numpy(dtype=float)
    d = xb - xa
    lo, hi = ci(d)
    print(f"{len(wide)} items scored for both systems")
    print(f"{a}: {xa.mean():.3f}; {b}: {xb.mean():.3f}")
    print(
        f"Difference {b} - {a} = {d.mean():+.3f} (paired bootstrap 95% CI {lo:+.3f} to {hi:+.3f})"
    )
    wins, losses, ties = int((d > 0).sum()), int((d < 0).sum()), int((d == 0).sum())
    print(f"{b} better on {wins} items, worse on {losses}, tied on {ties}")
    binary = set(np.unique(np.concatenate([xa, xb]))) <= {0.0, 1.0}
    if binary:
        # McNemar's exact test on the discordant pairs.
        n = wins + losses
        pv = stats.binomtest(wins, n, 0.5).pvalue if n else 1.0
        print(f"McNemar exact test on {n} discordant items: p = {pv:.4g}")
    elif wins + losses:
        pv = stats.wilcoxon(xb, xa, zero_method="zsplit").pvalue
        print(f"Wilcoxon signed-rank test: p = {pv:.4g}")
    else:
        pv = 1.0
    verdict = (
        "no established difference (the CI includes 0)"
        if lo <= 0 <= hi
        else (f"{b} is better" if d.mean() > 0 else f"{a} is better")
    )
    print(f"Verdict: {verdict}")
    sd = d.std(ddof=1) if len(d) > 1 else float("nan")
    if sd and not math.isnan(sd):
        need = math.ceil((2.8 * sd / 0.05) ** 2)
        print(f"Items needed to detect a 0.05 difference with 80% power: about {need}")


def agreement(df: pd.DataFrame, r1: str, r2: str) -> None:
    d = df[[r1, r2]].dropna()
    x, y = d[r1].astype(str), d[r2].astype(str)
    labels = sorted(set(x) | set(y))
    po = float((x == y).mean())
    pe = sum((x == lab).mean() * (y == lab).mean() for lab in labels)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    print(f"{len(d)} items; raw agreement {po:.1%}; Cohen's kappa = {kappa:.3f}")
    print("Interpretation: < 0.4 poor, 0.4-0.6 moderate, 0.6-0.8 substantial, > 0.8 near perfect")
    try:
        xs, ys = pd.to_numeric(d[r1]), pd.to_numeric(d[r2])
        r, _ = stats.spearmanr(xs, ys)
        print(f"Spearman rho between numeric scores = {r:.3f}")
    except (ValueError, TypeError):
        pass
    print("\nConfusion (rows = " + r1 + ", columns = " + r2 + "):")
    print(pd.crosstab(x, y).to_string())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary")
    s.add_argument("data")
    s.add_argument("--by")
    c = sub.add_parser("compare")
    c.add_argument("data")
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    g = sub.add_parser("agreement")
    g.add_argument("data")
    g.add_argument("--rater1", required=True)
    g.add_argument("--rater2", required=True)
    a = ap.parse_args()
    df = read(a.data)
    if a.cmd == "agreement":
        agreement(df, a.rater1, a.rater2)
        return
    for col in ("system", "item_id", "score"):
        if col not in df.columns:
            raise SystemExit(f"the results need a {col!r} column")
    if a.cmd == "summary":
        summary(df, a.by)
    else:
        compare(df, a.a, a.b)


if __name__ == "__main__":
    main()
