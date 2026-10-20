# Jarvis

Jarvis is a supervisor agent you chat with. It answers simple asks itself and, from phase 3 on,
designs, runs and tears down teams of sub-agents with their own tools and model tiers. The
design lives in the architecture doc, "Jarvis: Architecture & Research".

This repository is at **phase 5, production hardening**. Phase 1 gave sign-in
with invites and roles, private chats per user, Jarvis streaming replies over a WebSocket, every
model call going through LiteLLM with a per-user budget, and every call traced with its cost in
Langfuse. Phase 2 adds long-term memory (a profile of you, facts and preferences, summaries of
past chats) and a document library: upload PDF, Word, PowerPoint, Excel and more in a chat or on
the Library page and ask Jarvis about them. Phase 3 lets Jarvis build teams of sub-agents for
bigger jobs, with web search, a code sandbox that makes PDF, Word, PowerPoint and Excel files, a
shared skills library, per-run budgets, and your approval before anything risky. Phase 4 makes
the home screen a 3D orb: Jarvis in the middle, its live agent teams growing out of it and
pulling back in, and your memories and artifacts inside it. Phase 5 makes it safe to invite
people: rate limits, security headers and cross-site checks, two API replicas behind Traefik,
nightly backups with a restore script, and an Admin page with service health, traffic, usage,
the audit log, backups and a maintenance switch. All of it runs on one machine with Docker
Compose.

## Quick start

```sh
./scripts/init-env.sh          # writes .env with random secrets
$EDITOR .env                   # add OPENAI_API_KEY, and Google OIDC or DEV_LOGIN_ENABLED=true
brew install ollama            # local embeddings for memory and document search (free)
ollama pull bge-m3             # then keep Ollama running (the menu bar app, or `ollama serve`)
docker compose up -d --build
```

