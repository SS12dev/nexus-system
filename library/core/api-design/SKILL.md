---
name: api-design
description: Design and build HTTP APIs that are consistent, validated and safe - resource paths, methods and status codes, request and response shapes, one error format, validation, pagination, auth and idempotency. Use when planning or implementing a back end, adding routes, or reviewing an API.
metadata:
  category: engineering
---

# API design

The contract is written before the code (see `core/technical-design`), and both sides are
built and tested against it.

## Shape

- Resources are plural nouns: `/api/notes`, `/api/notes/{id}`. Actions that aren't plain
  create, read, update or delete get a sub-resource: `POST /api/games/{id}/moves`.
- Methods mean what HTTP says: GET never changes anything; PUT replaces; PATCH changes some
  fields; DELETE removes and is safe to repeat.
- One naming style for JSON fields throughout (`snake_case` for a Python back end,
  `camelCase` for Node), ISO 8601 UTC for times, strings for ids, integers in minor units
  for money.
- Version from the start only if outside clients will depend on it.

## Status codes

| Code | Use |
| --- | --- |
| 200 | Success with a body |
| 201 | Created; return the new resource |
| 204 | Success with no body (delete) |
| 400 | Malformed request |
| 401 | Not signed in |
| 403 | Signed in but not allowed |
| 404 | No such resource, or not this user's |
| 409 | Conflict: duplicate, stale version |
| 422 | Well-formed but invalid fields |
| 429 | Too many requests |
| 500 | A bug; never deliberately |

## One error format

Every error has the same body, so the client handles errors in one place:

```json
{"error": {"code": "validation_failed", "message": "Title is required.",
           "fields": {"title": "Required"}}}
```

`message` is fit to show a person. Never return stack traces, SQL or internal paths. Add a
handler for unexpected exceptions that logs the detail and returns a plain 500 in this shape.

## Validation

- Validate every input at the edge with a schema (Pydantic models in FastAPI, zod or similar
  in Node): types, required fields, lengths, ranges, formats, allowed values.
- Reject unknown fields rather than ignoring them, when that is cheap.
- Validate path and query parameters too. Cap page sizes and string lengths.
- Never trust ids from the client to imply ownership: load the row and check the owner.

## Lists

Paginate anything that can grow: `?limit=` (capped) with a cursor or `?offset=`. Return
`items` plus what the client needs to get the next page. Define the default order and make
it stable (a tie-breaker on id).

## Auth and safety

- Decide who may call each route and write it in the contract. Check on the server, every
  time; hiding a button isn't access control.
- Sessions in HttpOnly, Secure, SameSite cookies; protect state-changing routes against
  cross-site requests. Hash passwords with argon2 or bcrypt; never store or log them.
- Secrets come from environment variables, listed in `.env.example` without values.
- Parameterised queries only. Limit request body size. Rate-limit sign-in and anything costly.
- CORS: same origin by default; if another origin is needed, name it exactly.

## Being safe to retry

Clients retry. Make creates idempotent where a double submit would hurt (an idempotency key,
or a uniqueness rule that turns the duplicate into a 409). Updates that could overwrite a
newer change carry a version and return 409 when stale.

## Operability

- `GET /api/health` returns 200 quickly and checks its dependencies cheaply.
- Log one line per request: method, path, status, duration, a request id; no bodies, no
  secrets.
- Time out calls to other services and the database; decide what the user sees when they fail.

## Tests

Test the contract through the framework's test client against a temporary database: each
route's success case, each documented error, validation limits, another user's data (must
be 404 or 403), and the list's paging. These are the tests the front end relies on.
