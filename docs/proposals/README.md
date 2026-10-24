# Self-review proposals

Each file here is the spec of a code or tool change that Nexus's
[self-review](../../README.md#self-review) proposed and the owner approved on Admin ›
Improvements. Nexus never writes code itself: approving one opens a draft pull request on
`IMPROVE_REPO` (from `IMPROVE_BASE_BRANCH`) on a `nexus/proposal-<title>-<id>` branch, holding
only its spec, named `<date>-<title>.md`.

A spec has a title, its kind, impact and effort, why it is worth doing, the evidence the review
saw (counts only, never chat text), the spec itself and the files it likely touches.

To implement one, check out its branch and work on it there (by hand, with a Claude Code session
or a dev team): read [INDEX.md](../INDEX.md) and the architecture brief in
[library/core/nexus-system/SKILL.md](../../library/core/nexus-system/SKILL.md) first, update the
docs the change affects, and run `./scripts/check.sh`. Then mark the pull request ready and merge
as usual; nothing merges by itself. To drop a proposal, close its pull request. The spec stays in
this folder once merged, as the record of why the change was made.
