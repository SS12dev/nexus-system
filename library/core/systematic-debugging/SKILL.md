---
name: systematic-debugging
description: Find the real cause of a bug before changing code - reproduce it, read the evidence, narrow it down, form one hypothesis, test it, then fix and prove the fix. Use when something is broken, a test fails, a build or deploy fails, or a previous fix didn't hold.
metadata:
  category: engineering
---

# Systematic debugging

Most wasted effort in a fix comes from changing code before knowing what is wrong. The rule
of this skill: **no fix without a cause you have seen evidence for.**

## 1. Reproduce

- Get the exact failure in front of you: run the failing test, the build, or the steps the
  user described. Note the precise message, file and line.
- If it doesn't reproduce, the difference between your setup and theirs is the first clue:
  data, screen size, order of actions, a clean install, the built app rather than the dev
  server.
- Make the reproduction small and repeatable; a failing test is best. You will need it to
  prove the fix.

## 2. Read the evidence

- Read the whole error and stack trace, bottom to top, and open the first frame that is in
  this project's code.
- Check what changed: `git log` and `git diff` since it last worked.
- Look at real values: log or print the inputs and the intermediate state at the point of
  failure. In the browser, read the console and the network tab (status, request and
  response bodies).
- Don't trust comments, names or your memory of how a library behaves; check the code and
  the docs for the installed version.

## 3. Narrow it down

- Halve the search space: which half of the pipeline has the wrong value? Input, logic,
  storage, transport or display?
- Compare a working case with the broken one and list every difference.
- Remove things until it works, then add back the last one.
- `git bisect` when it worked at a known commit.

## 4. One hypothesis, one test

State it plainly: "The total is wrong because prices are added as strings." Predict what you
will see if it is true, then check exactly that. If the prediction fails, the hypothesis is
wrong: drop it, don't bend it. Change one thing at a time.

## 5. Fix the cause

- Fix where the fault starts, not where it shows. A null check at the crash site usually
  hides a value that should never have been null.
- Look for the same mistake elsewhere in the code and fix those too.
- Keep the fix small. Unrelated tidying goes in a separate commit.

## 6. Prove it

- The reproduction now passes, and it failed before the fix.
- The full test suite and the build still pass.
- For a user-facing bug, do the user's steps in the running app.
- Say in the commit message what the cause was, not only what changed.

## When stuck

After three failed hypotheses, stop guessing. Write down what you know for certain, what you
assumed, and what you have not looked at. The cause is usually in an assumption. Question the
architecture when fixes keep moving the bug rather than removing it.

## Common causes worth checking early

- Build passes in dev but fails in production: environment variables, case-sensitive paths,
  dev-only dependencies, type errors the dev server ignores.
- Works once, fails on repeat: state not reset, listeners added twice, cached data.
- Intermittent: timing, async code without await, order of effects, time zones.
- Blank page: an exception during first render; read the console.
- API returns 404 or CORS errors: wrong base path, proxy not configured, server not started.
