# Switching to Nexus

The app is Nexus. Your assistant keeps the name you give it (Jarvis by default), and that is
the only place the old name is still used on purpose.

## What changed in the second step
- Nexus answers only at https://nexus.servhub.cc. jarvis.servhub.cc is no longer served.
- Every internal name is nexus: the Compose project (containers like `nexus-api-1`, volumes
  like `nexus_postgres`), the Postgres database and user, the MinIO bucket, the LiteLLM model
  aliases (`nexus-nano`, `nexus-mini`, `nexus-pro`, `nexus-max`, `nexus-embed`), the app
  containers and labels (`nexus-app-*`), the private-app sign-in path (`/_nexus/auth`), and the
  branches teams push (`nexus/<run id>`).
- `JARVIS_HOST`, `JARVIS_URL`, `JARVIS_CHAT_MODEL`, `JARVIS_HTTP_PORT` and `OLD_HOST` are gone
  from `.env`; use the `NEXUS_*` and `CHAT_MODEL` names.
- The age key lives at `~/.config/nexus/age.key`.
- Langfuse traces from before the switch are in the org "Nexus (before rename)"; new ones go
  to the "nexus" project.

`scripts/move-to-nexus.sh` moved a running install over once: a backup, then copies of every
`jarvis_*` volume into `nexus_*`, the database, user and bucket renames, and a redeploy. The
old `jarvis_*` volumes are left in place until you delete them.

## By hand: Cloudflare
1. Open [Cloudflare Zero Trust](https://one.dash.cloudflare.com/), go to **Networks → Tunnels**
   and click the **jarvis** tunnel, then **Configure → Public Hostname**.
2. Delete the `jarvis.servhub.cc` entry if there is one. Keep `*.servhub.cc → http://traefik:80`.
3. Optional: rename the tunnel to `nexus` (**Configure → Overview**). The token doesn't change.
4. In the [Cloudflare dashboard](https://dash.cloudflare.com/), go to **servhub.cc → DNS →
   Records** and delete the `jarvis` record if there is one. Keep the `*` record.

## By hand: Google Cloud
1. Open [Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients?project=root-mapper-512210-c3)
   and click your Web client.
2. Under **Authorised JavaScript origins**, delete `https://jarvis.servhub.cc`.
3. Under **Authorised redirect URIs**, delete `https://jarvis.servhub.cc/auth/callback`.
4. Click **Save**. Keep the nexus.servhub.cc and localhost entries.
