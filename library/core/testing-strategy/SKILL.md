---
name: testing-strategy
description: Decide what to test and at which level - unit tests for logic, API tests for the contract, a few browser tests for journeys - and write tests that are reliable and catch real defects. Use when writing a test plan, testing an app someone else built, or judging whether a test suite is good enough.
metadata:
  category: engineering
---

# Testing strategy

A passing suite should mean "this works". That needs the right tests at the right level, not
many tests.

## Levels

| Level | Covers | How many | Tools |
| --- | --- | --- | --- |
| Unit | Rules, calculations, state changes, parsing, validation | Most of the tests | Vitest, pytest |
| API / integration | Each route against a real temporary database: success, errors, access | A handful per route | FastAPI TestClient, supertest |
| Browser journey | The main things a user does, end to end in the built app | Few: the journeys, not every button | Playwright |

Push each check to the lowest level that can catch the defect. A rule tested through the
browser is slow and breaks for unrelated reasons.

## From acceptance criteria to tests

Take the plan's acceptance criteria one by one. For each, write down the level and the
test's name. Criteria with no test are reported, not ignored. Then add what the criteria
missed: boundaries, invalid input, the error paths, and repeat actions.

## Unit tests

See `core/test-driven-development` for how to write them. As a tester of someone else's
code, look for logic buried in components or handlers and report it when it can't be tested
without rendering; that is a design defect worth sending back.

## API tests

- Success case with the exact response shape from the contract.
- Each documented error: validation (missing, too long, wrong type), not found, conflict.
- Access: unauthenticated, and one user asking for another's data.
- Lists: empty, one page, paging, order.
- Each test starts from a clean database.

## Browser journeys

- One test per journey: load, do the main thing as a user would, check what the user sees.
  Then one for the most important failure (invalid input, a failed request).
- Select by role and visible name (`getByRole('button', { name: 'Add note' })`), then by
  label; test ids only as a last resort. Never by CSS class or position.
- Wait for conditions (`await expect(...).toBeVisible()`), never for time.
- Fail on console errors and failed requests: collect them and assert there are none.
- Check a phone-sized viewport for the main journey when the app is meant for phones.
- Reload in the middle when data must persist.

## Reliability

A test that fails one run in twenty is a defect. Causes: shared state between tests,
dependence on real time or randomness, animations, unawaited promises, network calls to the
outside. Fix the cause. Retries and longer timeouts hide it.

## What good coverage means

Coverage numbers show what was run, not what was checked. Aim for the logic modules to be
well covered, and judge by this instead: if I broke a rule in the code, would a test fail?
Try it on one or two rules (change a comparison, remove a branch) and see.

## Reporting

- What ran at each level, with counts, and what failed with the cause as far as known.
- App defects found, each with steps to reproduce; test code is yours to fix, app code is
  reported to its builder.
- Criteria not covered, and why.
- Never report a test as passing that you did not see pass.