Before others use it, encrypt the secrets with `./scripts/secrets.sh init` and start Jarvis with
`./scripts/compose.sh` from then on (see [Secrets](#secrets)).

The first build takes a while: the doc-worker image includes Docling with CPU PyTorch and its
models (a few GB). `DOCLING=false` in `.env` builds a small one instead, without OCR.

| What | Where |
| --- | --- |
| Jarvis | http://localhost:8080 |
| Langfuse (traces, cost) | http://localhost:3000 (`LANGFUSE_PORT`), login from `LANGFUSE_ADMIN_EMAIL` / `LANGFUSE_ADMIN_PASSWORD` |
| LiteLLM admin API | http://localhost:4000, `LITELLM_MASTER_KEY` |
| MinIO console | http://localhost:9001 |
| Admin page (health, usage, audit log, backups) | http://localhost:8080/admin |

What each of the containers Compose starts is for, and how they connect, is in
[docs/containers.md](docs/containers.md).

The first person to sign in becomes the **owner**. Give Docker Desktop at least 8 GB of memory;
ClickHouse (for Langfuse) is the heaviest container.

### Signing in

- **Google (OIDC).** Create an OAuth client of type "Web application" in Google Cloud and add
  every callback URL you will use, for example `http://localhost:8080/auth/callback` and
  `https://jarvis.servhub.cc/auth/callback`. Put the client id and secret in `OIDC_CLIENT_ID` and
  `OIDC_CLIENT_SECRET`. Any OIDC provider works; set `OIDC_ISSUER`.
  Google sign-in also asks for the Google services in `GOOGLE_SERVICES`; see below.
- **Dev sign-in.** With `DEV_LOGIN_ENABLED=true`, the login page also accepts an email plus
  `DEV_LOGIN_TOKEN`. It follows the same invite rules, but anyone with the token can sign in as
  any invited user, so keep it off on a public hostname.

### Google services (Gmail, Calendar, Contacts, Tasks)

With Google sign-in, the same consent screen also asks for the services in `GOOGLE_SERVICES`
(default `gmail,calendar,contacts,tasks`; `drive` is also available, read-only). Each one has
its own checkbox, so people can untick any of them and still sign in. Jarvis keeps a refresh
token per user, encrypted with a key derived from `SECRET_KEY` (changing it disconnects
everyone until they sign in again). Each person sees what they allowed on the **Connections**
page, can allow more there, or disconnect, which also revokes the token at Google.

| Service | Scope | Google's class |
|---|---|---|
| gmail | `gmail.modify` (read, label, draft, send; no permanent delete) | restricted |
| calendar | `calendar` | sensitive |
| contacts | `contacts.readonly` | sensitive |
| tasks | `tasks` | sensitive |
| drive | `drive.readonly` | restricted |

One-time setup in Google Cloud, on the project that holds the OAuth client:

1. **APIs & Services → Library:** enable Gmail API, Google Calendar API, People API and Google
   Tasks API (and Google Drive API if you add `drive`).
2. **Google Auth Platform → Data Access → Add or remove scopes:** add the scopes in the table
   for the services you ask for.
3. **Audience:** choose how the app is published:
   - **In production, unverified (recommended for you and a few invitees).** Everyone sees a
     "Google hasn't verified this app" screen and clicks *Advanced → Go to Jarvis*. Refresh
     tokens last until revoked. Google caps an unverified app with sensitive or restricted
     scopes at 100 users, ever.
   - **Testing.** Only people listed as test users (up to 100) can sign in, and Google expires
     their refresh tokens after 7 days, so agents lose Gmail and Calendar access weekly until
     the person signs in again.
   - **Verified.** Needed only to open Jarvis to the public: sensitive scopes need Google's
     review, and restricted ones (Gmail, Drive) also a yearly third-party security assessment.
     Not worth it for a private Jarvis.

To sign in with name and email only, set `GOOGLE_SERVICES=` (empty).

What Jarvis and agents can do with them (only for services the person allowed):

| Service | Jarvis in chat | Agents only, approved on a card first |
|---|---|---|
| Gmail | search, read, save drafts, archive / mark read / star / label | send (`gmail_send`) |
| Calendar | list events, add or change own events without guests | send invitations, delete events |
| Contacts | search | |
| Tasks | list, add, complete | |

Nothing can trash mail or mark it as spam. Calendar times without an offset are read in the
calendar's own time zone.

### On your phone

Jarvis is an installable PWA. For Google sign-in from a phone, publish it through the Cloudflare
Tunnel at `jarvis.servhub.cc` (see [infra/cloudflared](infra/cloudflared/README.md)) and use
"Add to Home Screen". On your home network you can also open `http://<mac-ip>:8080` with dev
sign-in, since Google does not accept private IP addresses as callbacks.

## People, roles and budgets

- **Roles:** owner > admin > member > viewer, checked on every API route. Viewers can read their
  own chats but not send messages. Admins manage members and viewers on the People page; only
  owners grant admin or owner, and the last active owner can't be demoted or disabled.
- **Who is owner:** the first person ever to sign in becomes owner; everyone after that joins
  with the role they were invited with (member by default). Only owners see the Admin page and
  can call its APIs (`/api/admin/status`, `usage`, `audit`, `backups`, `maintenance`), so invite
  friends as members, or admins if they should help manage people.
- **Invites:** with `SIGNUP_POLICY=invite_only` (the default), an admin invites an email on the
  People page and that person joins the first time they sign in with it. `open` lets anyone join
  as a member.
- **Private data:** every chat belongs to one user. Other users, admins included, get a 404.
- **Budgets:** each user gets their own LiteLLM virtual key the first time they chat, capped at
  `DEFAULT_BUDGET_USD` (owner: `OWNER_BUDGET_USD`) per 30 days. Admins change budgets on the
  People page; disabling a user signs them out everywhere and blocks their key. When a budget runs
  out, Jarvis says so in the chat instead of failing silently.
- Admin actions, sign-ins and sign-ups are recorded in `audit_log`, shown on the Admin page.

## Models

All model calls go to LiteLLM using four aliases, so a model swap is a change to
[infra/litellm/config.yaml](infra/litellm/config.yaml):

| Alias | Model | Used for |
| --- | --- | --- |
| `jarvis-nano` | gpt-5.4-nano | routing, extraction, titles, summaries |
| `jarvis-mini` | gpt-5.4-mini | plain chat (phase 1 default), most sub-agents |
| `jarvis-pro` | gpt-5.4 | planning and designing agent systems |
| `jarvis-max` | gpt-5.5 | escalation only |
| `jarvis-embed` | bge-m3 in Ollama on the host | memory and document search |

LiteLLM runs in Compose with an exact-match Redis cache and sends every generation to Langfuse.
To use another LiteLLM (for example on a server later), set `LITELLM_BASE_URL` and its
`LITELLM_MASTER_KEY`. Each turn is one Langfuse trace (named `jarvis.turn`) grouped into a session
per chat and attributed to the user's id.

## How a message flows

1. The web app posts the message, with the ids of any attached files, to
   `POST /api/chats/{id}/messages`. The API stores it and an empty reply, then queues the turn on
   Redis (arq).
2. An orchestrator worker assembles context: your profile, the memories and past-chat summaries
   closest to the message, and the documents attached to the chat (whole if short, otherwise the
   best-matching excerpts).
3. It runs the Jarvis LangGraph graph, calling LiteLLM with the user's own key. Jarvis can call
   its own tools in a loop: `search_documents`, `read_document`, `list_documents`,
   `search_memory` and `remember`. Each tool sees only that user's data.
4. Tokens are published on the user's Redis channel as `message.delta` events (and tool use as
   `tool.call`). Every API replica holding a WebSocket for that user forwards them, so replicas can
   scale behind Traefik.
5. The finished reply, with token counts, is stored and sent as `message.completed`, and a
   memory job is queued for the turn.

## Memory

| Layer | What | When it is written | When it is read |
| --- | --- | --- | --- |
| Profile | A short impression of you, in sections | Nightly, on "Rebuild now", and soon after your first memories | Every turn, at the top of the prompt |
| Memories | Preferences, facts, goals and standing instructions, with confidence and source | After each turn | Every turn, the closest few to your message |
| Past chats | A summary of each chat | Once a chat has been quiet for 20 minutes, or grows a lot | When one is close to your message; also the start of a chat too long to send whole |

After each turn `jarvis-nano` proposes memories from the exchange. Each is compared with its
nearest existing memories and becomes ADD, UPDATE or NOOP (the Mem0 pattern); repeats raise a
memory's confidence, contradictions update it, and "forget that" deletes it. Pinned memories are
never changed by Jarvis. Each night, memories no chat has confirmed in 60 days lose confidence
(and go below 0.2), near duplicates are merged, and stale profiles are rebuilt; set the time with
`MEMORY_NIGHTLY_HOUR_UTC` (default 21:30 UTC, 03:00 IST).

On the **Memory** page you see the profile and every memory with where it came from and its
history, and can add, edit, pin and delete them. Deleting is real deletion; you can also turn
learning off or forget everything.

Search is hybrid everywhere: pgvector cosine similarity and Postgres full-text search fused by
reciprocal rank, so it still works by keyword if the embedder is down.

## Documents

Upload in a chat (paperclip, drag and drop, or paste) or on the **Library** page. Files go to
MinIO through the API (up to `MAX_UPLOAD_MB`), and the doc-worker parses them: Docling for PDFs
and images, MarkItDown for Word, PowerPoint, Excel, CSV and HTML. The text is chunked by heading
and sheet, embedded and indexed; see [services/doc-worker](services/doc-worker/README.md). The
Library page lists, searches inside, previews the extracted text, renames, downloads and deletes
files.

Embeddings go through LiteLLM under the `jarvis-embed` alias, which points at bge-m3 in Ollama
on the Mac (Docker on macOS can't use the GPU, so Ollama runs natively). Vectors are cached by
text hash, and every stored vector records its model, so swapping the embedder (for example to
OpenAI's `text-embedding-3-small` with `dimensions: 1024`, see
[infra/litellm/config.yaml](infra/litellm/config.yaml)) re-embeds everything in the background.

## Agent systems

For a big ask ("research EV battery suppliers and give me a report and a spreadsheet"), Jarvis
calls `build_agent_system`. Before Jarvis answers a message, the cheapest tier (`ROUTE_MODEL`,
jarvis-nano) decides whether it is work for a team (multi-source research, deliverables, many
items, sending email); when it is, Jarvis's first move is to build one. The architect model (`DESIGN_MODEL`, jarvis-pro) writes an
AgentSystemSpec: a small team, a topology, each agent's role, instructions, model tier, tools and
skills. The spec is validated against what you may use, compiled to a LangGraph graph and run on
the orchestrator queue; the team's card in the chat shows each agent live, and the result and
files are posted in the chat when it finishes. One-off teams are archived after their run, and
the orb only shows teams with a run in progress; "keep it" saves one as a template Jarvis
reuses for similar asks (Jarvis only keeps a team up front when you say so).

- **Topologies:** single agent, pipeline, lead and workers (`supervisor_workers`), parallel
  map-reduce, and worker with critic.
- **Model tiers:** the spec picks nano, mini or pro per agent. jarvis-max is only for a team's
  lead, and only for roles in `MAX_TIER_ROLES`. An agent whose model call fails retries one
  tier up; non-lead agents drop a tier once 80% of the run's budget is spent.
- **Budgets:** each run gets its own LiteLLM key capped at its budget (`DEFAULT_RUN_BUDGET_USD`,
  at most `MAX_RUN_BUDGET_USD` and what you have left this period), and a run whose estimate
  doesn't fit is refused before it starts. The key is revoked when the run ends; its cost counts
  against your budget and is shown on the card and the Agents page, with the Langfuse trace id.
- **Approvals:** sending email and deleting documents always pause the run for you: approve,
  edit the arguments, or reject, on the card in the chat or the Agents page. Every decision is in
  the audit log. Runs can also be paused, resumed and stopped. A parked run is checkpointed in
  Postgres, so it survives restarts.
- **Tools** (the Tool Registry, `/api/tools`): web search and page reading, the user's documents
  and memory, saving files, `run_code`, `send_email`, `delete_document`. Members get running code
  and sending only when an admin ticks them on the People page; owners and admins have both.
- **Agents page:** every team, its graph, agents, runs with cost and timeline, files made, and
  the steps waiting for you.

Web search uses Tavily or Brave when `TAVILY_API_KEY` or `BRAVE_API_KEY` is set (both have free
tiers), else DuckDuckGo's HTML page, which needs no key but is less reliable. Email needs the
`SMTP_*` settings (Gmail works with an app password).

### The sandbox

`run_code` runs Python or bash in the `sandbox` service: a read-only container on an internal
network (no internet, no access to Jarvis's databases or your LAN), all capabilities dropped,
memory, CPU and process limits, and per-job CPU, memory, file-size and time limits. The task's
files go in `in/`, the agent's skills in `skills/`, and whatever it writes to `out/` is saved to
your Library as a generated file (saving the same name again makes a new version). It has
pypdf, pdfplumber, ReportLab, WeasyPrint, python-docx, python-pptx, openpyxl, pandas and
matplotlib.

## Skills and instructions

Skills are folders in the Agent Skills format: a `SKILL.md` with a name and a one-line
description, plus `scripts/` and `references/`. Agents see only the names and descriptions; they
load a skill's instructions with `skills_load` when a task needs it and read its files with
`skills_read_file`, so prompts stay small. Core skills ship in [library/core](library/core) and
are synced into the database when the orchestrator starts. 27 skills sit on seven shelves:
documents (PDF, Word, PowerPoint, Excel, charts, images, conversions), research (web research,
literature reviews, reading papers, research gaps, paper writing, citations, fact-checking),
writing (reports, summaries, email, meeting notes), data (analysis, statistics, ML experiments,
LLM evaluation), productivity (inbox triage, scheduling, comparing options), agents (prompt
writing) and engineering (code review). Every script is tested in the sandbox; see
[library/README.md](library/README.md).

The **Skills** page lists core, shared, your own and learned skills, with editing, version
history, rollback, enable/disable and import (a `SKILL.md` or a `.zip`). Imported skills, and
skills Jarvis drafts after a long successful run, stay off until reviewed; skills whose runs
keep failing are flagged; it filters by shelf, and admins can share one of their own skills
(say, one Jarvis learned) with everyone. Its Instructions tab holds the layers every agent gets,
in order: the shipped safety rules and house conventions
([library/instructions](library/instructions)), global and per-role instructions (admins), then
your own house style. Jarvis's own chat follows the conventions, global instructions and yours.

## The orb

The home screen is a React Three Fiber scene ([apps/web/src/orb](apps/web/src/orb)) with the chat
box docked underneath; sending a message opens the chat.

- **The orb** is a shader sphere whose colour, churn and pulse follow what Jarvis is doing: blue
  and calm when idle, violet while thinking, bright and pulsing while answering.
- **Around it**, every team that isn't archived is a cluster tied to the orb by an edge: the
  lead hangs from the orb, workers from the lead (a pipeline is a chain). Agents are coloured
  by their live status (working, using a tool, waiting for your approval, done), and a spark
  runs down the edge to a busy agent. A new team grows out of the orb, agent by agent; when a
  team is deleted, or archived after its run, its agents pull back in and fade. Agents move on
  springs, so nothing jumps.
- **Inside it**, memories are particles clustered by kind (preferences, facts, goals,
  instructions), pinned ones brighter; artifacts (files Jarvis or a team made) are small
  crystals. A new one grows in with a flash.
- **Tap** an agent, memory or crystal for a card that opens the team, the memory on the Memory
  page, or the file. Drag to turn, pinch or scroll to zoom.
- **Phones** get fewer particles, no bloom, a lower pixel ratio and a 30 fps cap, and teams sit
  on a tighter band on portrait screens. **List** (top right) shows the same teams, memories and
  artifacts as plain lists; it is also what shows when the device has no WebGL. With
  reduced motion turned on in the OS, nothing spins or pulses and objects snap into place.

Everything is driven by the existing WebSocket events (`jarvis.state`, `system.*`, `run.updated`,
`agent.status`, `approval.*`, `memory.updated`, `artifact.created`, `file.*`), with a fresh fetch
on open and after a reconnect. three.js loads only on the home screen, as its own chunk.

## Running it for other people

### Admin page

Only owners get **Admin** in the menu (admins get **People** but not this page). A person
filter at the top narrows Status, Usage and the Audit log to one person (tap a name) or several
(tick them); service health, queues, API traffic and backups always cover all of Jarvis.

- **Status:** whether Postgres, Valkey, LiteLLM, MinIO and both workers are up (workers report a
  heartbeat every 30 seconds), jobs waiting in each queue, the last 24 hours (turns, tokens,
  agent runs and their cost, failures), model spend this period against the sum of budgets,
  when the last backup finished, API requests per minute for the last hour with errors and
  rate-limited calls, and the latest failed turns, runs and file parses with their error.
  The owner sees counts, costs and error messages, never chat content. Per-call traces stay in
  Langfuse (`LANGFUSE_URL`).
- **Maintenance mode:** while it is on, everyone sees your message in a banner and can read
  their chats, but only admins can start turns and agent runs. Turn it on before restarting or
  upgrading Jarvis.
- **Usage:** turns, tokens, agent runs and their cost, and files per person and per day, over 7,
  30 or 90 days.
- **Audit log:** sign-ins (with IP), invites, role, budget and grant changes, approvals,
  sign-outs, backups and maintenance, filterable by kind.
- **Backups:** the list, and **Back up now**.

On the People page an admin can also **sign someone out** on every device. Everyone can see and
sign out their own signed-in devices on the Connections page.

### Limits and security

| Limit | Default | Setting |
| --- | --- | --- |
| API requests per IP | 600 a minute | `RATE_LIMIT_API_PER_MINUTE` |
| Sign-in attempts per IP | 20 a minute | `RATE_LIMIT_AUTH_PER_MINUTE` |
| Chat messages per person | 20 a minute | `RATE_LIMIT_MESSAGES_PER_MINUTE` |
| Uploads per person | 30 a minute | `RATE_LIMIT_UPLOADS_PER_MINUTE` |
| Agent runs started from the Agents page per person | 30 an hour | `RATE_LIMIT_RUNS_PER_HOUR` |

Counters live in Valkey, so they hold across API replicas; 0 turns a limit off and
`RATE_LIMIT_ENABLED=false` turns them all off. Over the limit, the API answers 429 with
`Retry-After` and the app shows the message. Behind the tunnel the caller's IP is Cloudflare's
`CF-Connecting-IP`. Model spend stays capped by each person's LiteLLM budget as before.

- **Cross-site requests:** the session cookie is `SameSite=Lax` and `HttpOnly`, and the API also
  refuses any write whose `Origin` is another site (other apps on servhub.cc included). Add an
  origin to `ALLOWED_ORIGINS` only if you serve the web app from somewhere else.
- **Headers:** the web app sends a Content Security Policy (scripts only from Jarvis itself, no
  framing), `nosniff`, a strict referrer policy and, over https, HSTS. API responses are
  `Cache-Control: no-store`. Uploaded files download as attachments with `CSP: sandbox` (from
  phase 2).
- **Exposure:** only Traefik's port (`JARVIS_HTTP_PORT`) listens beyond the Mac's loopback;
  Postgres, LiteLLM, Langfuse and the MinIO console are bound to 127.0.0.1, and the API docs
  (`/docs`) aren't routed. Keep `DEV_LOGIN_ENABLED=false` and `SESSION_COOKIE_SECURE=true`
  on the public hostname.
- **Cloudflare Access** (optional) can sit in front of `jarvis.servhub.cc` for an extra
  Google or email check before anyone reaches the sign-in page; it needs no change here.

### Caching and load balancing

- LiteLLM caches identical model calls in Valkey for an hour (exact match only).
- Spend figures from LiteLLM are cached for a minute, so `/api/me` and the People and Admin
  pages don't call LiteLLM for every person on every load.
- Embeddings are cached by text hash in Postgres (phase 2).
- Web assets are content-hashed and cached for a year; nginx gzips them.
- Compose runs `API_REPLICAS` (default 2) API containers. Traefik balances requests and the
  WebSocket across them and stops sending to one whose `/healthz` fails; any replica can serve
  anyone, since sessions, limits and live events go through Postgres and Valkey.
  `ORCHESTRATOR_REPLICAS` (default 1) adds workers for more turns and runs at once, each using
  as much memory again.

### Secrets

`.env` starts out holding everything: plain settings and about twenty secrets (the OpenAI key,
the Google client secret, the tunnel token, database passwords). For production the secrets move
into `secrets/jarvis.sops.env`, encrypted with [SOPS](https://getsops.io) and an
[age](https://age-encryption.org) key, and `.env` keeps only settings:

```sh
brew install sops age
./scripts/secrets.sh init       # makes ~/.config/jarvis/age.key, moves the secrets, adds
                                # TOKEN_ENCRYPTION_KEYS; checks the file decrypts first
cat ~/.config/jarvis/age.key    # save this in your password manager
./scripts/compose.sh up -d      # docker compose with the secrets loaded, same arguments
```

Afterwards:

- `./scripts/compose.sh` replaces `docker compose` everywhere (it is plain `docker compose` when
  there is no encrypted file). Plain `docker compose up` stops with "... missing, use
  ./scripts/compose.sh", so nothing starts half-configured.
- `./scripts/secrets.sh set TAVILY_API_KEY` adds or changes one secret (typed hidden, or piped
  in), `edit` opens them all in `$EDITOR`, `list` shows the names. Apply with
  `./scripts/compose.sh up -d`.
- The decrypted values exist only in the environment of the command that starts the
  containers. Docker keeps them in each container's settings, so they survive restarts without
  the key; they are not written to disk in the repo.
- Saved Google tokens are encrypted with `TOKEN_ENCRYPTION_KEYS`, not `SECRET_KEY`, so either
  can be changed without signing everyone out of Google. To rotate the token key, put a new
  one first (`new,old`); each grant is re-encrypted the next time it is used, and the old key
  can be dropped later. Grants still under `SECRET_KEY` move over the same way.
- Every backup includes the encrypted file. It is useless without the age key, so keep the key
  somewhere other than the Mac.

On a new machine: clone the repo, put the age key at `~/.config/jarvis/age.key`, copy
`jarvis.sops.env` from a backup into `secrets/`, recreate `.env` from `.env.example` (settings
only), then restore the backup.

When Jarvis moves to a server, the same file can also be opened by a cloud key (SOPS supports
GCP KMS, AWS KMS and others as extra recipients), so the server needs no copy of the age key.

### Backups

The `backup` service backs up every night after `BACKUP_HOUR_UTC` (default 21, so 02:30 IST),
on its first start, whenever none has run for 26 hours (the Mac was asleep), and on **Back up
now**. Each backup is a folder in `BACKUP_DIR` (default `./backups`, gitignored) named by its
UTC time, with a `pg_dump` of the jarvis, litellm and langfuse databases, a tarball of the MinIO
data (uploads and generated files) and `SHA256SUMS`. Backups older than `BACKUP_KEEP_DAYS`
(14) are deleted, always keeping the newest `BACKUP_KEEP_MIN` (3). The Admin page warns when the
last good one is older than `BACKUP_STALE_HOURS` (36).

Each backup also holds the encrypted secrets file once you've run `./scripts/secrets.sh init`.
Not included: `.env` and your age key (keep the key in your password manager; without it, or
the old `.env` before encrypting, saved Google connections can't be decrypted), ClickHouse
(Langfuse's trace details) and Valkey (queues and caches). To keep a copy off the Mac, point
`BACKUP_DIR` at a synced or external folder.

To restore, with the same secrets:

```sh
./scripts/restore.sh backups/2026-10-20T210000Z
```

It checks the checksums, asks you to type `restore`, stops Jarvis, recreates each database from
its dump, replaces the MinIO data, and starts everything again (the tunnel too, if it was up).

## Repository layout

```
apps/web/               React + Vite + Tailwind PWA (orb home, chat, Library, Memory, Agents,
                        Skills, People); the 3D scene is in src/orb
services/api/           FastAPI: auth, chats, files, memories, skills, agents, approvals, admin,
                        WebSocket; Alembic migrations
services/orchestrator/  arq worker: the Jarvis graph, the Tool Registry, agent systems (design,
                        compile, run, approvals), the memory and learned-skill jobs
services/sandbox/       FastAPI: runs agent code with no network and resource limits
services/doc-worker/    arq worker: parses, chunks and embeds uploads
infra/backup/           the backup service (pg_dump and MinIO, nightly and on request)
packages/common/        settings, models, events, storage, embeddings, hybrid search, the agent
                        system spec, tool catalogue and skills helpers
library/                core skills and the shipped instruction layers
infra/                  LiteLLM config, Postgres init, Cloudflare Tunnel notes
secrets/                the encrypted secrets file (gitignored; README: Secrets)
docs/containers.md      what every Docker container does and how they fit together
compose.yaml            the whole stack
```

## Developing

`./scripts/check.sh` runs everything CI runs, on your machine: ruff, the backend tests against
throwaway Postgres and Valkey containers, the web typecheck, tests and build, and the Compose
config and image builds (`--no-images` skips the builds). Run it before merging while GitHub
Actions is unavailable.

```sh
uv sync                                   # Python workspace (Python 3.12+)
docker compose up -d postgres redis litellm langfuse-web langfuse-worker
uv run pytest                             # needs Postgres and Redis; see conftest.py
uv run ruff check . && uv run ruff format --check .

cd apps/web && npm install
npm run dev                               # proxies /api, /auth and /ws to localhost:8000
npm test && npm run typecheck
```

Run the API and worker on the host against the Compose services with the matching URLs, e.g.
`DATABASE_URL=postgresql+asyncpg://jarvis:<password>@localhost:5432/jarvis`,
`REDIS_URL=redis://localhost:6379/0` (publish Redis's port first),
`LITELLM_BASE_URL=http://localhost:4000` and `S3_ENDPOINT_URL=http://localhost:9000` with
`S3_SECRET_KEY` (publish MinIO's port 9000 first), then `uv run uvicorn jarvis_api.app:app
--reload` in `services/api`, `uv run arq jarvis_orchestrator.worker.WorkerSettings` and
`uv run arq jarvis_doc_worker.worker.WorkerSettings`. Without Docling installed the doc-worker
falls back to MarkItDown for PDFs.

Schema changes: edit `packages/common/src/jarvis_common/models.py`, then
`uv run alembic revision --autogenerate -m "..."` in `services/api`.

## Roadmap

1. Foundation · 2. Memory and documents · 3. Agent systems, tools and the skills library ·
4. The 3D orb · 5. **Production hardening** (this) · 6. Move to a server, voice
