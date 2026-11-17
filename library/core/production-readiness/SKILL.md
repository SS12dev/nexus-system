---
name: production-readiness
description: Bring a working application up to the standard a professional team ships - clean lint and types, error handling, configuration, logging, a real README, a changelog and a release checklist. Use before handing over or releasing software, and when reviewing whether a project is ready.
metadata:
  category: engineering
---

# Production readiness

"It runs" is the start. This is the checklist between a working build and something another
developer would be glad to inherit.

## Code health

- The linter and the type checker pass with no errors; warnings are fixed or justified in
  one line next to the suppression. No `any`, `@ts-ignore` or `# type: ignore` to get past
  a real problem.
- TypeScript in strict mode. Python with type hints on public functions.
- No dead code, commented-out blocks, stray `console.log`/`print`, or TODOs without an owner.
- Functions do one thing; files have one subject; names say what things are. Duplication
  removed once it appears a third time.
- Formatting is automatic (Prettier, Ruff format) and consistent.

## Errors

- Every call that can fail (network, storage, parsing, user input) has a decided outcome:
  handled with a message the user can act on, or allowed to fail loudly. Nothing is caught
  and ignored.
- The UI has an error boundary or equivalent so one failure doesn't blank the page.
- The server has one handler for unexpected errors: log the detail, return a plain message.
- Async work is awaited or its failure handled; no floating promises.

## Configuration

- Everything that differs between environments is an environment variable, read in one
  place, validated at start-up with a clear message when missing.
- `.env.example` lists every variable with a comment and no secret values.
- No secrets in the repository or in client-side code.
- Sensible defaults so the project starts after `install` and one command.

## Operability

- A health endpoint when there is a server.
- Structured, levelled logs: one line per request and one per failure, with a request id; no
  personal data or secrets.
- Start-up fails fast on bad configuration instead of limping.
- Data changes go through migrations.

## Dependencies

- A committed lockfile. Runtime and development dependencies separated.
- Each dependency earns its place; unused ones removed.
- The vulnerability audit is clean for what ships, or exceptions are listed with a reason.
- A stated runtime version (`engines` in package.json, `requires-python`).

## The README a developer needs

1. What it is, in two sentences, with a screenshot or the live link when there is one.
2. Requirements and the exact commands to install, run, test and build.
3. Configuration: the variables and what they do.
4. How it is laid out: the main folders and where to start reading.
5. Decisions worth knowing: why this stack, known limits, what is deliberately left out.

Keep it true: every command in it was run and worked.

## History

- Small commits with messages that say what changed and why, in the imperative
  ("Add undo for card moves"). No "fix", "wip", "updates".
- A `CHANGELOG.md` with an entry per release or per run: added, changed, fixed.

## Before saying it is ready

Run, in a clean checkout: install, lint, type check, tests, build, then start the built app
and do the main journey once. Report what you ran and the result of each. Anything that
didn't pass is listed as open, never left out.
