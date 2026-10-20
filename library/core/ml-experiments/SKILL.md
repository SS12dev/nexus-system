---
name: ml-experiments
description: Design and run ML experiments properly - leakage-safe splits, cross-validated scikit-learn baselines on tabular data, metrics with spread, ablations and experiment logs - and plan larger training runs (fine-tuning, RL) done elsewhere. Use for training a model on data, feature importance, or experiment plans.
metadata:
  category: data
---

# ML experiments

The sandbox (`run_code`) has scikit-learn, scipy, pandas and numpy, but no GPU and no
network: it is for tabular baselines, analysis of results, and planning. Training large
models happens on the user's own hardware or cloud; for that, write the plan and code and
say so.

## Quick baseline on a table (most common)

```bash
python skills/core/ml-experiments/scripts/baseline.py in/data.csv --target churned
python skills/core/ml-experiments/scripts/baseline.py in/data.csv --target price --drop id,name --folds 5 --out out/baseline
```

It detects classification or regression, builds a leakage-safe pipeline (imputation,
one-hot encoding, scaling fitted inside each fold), and runs a dummy baseline, a linear
model, random forest and histogram gradient boosting with stratified/plain k-fold CV
(seed 0). It prints mean +/- std for each metric (classification: accuracy, balanced
accuracy, macro F1, ROC AUC; regression: MAE, RMSE, R^2), the top permutation
importances of the best model on a held-out split, and with `--out` saves
`results.json` and `results.md`.

Read the result critically:

- A model barely above the dummy baseline means the features carry little signal.
- Near-perfect scores usually mean leakage: a feature computed from the target, an id that
  encodes time, or duplicate rows across folds. Check the top importances.
- Use `--group <col>` when rows from the same entity (user, patient) must not be split
  across folds, and `--time <col>` for time-ordered data (forward-chaining splits).

## Experiment design checklist (any scale)

1. **Question and hypothesis:** "Does process supervision improve multi-step accuracy over
   outcome supervision at 7B scale?" Name the expected effect size.
2. **Data and splits:** train/validation/test fixed up front; test touched once. Check for
   overlap and contamination (dedupe by normalised text or hashes).
3. **Baselines:** the simplest thing that could work, the current standard method, and the
   strongest published result you can reproduce. Tune baselines with the same budget.
4. **Metrics:** one primary metric decided in advance; secondary ones for diagnosis.
5. **Variance:** at least 3 seeds; report mean +/- std or a 95% CI (`core/statistics`).
6. **Ablations:** remove one component at a time; keep everything else identical.
7. **Compute budget:** estimate GPU hours before running (for LLM fine-tuning, tokens x
   6 x parameters is the rough training FLOPs).
8. **Log:** for every run keep config, git commit, seed, data version, metrics, wall-clock.

## Writing an experiment plan for the user

Give: goal, hypothesis, setup (model, data, hyper-parameters as a table), baselines, the
run matrix (configs x seeds), metrics, expected cost, what result would confirm or reject
the hypothesis, and the code skeleton (PyTorch / Hugging Face / TRL as appropriate) in a
file they can run.
