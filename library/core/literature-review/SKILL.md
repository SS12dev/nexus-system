---
name: literature-review
description: Find, screen and organise academic papers on a topic (arXiv, Semantic Scholar, OpenReview, ACL Anthology, Google Scholar) into a structured literature matrix with citations, and spot under-studied areas. Use for related-work sections, surveys, "what's the latest research on X", or as the first step of a research-gap analysis.
metadata:
  category: research
---

# Literature review

Uses `web_search` and `web_fetch` to find papers, and `run_code` with this skill's script to
build the matrix. Pair it with `core/paper-reading` (notes per paper), `core/research-gaps`
(turning the matrix into gaps) and `core/citations` (BibTeX and reference lists).

## 1. Scope before searching

Write down, in a few lines: the research question, the time window (default: the last 24
months for "latest", plus landmark older papers), the sub-areas to cover, and what counts as
in scope (e.g. "training or fine-tuning methods for reasoning in LLMs and VLMs; not
inference-only prompting"). This list is your stopping rule.

## 2. Search where papers actually are

| Source | How to query | Good for |
| --- | --- | --- |
| arXiv listing | `web_fetch https://export.arxiv.org/api/query?search_query=all:%22process%20reward%22&sortBy=submittedDate&max_results=25` (Atom XML: titles, abstracts, dates, ids) | Newest preprints |
| Semantic Scholar API | `web_fetch https://api.semanticscholar.org/graph/v1/paper/search?query=process+reward+models&fields=title,year,venue,citationCount,externalIds,abstract,url&limit=20` | Citation counts, venues, DOIs |
| Citations of a key paper | `.../graph/v1/paper/arXiv:2305.20050/citations?fields=title,year,citationCount&limit=50` | What built on it |
| OpenReview | `web_search "site:openreview.net <topic> ICLR 2026"` | Peer-review status, reviews |
| ACL Anthology | `web_search "site:aclanthology.org <topic>"` | NLP venues |
| Papers with Code / leaderboards | `web_search "<benchmark> leaderboard"` | Current state of the art |
| Surveys | `web_search "<topic> survey arXiv 2026"` | A map of the area; mine their tables |

- Use 3 to 5 phrasings per sub-area (method names, problem names, benchmark names).
- Snowball: from the 3 to 5 most-cited relevant papers, follow references (backward) and
  citations (forward) once. That finds most of what keyword search misses.
- Semantic Scholar rate-limits anonymous calls; if you get HTTP 429, fall back to
  `web_search` and arXiv rather than retrying in a loop.

## 3. Screen quickly

Read title and abstract only. Keep a paper when it is in scope and is either recent,
highly cited for its age, from a strong venue, or introduces a method/benchmark others use.
Record why you dropped borderline papers in one line; it helps the gap analysis. Aim for
15 to 40 kept papers for a related-work section, more for a survey.

## 4. Record each kept paper as JSON

One object per paper (fill what you know; leave the rest out):

```json
{"title": "Let's Verify Step by Step", "authors": "Lightman et al.", "year": 2023,
 "venue": "ICLR 2024", "url": "https://arxiv.org/abs/2305.20050", "arxiv": "2305.20050",
 "citations": 900, "problem": "multi-step math reasoning", "approach": "process supervision",
 "data": "PRM800K", "metrics": "MATH accuracy",
 "findings": "Process reward models beat outcome reward models for best-of-N selection.",
 "limitations": "Human step labels are expensive; math only.",
 "tags": {"method": "reward modelling", "modality": "text", "stage": "post-training"}}
```

Claims and numbers come from the paper's abstract or results, not from memory. Use
`core/paper-reading` when you need more than the abstract.

## 5. Build the matrix

```bash
python skills/core/literature-review/scripts/lit_matrix.py build papers.json out/literature-matrix.xlsx --md out/literature-matrix.md
python skills/core/literature-review/scripts/lit_matrix.py coverage papers.json --rows method --cols modality
python skills/core/literature-review/scripts/lit_matrix.py timeline papers.json --by method
```

- `build` writes one row per paper (sorted by year, newest first) with links, and a
  Markdown version for the report.
- `coverage` counts papers for each pair of tag values. Empty or thin cells are candidate
  gaps (check they aren't empty because you didn't search there).
- `timeline` shows papers per year per tag: rising, saturated and abandoned directions.

## Reporting

Lead with a short map of the area (3 to 6 clusters, one paragraph each, citing papers),
then the matrix, then open problems. Cite as `Author et al. (year)` with the URL. Say how
you searched (sources, queries, date) so the review can be repeated.
