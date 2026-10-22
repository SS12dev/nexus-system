"""GitHub as the Nexus GitHub App, installed on the organisation that holds Nexus's projects
(docs/github-app.md). The App signs a short JWT with its private key and swaps it for an
installation token, good for an hour and cached until shortly before it expires. That token
creates repos, moves main and transfers repos; it never leaves the orchestrator. A push gets its
own token, limited to the one repository and to its contents, because it is handed to the
workbench where agent code runs."""

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from nexus_common.settings import Settings

# Get a new installation token once the cached one has less than this left.
REFRESH_S = 300


class GitHubError(RuntimeError):
    """Safe to show to the user."""


@dataclass
class Repo:
    owner: str
    name: str
    url: str
    clone_url: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _repo(data: dict) -> Repo:
    return Repo(
        owner=data["owner"]["login"],
        name=data["name"],
        url=data["html_url"],
        clone_url=data["clone_url"],
    )


class GitHubApp:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None, clock=None):
        self.settings = settings
        self.org = settings.github_apps_org
        self._client = client or httpx.AsyncClient(timeout=30)
        self._clock = clock or time.time
        self._token: tuple[str, float] | None = None
        self._identity: tuple[str, str] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Authentication --------------------------------------------------------------------

    def jwt(self) -> str:
        """The App's own JWT (RS256), valid for nine minutes."""
        try:
            pem = base64.b64decode(self.settings.github_app_private_key_b64)
            key = serialization.load_pem_private_key(pem, password=None)
        except ValueError as exc:
            raise GitHubError(
                "GITHUB_APP_PRIVATE_KEY_B64 isn't a base64-encoded PEM private key."
            ) from exc
        now = int(self._clock())
        header = {"alg": "RS256", "typ": "JWT"}
        # Backdated a minute, as GitHub suggests, in case the clocks differ a little.
        claims = {"iat": now - 60, "exp": now + 540, "iss": str(self.settings.github_app_id)}
        signing = ".".join(
            _b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims)
        )
        signature = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing}.{_b64url(signature)}"

    async def _installation_token(self, body: dict | None = None) -> tuple[str, float]:
        path = f"/app/installations/{self.settings.github_app_installation_id}/access_tokens"
        data = (await self._request("POST", path, auth="jwt", json=body or {})).json()
        expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
        return data["token"], expires

    async def token(self) -> str:
        if self._token is None or self._token[1] - self._clock() < REFRESH_S:
            self._token = await self._installation_token()
        return self._token[0]

    async def push_token(self, repo: str) -> str:
        """A token that can only write the contents of this one repository."""
        token, _ = await self._installation_token(
            {"repositories": [repo], "permissions": {"contents": "write"}}
        )
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: str = "token",
        ok: tuple = (),
        accept: str = "application/vnd.github+json",
        **kw,
    ) -> httpx.Response:
        """ok: error statuses the caller handles itself (such as 404 for a missing repo)."""
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if auth == "jwt":
            headers["Authorization"] = f"Bearer {self.jwt()}"
        elif auth == "token":
            headers["Authorization"] = f"Bearer {await self.token()}"
        url = self.settings.github_api_url.rstrip("/") + path
        try:
            resp = await self._client.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise GitHubError("GitHub isn't reachable right now.") from exc
        if resp.status_code >= 400 and resp.status_code not in ok:
            try:
                message = resp.json().get("message", "")
            except ValueError:
                message = resp.text[:200]
            hint = (
                " Check the App's permissions and installation (docs/github-app.md)."
                if resp.status_code in (401, 403, 404)
                else ""
            )
            raise GitHubError(f"GitHub said {resp.status_code}: {message}.{hint}")
        return resp

    # --- What the App does -----------------------------------------------------------------

    async def identity(self) -> tuple[str, str]:
        """The App's bot as a git author, so commits show as made by it on GitHub."""
        if self._identity is None:
            slug = (await self._request("GET", "/app", auth="jwt")).json()["slug"]
            bot = (await self._request("GET", f"/users/{slug}%5Bbot%5D", auth=None)).json()
            name = f"{slug}[bot]"
            self._identity = (name, f"{bot['id']}+{name}@users.noreply.github.com")
        return self._identity

    async def get_repo(self, name: str, owner: str | None = None) -> Repo | None:
        resp = await self._request("GET", f"/repos/{owner or self.org}/{name}", ok=(404,))
        return None if resp.status_code == 404 else _repo(resp.json())

    async def create_repo(self, name: str, description: str = "") -> Repo:
        body = {
            "name": name,
            "description": description[:300],
            "private": True,
            "has_wiki": False,
            "has_projects": False,
        }
        return _repo((await self._request("POST", f"/orgs/{self.org}/repos", json=body)).json())

    async def update_main(self, repo: Repo, branch: str, sha: str) -> str:
        """Bring main to the pushed branch: create it on a new repo, fast-forward it when it can,
        otherwise merge the branch in. Returns what happened, in a few words."""
        base = f"/repos/{repo.owner}/{repo.name}"
        found = await self._request("GET", f"{base}/git/ref/heads/main", ok=(404, 409))
        if found.status_code != 200:
            await self._request(
                "POST", f"{base}/git/refs", json={"ref": "refs/heads/main", "sha": sha}
            )
            # The first branch pushed became the default; main is the one people should see.
            await self._request("PATCH", base, json={"default_branch": "main"})
            return "main created"
        moved = await self._request(
            "PATCH", f"{base}/git/refs/heads/main", json={"sha": sha, "force": False}, ok=(422,)
        )
        if moved.status_code == 200:
            return "main fast-forwarded"
        merged = await self._request(
            "POST",
            f"{base}/merges",
            json={"base": "main", "head": branch, "commit_message": f"Merge {branch}"},
            ok=(409,),
        )
        if merged.status_code == 409:
            raise GitHubError(f"{branch} conflicts with main, so it wasn't merged.")
        return "merged into main"

    async def read(self, path: str, raw: bool = False) -> httpx.Response | None:
        """A GET for a link's preview (nexus_common.links.github_preview), as the App, so it
        can see the organisation's private repos. None when GitHub refuses or is unreachable."""
        accept = "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
        try:
            resp = await self._request("GET", path, ok=(403, 404, 409, 422), accept=accept)
        except GitHubError:
            return None
        return resp if resp.status_code == 200 else None

    async def transfer(self, name: str, new_owner: str) -> Repo:
        """Hand a repo to someone's own GitHub account. GitHub emails them to accept it."""
        data = (
            await self._request(
                "POST",
                f"/repos/{self.org}/{name}/transfer",
                json={"new_owner": new_owner},
            )
        ).json()
        return Repo(
            owner=new_owner,
            name=data.get("name", name),
            url=f"https://github.com/{new_owner}/{data.get('name', name)}",
            clone_url=f"https://github.com/{new_owner}/{data.get('name', name)}.git",
        )
