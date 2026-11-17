---
name: security-review
description: Review an application for the security problems that matter - injection, broken access control, secrets, unsafe rendering, weak sessions, vulnerable dependencies and unsafe defaults - with a concrete failing scenario and a fix for each. Use when building or reviewing anything with a server, stored data, sign-in or user-generated content.
metadata:
  category: engineering
---

# Security review

Work through the list against the actual code. A finding needs a place (`file:line`), a
scenario someone could carry out, and the fix. "Consider adding validation" is not a finding.

## 1. Who can do what

The most common real flaw is a route that trusts the client.

- For every route that reads or changes data: is the caller identified, and is the row
  checked to belong to them? Try another user's id in the URL and the body.
- Admin-only actions are checked on the server, not only hidden in the UI.
- List endpoints filter by owner; search and export too.
- Direct file access: can a path or key be guessed or walked (`../`)?

## 2. Input reaching an interpreter

- SQL: parameterised queries or an ORM; no string-built SQL, including `ORDER BY` and table
  names taken from input.
- Shell: no user input in commands; if unavoidable, an argument list, never a shell string.
- HTML: frameworks escape by default, so look for the escape hatches
  (`dangerouslySetInnerHTML`, `innerHTML`, `v-html`, Markdown rendered without sanitising)
  and URLs in `href`/`src` taken from input (`javascript:`).
- Server-side fetches of user-supplied URLs: block private and loopback addresses, limit
  redirects and size.
- Deserialisation: no `pickle`, `yaml.load` or `eval` on untrusted data.
- File uploads: check type and size, store outside the web root under a generated name.

## 3. Secrets and configuration

- No keys, passwords or tokens in the repository, in client-side code or in logs. Search
  the code and the git history of this run.
- Anything the browser receives is public, including "hidden" environment variables bundled
  by the build (`VITE_*`).
- `.env.example` lists names without values; `.env` is git-ignored.
- Debug modes, verbose errors and open CORS are off in the production build.

## 4. Sessions and sign-in

- Passwords hashed with argon2 or bcrypt. Sign-in, sign-up and reset are rate-limited and
  don't reveal whether an account exists.
- Session cookies: `HttpOnly`, `Secure`, `SameSite=Lax` or stricter. Tokens are never kept in
  `localStorage` when a cookie will do.
- State-changing requests are protected against cross-site requests.
- Sign-out invalidates the session on the server.

## 5. Dependencies

- Run the audit (`npm audit --omit=dev`, `pip-audit`). High and critical findings in what
  ships are fixed or justified; dev-only tooling is noted, not blocking.
- Few, well-known packages, pinned by a lockfile that is committed.

## 6. Safe defaults

- Security headers on HTML responses where the stack makes it easy: a content security
  policy without `unsafe-inline` scripts, `X-Content-Type-Options: nosniff`, frame
  restrictions.
- Request size limits and timeouts. Pagination caps.
- Errors shown to users carry no stack traces, queries or file paths.
- Personal data: collect what is needed, say where it is stored, let it be deleted.

## For a static, no-server app

Most of the above doesn't apply. Check: no secrets in the bundle, user-entered text isn't
injected as HTML, data from `localStorage` or the URL is validated before use, third-party
scripts are few and pinned, and external links opened in a new tab use `rel="noopener"`.

## Reporting

Order by severity: **critical** (data of other users, code execution, leaked secrets),
**high** (likely exploitable), **medium**, **low**. For each: where, the scenario in one or
two sentences, the fix. Say what you checked and found sound, briefly, so the reader knows
the coverage. Don't pad the list with theoretical issues the app's design rules out.
