# Setting Nexus up for friends

A checklist for the owner, in the order to do it. Each part says what to click and what to put
where. Sections 3 to 6 are optional: skip any channel you don't want, and Nexus hides it in the
app.

## 1. Deploy the update

```sh
git pull
./scripts/compose.sh pull
./scripts/compose.sh --profile tunnel up -d --build
```

The `migrate` container runs migrations `0020` (onboarding, routines) and `0021` (push,
feedback, terms) before the API starts. You (and anyone who already used Nexus) skip the setup
interview and the tour; owners are never asked to accept the terms.

Check it worked: open Nexus, go to **Routines** (empty is fine) and **Settings** (About you,
Appearance and Privacy sections show).

## 2. Google Cloud (sign-in and the consent screen)

Nothing about scopes changes. What changes is the information on the consent screen and making
sure tokens don't expire. In [Google Auth Platform](https://console.cloud.google.com/auth), on
the project that holds the OAuth client:

### Branding

| Field | Put |
| --- | --- |
| App name | `Nexus` |
| User support email | `shivam12.dev@gmail.com` |
| App logo | **Leave empty.** Uploading a logo makes Google require verification before the consent screen works for anyone outside test users. |
| Application home page | `https://nexus.servhub.cc` |
| Application privacy policy link | `https://nexus.servhub.cc/privacy` |
| Application terms of service link | `https://nexus.servhub.cc/terms` |
| Authorized domains | `servhub.cc` |
| Developer contact information | `shivam12.dev@gmail.com` |

Save. Both pages load without signing in (nginx serves `apps/web/public/privacy.html` and
`terms.html`). Open them once to check before saving.

"Your branding needs to be verified before it can be shown to users" is expected: an
unverified app shows Google's own "Google hasn't verified this app" screen instead of your
name. For a few friends that is fine; they click **Advanced → Go to Nexus (unsafe)**. The
approval email and the setup screen already tell them to.

*Optional, if you want your name shown instead:* click **Verify branding**. Google asks you to
prove you own `servhub.cc` in [Search Console](https://search.google.com/search-console)
(add a DNS TXT record in Cloudflare: DNS → Add record → TXT, name `@`, the value Search Console
gives you), and checks that the home page describes the app and links the privacy policy (the
sign-in page does). Branding verification takes a few days and is separate from scope
verification: the unverified-app warning for Gmail stays until scopes are verified, which needs
a paid security assessment and isn't worth it for friends.

### Audience

Publishing status must be **In production** (not *Testing*). In Testing, Google expires
refresh tokens after 7 days, which quietly breaks morning briefs and every routine that reads
Gmail or Calendar. Unverified apps in production are capped at 100 users, which is plenty.

### Clients

The OAuth client's authorised redirect URIs must include
`https://nexus.servhub.cc/auth/callback` (and `http://localhost:8080/auth/callback` if you sign
in locally). No change if sign-in already works.

### Cloudflare

Nothing to change: `/privacy`, `/terms` and the new pages are on the same hostname and tunnel.

## 3. Push notifications (phones and browsers)

Free, no account needed. Nexus signs notifications with its own key pair.

1. Make the keys once:

   ```sh
   uv run python -m nexus_common.push
   # or, without uv on the host:
   ./scripts/compose.sh run --rm api python -m nexus_common.push
   ```

   It prints `VAPID_PUBLIC_KEY=…` and `VAPID_PRIVATE_KEY=…`.
2. Put the public key and a contact in `.env`:

   ```
   VAPID_PUBLIC_KEY=B...
   VAPID_SUBJECT=mailto:shivam12.dev@gmail.com
   ```

   and the private key in the encrypted secrets: `./scripts/secrets.sh set VAPID_PRIVATE_KEY`.
   Keep the pair: a new pair breaks every device's subscription (people would turn
   notifications on again).
3. Restart: `./scripts/compose.sh up -d`.
4. Test: Settings → Notifications → **Turn on for this device** → **Send a test**.

What to tell friends:

- **Android / Chrome / Edge / Firefox:** Settings → Notifications → Turn on, and allow when the
  browser asks. Installing the app ("Add to Home screen") is optional.
- **iPhone / iPad (iOS 16.4+):** push only works for the installed app. In Safari open
  `https://nexus.servhub.cc`, tap **Share → Add to Home Screen**, open Nexus from the Home
  Screen, then Settings → Notifications → Turn on.

Push goes out when a routine with **Push** ticked has something new, and when a team is waiting
for their approval.

## 4. WhatsApp notices

Nexus only *sends* short notices ("Your routine Morning brief is ready: …") to people who added
their number in Settings and ticked WhatsApp on a routine. Nobody can message Nexus back on
WhatsApp. It uses Meta's WhatsApp Business **Cloud API**.

### 4.1 Accounts

