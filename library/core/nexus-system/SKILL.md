---
name: nexus-system
description: How Nexus itself is built (services, stores, models, agent teams, memory, skills, admin) and where each part lives in the repo. Use for any task about Nexus itself, such as explaining how it works, comparing a tool or framework against it, or proposing or planning a change to it.
metadata:
  category: engineering
---

# Nexus: how it is built

Use these facts instead of guessing when a task is about Nexus. When something here doesn't
cover the question, say so rather than assuming. The repository is SS12dev/nexus-system;
`docs/INDEX.md` there lists every doc, and `README.md` is the full manual.

## What it is

A personal supervisor assistant. Each person names their own assistant. It answers quick
things itself and gives all real work to agent teams, which it designs, runs and removes.
Web app for laptop and phone, with a 3D orb home page (teams around it; inside, a
constellation of the people, projects and topics it knows, lighting up what each reply
recalled). It runs with Docker Compose on one machine, reached through a Cloudflare tunnel at nexus.servhub.cc.

## Services (compose.yaml)

| Service | Code | Job |
| --- | --- | --- |
| web | apps/web | React + Vite PWA: setup interview, orb, chat, Library, Routines, Memory, Agents, Skills, People, Admin |
| api | services/api | FastAPI: auth (Google sign-in), chats, files, memories, skills, teams, approvals, admin, WebSocket |
| orchestrator | services/orchestrator | arq worker: the assistant's LangGraph graph, tools, agent teams, routines, memory jobs, self-review |
| doc-worker | services/doc-worker | parses uploads (Docling), chunks and embeds them |
| sandbox | services/sandbox | runs agent code (`run_code`) with no network and resource limits |
| workbench, egress-proxy | services/workbench, infra/egress-proxy | dev teams' persistent project folders, commands and git; npm/PyPI/GitHub-only network |
| deployer, docker-gate | services/deployer | builds finished projects as apps at `<app>.servhub.cc`, through a filtered Docker socket |
| litellm | infra/litellm | the model gateway: aliases nexus-nano/mini/pro/max (OpenAI models), per-user and per-run budgets, spend logs |
| langfuse-web, langfuse-worker, clickhouse | (images) | traces of every model call |
| postgres, redis (Valkey), minio | infra/postgres | the stores below |
| traefik, cloudflared | infra | routing and the tunnel |
| backup | infra/backup | nightly pg_dump and MinIO copy |

## Stores

- **Postgres 17 with pgvector**: all app data (users, chats, messages, files metadata,
  memories, skills, teams and runs, projects, links, settings overrides, proposals) and the
  **LangGraph checkpoints** (AsyncPostgresSaver), so paused runs survive restarts. LiteLLM
  keeps its own database alongside.
- **Valkey** (the `redis` service): the arq job queue, per-user event channels for the
  WebSocket (`message.delta`, `tool.call`, ...), the settings version that live-applies Admin
  changes, rate limits and caches. Nothing durable lives only here.
- **MinIO (S3)**: uploaded and generated files.
- Embeddings: Ollama bge-m3 on the host; search is hybrid (vector + keyword) and falls back to
  keywords when embeddings are down.

## How a turn works

API stores the message and queues the turn → the orchestrator builds context (profile,
nearest memories, past-chat summaries, attached documents) → a nano router decides direct or
team → the assistant's graph calls LiteLLM with the user's key, streaming over Valkey → the
reply is stored and a memory job extracts durable facts.

## Agent teams

`build_agent_system` asks a designer model for a spec (`nexus_common/spec.py`): topology
single, pipeline, supervisor_workers, parallel_map_reduce, critic_loop or dev_project; agents
with tools, skills and tiers; a budget. Runs compile to LangGraph graphs
(`systems/compile.py`, `systems/runner.py`), each with its own budgeted LiteLLM key. Risky
tools pause the run for an approval card. dev_project (`systems/dev.py`) is a fixed team:
brief, plan, frontend + backend build, test (Nexus wires and runs the suites itself,
`systems/suites.py`), review, up to three rounds, then release to a private GitHub repo and
deploy.

## Memory, skills, instructions

