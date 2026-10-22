# Switching to Nexus, step by step

Your assistant stays Jarvis. jarvis.servhub.cc keeps working throughout, so nothing goes down.

## Already done
- **GitHub repo renamed** to `SS12dev/nexus-system`. Your Mac's `origin` already points there.
- **`.env` on your Mac updated by Claude:**
  - `JARVIS_CHAT_MODEL` is now `CHAT_MODEL`.
  - `JARVIS_HTTP_PORT` is now `NEXUS_HTTP_PORT`.
  - `NEXUS_HOST=nexus.servhub.cc` is added.
  - The old file is saved as `backups/env.before-nexus`.
  - The running app doesn't use any of this until you deploy PR #35.

## 1. Claude project: point it at the renamed repo
Open Claude project settings → Repositories. Remove `SS12dev/jarvis-system` and add `SS12dev/nexus-system`. GitHub redirects the old name, so nothing breaks in the meantime.

## 2. Google Cloud: allow sign-in on nexus.servhub.cc
1. Open [Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients?project=root-mapper-512210-c3) and click your Web client. Older consoles have it under [APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials?project=root-mapper-512210-c3).
2. Under **Authorised JavaScript origins**, click **Add URI** and enter `https://nexus.servhub.cc`
3. Under **Authorised redirect URIs**, click **Add URI** and enter `https://nexus.servhub.cc/auth/callback`
4. Click **Save**. Leave the localhost and jarvis.servhub.cc entries as they are.
5. Open [Google Auth Platform → Branding](https://console.cloud.google.com/auth/branding?project=root-mapper-512210-c3). Change **App name** to `Nexus`, then save. Optionally, set **Application home page** to `https://nexus.servhub.cc` and **Privacy policy** to `https://nexus.servhub.cc/privacy.html`.

Google can take a few minutes to pick up new redirect URIs.

## 3. Cloudflare: nothing to add, just check
Your tunnel already has the `*.servhub.cc → http://traefik:80` route, so nexus.servhub.cc reaches the Mac without a new entry. To confirm:
1. Open [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) and go to **Networks → Tunnels**. Click the **jarvis** tunnel, then **Configure → Public Hostname**.
2. You should see `*.servhub.cc` pointing to `http://traefik:80`. If it's missing, click **Add a public hostname** and set subdomain `nexus`, domain `servhub.cc`, service type `HTTP`, URL `traefik:80`.
3. In the [Cloudflare dashboard](https://dash.cloudflare.com/), go to **servhub.cc → DNS → Records**. There should be a `*` CNAME pointing to the tunnel. A separate `nexus` record isn't needed.

Renaming the tunnel from "jarvis" to "nexus" is optional, cosmetic, and doesn't change the token.

## 4. Mac: deploy
Merge [PR #35](https://github.com/SS12dev/nexus-system/pull/35), then in Terminal:

    cd ~/Desktop/"jarvis local"/jarvis-system
    git pull
    ./scripts/compose.sh --profile tunnel up -d --build

- The first run renames `secrets/jarvis.sops.env` to `secrets/nexus.sops.env`. Your age key is still found at `~/.config/jarvis/age.key`.
- Everyone is signed out once, because the sign-in cookie has a new name.

## 5. Check
- Open https://nexus.servhub.cc and sign in with Google. The login page should say Nexus.
- https://jarvis.servhub.cc should still open Nexus.
- Your assistant should still be called Jarvis. You can rename it in Settings whenever you like.

## Later (optional)
- **Retire jarvis.servhub.cc** once nobody uses it: add `OLD_HOST=nexus.servhub.cc` to `.env`, remove the jarvis entries in Google, and redeploy.
- **Rename the folder on your Mac** from `jarvis local/jarvis-system`. If you do, tell Claude the new path so the approved device folder can be updated.

## What keeps the jarvis name, and why
These all live inside Docker and the databases, and nobody sees them in the app.
- **The Compose project `jarvis`:** this is why containers are still named like `jarvis-api-1`. Docker volume, network and image names come from it, so renaming it would start Nexus on empty volumes and cut off your running apps.
- **The Postgres role and database, and the MinIO bucket.**
- **The LiteLLM model aliases (`jarvis-mini` and the others):** they're built into everyone's gateway keys and stored embeddings.
- **The app container names and labels (`jarvis-app-*`):** the deployer's safety gate checks these.

These can be renamed later as one planned maintenance window: take a backup, stop everything, move the data, then redeploy every app.
