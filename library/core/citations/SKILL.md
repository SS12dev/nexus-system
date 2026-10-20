---
name: citations
description: Build, clean and format bibliographies - BibTeX entries from paper metadata (arXiv, DOI, Semantic Scholar), duplicate and missing-field checks, and reference lists in APA, IEEE or Markdown. Use whenever a paper, report or literature review needs references, a .bib file, or citations in a specific style.
metadata:
  category: research
---

# Citations and BibTeX

`scripts/bib_tools.py` does the mechanical work in the sandbox (`run_code`); getting
correct metadata is your job.

## Get correct metadata

- arXiv: `web_fetch https://export.arxiv.org/api/query?id_list=2305.20050` gives title,
  authors, date. Prefer the published version when one exists: search Semantic Scholar
  (`https://api.semanticscholar.org/graph/v1/paper/arXiv:2305.20050?fields=title,authors,year,venue,externalIds,publicationVenue`).
- DOI: `web_fetch https://api.crossref.org/works/<doi>` returns full metadata as JSON.
- Never make up authors, venues, pages or DOIs. Leave a field out rather than guess.

## Make a .bib from JSON

```bash
python skills/core/citations/scripts/bib_tools.py from-json refs.json out/references.bib
```

`refs.json` is a list like
`[{"title": "...", "authors": ["Hunter Lightman", "Vineet Kosaraju"], "year": 2023,
"venue": "ICLR", "arxiv": "2305.20050", "doi": "...", "url": "...", "type": "inproceedings"}]`.
Keys are generated as `lastnameYEARfirstword` (e.g. `lightman2023lets`) and made unique.
`type` defaults to `article` with a journal, `inproceedings` with a conference venue, and
`misc` for arXiv-only preprints.

## Check, clean and format

```bash
python skills/core/citations/scripts/bib_tools.py check in/references.bib
python skills/core/citations/scripts/bib_tools.py dedupe in/references.bib out/references.bib
python skills/core/citations/scripts/bib_tools.py format in/references.bib --style apa
python skills/core/citations/scripts/bib_tools.py format in/references.bib --style ieee --out out/refs.md
```

- `check` reports duplicate keys, duplicate papers (same DOI, arXiv id or title), and
  entries missing fields their type needs.
- `dedupe` keeps the most complete entry of each duplicate group and prints the key
  mapping so you can update `\cite{}` calls.
- `format` prints a numbered (IEEE) or alphabetical (APA) reference list in Markdown.

## Conventions

- In LaTeX use natbib (`\citet{key}` for "Author et al. (2023)", `\citep{key}` for
  "(Author et al., 2023)").
- In Markdown reports, cite inline as `(Lightman et al., 2023)` linking to the URL, and end
  with a reference list from `format`.
- Protect capitals in titles with braces in BibTeX: `{LLM}s`, `{GPT}-4`.
