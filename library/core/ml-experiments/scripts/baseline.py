"""Cross-validated baselines for a tabular dataset with scikit-learn. See SKILL.md.

python baseline.py data.csv --target COL [--task auto|classification|regression]
       [--drop a,b] [--group COL | --time COL] [--folds 5] [--sample 50000] [--out out/baseline]
"""

import argparse
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api import types
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.model_selection import (
    GroupKFold,
    KFold,
    StratifiedKFold,
    TimeSeriesSplit,
    cross_validate,
    train_test_split,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

SEED = 0
warnings.filterwarnings("ignore")


def read(path: str) -> pd.DataFrame:
    suffix = Path(path).suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path)
    if suffix == ".jsonl":
        return pd.read_json(path, lines=True)
    return pd.read_csv(path)


def preprocess(X: pd.DataFrame, scale: bool) -> ColumnTransformer:
    num = [c for c in X.columns if types.is_numeric_dtype(X[c]) and not types.is_bool_dtype(X[c])]
    cat = [c for c in X.columns if c not in num]
    num_steps = [("impute", SimpleImputer(strategy="median"))]
    if scale:
        num_steps.append(("scale", StandardScaler()))
    return ColumnTransformer(
        [
            ("num", Pipeline(num_steps), num),
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(
                                handle_unknown="ignore", max_categories=30, sparse_output=False
                            ),
                        ),
                    ]
                ),
                cat,
            ),
        ]
    )


