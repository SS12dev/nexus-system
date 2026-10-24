# Docs index

Start here to find how a part of Nexus works and where its code lives. Keep this file current:
a change that adds a doc, a service or a major module adds a line here too.

| Doc | What it covers |
| --- | --- |
| [README.md](../README.md) | The manual: quick start, sign-in, people and budgets, models, how a message flows, memory, documents, agent teams, the sandbox and workbench, software teams, skills, the orb, Admin, self-review, settings, logs, security, caching, secrets, backups, repository layout, developing |
| [library/core/nexus-system/SKILL.md](../library/core/nexus-system/SKILL.md) | The architecture brief: services, stores, a turn, agent teams, memory, Admin, and where to change things. Agents load it for tasks about Nexus itself, and the self-review reads it before proposing changes |
| [docs/containers.md](containers.md) | Every container in compose.yaml and what it does |
| [docs/app-urls.md](app-urls.md) | How built apps get their `<app>.servhub.cc` address and who can open them |
| [docs/github-app.md](github-app.md) | Setting up the GitHub App that dev teams and the self-review push with |
| [docs/plans/ready-for-friends.md](plans/ready-for-friends.md) | Plan for opening Nexus to friends: onboarding interview, profile packs, scheduled routines, UX polish, privacy |
| [docs/proposals/](proposals/) | Specs of approved self-review proposals, one per pull request |
| [library/README.md](../library/README.md) | The shared skills and instruction layers |
| [.env.example](../.env.example) | Start-up settings; runtime settings can live in Admin › Configuration instead |

## Where things live

| Area | Code |
| --- | --- |
| Web app | `apps/web/src` (pages in `pages/`, the orb in `orb/`) |
| API | `services/api/src/nexus_api` (routes in `routes/`, migrations in `services/api/alembic`) |
| Assistant, tools, teams | `services/orchestrator/src/nexus_orchestrator` (teams in `systems/`) |
| Shared models and settings | `packages/common/src/nexus_common` (`models.py`, `settings.py`, `config.py`, `spec.py`) |
| Sandbox, workbench, deployer, doc-worker | `services/<name>` |
| Skills and instructions | `library/core`, `library/instructions` |
| Infrastructure | `compose.yaml`, `infra/`, `scripts/` |