- Memory (`orchestrator/memory.py`): facts about the person, never task requests; a decider
  adds, updates or ignores each candidate; a profile is rebuilt nightly.
- Knowledge graph (`nexus_common/graph.py`): entities (people, projects, organisations,
  places, topics, tools, plus one for the user), relations between them with valid_from and
  valid_to, and mentions tying each to the memories, past chats, files and chats about it.
  Extracted in the same memory-model call; `link_graph` backfills the rest every ten minutes;
  entities nothing mentions are pruned. People browse it on the Knowledge page (/knowledge).
- Skills: SKILL.md folders. Core skills ship in `library/core` and sync on start; learned
  skills are drafted from runs and stay off until approved.
- Instructions: layered (global, role, user) from `library/instructions` and the database.

## Admin

Owner-only Admin page: Status, Alerts, Models (LLM monitoring from LiteLLM spend logs),
Usage, Improvements (self-review proposals the owner approves or denies; skills,
instructions and settings apply at once and can be undone, tools and code become draft pull
requests with a spec in `docs/proposals/`), Feedback, Recall (scores memory recall on the owner's own
memories), Monitoring (logs with request ids), Audit,
Backups and Configuration. Runtime settings are declared in `nexus_common/config.py`;
overrides are stored in the database and win over `.env`. Secrets live in
`secrets/nexus.sops.env`, never in Admin.

## Where to change things

| To change | Look in |
| --- | --- |
| a runtime setting | `packages/common/src/nexus_common/config.py` (+ settings.py) |
| the assistant's prompt or tools | `orchestrator/prompts.py`, `orchestrator/tools.py`, `nexus_common/toolinfo.py` |
| team design or running | `orchestrator/systems/` |
| the dev team | `nexus_common/devteam.py`, `systems/dev.py`, `suites.py`, `release.py` |
| memory | `orchestrator/memory.py`, `nexus_common/graph.py`, recall in `orchestrator/context.py` |
| onboarding and profile packs | `api/onboarding.py`, `nexus_common/profiles.py`, `library/profiles/*.yaml`, `web/src/pages/OnboardingPage.tsx` |
| routines | `nexus_common/routines.py` (schedules, storing), `orchestrator/routines.py` (running, delivery), `orchestrator/routine_tools.py`, `api/routes/routines.py` |
| WhatsApp notices | `nexus_common/whatsapp.py` |
| push notifications | `nexus_common/push.py`, `api/routes/push.py`, `web/public/push-sw.js`, `web/src/lib/push.ts` |
| feedback | `api/routes/feedback.py`, `orchestrator/notify.py`, `web/src/components/FeedbackDialog.tsx`, `pages/AdminFeedback.tsx` |
| terms and privacy pages | `web/public/terms.html`, `privacy.html`, `TERMS_VERSION` in `nexus_common/models.py` |
| light theme, tour | `web/src/index.css`, `lib/theme.ts`, `components/Tour.tsx` |
| the orb's layout (stars, memories, moons outside) | `web/src/orb/model.ts`; drawing in `orb/Inside.tsx`, `Network.tsx`, `Satellites.tsx` |
| the logo | `web/src/components/Orb.tsx` (`NexusMark`), `web/public/icon.svg` |
| checking one account | `api/inspect_user.py`, `scripts/inspect-user.sh` |
| what Langfuse stores | `orchestrator/llm.py` (`private`), `nexus_common/embeddings.py` |
| self-review | `orchestrator/improve.py`, `nexus_common/improve.py`, `api/routes/improvements.py`, `web/src/pages/AdminImprovements.tsx` |
| alerts | `api/alerts.py`, `web/src/pages/AdminAlerts.tsx` |
| model monitoring (Models tab) | `nexus_common/llmstats.py`, `web/src/pages/AdminModels.tsx` |
| logs, request ids, Monitoring tab | `nexus_common/logs.py`, `nexus_common/metrics.py`, `api/middleware.py` |
| job retries | `orchestrator/retry.py` |
| an API route | `services/api/src/nexus_api/routes/` |
| a page | `apps/web/src/pages/` |
| the database schema | `nexus_common/models.py` + an Alembic migration in `services/api/alembic` |

Every change also updates the README section it affects. Checks: `./scripts/check.sh`.
