---
name: llm-evaluation
description: Evaluate LLMs, prompts, RAG and agents rigorously - test sets, metrics, LLM-as-judge rubrics with bias controls, contamination checks, and system comparisons with bootstrap CIs and paired tests. Use to benchmark or compare models or prompts, design an eval, or judge outputs.
metadata:
  category: data
---

# Evaluating LLM systems

`scripts/eval_report.py` turns scored results into a comparison with confidence intervals;
`references/judge-rubric.md` is a ready-to-adapt LLM-as-judge prompt. Pair with
`core/statistics` for other tests and `core/ml-experiments` for the experiment checklist.

## 1. Define what "good" means

Write 3 to 6 criteria tied to the use case (correct final answer, faithful to sources,
follows format, safe, concise). For each, decide how it is scored:

| Kind of output | Scoring |
| --- | --- |
| One right answer (math, extraction, classification) | Exact or normalised match; unit tests for code (pass@k) |
| Grounded in sources (RAG, summaries) | Claim-level faithfulness: each claim supported by the context? |
| Open-ended (writing, advice, plans) | Rubric with an LLM judge, calibrated on human labels |
| Agents and tool use | Task success on the final state, plus steps, cost and errors per task |

## 2. Build the test set

- 50 to 200 items per slice is usually enough to see a 5 to 10 point difference; check
  with the CI width below. Cover the real distribution and the known hard cases; tag each
  item with a `category` for per-slice results.
- Keep a frozen test split; never tune prompts on it.
- Contamination: for public benchmarks, prefer items newer than the model's cutoff, or
  rephrased/perturbed versions; report when contamination can't be ruled out.

## 3. LLM-as-judge, done carefully

- Use a stronger or different-family model than the one judged; temperature 0.
- Ask for the reasoning first, then a score on a small scale (1 to 5 or pass/fail) with
  anchored descriptions (see `references/judge-rubric.md`).
- Pairwise comparisons: run both orders (A/B and B/A) to cancel position bias; count a win
  only if it holds in both orders, otherwise a tie.
- Control length bias: tell the judge length is not quality, and check whether scores
  correlate with length.
- Calibrate: label 30 to 50 items by hand, compute agreement with the judge
  (`eval_report.py agreement`); a Cohen's kappa below about 0.6 means fix the rubric.

## 4. Compare systems

Store one row per (system, item) in CSV or JSONL with columns `system`, `item_id`, `score`
(0/1 or numeric) and optional `category`, `cost_usd`, `latency_s`, `tokens`.

```bash
E=skills/core/llm-evaluation/scripts/eval_report.py
python $E summary results.csv                       # mean score + bootstrap 95% CI per system, per category
python $E compare results.csv --a baseline --b new  # paired difference, CI, sign test / McNemar
python $E agreement labels.csv --rater1 human --rater2 judge   # Cohen's kappa, accuracy
```

Report: the primary metric with CIs, the paired difference with its CI, per-category
results (where one system wins and loses), cost and latency per item, and 3 to 5 example
items that show the difference. A difference whose CI includes 0 is not established.