1. Go to [business.facebook.com](https://business.facebook.com) and create a **business
   portfolio** (your name as the business is fine).
2. Register as a Meta developer (once per Facebook account): logged into Facebook with the
   account that owns the portfolio, open
   [developers.facebook.com/async/registration](https://developers.facebook.com/async/registration)
   (or **Get Started** at the top of developers.facebook.com), accept the terms, confirm your
   phone and email, and choose **Developer**. Until you do, developers.facebook.com/apps just
   shows the marketing homepage.
3. Open [developers.facebook.com/apps/creation](https://developers.facebook.com/apps/creation/),
   name it `Nexus`, pick the use case **Connect with customers through WhatsApp** (or **Other**
   → type **Business**), and pick your business portfolio.
4. In the app's dashboard, **Add product → WhatsApp → Set up**. Meta creates a WhatsApp
   Business Account and a **test phone number**.

### 4.2 Choose a sending number

- **Test number (start here):** free, ready at once, but it can only message up to **5
  recipient numbers** you list. In **WhatsApp → API Setup → To**, add each friend's number and
  enter the code WhatsApp sends them. Fine for a few friends.
- **Your own number (later):** **WhatsApp → API Setup → Add phone number**. It must be a number
  that is *not* registered in the WhatsApp or WhatsApp Business app (or delete that account
  first), able to get an SMS or call. Set a display name (e.g. "Nexus"); Meta reviews it. Add a
  payment method in the WhatsApp Manager: Meta charges per template message (utility messages
  cost a fraction of a rupee/cent; see Meta's
  [pricing page](https://developers.facebook.com/docs/whatsapp/pricing) for your country).

Copy the **Phone number ID** shown under API Setup (a long number, not the phone number
itself).

### 4.3 The message template

Meta only lets a business start a conversation with an approved template. In
[WhatsApp Manager](https://business.facebook.com/wa/manage/message-templates/) → **Message
templates → Create template**:

| Setting | Value |
| --- | --- |
| Category | **Utility** |
| Name | `nexus_routine` |
| Language | English (`en`). If you pick English (US), set `WHATSAPP_TEMPLATE_LANGUAGE=en_US` |
| Header | none |
| Body | `Your routine *{{1}}* is ready: {{2}}` then a new line `Open Nexus to read it: https://nexus.servhub.cc` |
| Sample for {{1}} | `Morning brief` |
| Sample for {{2}} | `3 meetings today, 2 emails need a reply, standup at 09:30.` |
| Footer | `Turn this off in Nexus Settings` (optional) |
| Buttons | none |

Submit. Approval usually takes minutes to a few hours. Nexus sends `{{1}}` = the routine's name
and `{{2}}` = a one-line preview (it removes line breaks and formatting, which Meta refuses
inside parameters).

### 4.4 A permanent token

The token on the API Setup page expires in 24 hours. Make a permanent one:

1. [Business settings](https://business.facebook.com/settings) → **Users → System users →
   Add** → name `nexus`, role **Admin**.
2. Select it → **Assign assets** → **Apps** → `Nexus` → **Full control**; then **WhatsApp
   accounts** → your WhatsApp Business Account → **Full control**.
3. **Generate new token** → app `Nexus` → expiry **Never** → permissions
   `whatsapp_business_messaging` and `whatsapp_business_management` → Generate. Copy it now; it
   isn't shown again.

### 4.5 Tell Nexus

```sh
./scripts/secrets.sh set WHATSAPP_TOKEN          # paste the system user token
```

and in `.env`:

```
WHATSAPP_PHONE_NUMBER_ID=123456789012345
WHATSAPP_TEMPLATE=nexus_routine
WHATSAPP_TEMPLATE_LANGUAGE=en
```

Restart (`./scripts/compose.sh up -d`). Check it from the host before trying it in the app
(replace the values; `to` is a number you added in 4.2, digits only):

```sh
curl -s -X POST "https://graph.facebook.com/v25.0/$WHATSAPP_PHONE_NUMBER_ID/messages" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{"messaging_product":"whatsapp","to":"919812345678","type":"template",
       "template":{"name":"nexus_routine","language":{"code":"en"},
       "components":[{"type":"body","parameters":[{"type":"text","text":"Test"},
       {"type":"text","text":"It works."}]}]}}'
```

A reply with `"messages":[{"id":"wamid…"}]` means it was accepted. Common errors: `132001`
(template name or language doesn't match), `131030` (recipient not in the test number's list),
`190` (token expired or wrong).

In the app: Settings → About you → WhatsApp number (`+91…`), then tick **WhatsApp** on a
routine and press **Run now**.

## 5. Email notices

Routine emails, feedback emails to you, and access-request emails use the SMTP settings. With
Gmail: turn on 2-step verification, create an [app password](https://myaccount.google.com/apppasswords),
then in `.env`:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_FROM=Nexus <you@gmail.com>
```

and `./scripts/secrets.sh set SMTP_PASSWORD`.

## 6. Inviting friends

1. **People → Invite**: their Google address, role **member**. Set their monthly budget there
   ($5 covers chat and a few routines). To give them everything except admin, also tick **Run
   code** (agents running code, needed for documents, spreadsheets and building apps) and
   **Send** (agents sending email after their approval) on their row: members don't get these
   two by default.
2. They open `https://nexus.servhub.cc`, sign in with Google (click through the unverified
   screen), tick the boxes for the Google services they want, agree to the Terms and Privacy
   Policy, and answer the two-minute setup. They land in a Welcome chat with a short tour.
3. Their feedback arrives by email and under **Admin → Feedback**, and feeds the weekly
   self-review.

## 7. After the first day

- **Langfuse:** open a friend's trace; prompts and replies should read `redacted-by-litellm`,
  with tokens and cost still there.
- **Admin → Models:** routine runs show as ordinary chat turns on nexus-mini.
- **Routines:** each friend's routines have run (history on the Routines page).

## 8. Changing the terms later

Edit `apps/web/public/terms.html` and/or `privacy.html`, change the date on both pages, and set
`TERMS_VERSION` in `packages/common/src/nexus_common/models.py` to the same date (a test checks
they match). After deploying, everyone except owners is asked to agree again the next time they
open Nexus.
