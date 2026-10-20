"""Statistical tests that pick themselves and report effect sizes. See SKILL.md.

python stats_tests.py compare data.csv --group G --value V [--paired ID]
python stats_tests.py proportions --success 120 --total 2400 --success 151 --total 2390
python stats_tests.py correlate data.csv --x A --y B
python stats_tests.py bootstrap data.csv --value V [--stat mean|median|p95] [--by G]
"""

import argparse
import math
from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats

ALPHA = 0.05
rng = np.random.default_rng(0)


def p(v: float) -> str:
    return "p < 0.001" if v < 0.001 else f"p = {v:.3f}"


def normalish(x: np.ndarray) -> bool:
    if len(x) >= 30:
        return True
    return len(x) >= 3 and stats.shapiro(x[:5000]).pvalue > ALPHA


def holm(pvals: list[float]) -> list[float]:
    order = np.argsort(pvals)
    adj, running = [0.0] * len(pvals), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvals) - rank) * pvals[i]))
        adj[i] = running
    return adj


def two_groups(a: np.ndarray, b: np.ndarray, na: str, nb: str) -> float:
    diff = a.mean() - b.mean()
    if normalish(a) and normalish(b):
        r = stats.ttest_ind(a, b, equal_var=False)
        sp = math.sqrt(
            ((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2)
        )
        d = diff / sp if sp else float("nan")
        g = d * (1 - 3 / (4 * (len(a) + len(b)) - 9))
        se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        dof = se**4 / (
            (a.var(ddof=1) / len(a)) ** 2 / (len(a) - 1)
            + (b.var(ddof=1) / len(b)) ** 2 / (len(b) - 1)
        )
        t = stats.t.ppf(0.975, dof)
        print(f"Welch's t-test: t = {r.statistic:.3f}, {p(r.pvalue)}")
        print(
            f"Mean difference {na} - {nb} = {diff:.4g} (95% "
            f"CI {diff - t * se:.4g} to {diff + t * se:.4g})"
        )
        print(f"Effect size: Hedges' g = {g:.3f} ({size(abs(g), (0.2, 0.5, 0.8))})")
        return r.pvalue
    r = stats.mannwhitneyu(a, b, alternative="two-sided")
    rb = 1 - 2 * r.statistic / (len(a) * len(b))
    print("Groups don't look normal and are small: Mann-Whitney U test")
    print(f"U = {r.statistic:.1f}, {p(r.pvalue)}; medians {np.median(a):.4g} vs {np.median(b):.4g}")
    print(f"Effect size: rank-biserial r = {-rb:.3f} ({size(abs(rb), (0.1, 0.3, 0.5))})")
    return r.pvalue


def size(v: float, cuts: tuple[float, float, float]) -> str:
    return (
        "negligible"
        if v < cuts[0]
        else "small"
        if v < cuts[1]
        else "medium"
        if v < cuts[2]
        else "large"
    )


def compare(df: pd.DataFrame, group: str, value: str, paired: str | None) -> None:
    df = df[[group, value] + ([paired] if paired else [])].dropna()
    names = sorted(df[group].unique(), key=str)
    if len(names) < 2:
        raise SystemExit(f"{group} needs at least two groups")
    for n in names:
        x = df.loc[df[group] == n, value]
        print(
            f"{n}: n = {len(x)}, mean = {x.mean():.4g}, sd "
            f"= {x.std():.4g}, median = {x.median():.4g}"
        )
    print()
    if paired:
        wide = df.pivot_table(index=paired, columns=group, values=value, aggfunc="mean").dropna()
        print(f"Paired on {paired}: {len(wide)} items present in every group")
        if len(names) == 2:
            a, b = wide[names[0]].to_numpy(), wide[names[1]].to_numpy()
            d = a - b
            if normalish(d):
                r = stats.ttest_rel(a, b)
                dz = d.mean() / d.std(ddof=1) if d.std(ddof=1) else float("nan")
                print(
                    f"Paired t-test: t = {r.statistic:.3f}, {p(r.pvalue)}; "
                    f"mean diff = {d.mean():.4g}; d_z = {dz:.3f}"
                )
            else:
                r = stats.wilcoxon(a, b)
                print(
                    f"Wilcoxon signed-rank: W = {r.statistic:.1f}, "
                    f"{p(r.pvalue)}; median diff = {np.median(d):.4g}"
                )
            lo, hi = boot(d, np.mean)
            print(f"Bootstrap 95% CI of the mean difference: {lo:.4g} to {hi:.4g}")
            return
        r = stats.friedmanchisquare(*[wide[n].to_numpy() for n in names])
        print(f"Friedman test: chi2 = {r.statistic:.3f}, {p(r.pvalue)}")
        return
    groups = [df.loc[df[group] == n, value].to_numpy(dtype=float) for n in names]
    if len(names) == 2:
        two_groups(groups[0], groups[1], str(names[0]), str(names[1]))
        return
    if all(normalish(g) for g in groups):
        equal = stats.levene(*groups).pvalue > ALPHA
        if equal:
            r = stats.f_oneway(*groups)
            label = "One-way ANOVA"
        else:
            r = stats.alexandergovern(*groups)
            label = "Variances differ: Alexander-Govern test (Welch-type ANOVA)"
        grand = np.concatenate(groups)
        ss_b = sum(len(g) * (g.mean() - grand.mean()) ** 2 for g in groups)
        eta = ss_b / ((grand - grand.mean()) ** 2).sum()
        print(f"{label}: statistic = {r.statistic:.3f}, {p(r.pvalue)}; eta^2 = {eta:.3f}")
        test = lambda a, b: stats.ttest_ind(a, b, equal_var=False).pvalue  # noqa: E731
    else:
        r = stats.kruskal(*groups)
        print(f"Kruskal-Wallis: H = {r.statistic:.3f}, {p(r.pvalue)}")
        test = lambda a, b: stats.mannwhitneyu(a, b).pvalue  # noqa: E731
    pairs = list(combinations(range(len(names)), 2))
    raw = [test(groups[i], groups[j]) for i, j in pairs]
    print("\nPairwise (Holm-adjusted):")
    for (i, j), pr, pa in zip(pairs, raw, holm(raw), strict=True):
        print(
            f"- {names[i]} vs {names[j]}: diff = "
            f"{groups[i].mean() - groups[j].mean():.4g}, {p(pr)}, adjusted {p(pa)}"
        )


def proportions(succ: list[int], tot: list[int]) -> None:
    if len(succ) != 2 or len(tot) != 2:
        raise SystemExit("give --success and --total twice: group A then group B")
    (x1, x2), (n1, n2) = succ, tot
    p1, p2 = x1 / n1, x2 / n2
    print(f"A: {x1}/{n1} = {p1:.2%}; B: {x2}/{n2} = {p2:.2%}")
    table = [[x1, n1 - x1], [x2, n2 - x2]]
    if min(min(r) for r in table) < 5:
        r = stats.fisher_exact(table)
        print(f"Small counts: Fisher's exact test, {p(r.pvalue)}")
    else:
        pool = (x1 + x2) / (n1 + n2)
        z = (p2 - p1) / math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
        pv = 2 * stats.norm.sf(abs(z))
        print(f"Two-proportion z-test: z = {z:.3f}, {p(pv)}")
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = p2 - p1
    print(
        f"Difference B - A = {d * 100:+.2f} pts (95% CI "
        f"{(d - 1.96 * se) * 100:+.2f} to {(d + 1.96 * se) * 100:+.2f})"
    )
    if p1:
        print(f"Relative lift = {d / p1:+.1%}")


def correlate(df: pd.DataFrame, x: str, y: str) -> None:
    d = df[[x, y]].dropna()
    n = len(d)
    for name, fn in (("Pearson r", stats.pearsonr), ("Spearman rho", stats.spearmanr)):
        r, pv = fn(d[x], d[y])
        z, se = math.atanh(max(min(r, 0.999999), -0.999999)), 1 / math.sqrt(max(n - 3, 1))
        print(
            f"{name} = {r:.3f} (95% CI {math.tanh(z - 1.96 * se):.3f} "
            f"to {math.tanh(z + 1.96 * se):.3f}), {p(pv)}, n = {n}"
        )


def boot(x: np.ndarray, fn, n: int = 10_000) -> tuple[float, float]:
    idx = rng.integers(0, len(x), size=(n, len(x)))
    vals = (
        np.apply_along_axis(fn, 1, x[idx]) if fn not in (np.mean, np.median) else fn(x[idx], axis=1)
    )
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def bootstrap(df: pd.DataFrame, value: str, stat: str, by: str | None) -> None:
    if stat == "mean":
        fn = np.mean
    elif stat == "median":
        fn = np.median
    elif stat.startswith("p") and stat[1:].isdigit():
        q = int(stat[1:])
        fn = lambda a: np.percentile(a, q)  # noqa: E731
    else:
        raise SystemExit("--stat: mean, median or pNN (e.g. p95)")
    parts = df.groupby(by) if by else [("all", df)]
    for name, part in parts:
        x = part[value].dropna().to_numpy(dtype=float)
        if len(x) < 2:
            continue
        lo, hi = boot(x, fn)
        print(f"{name}: {stat} = {float(fn(x)):.4g} (95% CI {lo:.4g} to {hi:.4g}), n = {len(x)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("csv")
    c.add_argument("--group", required=True)
    c.add_argument("--value", required=True)
    c.add_argument("--paired")
    pr = sub.add_parser("proportions")
    pr.add_argument("--success", type=int, action="append", required=True)
    pr.add_argument("--total", type=int, action="append", required=True)
    co = sub.add_parser("correlate")
    co.add_argument("csv")
    co.add_argument("--x", required=True)
    co.add_argument("--y", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("csv")
    b.add_argument("--value", required=True)
    b.add_argument("--stat", default="mean")
    b.add_argument("--by")
    a = ap.parse_args()
    if a.cmd == "proportions":
        proportions(a.success, a.total)
        return
    df = pd.read_csv(a.csv)
    if a.cmd == "compare":
        compare(df, a.group, a.value, a.paired)
    elif a.cmd == "correlate":
        correlate(df, a.x, a.y)
    else:
        bootstrap(df, a.value, a.stat, a.by)


if __name__ == "__main__":
    main()
