---
name: code-review
description: Review code or a diff the way a senior engineer does - against the requirements first, then correctness, security, data, error handling, design, tests and UX - with specific, prioritised findings and fixes; also explain unfamiliar code. Use when asked to review, audit, explain or improve code, a pull request, a project, a notebook or a script.
metadata:
  category: engineering
---

# Code review

A review answers two questions: does this do what was asked, and would I be content to
maintain it? Evidence beats opinion: run the code, the tests and the app whenever you can,
and say what you ran.

Code arrives as an uploaded file (`read_document`), pasted text, a file in `in/`, or a
project folder (`ws_diff`, `ws_read`, `ws_run`). In the sandbox you can run Python
(`run_code`, no network) to test behaviour.

## Order of work

1. **Understand the intent.** Read the request, the plan and its acceptance criteria before
   the code. Review against those, not against how you would have built it.
2. **Read the measured results.** Test counts, lint, type check, security and dependency
   audits that were run for you are facts; start from them.
3. **Read the change as a whole**, then file by file. Follow the main journey through the
   code from input to output.
4. **Run it** when you have the tools: the build, the tests, then the app itself. Look at
   screenshots when they are provided. Try the unhappy paths: empty, invalid, twice, offline.
5. **Write findings**, ordered by severity.

## What to look for

1. **Requirements:** every acceptance criterion met; nothing asked for is missing; nothing
   unasked was added that changes behaviour.
2. **Correctness:** edge cases (empty, null, zero, off-by-one, duplicates, large input,
   Unicode, time zones), state that goes stale, async without await, races, resources not
   released.
3. **Security:** load `core/security-review` for anything with a server, sign-in, stored
   data or user-generated content. At minimum: access checks on every route, no string-built
   queries, no secrets in code, no unsanitised HTML.
4. **Data:** schema constraints, migrations present, transactions where several rows change,
   no unbounded queries.
5. **Errors:** every failure has a decided outcome and the user is told something useful;
   nothing swallowed.
6. **Design:** logic separated from UI and transport so it can be tested; one source of
   truth for each piece of state; no needless dependencies; names and file layout a
   newcomer could follow.
7. **Tests:** would they fail if the behaviour broke? Risky logic covered? No test weakened
   or skipped to pass?
8. **User experience** (for anything with a screen): load `core/frontend-design`. Check
   phone and laptop widths, empty, loading and error states, keyboard use, contrast, and
   that no debug text is visible.
9. **Data and ML specifics:** train/test leakage, unseeded randomness, silent dtype casts,
   NaN handling, metrics on the wrong split.
10. **Performance:** quadratic loops over growing data, repeated I/O or model calls in
    loops, oversized bundles and images.

## Writing findings

- One finding per item: where (`file:line` or function), what is wrong, a concrete failing
  input or scenario, and the fix (a short snippet when it helps).
- Severity: **must fix** (broken, insecure, data loss, a criterion unmet), **should fix**
  (likely problem, hard to maintain), **nit** (style). Lead with must-fix; leave nits out
  when there are real problems.
- Say who should fix it when the work has several owners.
- If you ran something to confirm a finding, show the command and the relevant output.
- No praise padding, no restating the code.

## Deciding

Approve when every must-fix is cleared, the measured checks pass and the acceptance criteria
are met. Don't hold work back for taste, for things the plan left open, or for polish nobody
asked for. Don't approve work you could not verify without saying exactly what was not
checked.

## Explaining code

Start with what it does in one paragraph, then the main flow step by step, then the
non-obvious parts (why something is done a particular way), then risks.
