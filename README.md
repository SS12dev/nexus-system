# Nexus

Nexus gives each person an assistant they name themselves (Jarvis by default). The assistant is a supervisor agent: it answers simple asks itself and
designs, runs and tears down teams of sub-agents with their own tools and model tiers. The
design lives in the architecture doc, "Nexus: Architecture & Research".

This repository is at **phase 5, production hardening**. Phase 1 gave sign-in
with invites and roles, private chats per user, Nexus streaming replies over a WebSocket, every
model call going through LiteLLM with a per-user budget, and every call traced with its cost in
Langfuse. Phase 2 adds long-term memory (a profile of you, facts and preferences, summaries of
past chats) and a document library: upload PDF, Word, PowerPoint, Excel and more in a chat or on
the Library page and ask Nexus about them. Phase 3 lets Nexus build teams of sub-agents for
bigger jobs, with web search, a code sandbox that makes PDF, Word, PowerPoint and Excel files, a
shared skills library, per-run budgets, and your approval before anything risky. Phase 4 makes
the home screen a 3D orb: Nexus in the middle, its live agent teams growing out of it and
pulling back in, and your memories and artifacts inside it. Phase 5 makes it safe to invite
people: rate limits, security headers and cross-site checks, two API replicas behind Traefik,
nightly backups with a restore script, and an Admin page with service health, traffic, usage,
the audit log, backups and a maintenance switch. Since then it has gained structured logs with
request ids, a Monitoring tab with alerts, and a Configuration tab where the owner changes
runtime settings without a redeploy. All of it runs on one machine with Docker Compose.

## Quick start

```sh
./scripts/init-env.sh          # writes .env with random secrets
$EDITOR .env                   # add OPENAI_API_KEY, and Google OIDC or DEV_LOGIN_ENABLED=true
brew install ollama            # local embeddings for memory and document search (free)
ollama pull bge-m3             # then keep Ollama running (the menu bar app, or `ollama serve`)
./scripts/compose.sh up -d --build   # plain `docker compose` until the secrets are encrypted
```

