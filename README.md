# Jarvis

Jarvis is a supervisor agent you chat with. It answers simple asks itself and, from phase 3 on,
designs, runs and tears down teams of sub-agents with their own tools and model tiers. The
design lives in the architecture doc, "Jarvis: Architecture & Research".

This repository is at **phase 2, Memory and documents**. Phase 1 gave sign-in with invites and
roles, private chats per user, Jarvis streaming replies over a WebSocket, every model call going
through LiteLLM with a per-user budget, and every call traced with its cost in Langfuse. Phase 2
adds long-term memory (a profile of you, facts and preferences, summaries of past chats) and a
document library: upload PDF, Word, PowerPoint, Excel and more in a chat or on the Library page
and ask Jarvis about them. All of it runs on one machine with Docker Compose.

## Quick start

```sh
./scripts/init-env.sh          # writes .env with random secrets
$EDITOR .env                   # add OPENAI_API_KEY, and Google OIDC or DEV_LOGIN_ENABLED=true
brew install ollama            # local embeddings for memory and document search (free)
ollama pull bge-m3             # then keep Ollama running (the menu bar app, or `ollama serve`)
docker compose up -d --build
```

The first build takes a while: the doc-worker image includes Docling with CPU PyTorch and its
models (a few GB). `DOCLING=false` in `.env` builds a small one instead, without OCR.

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

## Repository layout

```
apps/web/               React + Vite + Tailwind PWA (login, chat, Library, Memory, People)
services/api/           FastAPI: auth, chats, files, memories, admin, WebSocket; Alembic migrations
services/orchestrator/  arq worker: the LangGraph Jarvis graph, its tools and the memory jobs
services/doc-worker/    arq worker: parses, chunks and embeds uploads
packages/common/        settings, models, events, storage, embeddings, hybrid search
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
`REDIS_URL=redis://localhost:6379/0` (publish Redis's port first),
`LITELLM_BASE_URL=http://localhost:4000` and `S3_ENDPOINT_URL=http://localhost:9000` with
`S3_SECRET_KEY` (publish MinIO's port 9000 first), then `uv run uvicorn jarvis_api.app:app
--reload` in `services/api`, `uv run arq jarvis_orchestrator.worker.WorkerSettings` and
`uv run arq jarvis_doc_worker.worker.WorkerSettings`. Without Docling installed the doc-worker
falls back to MarkItDown for PDFs.

Schema changes: edit `packages/common/src/jarvis_common/models.py`, then
`uv run alembic revision --autogenerate -m "..."` in `services/api`.

## Roadmap

1. Foundation · 2. **Memory and documents** (this) · 3. Agent systems, tools and the skills
library · 4. The 3D orb · 5. Production hardening (Cloudflare Access, admin console, dashboards,
backups) · 6. Move to a server, voice
