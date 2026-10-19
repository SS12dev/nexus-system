# Jarvis

Jarvis is a supervisor agent you chat with. It answers simple asks itself and, from phase 3 on,
designs, runs and tears down teams of sub-agents with their own tools and model tiers. The
design lives in the architecture doc, "Jarvis: Architecture & Research".

This repository is at **phase 1, Foundation**: sign-in with invites and roles, private chats per
user, Jarvis streaming replies over a WebSocket, every model call going through LiteLLM with a
per-user budget, and every call traced with its cost in Langfuse. All of it runs on one machine
with Docker Compose.

## Quick start

```sh
./scripts/init-env.sh          # writes .env with random secrets
$EDITOR .env                   # add OPENAI_API_KEY, and Google OIDC or DEV_LOGIN_ENABLED=true
docker compose up -d --build
```

| What | Where |
| --- | --- |
| Jarvis | http://localhost:8080 |
| Langfuse (traces, cost) | http://localhost:3000 (`LANGFUSE_PORT`), login from `LANGFUSE_ADMIN_EMAIL` / `LANGFUSE_ADMIN_PASSWORD` |
| LiteLLM admin API | http://localhost:4000, `LITELLM_MASTER_KEY` |
| MinIO console | http://localhost:9001 |

The first person to sign in becomes the **owner**. Give Docker Desktop at least 8 GB of memory;
ClickHouse (for Langfuse) is the heaviest container.

### Signing in

- **Google (OIDC).** Create an OAuth client of type "Web application" in Google Cloud and add
  every callback URL you will use, for example `http://localhost:8080/auth/callback` and
  `https://jarvis.servhub.cc/auth/callback`. Put the client id and secret in `OIDC_CLIENT_ID` and
  `OIDC_CLIENT_SECRET`. Any OIDC provider works; set `OIDC_ISSUER`.
- **Dev sign-in.** With `DEV_LOGIN_ENABLED=true`, the login page also accepts an email plus
  `DEV_LOGIN_TOKEN`. It follows the same invite rules, but anyone with the token can sign in as
  any invited user, so keep it off on a public hostname.

### On your phone

Jarvis is an installable PWA. For Google sign-in from a phone, publish it through the Cloudflare
Tunnel at `jarvis.servhub.cc` (see [infra/cloudflared](infra/cloudflared/README.md)) and use
"Add to Home Screen". On your home network you can also open `http://<mac-ip>:8080` with dev
sign-in, since Google does not accept private IP addresses as callbacks.

## People, roles and budgets

- **Roles:** owner > admin > member > viewer, checked on every API route. Viewers can read their
  own chats but not send messages. Admins manage members and viewers; only owners grant admin or
  owner, and the last active owner can't be demoted or disabled.
- **Invites:** with `SIGNUP_POLICY=invite_only` (the default), an admin invites an email on the
  People page and that person joins the first time they sign in with it. `open` lets anyone join
  as a member.
- **Private data:** every chat belongs to one user. Other users, admins included, get a 404.
- **Budgets:** each user gets their own LiteLLM virtual key the first time they chat, capped at
  `DEFAULT_BUDGET_USD` (owner: `OWNER_BUDGET_USD`) per 30 days. Admins change budgets on the
  People page; disabling a user signs them out everywhere and blocks their key. When a budget runs
  out, Jarvis says so in the chat instead of failing silently.
- Admin actions and sign-ups are recorded in `audit_log`.

## Models

All model calls go to LiteLLM using four aliases, so a model swap is a change to
[infra/litellm/config.yaml](infra/litellm/config.yaml):

| Alias | Model | Used for |
| --- | --- | --- |
| `jarvis-nano` | gpt-5.4-nano | routing, extraction, titles, summaries |
| `jarvis-mini` | gpt-5.4-mini | plain chat (phase 1 default), most sub-agents |
| `jarvis-pro` | gpt-5.4 | planning and designing agent systems |
| `jarvis-max` | gpt-5.5 | escalation only |

LiteLLM runs in Compose with an exact-match Redis cache and sends every generation to Langfuse.
To use another LiteLLM (for example on a server later), set `LITELLM_BASE_URL` and its
`LITELLM_MASTER_KEY`. Each turn is one Langfuse trace (named `jarvis.turn`) grouped into a session
per chat and attributed to the user's id.

## How a message flows

1. The web app posts the message to `POST /api/chats/{id}/messages`. The API stores it and an
   empty reply, then queues the turn on Redis (arq).
2. An orchestrator worker runs the Jarvis LangGraph graph with the chat history, calling LiteLLM
   with the user's own key.
3. Tokens are published on the user's Redis channel as `message.delta` events. Every API replica
   holding a WebSocket for that user forwards them, so replicas can scale behind Traefik.
4. The finished reply, with token counts, is stored and sent as `message.completed`.

## Repository layout

```
apps/web/               React + Vite + Tailwind PWA (login, chat, People)
services/api/           FastAPI: auth, chats, admin, WebSocket; Alembic migrations
services/orchestrator/  arq worker running the LangGraph Jarvis graph
services/doc-worker/    phase 2 (documents)
packages/common/        settings, SQLAlchemy models, events, LiteLLM admin client
infra/                  LiteLLM config, Postgres init, Cloudflare Tunnel notes
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
`REDIS_URL=redis://localhost:6379/0` (publish Redis's port first) and
`LITELLM_BASE_URL=http://localhost:4000`, then `uv run uvicorn jarvis_api.app:app --reload` in
`services/api` and `uv run arq jarvis_orchestrator.worker.WorkerSettings`.

Schema changes: edit `packages/common/src/jarvis_common/models.py`, then
`uv run alembic revision --autogenerate -m "..."` in `services/api`.

## Roadmap

1. **Foundation** (this) · 2. Memory and documents · 3. Agent systems, tools and the skills
library · 4. The 3D orb · 5. Production hardening (Cloudflare Access, admin console, dashboards,
backups) · 6. Move to a server, voice
