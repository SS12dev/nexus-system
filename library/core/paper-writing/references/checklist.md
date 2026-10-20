# Pre-submission checklist

## Claims
- [ ] Every contribution bullet is supported by a specific section, table or figure.
- [ ] Abstract numbers match the tables exactly.
- [ ] No claim is broader than the evidence (domains, scales, model families tested).

## Experiments
- [ ] Baselines are recent, strong and tuned with the same budget as the method.
- [ ] Results report mean and spread over >= 3 seeds (or say why not).
- [ ] Benchmarks are checked for contamination / train-test overlap where it matters.
- [ ] Ablations isolate each component.
- [ ] Compute (hardware, hours) and model sizes are reported.
- [ ] Hyper-parameters and prompts are in the appendix.

## Reproducibility and ethics
- [ ] Data sources, licences and preprocessing are described.
- [ ] Code/data release plan is stated.
- [ ] Limitations and potential misuse are discussed.
- [ ] Human-subject or annotation work: consent, pay, instructions described.

## Presentation
- [ ] Figures readable in greyscale and at print size; axes labelled with units.
- [ ] Every symbol defined; notation consistent.
- [ ] Every citation key resolves; references have venues and years (run bib_tools.py check).
- [ ] Anonymised if the venue is double-blind (no names, links or acknowledgements).
- [ ] Within the page limit with the venue's official style file.
