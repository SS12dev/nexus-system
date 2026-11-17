---
name: test-driven-development
description: Build logic test-first - write a failing test for one behaviour, make it pass with the least code, then tidy - and keep tests fast, independent and about behaviour. Use when writing or changing code with real logic (rules, calculations, state, parsing, API handlers) or when fixing a bug.
metadata:
  category: engineering
---

# Test-driven development

Tests written after the code tend to describe what the code does, bugs included. Tests
written first describe what it should do. Use this loop for anything with rules in it.

## The loop

1. **Red.** Write one small test for the next behaviour. Run it and watch it fail for the
   right reason (the assertion, not an import error). A test you never saw fail proves
   nothing.
2. **Green.** Write the least code that makes it pass. Resist finishing the feature.
3. **Tidy.** With tests green, remove duplication and improve names, in the code and in the
   tests. Run the tests again.
4. Commit. Repeat with the next behaviour.

Order the behaviours from the simplest case outward: empty input, one item, the normal case,
the boundaries, then the errors.

## What to test first

- Pure logic: game rules, scoring, pricing, date maths, validation, parsers, reducers. Pull
  it out of UI components and route handlers into plain functions so it is testable without
  a browser or a server.
- Each acceptance criterion that is about behaviour gets at least one test named after it.
- Every bug fix starts with a test that reproduces the bug and fails.

Don't test-drive layout, styling or glue code that only wires libraries together; the
browser test covers those.

## A good test

- Tests one behaviour and has a name that says it: `moves a card from waste to foundation
  when the suit matches and the rank is next`.
- Arrange, act, assert: set up, one action, check the outcome.
- Checks results and visible state, not private details. If a refactor that keeps behaviour
  breaks the test, the test was about the wrong thing.
- Independent: no shared mutable state, no dependence on order, on the clock, on randomness
  or on the network. Inject the clock and the random source; use a seeded generator.
- Fast. Unit tests run in milliseconds; anything slow belongs in a different suite.

## Boundaries worth a case each

Empty, one, many. Zero and negative. The limit, one under and one over. Duplicates. Unicode
and very long strings. Missing and null fields. Midnight, month ends, time zones and daylight
saving for dates. The same action twice.

## Test doubles

Prefer real objects. Fake only what is slow, random or outside your control (network,
clock, storage), and fake it at the edge. A test where everything is mocked tests the mocks.
For HTTP handlers use the framework's test client against the real app with a temporary
database, not mocked handlers.

## When a test fails

Read the failure fully before changing anything. Decide whether the test or the code is
wrong by going back to the requirement. Never weaken an assertion, skip a test or add a
retry to get green; that hides the defect and the reviewer will find it.

## Done means

All tests pass, the new behaviour has tests that would fail without it, and no test was
deleted or loosened without a reason written in the commit message.
