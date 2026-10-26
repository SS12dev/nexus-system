# Nexus's own mailbox

Nexus sends email from its own address, not yours: routine results, access requests and
approvals, feedback, alerts, the self-review digest, and emails agents write (each one asks
first). Everything goes through one SMTP account (`SMTP_*`, code in
`packages/common/src/nexus_common/mail.py`).

How each email looks:

| Email | From | Replies go to |
| --- | --- | --- |
| Routine results, alerts, approvals, self-review | `Nexus <nexus@servhub.cc>` | Nexus's address, or `SMTP_REPLY_TO` |
| Access request (to owners and admins) | `Nexus <nexus@servhub.cc>` | The person asking |
| Feedback (to owners) | `Nexus <nexus@servhub.cc>` | Whoever sent it |
| An agent's `send_email` for someone | `Shivam via Nexus <nexus@servhub.cc>` | That person |

A bare `SMTP_FROM` address is named "Nexus"; write `Name <address>` to pick another name.
Port 587 upgrades with STARTTLS, port 465 is TLS from the start. **Admin › Configuration ›
Email › Send a test email** sends you one with the saved settings.

`gmail_send` is separate: it sends from the person's own Gmail, for emails that should come
from them.

## Choosing a mailbox

| Option | Cost | From address | Notes |
| --- | --- | --- | --- |
| **Resend on servhub.cc** (recommended) | Free up to 3,000 emails a month, 100 a day | `nexus@servhub.cc` | Your own domain, signed (DKIM), so it lands in the inbox. DNS is on Cloudflare already |
| A Gmail account just for Nexus | Free, about 500 a day | `something@gmail.com` | Five minutes, no DNS. Looks less professional; Google sometimes locks new accounts that only send from scripts |
| Brevo or Amazon SES | Free 300 a day / about $0.10 per 1,000 | Your domain | Same idea as Resend; SES starts in a sandbox until AWS approves it |

Nexus sends a handful of emails a day, so every free tier is plenty.

## Set up Resend (recommended)

1. Sign up at [resend.com](https://resend.com) (Google sign-in works).
2. **Domains › Add domain** › `servhub.cc`. Use **Auto configure** with Cloudflare, or copy the
   records into Cloudflare › servhub.cc › DNS by hand: a `TXT` at `resend._domainkey` (DKIM),
   and an `MX` and a `TXT` (SPF) at `send`. Set them to **DNS only** (grey cloud). They sit on
   `send.servhub.cc`, so they don't touch the `*.servhub.cc` tunnel records.
3. Add a DMARC record in Cloudflare too: `TXT` at `_dmarc`, value `v=DMARC1; p=none;`. Gmail
   and Outlook trust mail from domains that have one.
4. Wait for Resend to show the domain as **Verified** (usually a few minutes), then
   **API Keys › Create API key**: permission *Sending access*, domain `servhub.cc`. Copy it.
5. On the Mac, in the repo folder:

   ```
   ./scripts/secrets.sh set SMTP_PASSWORD      # paste the Resend API key when it asks
   ```

   and in `.env`:

   ```
   SMTP_HOST=smtp.resend.com
   SMTP_PORT=587
   SMTP_USERNAME=resend
   SMTP_FROM=Nexus <nexus@servhub.cc>
   ```

   then `./scripts/compose.sh --profile tunnel up -d`. If Admin › Configuration › Email shows any
   of these as changed from .env, press **Reset** on them (or type the new values there).
6. **Admin › Configuration › Email › Send a test email.**

### Getting replies (optional, free)

Resend only sends. To let replies to `nexus@servhub.cc` reach you, turn on Cloudflare Email
Routing: Cloudflare › servhub.cc › **Email › Email Routing** › enable (it adds its own `MX` and
`SPF` records on `servhub.cc`), add your Gmail as a destination and confirm the email Cloudflare
sends, then add a custom address `nexus` › send to that Gmail. Without it, set
`SMTP_REPLY_TO` (Admin › Configuration › Email › Reply-to address) to an address that reads
mail.

## Set up a Gmail account instead

1. Create a new Google account for Nexus, turn on 2-Step Verification, then make an
   [app password](https://myaccount.google.com/apppasswords).
2. `./scripts/secrets.sh set SMTP_PASSWORD` (paste the app password), and in `.env`:
   `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME=<the new address>`,
   `SMTP_FROM=Nexus <the new address>`.
3. `./scripts/compose.sh --profile tunnel up -d`, then send a test email from Admin.
