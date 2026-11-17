---
name: technical-design
description: Write the design for a piece of software before any code - requirements, data model, API contract, components, error cases, acceptance criteria and test plan - so builders agree and reviewers have something to check against. Use when planning an app, a feature, a service or a non-trivial change.
metadata:
  category: engineering
---

# Technical design

A design is short and decides things. It exists so that two builders working side by side
produce parts that fit, and so the reviewer can say "the plan said X" rather than argue taste.
Write it in the order below; skip a section only by saying why it doesn't apply.

## 1. What and for whom

- The user's goal in one sentence, in their words.
- Who uses it and on what (phone, laptop, both). The main journey, step by step, as the user
  experiences it. Everything else in the design serves this journey.
- Out of scope: what a reader might assume is included but isn't.

## 2. Requirements

Number them. Each one is a behaviour someone can observe, not an implementation.

- Functional: "R3. A saved note survives a page reload."
- Quality: load time, works offline or not, accessibility level (keyboard use, contrast, labels),
  data kept private to its owner, supported screen widths.
- Constraints: the stack, hosting, what may not be used.

Unknowns get an explicit assumption ("A2. Assumes a single user; no sign-in") so a wrong
guess is visible and cheap to correct.

## 3. Data model

For anything that stores data, before the API:

- Each entity with its fields, types, which are required, defaults and limits (max length,
  ranges). Name the primary key and every uniqueness rule.
- Relationships and what happens on delete.
- Where it lives (browser storage, SQLite file, Postgres) and why.
- How it changes later: a migration tool from the first version when there is a database.

Load `core/data-modelling` when the model has more than two entities.

## 4. API contract

One table: method, path, request body, success response, error responses. Then the shared
rules once: error shape, status codes used, validation, pagination, auth. The front end is
built against this table and the back end is tested against it, so it must be exact: field
names, types, which fields are optional. Load `core/api-design` for the rules.

## 5. Components and files

- The file layout, with one line per file or folder saying what it owns.
- State: what is held where (server, URL, component, storage), and the one source of truth
  for each piece.
- Dependencies to add, each with its reason. Prefer the platform and the standard library;
  every dependency is something to audit and upgrade.

## 6. Failure and edge cases

List what goes wrong and what the user sees: empty state, first run, slow or failed network,
invalid input, duplicate submit, very long text, hundreds of items, two tabs open, storage
full, a refresh in the middle of an action. A design with no entries here isn't finished.

## 7. Acceptance criteria

Numbered, each checkable by a test or by one concrete action in the running app:
"AC4. With the network off, adding a note shows 'Saved on this device' and the note is there
after reload." Cover every requirement and the important failure cases. Avoid criteria nobody
can check ("the UI is intuitive").

## 8. Test plan

Which criteria are covered by unit tests (logic), which by API tests (contract), which by
the browser test (journeys). Name the risky logic that needs the most cases.

## For a change to existing software

Read the code first. State what exists, what changes and what must not change. Each reported
problem gets a cause (found, not guessed), a fix and an acceptance criterion that fails before
the fix and passes after.

## Quality bar

- A builder could start from this alone without asking a question.
- Nothing is planned that the request didn't ask for or need.
- It fits on two or three screens. Long designs hide the decisions.
