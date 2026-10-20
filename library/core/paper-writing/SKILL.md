---
name: paper-writing
description: Plan and write ML research papers and technical reports - contribution framing, outline, section guidance, results tables, related work, limitations, a pre-submission checklist and a LaTeX skeleton. Use when drafting, outlining or improving a paper, preprint or thesis chapter.
metadata:
  category: research
---

# Writing a research paper

`references/outline.md` has the section-by-section guide, `references/checklist.md` the
pre-submission checklist, and `references/paper.tex` a LaTeX skeleton (copy it into `out/`
and fill it in; the sandbox has no LaTeX compiler, so the user compiles it, e.g. on Overleaf).
For a quick shareable draft, write Markdown and convert it with `core/docx` or `core/pdf`.

## Start from the contribution, not the outline

Before writing any section, write these four sentences and get them right:

1. **Problem:** what doesn't work today, for whom, and why it matters.
2. **Gap:** why existing approaches don't solve it (cite 2 or 3 closest works).
3. **Contribution:** what this paper does that is new (method, dataset, benchmark, analysis,
   theory); 2 to 4 bullet points, each checkable.
4. **Evidence:** the single most convincing result, with the number.

The abstract is these four sentences polished; the introduction expands them; every
section exists to support one of the contribution bullets.

## Process

1. Draft the title, abstract and contribution bullets.
2. Draft figure 1 and the main results table first: the paper is built around them.
3. Write method and experiments, then introduction, then related work, then conclusion and
   limitations, then the abstract again.
4. Run `references/checklist.md` before calling a draft done.

## Style

- One idea per paragraph; the first sentence says it.
- Claims scale with evidence: "improves" needs a result beyond seed variance; "solves" or
  "AGI-level" almost never survives review. Prefer precise, measurable claims.
- Define every symbol once, near first use; keep notation consistent.
- Report means with spread (std or 95% CI) over seeds; bold only statistically clear wins.
- Related work is organised by idea, not by paper, and ends each paragraph with how this
  work differs.
- Cite with `\citep{key}`/`\citet{key}` and keep `references.bib` clean with
  `core/citations`.
