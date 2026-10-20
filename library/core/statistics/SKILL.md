---
name: statistics
description: Pick and run the right statistical test - group comparisons (t-test, Mann-Whitney, ANOVA, Kruskal-Wallis), A/B proportions, correlations, bootstrap CIs - and report effect sizes and assumptions plainly. Use when asked if a difference is real or significant, or for confidence intervals.
metadata:
  category: data
---

# Statistics

`scripts/stats_tests.py` runs in the sandbox (`run_code`) on a CSV in long format (one
row per observation). It checks assumptions and picks the test, so you don't have to.

```bash
S=skills/core/statistics/scripts/stats_tests.py
python $S compare in/data.csv --group variant --value revenue          # 2+ groups, numeric outcome
python $S compare in/data.csv --group model --value score --paired item_id   # same items in each group
python $S proportions --success 120 --total 2400 --success 151 --total 2390  # A/B conversion
python $S correlate in/data.csv --x hours --y score
python $S bootstrap in/data.csv --value latency_ms --stat median --by region
```

## How it decides

- **compare, 2 groups:** Welch's t-test when both groups look normal (Shapiro-Wilk on up to
  5000 points) or have n >= 30 each; otherwise Mann-Whitney U. Paired: paired t-test or
  Wilcoxon signed-rank. Effect size: Cohen's d / Hedges' g, or rank-biserial r.
- **compare, 3+ groups:** one-way ANOVA (Welch's if variances differ, by Levene) or
  Kruskal-Wallis; then pairwise tests with Holm correction.
- **proportions:** two-proportion z-test with a 95% CI for the difference and the relative
  lift; Fisher's exact test when any cell has fewer than 5.
- **correlate:** Pearson and Spearman with 95% CIs.
- **bootstrap:** percentile CI (10,000 resamples, fixed seed) for mean, median or any
  quantile `--stat p95`.

## Reporting

- Report the effect and its CI first, the p-value second:
  "Variant B raised conversion from 5.0% to 6.3% (+1.3 pts, 95% CI +0.1 to +2.5; p = 0.03)."
- Say what the test assumed and the sample sizes.
- p > 0.05 means "no clear evidence of a difference at this sample size", not "no
  difference"; give the CI to show what effects are still plausible.
- Many comparisons inflate false positives: use the Holm-adjusted p-values the script
  prints, and say how many tests you ran.
- Correlation is not causation; mention confounders that could explain it.
