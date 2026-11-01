# Docs index

Start here to find how a part of Nexus works and where its code lives. Keep this file current:
a change that adds a doc, a service or a major module adds a line here too.

| Doc | What it covers |
| --- | --- |
| [README.md](../README.md) | The manual: quick start, sign-in, people and budgets, models, how a message flows, memory and the knowledge graph, documents, onboarding and routines, agent teams, the sandbox and workbench, software teams, skills, the universe, Admin (alerts, models, improvements, recall, monitoring, configuration), self-review, settings, logs and job retries, privacy, security, caching, secrets, backups, repository layout, developing and pinned images |
| [library/core/nexus-system/SKILL.md](../library/core/nexus-system/SKILL.md) | The architecture brief: services, stores, a turn, agent teams, memory, Admin, and where to change things. Agents load it for tasks about Nexus itself, and the self-review reads it before proposing changes |
| [docs/containers.md](containers.md) | Every container in compose.yaml and what it does |
| [docs/app-urls.md](app-urls.md) | How built apps get their `<app>.servhub.cc` address and who can open them |
| [docs/github-app.md](github-app.md) | Setting up the GitHub App that dev teams and the self-review push with |
| [docs/email.md](email.md) | Nexus's own mailbox: what each email looks like, choosing a provider, setting up Resend on servhub.cc or a Gmail account, replies |
| [docs/setup-for-friends.md](setup-for-friends.md) | Owner checklist for inviting friends: deploy, Google Cloud branding, push keys, WhatsApp, SMTP, invites, changing the terms |
| [docs/plans/ready-for-friends.md](plans/ready-for-friends.md) | Plan for opening Nexus to friends (onboarding, profile packs, routines, privacy), with what is built and what is left |
| [docs/proposals/](proposals/) | Specs of approved self-review proposals, one per pull request |
| [services/doc-worker/README.md](../services/doc-worker/README.md) | How uploads are parsed, chunked and embedded, and which Docling models the image bakes in |
| [library/README.md](../library/README.md) | The shared skills and instruction layers |
| [.env.example](../.env.example) | Start-up settings; runtime settings can live in Admin › Configuration instead |

## Where things live

| Area | Code |
| --- | --- |
| Web app | `apps/web/src` (pages in `pages/`, the home universe in `universe/`) |
| API | `services/api/src/nexus_api` (routes in `routes/`, migrations in `services/api/alembic`) |
| Assistant, tools, teams | `services/orchestrator/src/nexus_orchestrator` (teams in `systems/`) |
| Shared models and settings | `packages/common/src/nexus_common` (`models.py`, `settings.py`, `config.py`, `spec.py`) |
| Sandbox, workbench, deployer, doc-worker | `services/<name>` |
| Skills and instructions | `library/core`, `library/instructions` |
| Onboarding and profile packs | `services/api/src/nexus_api/onboarding.py`, `library/profiles`, `nexus_common/profiles.py` |
| Routines | `nexus_common/routines.py`, `services/orchestrator/src/nexus_orchestrator/routines.py` |
| Self-review, alerts, model monitoring | `nexus_orchestrator/improve.py`, `nexus_api/alerts.py`, `nexus_common/llmstats.py`, `apps/web/src/pages/Admin*.tsx` |
| Infrastructure | `compose.yaml`, `infra/`, `scripts/` |
