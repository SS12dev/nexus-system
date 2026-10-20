---
name: research-gaps
description: Turn a body of literature into a rigorous research-gap analysis - a taxonomy of the area, a gap matrix, evidence for each gap, and concrete research questions and hypotheses with how to test them. Use when asked for research gaps, open problems, a research agenda, thesis or paper ideas, or "what hasn't been done yet" in a field.
metadata:
  category: research
---

# Research-gap analysis

A gap is only useful if it is **real** (the literature shows it is open), **important**
(closing it changes something), and **tractable** (someone could test it with a plausible
budget). Every gap you report needs evidence for all three.

## Steps

1. **Collect the literature** with `core/literature-review` (15 to 40 papers, recent plus
   landmark). Tag every paper on 2 to 4 axes that matter for the question, for example for
   LLM/VLM training: `stage` (pre-training, SFT, RL/preference, inference-time), `method`,
   `capability` (math, code, planning, spatial, causal), `modality`, `evaluation`.
2. **Build the map.** Run `lit_matrix.py coverage` for each pair of axes and
   `lit_matrix.py timeline` for the method axis. Write a 1-paragraph summary per cluster.
3. **Find candidate gaps** using these lenses (each one gives different gaps):

   | Lens | Question to ask |
   | --- | --- |
   | Coverage | Which axis combinations have few or no papers (empty cells)? |
   | Contradiction | Where do papers report opposite results? What explains it? |
   | Assumption | What do all methods assume (labels available, single modality, short horizons)? |
   | Scale | Shown only at small scale, or only at frontier scale? |
   | Evaluation | Are gains measured on contaminated, saturated or proxy benchmarks? |
   | Mechanism | Is there a result without an explanation (why does it work)? |
   | Transfer | Does it hold across domains, languages, modalities, model families? |
   | Cost | Does the method need compute or labels most groups can't afford? |
   | Stated limitations | What do the papers' own limitations/future-work sections ask for? |

4. **Verify each candidate.** Search specifically for it (2 or 3 targeted queries, recent
   preprints included). If you find work that closes it, drop it or narrow it. Record the
   searches you ran: "searched X, Y, Z on 2026-10-20; closest work is A (2025), which only...".
5. **Score and rank.** For each surviving gap rate importance, novelty and feasibility
   (1 to 5) with one line of justification each. Keep the top 5 to 8.
6. **Turn gaps into research questions.** For each: a precise question, a falsifiable
   hypothesis, the minimal experiment (models, data, baselines, metric, expected effect
   size), risks, and the closest prior work it must beat or extend.

## Report shape

1. Scope and method (sources, dates, number of papers, axes).
2. Map of the field (clusters, with citations) and the coverage table.
3. Gaps, ranked: name, evidence it is open, why it matters, research question, hypothesis,
   minimal experiment, closest work.
4. A short research agenda: which gaps compose into one paper or thesis.
5. References (use `core/citations` for BibTeX).

## Pitfalls

- "No paper found" is weak evidence when you only searched one phrasing. Say how hard
  you looked.
- Don't present a gap that is really an engineering task ("scale it up") as a research
  question unless scale itself is the open question.
- Big framing words ("AGI", "true reasoning") need operational definitions: say which
  measurable capability you mean and how it would be tested.