def models(task: str) -> dict:
    if task == "classification":
        return {
            "dummy (most frequent)": (DummyClassifier(strategy="most_frequent"), False),
            "logistic regression": (LogisticRegression(max_iter=2000), True),
            "random forest": (
                RandomForestClassifier(n_estimators=300, n_jobs=1, random_state=SEED),
                False,
            ),
            "gradient boosting": (HistGradientBoostingClassifier(random_state=SEED), False),
        }
    return {
        "dummy (mean)": (DummyRegressor(), False),
        "ridge regression": (Ridge(), True),
        "random forest": (
            RandomForestRegressor(n_estimators=300, n_jobs=1, random_state=SEED),
            False,
        ),
        "gradient boosting": (HistGradientBoostingRegressor(random_state=SEED), False),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("data")
    ap.add_argument("--target", required=True)
    ap.add_argument("--task", choices=["auto", "classification", "regression"], default="auto")
    ap.add_argument("--drop", default="")
    ap.add_argument("--group")
    ap.add_argument("--time")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--sample", type=int, default=50_000, help="rows to use at most")
    ap.add_argument("--out")
    a = ap.parse_args()

    df = read(a.data)
    if a.target not in df.columns:
        raise SystemExit(f"no column {a.target!r}; columns: {', '.join(map(str, df.columns))}")
    df = df.dropna(subset=[a.target])
    if a.time:
        df = df.sort_values(a.time)
    if len(df) > a.sample:
        df = df.sample(a.sample, random_state=SEED).sort_index()
        print(f"Using a random sample of {a.sample:,} rows")
    y = df[a.target]
    task = a.task
    if task == "auto":
        task = (
            "regression"
            if types.is_float_dtype(y) and y.nunique() > 20
            else (
                "classification"
                if not types.is_numeric_dtype(y) or y.nunique() <= 20
                else "regression"
            )
        )
    drop = {a.target, *(c.strip() for c in a.drop.split(",") if c.strip())}
    groups = df[a.group] if a.group else None
    drop |= {c for c in (a.group, a.time) if c}
    X = df.drop(columns=[c for c in drop if c in df.columns])
    # Free-text and id-like columns explode one-hot encoding; leave them out and say so.
    skipped = [
        c
        for c in X.columns
        if not types.is_numeric_dtype(X[c]) and X[c].nunique() > max(50, 0.5 * len(X))
    ]
    if skipped:
        print(f"Left out high-cardinality text columns: {', '.join(map(str, skipped))}")
        X = X.drop(columns=skipped)

    if task == "classification":
        y = y.astype(str)
        n_classes = y.nunique()
        print(f"Classification: {len(X):,} rows, {X.shape[1]} features, {n_classes} classes")
        print(
            "Class balance: "
            + ", ".join(f"{k} {v:.1%}" for k, v in y.value_counts(normalize=True).head(10).items())
        )
        scoring = {
            "accuracy": "accuracy",
            "balanced_accuracy": "balanced_accuracy",
            "f1_macro": "f1_macro",
        }
        scoring["roc_auc"] = "roc_auc" if n_classes == 2 else "roc_auc_ovr"
        primary = "roc_auc"
    else:
        print(
            f"Regression: {len(X):,} rows, {X.shape[1]} features; "
            f"target mean {y.mean():.4g}, sd {y.std():.4g}"
        )
        scoring = {
            "mae": "neg_mean_absolute_error",
            "rmse": "neg_root_mean_squared_error",
            "r2": "r2",
        }
        primary = "r2"

    if a.time:
        cv, split = TimeSeriesSplit(n_splits=a.folds), f"time-ordered splits on {a.time}"
    elif a.group:
        cv, split = GroupKFold(n_splits=a.folds), f"group k-fold on {a.group}"
    elif task == "classification":
        cv, split = StratifiedKFold(a.folds, shuffle=True, random_state=SEED), "stratified k-fold"
    else:
        cv, split = KFold(a.folds, shuffle=True, random_state=SEED), "k-fold"
    print(f"Validation: {a.folds} folds, {split}, seed {SEED}\n")

    results = {}
    fitted = {}
    for name, (model, scale) in models(task).items():
        pipe = Pipeline([("prep", preprocess(X, scale)), ("model", model)])
        scores = cross_validate(
            pipe, X, y, cv=cv, scoring=scoring, groups=groups, error_score="raise"
        )
        row = {}
        for m in scoring:
            v = scores[f"test_{m}"]
            if scoring[m].startswith("neg_"):
                v = -v
            row[m] = {"mean": float(np.mean(v)), "std": float(np.std(v))}
        row["fit_seconds"] = float(np.mean(scores["fit_time"]))
        results[name] = row
        fitted[name] = pipe

    metrics = list(scoring)
    lines = [
        "| Model | " + " | ".join(metrics) + " | fit s |",
        "| --- |" + " --- |" * (len(metrics) + 1),
    ]
    for name, row in results.items():
        cells = [f"{row[m]['mean']:.4f} ± {row[m]['std']:.4f}" for m in metrics]
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {row['fit_seconds']:.2f} |")
    table = "\n".join(lines)
    print(table)

    best = max(
        (n for n in results if not n.startswith("dummy")), key=lambda n: results[n][primary]["mean"]
    )
    print(f"\nBest by {primary}: {best}")
    strat = y if task == "classification" and y.value_counts().min() >= 2 else None
    if a.time or a.group:
        cut = int(len(X) * 0.8)
        Xtr, Xte, ytr, yte = X.iloc[:cut], X.iloc[cut:], y.iloc[:cut], y.iloc[cut:]
    else:
        Xtr, Xte, ytr, yte = train_test_split(
            X, y, test_size=0.2, random_state=SEED, stratify=strat
        )
    pipe = fitted[best].fit(Xtr, ytr)
    imp = permutation_importance(
        pipe, Xte, yte, n_repeats=5, random_state=SEED, scoring=scoring[primary], n_jobs=1
    )
    order = np.argsort(-imp.importances_mean)[:15]
    importances = [
        {
            "feature": str(X.columns[i]),
            "mean": float(imp.importances_mean[i]),
            "std": float(imp.importances_std[i]),
        }
        for i in order
    ]
    print(f"\nPermutation importance on a 20% hold-out ({primary} drop):")
    for r in importances:
        print(f"- {r['feature']}: {r['mean']:.4f} ± {r['std']:.4f}")

    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        payload = {
            "task": task,
            "rows": len(X),
            "features": list(map(str, X.columns)),
            "validation": split,
            "folds": a.folds,
            "seed": SEED,
            "results": results,
            "best": best,
            "importances": importances,
        }
        (out / "results.json").write_text(json.dumps(payload, indent=1), "utf-8")
        md = (
            f"# Baselines: {a.target} ({task})\n\n{len(X):,} rows, "
            f"{a.folds}-fold {split}, seed {SEED}.\n\n{table}\n\n"
        )
        md += f"Best by {primary}: **{best}**.\n\n## Permutation importance\n\n" + "\n".join(
            f"- {r['feature']}: {r['mean']:.4f} ± {r['std']:.4f}" for r in importances
        )
        (out / "results.md").write_text(md + "\n", "utf-8")
        print(f"\nSaved {out}/results.json and results.md")


if __name__ == "__main__":
    main()
