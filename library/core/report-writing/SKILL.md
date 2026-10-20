---
name: report-writing
description: Structure and write clear reports, briefs and memos - answer-first executive summary, findings with evidence, recommendations, and appendices - in a consistent house format ready to turn into Word or PDF. Use when a task's deliverable is a report, brief, memo, white paper, one-pager or written analysis.
metadata:
  category: writing
---

# Report writing

Write in Markdown first; convert at the end with `core/docx` (Word) or `core/pdf`. Charts
come from `core/charts`, tables from the data.

## Shape (adapt the length, keep the order)

```markdown
# <Title that states the topic, not the conclusion>
<Prepared for Shivam · 20 October 2026 · Sources: web research, uploaded report.docx>

## Summary
3-5 sentences: the answer, the 2-3 facts that support it, the recommendation.

## Key findings
1. **Finding as a full sentence.** Evidence with numbers and sources.
2. ...

## Recommendation / Next steps
What to do, who, by when; options with trade-offs if a decision is needed.

## Details
Sections per theme, tables, charts.

## Method and limitations
How the information was gathered, dates, what couldn't be verified.

## Sources
Numbered list with URLs and access dates.
```

## Rules

- **Answer first.** Someone who reads only the summary should know the conclusion.
- Headings say something ("Prices fell 12% in 2025") when the reader skims; neutral
  headings ("Pricing") are fine in reference sections.
- One idea per paragraph, short sentences, active voice, no filler ("It is important to
  note that...").
- Numbers: units and dates every time; same precision throughout; tables for 3+ comparable
  items; charts only when the shape matters.
- Every fact traceable: inline `(Source, 2026)` linking to the URL, or a footnote number.
- Separate facts, estimates and opinions; label recommendations as such.
- Lengths: one-pager about 400 words; brief 1-3 pages; full report as long as the evidence
  needs, with the summary still under 200 words.

## Before you hand it over

Re-read the summary against the findings (do they match?), check every number against
its source, and check the file opens (the conversion scripts print a summary).
