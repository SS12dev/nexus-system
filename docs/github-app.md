# GitHub App setup

Every app a software team builds gets its own private repo in a GitHub organisation you own.
Nexus writes to it as a GitHub App called Nexus, so commits show as `<app-name>[bot]`, its
tokens last an hour, and it can only touch that organisation. Without this setup, projects
still build and show in the Library; they just stay in the workbench.

Each release also adds a link to the repo (and, once deployed, to the live app) to the user's
Library, shown on the team's answer. The orchestrator reads the repo as the App for the link's
preview (description, README), so previews work although the repos are private. Links to repos
outside the organisation are read without signing in, so only public ones preview.

## 1. The organisation

Create a free organisation for Nexus's projects (Settings → Organizations → New
organization), for example `servhub-apps`. Keeping them out of your own account means Nexus
never has access to your other repos.

## 2. The App

In the organisation: Settings → Developer settings → GitHub Apps → New GitHub App.

| Field | Value |
| --- | --- |
| GitHub App name | must be unique on all of GitHub, e.g. `servhub-nexus`; commits show as `<name>[bot]` |
| Homepage URL | `https://nexus.servhub.cc` |
| Callback URL, Setup URL | leave empty; leave every box under them unticked |
| Webhook | untick **Active**: Nexus doesn't listen for events |
| Where can this GitHub App be installed? | Only on this account |

Repository permissions, and nothing else:

| Permission | Access | Why |
| --- | --- | --- |
| Administration | Read and write | repo settings (making `main` the default branch) and moving a repo to your account ("move it to my GitHub") |
| Repository creation | Read and write | create a repo per project (newer GitHub forms list this separately; on older ones Administration covers it) |
| Contents | Read and write | push the team's branch and move `main` to it |
| Metadata | Read-only | required by GitHub for every App |

No organisation or account permissions are needed.

After creating it, note the **App ID** at the top of its page, then under **Private keys**
choose **Generate a private key**; your browser downloads a `.pem` file.

## 3. Install it

On the App's page: Install App → your organisation → **All repositories** (so the repos Nexus
creates are covered). The address you land on ends in `/installations/<number>`: that number
is the installation id.

## 4. Tell Nexus

Plain settings go in `.env`:

```sh
GITHUB_APPS_ORG=servhub-apps
GITHUB_APP_ID=123456
GITHUB_APP_INSTALLATION_ID=78901234
```

The private key is a secret. `scripts/secrets.sh` stores one line per value, so it goes in as
base64 on one line, straight from the file (it never appears on screen or in your history):

```sh
base64 -i ~/Downloads/<app name>.*.private-key.pem | tr -d '\n' \
  | ./scripts/secrets.sh set GITHUB_APP_PRIVATE_KEY_B64
```

Then delete the downloaded `.pem` (GitHub can make a new one any time) and restart:

```sh
./scripts/compose.sh --profile tunnel up -d
```

## What happens on a release

- The repo is named after the app (`2048`); projects of people other than the owner get their
  name in front (`priya-2048`). A taken name gets `-2`, `-3` and so on.
- Each run pushes to `nexus/<run id>`. `main` moves to it only when the reviewer approved and
  every test passed; otherwise the reply says why it stayed on the branch.
- A follow-up ("add a high score table") pushes to the same repo.
- Pushes go from the workbench through its allowlist proxy, with a token limited to that one
  repo's contents. The broader token that creates repos never leaves the orchestrator.

## Moving a repo to your account

Say "move 2048 to my GitHub, I'm SS12dev". Nexus starts a small team whose agent asks you on
an approval card before calling `github_transfer`. GitHub then emails that account to accept
the transfer. Only the owner can do this. Once moved, Nexus stops pushing to it; later
changes stay in the workbench.

## If something goes wrong

The final reply says `Repo: not pushed: …` with GitHub's own message. 401 usually means a wrong
App ID or key, 404 an installation id that doesn't match the organisation or an App not
installed on all repositories, and 403 a missing permission from the table above (after
changing permissions, accept the new ones on the installation page).

## Pull requests on Nexus itself

Approved code and tool proposals from [self-review](../README.md#self-review) become draft pull
requests on the repository in `IMPROVE_REPO` (for example `SS12dev/nexus-system`). That
repository usually lives outside the organisation the App was made for, so it needs its own
installation, and the App one more permission:

1. On the App's settings page, **Permissions and events**: add **Pull requests: Read and write**
   (Contents is already read and write). Save, then accept the new permission on the existing
   installation (Settings, GitHub Apps, Configure on the organisation).
2. If the App says "Only on this account", switch **Where can this GitHub App be installed?** to
   **Any account** under **Advanced**, so your personal account can install it. Nobody else can
   use it without its private key.
3. **Install App**, choose your personal account, **Only select repositories**, and pick the
   Nexus repository.
4. Set **Repository for code proposals** on the Admin page's Configuration tab (Self-review), or
   `IMPROVE_REPO` in `.env`.

Nexus finds that installation by itself (`GET /repos/{owner}/{name}/installation`) and asks for a
token limited to that one repository's contents and pull requests. It only ever creates a
`nexus/proposal-...` branch, commits the spec under `docs/proposals/` and opens a draft pull
request; it never pushes to `main` or merges. Moving the repository into the organisation
instead also works, and then steps 2 and 3 aren't needed.