Before others use it, encrypt the secrets with `./scripts/secrets.sh init` and start Nexus with
`./scripts/compose.sh` from then on (see [Secrets](#secrets)).

The first build takes a while: the doc-worker image includes Docling with CPU PyTorch and its
models (a few GB). `DOCLING=false` in `.env` builds a small one instead, without OCR.

| What | Where |
| --- | --- |
| Nexus | http://localhost:8080 |
| Langfuse (traces, cost) | http://localhost:3000 (`LANGFUSE_PORT`), login from `LANGFUSE_ADMIN_EMAIL` / `LANGFUSE_ADMIN_PASSWORD` |
| LiteLLM admin API | http://localhost:4000, `LITELLM_MASTER_KEY` |
| MinIO console | http://localhost:9001 |
| Admin page (health, monitoring, usage, audit log, backups, configuration) | http://localhost:8080/admin |

What each of the containers Compose starts is for, and how they connect, is in
[docs/containers.md](docs/containers.md).

The first person to sign in becomes the **owner**. Give Docker Desktop at least 8 GB of memory;
ClickHouse (for Langfuse) is the heaviest container.

### Signing in

- **Google (OIDC).** Create an OAuth client of type "Web application" in Google Cloud and add
  every callback URL you will use, for example `http://localhost:8080/auth/callback` and
  `https://nexus.servhub.cc/auth/callback`. Put the client id and secret in `OIDC_CLIENT_ID` and
  `OIDC_CLIENT_SECRET`. Any OIDC provider works; set `OIDC_ISSUER`.
  Google sign-in also asks for the Google services in `GOOGLE_SERVICES`; see below.
- **Dev sign-in.** With `DEV_LOGIN_ENABLED=true`, the login page also accepts an email plus
  `DEV_LOGIN_TOKEN`. It follows the same invite rules, but anyone with the token can sign in as
  any invited user, so keep it off on a public hostname.

### Google services (Gmail, Calendar, Contacts, Tasks)

With Google sign-in, the same consent screen also asks for the services in `GOOGLE_SERVICES`
(default `gmail,calendar,contacts,tasks`; `drive` is also available, read-only). Each one has
its own checkbox, so people can untick any of them and still sign in. Nexus keeps a refresh
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
     "Google hasn't verified this app" screen and clicks *Advanced → Go to Nexus*. Refresh
     tokens last until revoked. Google caps an unverified app with sensitive or restricted
     scopes at 100 users, ever.
   - **Testing.** Only people listed as test users (up to 100) can sign in, and Google expires
     their refresh tokens after 7 days, so agents lose Gmail and Calendar access weekly until
     the person signs in again.
   - **Verified.** Needed only to open Nexus to the public: sensitive scopes need Google's
     review, and restricted ones (Gmail, Drive) also a yearly third-party security assessment.
     Not worth it for a private Nexus.

To sign in with name and email only, set `GOOGLE_SERVICES=` (empty).

What Nexus and agents can do with them (only for services the person allowed):

| Service | Nexus in chat | Agents only, approved on a card first |
|---|---|---|
| Gmail | search, read, save drafts, archive / mark read / star / label | send (`gmail_send`) |
| Calendar | list events, add or change own events without guests | send invitations, delete events |
| Contacts | search | |
| Tasks | list, add, complete | |

Nothing can trash mail or mark it as spam. Calendar times without an offset are read in the
calendar's own time zone.

### On your phone

Nexus is an installable PWA. For Google sign-in from a phone, publish it through the Cloudflare
Tunnel at `nexus.servhub.cc` (see [infra/cloudflared](infra/cloudflared/README.md)) and use
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
  as a member. Unused invites can be removed from the People page.
- **Access requests:** someone without an invite who signs in with Google is offered "Request
  access" instead of a dead end, with an optional note. The address is always the one Google
  just verified for that browser (the page never takes a typed one), so nobody can ask on
  someone else's behalf. Owners and admins see requests at the top of the People page and as a
  live badge on People in the sidebar; with SMTP set up they're also emailed (all active owners
  and admins, or `ACCESS_REQUEST_NOTIFY`). Approving picks the role and sends the person an
  email saying they're in; denying is silent, and the same address can ask again after
  `ACCESS_REQUEST_COOLDOWN_DAYS`. Everything is in the audit log (`access.requested`,
  `access.approved`, `access.denied`). `ACCESS_REQUESTS_ENABLED=false` turns it off.
- **Private data:** every chat belongs to one user. Other users, admins included, get a 404.
- **Budgets:** each user gets their own LiteLLM virtual key the first time they chat, capped at
  `DEFAULT_BUDGET_USD` (owner: `OWNER_BUDGET_USD`) per 30 days. Admins change budgets on the
  People page; disabling a user signs them out everywhere and blocks their key. When a budget runs
  out, Nexus says so in the chat instead of failing silently.
- Admin actions, sign-ins and sign-ups are recorded in `audit_log`, shown on the Admin page.

## Models

All model calls go to LiteLLM using four aliases, so a model swap is a change to
[infra/litellm/config.yaml](infra/litellm/config.yaml):

| Alias | Model | Used for |
| --- | --- | --- |
| `nexus-nano` | gpt-5.4-nano | routing, extraction, titles, summaries |
| `nexus-mini` | gpt-5.4-mini | plain chat (`CHAT_MODEL`), most sub-agents |
| `nexus-pro` | gpt-5.4 | planning and designing agent systems |
| `nexus-max` | gpt-5.5 | escalation only |
| `nexus-embed` | bge-m3 in Ollama on the host | memory and document search |

LiteLLM runs in Compose with an exact-match Redis cache and sends every generation to Langfuse.
To use another LiteLLM (for example on a server later), set `LITELLM_BASE_URL` and its
`LITELLM_MASTER_KEY`. Each turn is one Langfuse trace (named `nexus.turn`) grouped into a session
per chat and attributed to the user's id.

## How a message flows

1. The web app posts the message, with the ids of any attached files, to
   `POST /api/chats/{id}/messages`. The API stores it and an empty reply, then queues the turn on
   Redis (arq).
2. An orchestrator worker assembles context: your profile, the memories and past-chat summaries
   closest to the message, and the documents attached to the chat (whole if short, otherwise the
   best-matching excerpts).
3. It runs the Nexus LangGraph graph, calling LiteLLM with the user's own key. Nexus can call
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
| Connections | The people, projects, organisations, places, topics and tools your memories, chats and files are about, and how they relate | With each memory and summary; files and memories you add yourself within ten minutes | On the Memory page |

After each turn `nexus-nano` proposes memories from the exchange. Each is compared with its
nearest existing memories and becomes ADD, UPDATE or NOOP (the Mem0 pattern); repeats raise a
memory's confidence, contradictions update it, and "forget that" deletes it. Pinned memories are
never changed by Nexus. Each night, memories no chat has confirmed in 60 days lose confidence
(and go below 0.2), near duplicates are merged, and stale profiles are rebuilt; set the time with
`MEMORY_NIGHTLY_HOUR_UTC` (default 21:30 UTC, 03:00 IST).

On the **Memory** page you see the profile and every memory with where it came from and its
history, and can add, edit, pin and delete them. Deleting is real deletion; you can also turn
learning off or forget everything.

**Connections** (the knowledge graph, `nexus_common/graph.py`) sit in three tables beside the
memories: `entities` (one per person, project, organisation, place, topic or tool, plus one for
you), `relations` between them ("Nexus uses LangGraph", with `valid_from` and `valid_to`, so a
fact that stops being true is kept with the date it ended) and `mentions`, which tie an entity
to each memory, past-chat summary, file and chat about it. The same `nexus-nano` call that
proposes memories proposes them, so chats cost nothing extra; names resolve to an existing
entity by name or alias, then by meaning within the same type. Memories you add or edit
yourself, files, and anything from before are linked by a background job every ten minutes
(one call per twenty items). Deleting a memory, chat or file deletes its mentions, and an
entity nothing mentions any more goes with its relations. The Memory page's Connections view
lists them by type with their facts; removing one keeps its memories. `GRAPH_ENABLED=false`
(Admin › Configuration) stops adding to it.

Search is hybrid everywhere: pgvector cosine similarity and Postgres full-text search fused by
reciprocal rank, so it still works by keyword if the embedder is down.

## Documents

Upload in a chat (paperclip, drag and drop, or paste) or on the **Library** page. Files go to
MinIO through the API (up to `MAX_UPLOAD_MB`), and the doc-worker parses them: Docling for PDFs
and images, MarkItDown for Word, PowerPoint, Excel, CSV and HTML. The text is chunked by heading
and sheet, embedded and indexed; see [services/doc-worker](services/doc-worker/README.md). The
Library page lists, searches inside, previews the extracted text, renames, downloads and deletes
files.

Embeddings go through LiteLLM under the `nexus-embed` alias, which points at bge-m3 in Ollama
on the Mac (Docker on macOS can't use the GPU, so Ollama runs natively). Vectors are cached by
text hash, and every stored vector records its model, so swapping the embedder (for example to
OpenAI's `text-embedding-3-small` with `dimensions: 1024`, see
[infra/litellm/config.yaml](infra/litellm/config.yaml)) re-embeds everything in the background.

## Agent systems

For a big ask ("research EV battery suppliers and give me a report and a spreadsheet"), Nexus
calls `build_agent_system`. Before Nexus answers a message, the cheapest tier (`ROUTE_MODEL`,
nexus-nano) decides whether it is work for a team; when it is, Nexus's first move is to build
one. Nexus is an orchestrator: any real piece of work (research, writing, analysis, code,
documents) goes to a team, as small as one agent, and Nexus itself only does direct one-step
things (chat, a quick fact, one look at mail or calendar, adding a task) and manages teams. The architect model (`DESIGN_MODEL`, nexus-pro) writes an
AgentSystemSpec: a small team, a topology, each agent's role, instructions, model tier, tools and
skills. The spec is validated against what you may use, compiled to a LangGraph graph and run on
the orchestrator queue; the team's card in the chat shows each agent live, and the result and
files are posted in the chat when it finishes. One-off teams are archived after their run, and
the orb only shows teams with a run in progress; "keep it" saves one as a template Nexus
reuses for similar asks (Nexus only keeps a team up front when you say so).

- **Topologies:** single agent, pipeline, lead and workers (`supervisor_workers`), parallel
  map-reduce, and worker with critic.
- **Model tiers:** the spec picks nano, mini or pro per agent. nexus-max is only for a team's
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
network (no internet, no access to Nexus's databases or your LAN), all capabilities dropped,
memory, CPU and process limits, and per-job CPU, memory, file-size and time limits. The task's
files go in `in/`, the agent's skills in `skills/`, and whatever it writes to `out/` is saved to
your Library as a generated file (saving the same name again makes a new version). It has
pypdf, pdfplumber, ReportLab, WeasyPrint, python-docx, python-pptx, openpyxl, pandas and
matplotlib.

### The workbench

Dev teams work in the `workbench` service instead: one persistent git folder per project, kept
between steps and runs, with Node 22, Python 3.13 and uv, git and headless Chromium for
Playwright tests. Agents use it through `ws_tree`, `ws_read`, `ws_write`, `ws_edit`, `ws_run`
(needs the exec capability, like `run_code`), `ws_diff` and `ws_commit`; a run works in a project
named after itself. Its only way out is the `egress-proxy` allowlist (npm, PyPI, GitHub), each
command is limited to its own project folder, and it has the sandbox's hardening with more room
for builds. Details and the network check are in [docs/containers.md](docs/containers.md).

### Software teams

Asking Nexus to build, fix or extend software ("make me a 2048 game") starts its fixed
software team, `dev_project`, in a workbench project:

| Stage | Agent (tier) | Does |
| --- | --- | --- |
| 1. Brief | Researcher (mini) | Looks at similar apps, picks a house stack (static with Vite, Vite + FastAPI, or Vite + Express), writes `BRIEF.md` |
| 2. Plan | Architect (pro) | Writes `PLAN.md`: features, file layout, acceptance criteria, test plan; posted in the chat |
| 3. Build | Frontend and backend builders (mini) | Build side by side in their own folders and commit as they go; no backend for a static app |
| 4. Test | Tester (mini) | Unit tests and a headless Playwright smoke test; writes `TEST_REPORT.md` |
| 5. Review | Reviewer (pro) | Approves, or sends numbered fixes back to stage 3, at most three rounds |
| 6. Release | (code) | Commits what is left, saves the project to the Library, pushes it to its own private GitHub repo and, once approved and green, runs it as an app |

The plan is posted and the team carries on; say "plan first" or "wait for me" and it stops
after the plan until you say go. The reply gives what was built, the stack, the test count and
the review rounds. A follow-up such as "add a high score table" continues the same project.
Each agent may take 40 steps, and a run's default budget is `DEV_RUN_BUDGET_USD` ($3), still
capped by `MAX_RUN_BUDGET_USD`; a lower budget Nexus picks itself, without the user naming a sum,
is ignored, so the team always has room for its three review rounds and the deploy. It needs the Run code (exec) permission.

Projects show in the Library as one card each (stack, latest commit, repo link), never as loose
files. With the Nexus GitHub App set up ([docs/github-app.md](docs/github-app.md)), each gets a
private repo in `GITHUB_APPS_ORG`: the run's work goes to a `nexus/<run id>` branch (a follow-up keeps using the branch while it
isn't merged), and `main`
moves to it only when the reviewer approved and every test passed. "Move it to my GitHub"
transfers a repo to your own account, after you approve it on a card (owner only).

When the reviewer approved and every test passed, the `deployer` runs the app as a container and
the reply gives its link, `https://<name>.servhub.cc` (`2048`, or `2048-priya` for other people),
with a screenshot. Apps are private: only their owner and Nexus's owner can open them, after
signing in to Nexus. "Make it public" (in chat or on the **Apps** page) shares the link with
anyone. The Apps page lists them with stop, restart, redeploy, public or private and delete;
members can run three at once. How addresses and sign-in work, and moving them to another
server: [docs/app-urls.md](docs/app-urls.md). How apps are built and boxed in:
[docs/containers.md](docs/containers.md).

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
skills Nexus drafts after a long successful run, stay off until reviewed; skills whose runs
keep failing are flagged; it filters by shelf, and admins can share one of their own skills
(say, one Nexus learned) with everyone. Its Instructions tab holds the layers every agent gets,
in order: the shipped safety rules and house conventions
([library/instructions](library/instructions)), global and per-role instructions (admins), then
your own house style. Nexus's own chat follows the conventions, global instructions and yours.

## The orb

The home screen is a React Three Fiber scene ([apps/web/src/orb](apps/web/src/orb)) with the chat
box docked underneath; sending a message opens the chat.

- **The orb** is a shader sphere whose colour, churn and pulse follow what Nexus is doing: blue
  and calm when idle, violet while thinking, bright and pulsing while answering.
- **Around it**, every team that isn't archived is a cluster tied to the orb by an edge: the
  lead hangs from the orb, workers from the lead (a pipeline is a chain). Agents are coloured
  by their live status (working, using a tool, waiting for your approval, done), and a spark
  runs down the edge to a busy agent. A new team grows out of the orb, agent by agent; when a
  team is deleted, or archived after its run, its agents pull back in and fade. Agents move on
  springs, so nothing jumps.
- **Inside it**, memories are particles clustered by kind (preferences, facts, goals,
  instructions), pinned ones brighter; artifacts (files Nexus or a team made) are small
  crystals. A new one grows in with a flash.
- **Tap** an agent, memory or crystal for a card that opens the team, the memory on the Memory
  page, or the file. Drag to turn, pinch or scroll to zoom.
- **Phones** get fewer particles, no bloom, a lower pixel ratio and a 30 fps cap, and teams sit
  on a tighter band on portrait screens. **List** (top right) shows the same teams, memories and
  artifacts as plain lists; it is also what shows when the device has no WebGL. With
  reduced motion turned on in the OS, nothing spins or pulses and objects snap into place.

Everything is driven by the existing WebSocket events (`assistant.state`, `system.*`, `run.updated`,
`agent.status`, `approval.*`, `memory.updated`, `artifact.created`, `file.*`), with a fresh fetch
on open and after a reconnect. three.js loads only on the home screen, as its own chunk.

## Running it for other people

### Admin page

Only owners get **Admin** in the menu (admins get **People** but not this page). Its sections
are grouped down the side (one scrolling row on a phone), with badges for active alerts and
improvements waiting; the open one is in the address (`/admin?tab=models`). A person filter
narrows Status, Models, Usage and the Audit log to one person (tap a name) or several (tick
them); service health, queues, API traffic and backups always cover all of Nexus.

- **Status:** whether Postgres, Valkey, LiteLLM, MinIO and both workers are up (workers report a
  heartbeat every 30 seconds), jobs waiting in each queue, the last 24 hours (turns, tokens,
  agent runs and their cost, failures), model spend this period against the sum of budgets,
  when the last backup finished, API requests per minute for the last hour with errors and
  rate-limited calls, and the latest failed turns, runs and file parses with their error.
  The owner sees counts, costs and error messages, never chat content. Per-call traces stay in
  Langfuse (`LANGFUSE_URL`).
- **Alerts:** what needs you now, every check with its current threshold, and the last 50
  raised or resolved. Every `ALERT_INTERVAL_S` (60) one API replica checks the services above,
  the worker heartbeats, the last backup, API errors in the last 5 minutes
  (`ALERT_API_ERRORS_5M`, 5), failed jobs in the last 15 (`ALERT_JOB_FAILURES_15M`, 3), failed
  model calls in the last 15 (`ALERT_LLM_FAILURES_15M`, 5), model spend since midnight UTC
  (`ALERT_DAILY_SPEND_USD`, $10) and anyone who has spent most of their budget
  (`ALERT_BUDGET_PERCENT`, 90%). A problem seen in two checks in a row is raised; with SMTP set
  up every owner gets an email, and another when it clears (`ALERT_EMAIL=false` keeps them on
  the page only). If the API itself is down nothing can send, so a dead Mac or Docker stays
  silent.
- **Models:** every model call over 24 hours, 7, 30 or 90 days or chosen dates, from LiteLLM's
  spend logs (its own `litellm` database next to Nexus's; `LITELLM_DATABASE_URL` if elsewhere):
  cost, calls and failures, tokens, latency (typical, slowest 5%, first token), LiteLLM's
  response-cache hit rate and what it saved, and how much input OpenAI's prompt cache billed at
  the cached price. Per hour or day, then by model, by what the call was for (chat replies,
  routing, team design, agents, memory, skill drafting, self-review, embeddings: every call
  carries a tag saying so) and by person, with the latest failed calls. Filter by person, model
  and purpose. Valkey's own hit rate, memory and evictions sit underneath. Prompts and replies
  are never read.
- **Improvements:** what Nexus proposes to change about itself; see
  [Self-review](#self-review).
- **Recall:** how well memory finds the right thing, on your own memories. The memory model
  writes one question per memory and past chat (once; later checks only ask about new ones),
  and **Run check** looks each up exactly as a chat turn does, scoring how often the right one
  comes back and how high, side by side for each way of recalling, with the questions it
  missed. Run one before and after changing how memory works.
- **Monitoring:** over the last hour, 6 hours or 24 hours, every API route and every background
  job with its calls, failures, the time half of them beat and the time 95% beat (and, for
  jobs, how long they waited in the queue), and the latest 200 errors logged by the API, the
  workers or someone's browser, each with its request, user, chat, job and trace ids and its
  stack. Figures are kept a day in Valkey.
- **Configuration:** about seventy runtime settings in eleven groups (logging, models, budgets,
  agent teams, memory and context, sign-in and access, rate limits, alerts and backups,
  self-review, email, apps),
  each with what it does, its `.env` name and its `.env` value. See
  [Changing settings without a redeploy](#changing-settings-without-a-redeploy).
- **Maintenance mode:** while it is on, everyone sees your message in a banner and can read
  their chats, but only admins can start turns and agent runs. Turn it on before restarting or
  upgrading Nexus.
- **Usage:** turns, tokens, agent runs and their cost, and files per person and per day, over 7,
  30 or 90 days.
- **Audit log:** sign-ins (with IP), invites, access requests and their decisions, role, budget
  and grant changes, approvals, sign-outs, backups and maintenance, filterable by kind.
- **Backups:** the list, and **Back up now**.

On the People page an admin can also **sign someone out** on every device. Everyone can see and
sign out their own signed-in devices on the Connections page.

### Self-review

Every `IMPROVE_EVERY_DAYS` (7; 0 turns the schedule off) the orchestrator looks at how Nexus has
been used since the last review, and **Review now** on the Improvements tab starts one at once.
It gathers counts only: chat turns and agent runs and how many failed; each tool's calls,
failures and time; the roles, tools and shapes of the teams built; which shared skills were
used; model cost by purpose; and failure messages with anything that looks like data (numbers,
ids, quoted text, addresses) taken out. With `IMPROVE_READ_REQUESTS` on, the topic model
(`IMPROVE_TOPIC_MODEL`, nano) also reads each request once, with the start of its reply, and
names its topic in a few generic words and what the assistant lacked if it couldn't help; only
the counts are kept, and only topics asked `IMPROVE_MIN_COUNT` (3) times or more. People who
turned memory off in Settings are left out. None of it holds a name, an email, a user id or chat
text, and the Improvements tab shows exactly what the review saw.

One call to `IMPROVE_MODEL` (mini, a cent or two) then drafts up to `IMPROVE_MAX_PROPOSALS` (5)
proposals from it, given what Nexus can already do, how it is built (the architecture brief in
`library/core/nexus-system/SKILL.md`, which [docs/INDEX.md](docs/INDEX.md) points to, so code
proposals name real files and the docs to update) and every earlier proposal with your reasons
for denials, so it doesn't repeat itself. Each is checked before it is kept: a skill must parse,
an instruction must name a real role, a setting must be a valid value (and never sign-up,
dev login or where code goes). You get an email when SMTP is set up. Nothing changes until you
approve:

| Kind | On approval | Undo |
| --- | --- | --- |
| Skill | a shared skill, on for everyone | turns it off |
| Instruction | added to the global or a role's instructions | puts the text back, unless edited since |
| Setting | changed as on the Configuration tab | puts the old value back |
| Tool, code change | a draft pull request on `IMPROVE_REPO` with the spec in `docs/proposals/`, opened by the GitHub App | close the pull request |

Implement a code proposal on its branch (by hand, with a Claude Code session or a dev team),
then mark it ready and merge as usual; nothing merges by itself. Without `IMPROVE_REPO` (set it
to `SS12dev/nexus-system` on the Configuration tab) an approved code proposal stays a spec on
the page. The App needs installing on that repository too; see
[docs/github-app.md](docs/github-app.md#pull-requests-on-nexus-itself).

### Changing settings without a redeploy

`.env` (through [compose.yaml](compose.yaml)) gives every setting its value at start-up. On the
Admin page's **Configuration** tab the owner can override the plain ones: the log format and
level, which model tier chats, routes, designs teams and writes memories, budgets, agent-team
limits, how much memory and document context each turn gets, sign-up and access requests,
rate limits, alert thresholds, the SMTP server (not its password), web search and app limits.
Each comes with a description, its `.env` name and its `.env` value, and **Reset** goes back
to that.

A save is stored in the `app_settings` table (key `config`), applied at once by the API replica
that took it and announced through Valkey; every other API replica, orchestrator and
doc-worker checks every 5 seconds (and re-reads the table every minute regardless) and applies
it, so the next request, turn or job uses it. Code always reads settings when it needs them,
which is what makes this work ([packages/common/src/nexus_common/config.py](packages/common/src/nexus_common/config.py)).

A few settings are read only when a service starts: job timeouts and the nightly memory time
(orchestrator), the Google services asked for at sign-in, the alert interval (API) and the
deploy time limit (both). They're marked *after restart*; once saved, the tab lists the ones
waiting and the command that applies them, such as `./scripts/compose.sh restart api`. A
restarted service starts with the stored values, so nothing is lost on a redeploy. Each save
is in the audit log as `config.changed` with every old and new value.

Never on this tab: secrets (API keys, passwords, tokens, the Google client secret), which stay
in `secrets/nexus.sops.env` ([Secrets](#secrets)), and addresses and wiring (database, Valkey and
service URLs, domains, ports, replicas, `DEV_LOGIN_*`), which need `.env` and a redeploy. The
API refuses any name not on its list, so a secret can't be set through it.

### Logs and request ids

Every line from the API, orchestrator and doc-worker carries whatever is known at the time:
`request_id`, `user_id`, `chat_id`, `run_id`, `job`, `job_id` and `trace_id`, the turn's or run's
Langfuse trace. With `LOG_FORMAT=text` they go as `key=value` at the end of the line; with
`json` each line is one object (`ts`, `level`, `service`, `logger`, `msg`, the ids and any
structured fields) for a log collector. Every API response carries `X-Request-ID` (kept from
the caller when it sends a sane one); the jobs a request queues carry the same id, and so does
each model call's metadata in Langfuse. An unexpected error answers
`{"detail": ..., "request_id": ...}`, so one id finds every line it caused:

```sh
./scripts/compose.sh logs api orchestrator doc-worker | grep 4f1c2a9e0b7d4e21a8c3f06e9d5b7a12
./scripts/compose.sh logs orchestrator | grep ' ERROR '
```

The API writes one access line per request (`nexus.access`, with the route, status and time;
health checks left out), and each worker writes `job done` or `job failed` per job with its run
time and how long it waited in the queue. 5xx responses and requests slower than 5 s are
logged as `http.error` / `http.slow`, refusals (rate limit, cross-site) as `http.refused`, and
access requests as `access.*` events. Docker keeps 5 files of 10 MB per container, so logs
can't fill the disk. The orchestrator and doc-worker have Docker health checks on their arq
heartbeat, so `docker compose ps` shows a stuck worker as unhealthy.

### Limits and security

| Limit | Default | Setting |
| --- | --- | --- |
| API requests per IP | 600 a minute | `RATE_LIMIT_API_PER_MINUTE` |
| Sign-in attempts and access requests per IP | 20 a minute | `RATE_LIMIT_AUTH_PER_MINUTE` |
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
- **Headers:** the web app sends a Content Security Policy (scripts only from Nexus itself, no
  framing), `nosniff`, a strict referrer policy and, over https, HSTS. API responses are
  `Cache-Control: no-store`. Uploaded files download as attachments with `CSP: sandbox` (from
  phase 2).
- **Exposure:** only Traefik's port (`NEXUS_HTTP_PORT`) listens beyond the Mac's loopback;
  Postgres, LiteLLM, Langfuse and the MinIO console are bound to 127.0.0.1, and the API docs
  (`/docs`) aren't routed. Keep `DEV_LOGIN_ENABLED=false` and `SESSION_COOKIE_SECURE=true`
  on the public hostname.
- **Cloudflare Access** (optional) can sit in front of `nexus.servhub.cc` for an extra
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
into `secrets/nexus.sops.env`, encrypted with [SOPS](https://getsops.io) and an
[age](https://age-encryption.org) key, and `.env` keeps only settings:

```sh
brew install sops age
./scripts/secrets.sh init       # makes ~/.config/nexus/age.key, moves the secrets, adds
                                # TOKEN_ENCRYPTION_KEYS; checks the file decrypts first
cat ~/.config/nexus/age.key    # save this in your password manager
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

On a new machine: clone the repo, put the age key at `~/.config/nexus/age.key`, copy
`nexus.sops.env` from a backup into `secrets/`, recreate `.env` from `.env.example` (settings
only), then restore the backup.

When Nexus moves to a server, the same file can also be opened by a cloud key (SOPS supports
GCP KMS, AWS KMS and others as extra recipients), so the server needs no copy of the age key.

### Backups

The `backup` service backs up every night after `BACKUP_HOUR_UTC` (default 21, so 02:30 IST),
on its first start, whenever none has run for 26 hours (the Mac was asleep), and on **Back up
now**. Each backup is a folder in `BACKUP_DIR` (default `./backups`, gitignored) named by its
UTC time, with a `pg_dump` of the nexus, litellm and langfuse databases, a tarball of the MinIO
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

It checks the checksums, asks you to type `restore`, stops Nexus, recreates each database from
its dump, replaces the MinIO data, and starts everything again (the tunnel too, if it was up).

## Repository layout

```
apps/web/               React + Vite + Tailwind PWA (orb home, chat, Library, Memory, Agents,
                        Skills, People); the 3D scene is in src/orb
services/api/           FastAPI: auth, chats, files, memories, skills, agents, approvals, admin,
                        WebSocket; Alembic migrations
services/orchestrator/  arq worker: the Nexus graph, the Tool Registry, agent systems (design,
                        compile, run, approvals), the memory and learned-skill jobs
services/sandbox/       FastAPI: runs agent code with no network and resource limits
services/workbench/     FastAPI: dev teams' persistent project folders, commands and git
services/deployer/      FastAPI: builds and runs finished projects as apps, and the Docker gate
services/doc-worker/    arq worker: parses, chunks and embeds uploads
infra/backup/           the backup service (pg_dump and MinIO, nightly and on request)
infra/egress-proxy/     the workbench's allowlist proxy (npm, PyPI, GitHub)
packages/common/        settings, models, events, storage, embeddings, hybrid search, the agent
                        system spec, tool catalogue and skills helpers
library/                core skills and the shipped instruction layers
infra/litellm, postgres, cloudflared   model aliases, database init, tunnel notes
secrets/                the encrypted secrets file (gitignored; README: Secrets)
docs/                   INDEX.md (start here), containers, app URLs, the GitHub App, proposals
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
`DATABASE_URL=postgresql+asyncpg://nexus:<password>@localhost:5432/nexus`,
`REDIS_URL=redis://localhost:6379/0` (publish Redis's port first),
`LITELLM_BASE_URL=http://localhost:4000` and `S3_ENDPOINT_URL=http://localhost:9000` with
`S3_SECRET_KEY` (publish MinIO's port 9000 first), then `uv run uvicorn nexus_api.app:app
--reload` in `services/api`, `uv run arq nexus_orchestrator.worker.WorkerSettings` and
`uv run arq nexus_doc_worker.worker.WorkerSettings`. Without Docling installed the doc-worker
falls back to MarkItDown for PDFs.

Adding a setting to the Configuration tab: add an `Option` to `OPTIONS` in
`packages/common/src/nexus_common/config.py` (group, label, description, type, limits, and
`restart` if a service reads it only at start-up). Code must read it from the live `Settings`
object when it's used, not copy it at start-up.

Schema changes: edit `packages/common/src/nexus_common/models.py`, then
`uv run alembic revision --autogenerate -m "..."` in `services/api`.

## Roadmap

1. Foundation · 2. Memory and documents · 3. Agent systems, tools and the skills library ·
4. The 3D orb · 5. **Production hardening** (this: logs, monitoring, alerts, runtime
configuration) · 6. Move to a server, voice
