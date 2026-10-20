---
name: paper-reading
description: Read a research paper (PDF, arXiv page or uploaded file) efficiently and turn it into structured, citable notes - problem, contribution, method, setup, results with exact numbers, limitations, and how it relates to other work. Use when a task needs more from a paper than its abstract, or when summarising, critiquing or comparing papers.
metadata:
  category: research
---

# Reading a paper

Get the text with `read_document` (uploaded files) or `web_fetch` (arXiv: fetch
`https://arxiv.org/pdf/<id>`; the abstract page `https://arxiv.org/abs/<id>` is shorter
and has the metadata). Long PDFs are cut; fetch the HTML version
(`https://arxiv.org/html/<id>`) when the PDF text stops early.

## Three passes, stop as soon as the task is answered

1. **Survey (2 minutes of reading):** title, abstract, introduction's last paragraph (the
   contribution list), section headings, conclusion, figures and table captions. Enough to
   classify the paper and decide whether it matters.
2. **Content:** method section and the main results table. Write down the setup exactly:
   base models and sizes, data, compute, baselines, metrics, number of seeds/runs.
3. **Critique (only for papers central to the task):** check each claim against the
   evidence. Are baselines tuned as carefully as the method? Is the gain larger than the
   run-to-run spread? Are the benchmarks at risk of contamination? Does the ablation isolate
   the claimed cause? What's missing (other domains, scales, modalities)?

## Notes format

```markdown
## <Title> (<First author> et al., <year>, <venue>) <url>
- Problem: one sentence.
- Contribution: what is new, in their words where possible (quote briefly, with section).
- Method: 3-5 bullets; the key idea first.
- Setup: models, data, compute, baselines, metrics, seeds.
- Results: the headline numbers exactly as reported, with the table/figure they come from
  ("+4.1 pts on MATH vs. ORM best-of-1860, Table 2").
- Limitations: stated by the authors / noticed by me (label which).
- Relevance: how it bears on our question; papers it builds on or contradicts.
```

For a literature matrix, also emit the JSON object described in `core/literature-review`.

## Rules

- Never invent numbers, datasets or author names. If the text you have doesn't show it,
  write "not in the text I read" and say which part was missing.
- Distinguish what the paper shows from what it claims; abstracts often overstate.
- Note the version (arXiv v1/v2, camera-ready) when results differ between versions.
- Equations: describe them in words unless the task needs the LaTeX; then copy it exactly.
