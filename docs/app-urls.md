# App addresses

Every app a software team deploys gets its own address on `APPS_DOMAIN`, for example
`https://2048.servhub.cc`. Apps are private until their owner makes them public.

## Names

- The owner's apps are named after the app: `2048.servhub.cc`.
- Other people's carry their first name too: `2048-priya.servhub.cc`.
- A taken name gets a short random suffix: `2048-k7.servhub.cc`.
- `jarvis`, `www`, `mail`, `api`, `admin` and `traefik` are never given out, nor anything in
  `RESERVED_HOSTNAMES` (comma separated in `.env`). List every name your other services use on
  the same domain there, for example the homeserver tunnel's hostnames, so an app can never
  take one.
- Names are one DNS label (`2048-priya`, not `2048.priya`): Cloudflare's free certificate
  covers `*.servhub.cc` but not a level below it.
- A name is stored on the project and kept across redeploys. **Delete** on the Apps page frees
  it; the next deploy of that project picks again.

Traefik routes `Host(<name>.servhub.cc)` to the app's containers. Jarvis's own routers answer
only `JARVIS_HOST` and hosts outside `APPS_DOMAIN`, so an address with no app behind it gets a
plain 404.

## Private and public

A private app has Traefik's `app-auth` forwardAuth middleware in front of it. For every request
Traefik asks the API (`/auth/app/check`):

1. With no app cookie, the visitor goes to `https://jarvis.servhub.cc/auth/app/start`, signs in
   with Google if they aren't already, and Jarvis checks they may open the app: its owner and
   Jarvis's owner may.
2. Jarvis sends them back to `https://<app>.servhub.cc/_jarvis/auth` with a signed token that
   works once, for that app only, for two minutes.
3. Traefik passes that to the API, which swaps it for a `jarvis_app` cookie on the app's host
   alone (no `Domain`), good for `APP_COOKIE_HOURS` (12), then sends them to the page they
   wanted.

The Jarvis session cookie has no `Domain` either, so browsers only ever send it to
`jarvis.servhub.cc`; app hosts never see it, and the check ignores it even if sent by hand. An
app's cookie opens that one app and nothing else, so an app reading its own cookies learns
nothing useful.

**Make public** on the Apps page (or "make 2048 public" to Jarvis) removes the middleware;
**Make private** adds it back. Either takes a few seconds: the app's containers are recreated
from the same images, not rebuilt.

The deployer's own health check and screenshot get past the check with a key for that one host,
derived from `DEPLOYER_TOKEN`.

## Settings

| Setting | Default | What |
| --- | --- | --- |
| `APPS_DOMAIN` | `apps.localhost` | apps live at `<name>.<APPS_DOMAIN>` |
| `JARVIS_HOST` | `jarvis.servhub.cc` | Jarvis's own host |
| `JARVIS_URL` | `https://<JARVIS_HOST>` | where private apps send visitors to sign in |
| `RESERVED_HOSTNAMES` | empty | more names apps may never take |
| `APPS_URL` | `https://{host}` (`http://{host}:<port>` for `*.localhost`) | the link format, if yours differs |
| `APP_COOKIE_HOURS` | 12 | how long an app's sign-in lasts |
| `DEPLOYER_TOKEN` | secret | also signs the deployer's health-check key; set it with `./scripts/secrets.sh set DEPLOYER_TOKEN` |

Without a domain (`APPS_DOMAIN=apps.localhost`), apps are at `http://<name>.apps.localhost:8080`
on the Mac itself and the sign-in round trip goes through `JARVIS_URL`, so set
`JARVIS_URL=http://localhost:8080` there.

## Cloudflare

The Jarvis tunnel needs two public hostnames, both to `http://traefik:80`:

1. `jarvis.servhub.cc`
2. `*.servhub.cc`

and a DNS record `*` → `<jarvis tunnel id>.cfargotunnel.com`, proxied. Specific records (the
homeserver tunnel's hostnames) win over the wildcard, so they keep working.

If adding the wildcard route says "An A, AAAA, or CNAME record with that host already exists",
the route was saved but a `*` record exists already. In DNS → Records, look at `*`: if it is a
proxied CNAME to the Jarvis tunnel's `cfargotunnel.com` name, nothing to do. If it points
somewhere else, edit it to the Jarvis tunnel (check first that nothing you use relies on the old
wildcard; named records are unaffected). Then `curl -I https://nothing-here.servhub.cc` should
give a 404 from Traefik.

## Moving apps to another server or domain

The apps move with Jarvis: same Compose, same volumes.

1. On the new server, run the same Compose (`./scripts/compose.sh --profile tunnel up -d`).
2. In `.env`, set `APPS_DOMAIN` (and `JARVIS_HOST` if Jarvis moves too) and
   `RESERVED_HOSTNAMES` for the names other services use on that domain.
3. In the tunnel, route `*.<APPS_DOMAIN>` → `http://traefik:80` and add the wildcard DNS record
   as above. Make `APPS_DOMAIN` the zone itself (`example.com`, not `apps.example.com`): the
   free certificate covers one level below the zone only.
4. Redeploy each app from the Apps page. Names are kept; the new domain applies from each app's
   next deploy.
