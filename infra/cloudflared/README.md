# Cloudflare Tunnel

Publishes Jarvis at `https://jarvis.servhub.cc` without opening ports on the Mac.

1. In the Cloudflare dashboard: Zero Trust → Networks → Tunnels → Create a tunnel
   (Cloudflared). Copy its token into `CLOUDFLARE_TUNNEL_TOKEN` in `.env`.
2. Add a public hostname to the tunnel: `jarvis.servhub.cc` → service `http://traefik:80`.
3. In `.env` set `SESSION_COOKIE_SECURE=true`, and
   add `https://jarvis.servhub.cc/auth/callback` as an authorised redirect URI on the Google
   OAuth client. Under Google Auth Platform → Branding, add `servhub.cc` as an authorised
   domain; the consent screen for Gmail and Calendar access needs it (see "Google services" in
   the main README).
4. Start it with the rest of the stack: `docker compose --profile tunnel up -d`.

Cloudflare Access in front of the hostname is part of phase 5 (hardening). Jarvis's own OIDC
login and roles protect it in the meantime, so turn dev sign-in off before exposing it.
