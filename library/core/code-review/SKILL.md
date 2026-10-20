---
name: code-review
description: Review code or a diff for correctness, security, performance, readability and tests, with specific, prioritised findings and suggested fixes; also explain unfamiliar code. Use when asked to review, audit, explain or improve code, a pull request, a notebook or a script.
metadata:
  category: engineering
---

# Code review

The code arrives as an uploaded file (`read_document`), pasted text, or a file in `in/`.
You can run Python in the sandbox (`run_code`, no network) to test behaviour: do it for
anything you're unsure about rather than guessing.

## Order of attention

1. **Correctness:** does it do what it claims? Edge cases (empty input, None, zero,
   off-by-one, time zones, Unicode, large inputs), error handling, concurrency, resource
   cleanup.
2. **Security:** injection (SQL, shell, prompt), secrets in code, unsafe deserialisation
   (`pickle`, `yaml.load`), path traversal, missing auth checks, SSRF in fetchers.
3. **Data and ML specifics:** train/test leakage, unseeded randomness, silent dtype casts,
   NaN handling, metrics computed on the wrong split, GPU/CPU device mismatches,
   tokenizer/model mismatches.
4. **Performance:** needless quadratic loops, repeated I/O or model calls in loops,
   loading whole files when streaming works.
5. **Readability and design:** names, function size, duplication, dead code, comments
   that explain why.
6. **Tests:** are the risky paths covered? Suggest the specific missing test.

## Writing findings

- One finding per item: location (`file:line` or function), what's wrong, a concrete
  failing input or scenario, and the fix (a short code snippet).
- Label severity: **must fix** (bug, security), **should fix** (likely problem,
  maintainability), **nit** (style). Lead with must-fix items.
- Don't pad with praise or restate the code; skip nits when there are real problems.
- If you ran code to confirm a finding, say so and show the output.

## Explaining code

Start with what it does in one paragraph, then the main flow step by step, then the
non-obvious parts (why something is done a particular way), then risks.
