---
name: fact-check
description: Verify claims, figures and quotes against primary sources and give each a verdict (supported, partly supported, contradicted, unverified) with evidence. Use when reviewing a draft, report or email before it goes out, checking something the user read, or as the reviewer step in an agent team.
metadata:
  category: research
---

# Fact-checking

Uses `web_search`, `web_fetch`, and `read_document`/`search_documents` for the user's own
files.

## Steps

1. **Extract checkable claims.** List every statement of fact: numbers, dates, names,
   rankings, quotes, causal claims ("X led to Y"), and superlatives ("the largest").
   Skip opinions and obvious common knowledge. Number them.
2. **Prioritise.** Check first what the conclusion depends on and anything surprising.
3. **Trace to the origin.** For each claim find the primary source: the official statistic,
   filing, paper, press release or transcript, not an article quoting it. Check the date:
   a correct number from 2022 can be wrong for 2026.
4. **Compare precisely.** Same metric, unit, scope and period? ("EU sales" vs "Europe incl.
   UK and Norway" is a common mismatch.) Quotes must match word for word.
5. **Verdict** per claim:
   - Supported: primary source matches.
   - Partly supported: right direction, wrong number/scope/date; give the correct version.
   - Contradicted: primary source says otherwise; give it.
   - Unverified: no reliable source found after a real search; say what you searched.

## Output

| # | Claim | Verdict | Correct version | Source (URL, date) |
| --- | --- | --- | --- | --- |

Then a short summary: how many claims checked, what must change before the text is used.
When reviewing another agent's work, list the fixes needed as specific edits.

## Red flags

Round numbers without a source, statistics with no date, "studies show", screenshots of
tables, figures that only appear on one site, and pages that copy each other.
