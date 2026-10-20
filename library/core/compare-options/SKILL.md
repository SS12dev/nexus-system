---
name: compare-options
description: Compare products, tools, vendors, models or plans with a weighted decision matrix - criteria and weights, normalised scores from real data, a ranking, and a sensitivity check showing whether the winner changes when weights shift. Use for "which should I choose", "compare X vs Y", shortlists, buying decisions, and technology or model selection.
metadata:
  category: productivity
---

# Comparing options

## Steps

1. **Criteria from the user's goal**, 4 to 8 of them (price, performance, ease of use,
   support, risk...). Mark must-haves separately: options failing one are out, whatever
   their score.
2. **Weights** that sum to 100, from what the user said matters; state them so the user can
   change them.
3. **Facts per option** from sources (`core/web-research`): raw values with units and a
   source each (price in INR/USD with date, benchmark scores, specs). Use raw values where
   they exist; use a 1-5 rating only for judgement criteria, with a one-line reason.
4. **Score** with the script, then sanity-check the ranking against intuition and explain
   any surprise.

```bash
python skills/core/compare-options/scripts/decision_matrix.py options.json --md out/comparison.md --xlsx out/comparison.xlsx
```

```json
{
  "criteria": [
    {"name": "Price (USD/month)", "weight": 30, "better": "lower"},
    {"name": "Benchmark score", "weight": 40, "better": "higher"},
    {"name": "Ease of setup", "weight": 30, "better": "higher", "scale": [1, 5]}
  ],
  "options": [
    {"name": "Option A", "values": {"Price (USD/month)": 20, "Benchmark score": 71, "Ease of setup": 4},
     "notes": "https://... (Oct 2026)"},
    {"name": "Option B", "values": {"Price (USD/month)": 35, "Benchmark score": 78, "Ease of setup": 3},
     "must_fail": "no SSO"}
  ]
}
```

- Values are min-max normalised to 0-1 per criterion (flipped for `"better": "lower"`);
  `scale` fixes the range for ratings so one option isn't 0 just for being lowest.
- `must_fail` marks an option that misses a must-have: it is listed but not ranked.
- The script prints the ranking, each option's strongest and weakest criteria, and the
  sensitivity check: whether the winner survives each weight moving +/-20 points.

## Report

Lead with the recommendation and why in one or two sentences, then the matrix, then
"when you'd pick something else" (e.g. "if price matters more than 45%, Option A wins").
Give dates for prices and versions; they go stale fast.
