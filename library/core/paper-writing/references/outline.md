# Section-by-section guide (ML conference paper, ~8-9 pages + appendix)

## Title
Specific and searchable: method or finding + task. Avoid questions and puns unless the venue likes them.

## Abstract (150-250 words)
Problem (1-2 sentences) -> gap -> what we do -> key result with numbers -> implication. No citations.

## 1 Introduction (~1 page)
1. Context and why the problem matters (concrete example).
2. What current methods do and where they fail (cite closest work).
3. Our idea in one paragraph, the intuition before the details.
4. Contribution bullets (each points to a section).
5. Optional: Figure 1 that shows the core idea or the headline result.

## 2 Related work (0.5-1 page)
Grouped by theme; each paragraph ends with how we differ. Can move after experiments.

## 3 Problem setup / background
Notation, task definition, assumptions. Only what the method needs.

## 4 Method
The idea, then the algorithm (pseudo-code box if it helps), then design choices with reasons.
Separate what is essential from implementation details (move the latter to the appendix).

## 5 Experiments
- Setup: models, data, baselines (and how they were tuned), metrics, compute, seeds.
- Main results table: method vs. strong, fairly tuned baselines; mean +/- spread.
- Ablations: remove each component; show which ones matter.
- Analysis: when does it fail, how does it scale, qualitative examples.
- Each subsection opens with the question it answers.

## 6 Discussion / Limitations
Honest scope: domains, scales, costs, assumptions; what would falsify the claims.

## 7 Conclusion
3-5 sentences; no new results.

## Appendix
Hyper-parameters, full results, prompts, dataset details, extra examples, proofs.
