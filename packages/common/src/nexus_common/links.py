"""Links in the library: pointers to things that live elsewhere. Code a dev team writes lives in
its GitHub repo, a Google Doc lives in Google Drive and a running app lives at its own address,
so the library keeps the address and a snapshot for the preview, never the thing itself.

One row per address and user: saving the same address again (Nexus re-saving a repo after the
next release, say) updates that row rather than adding a second one. Uploads are the other way
round: the same file uploaded twice stays two files, since each may belong to a different piece
of work."""

import base64
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus_common.events import Event, publish
from nexus_common.models import Link, utcnow

URL_MAX = 2048
TITLE_MAX = 300
NOTE_MAX = 2000
# How much of a README, file or pull request description the preview keeps.
TEXT_MAX = 60_000
# Bigger files aren't fetched for the preview; GitHub shows them.
FILE_MAX = 300_000
LIST_MAX = 200

LINK_KINDS = (
    "github_repo",
    "github_file",
    "github_folder",
    "github_pr",
    "github_issue",
    "github_commit",
    "google_doc",
    "google_sheet",
    "google_slides",
    "google_drive",
    "app",
    "video",
    "design",
    "web",
)
GITHUB_KINDS = {k for k in LINK_KINDS if k.startswith("github_")}

# Parts of github.com that aren't someone's account.
GITHUB_RESERVED = {
    "about", "apps", "collections", "explore", "features", "issues", "login", "marketplace",
    "new", "notifications", "orgs", "organizations", "pricing", "pulls", "search", "settings",
    "sponsors", "topics", "trending",
}  # fmt: skip
GITHUB_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
GOOGLE_DOC_KINDS = {
    "document": "google_doc",
    "spreadsheets": "google_sheet",
    "presentation": "google_slides",
}


class LinkError(ValueError):
    """Safe to show to the user."""


@dataclass(frozen=True)
class LinkTarget:
    """What an address points to, worked out from the address alone."""

    url: str
    kind: str
    host: str
    # GitHub only.
    owner: str | None = None
    repo: str | None = None
    ref: str | None = None
    path: str | None = None
    number: int | None = None

    @property
    def full_name(self) -> str | None:
        return f"{self.owner}/{self.repo}" if self.owner and self.repo else None

    def default_title(self) -> str:
        if self.kind == "github_repo":
            return self.full_name
        if self.kind in ("github_file", "github_folder"):
            return f"{self.repo}/{self.path}" if self.path else self.full_name
        if self.kind in ("github_pr", "github_issue"):
            return f"{self.full_name} #{self.number}"
        if self.kind == "github_commit":
            return f"{self.full_name} @ {(self.ref or '')[:7]}"
        if self.kind in ("google_doc", "google_sheet", "google_slides", "google_drive"):
            return {
                "google_doc": "Google Doc",
                "google_sheet": "Google Sheet",
                "google_slides": "Google Slides",
                "google_drive": "Google Drive",
            }[self.kind]
        tail = urlsplit(self.url).path.rstrip("/")
        return (self.host + unquote(tail))[:TITLE_MAX] or self.url[:TITLE_MAX]


# Names reserved for examples and tests (RFC 2606, RFC 6761), and made-up stand-ins.
PLACEHOLDER_TLDS = (".example", ".invalid", ".test", ".localhost")
PLACEHOLDER_HOSTS = ("example.com", "example.org", "example.net")
PLACEHOLDER_WORDS = re.compile(r"(^|[.-])(placeholder|your-?app|your-?domain|tbd|todo)([.-]|$)")


def is_placeholder(url: str) -> bool:
    """An address made up to stand in for something that doesn't exist yet."""
    host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
    return (
        host.endswith(PLACEHOLDER_TLDS)
        or any(host == h or host.endswith("." + h) for h in PLACEHOLDER_HOSTS)
        or bool(PLACEHOLDER_WORDS.search(host))
    )


def parse_link(url: str) -> LinkTarget:
    """Check an address and say what it points to. Only plain http(s) addresses without a
    password in them are accepted, so a link can never run script when it is opened."""
    raw = (url or "").strip()
    if not raw:
        raise LinkError("The link is empty.")
    if len(raw) > URL_MAX:
        raise LinkError(f"Links can be up to {URL_MAX} characters.")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise LinkError("Only http and https links can be saved.")
    if parts.username or parts.password:
        raise LinkError("Links can't carry a username or password.")
    host = (parts.hostname or "").lower()
    if host == "www.github.com":
        host = "github.com"
    if not host or ("." not in host and host != "localhost"):
        raise LinkError(f"{url} isn't a web address.")
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    path = parts.path or ""
    if host == "github.com":
        path = path.rstrip("/")
        path = path.removesuffix(".git")
    elif path == "/":
        path = ""
    clean = urlunsplit((scheme, netloc, path, parts.query, parts.fragment))
    if host == "github.com":
        return _github(clean, path)
    if host == "docs.google.com":
        first = path.strip("/").split("/")[0]
        return LinkTarget(clean, GOOGLE_DOC_KINDS.get(first, "google_drive"), host)
    if host == "drive.google.com":
        return LinkTarget(clean, "google_drive", host)
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "vimeo.com"):
        return LinkTarget(clean, "video", host)
    if host in ("figma.com", "www.figma.com"):
        return LinkTarget(clean, "design", host)
    return LinkTarget(clean, "web", host)


def _github(url: str, path: str) -> LinkTarget:
    seg = [unquote(s) for s in path.strip("/").split("/") if s]
    host = "github.com"
    if len(seg) < 2 or seg[0].lower() in GITHUB_RESERVED:
        return LinkTarget(url, "web", host)
    owner, repo = seg[0], seg[1]
    if not (GITHUB_NAME.match(owner) and GITHUB_NAME.match(repo)):
        return LinkTarget(url, "web", host)
    rest = seg[2:]
    base = {"url": url, "host": host, "owner": owner, "repo": repo}
    if not rest:
        return LinkTarget(kind="github_repo", **base)
    head = rest[0]
    if head in ("blob", "tree") and len(rest) >= 2:
        # The ref can hold slashes too (feature/x); the first segment is the usual case.
        kind = "github_file" if head == "blob" else "github_folder"
        return LinkTarget(kind=kind, ref=rest[1], path="/".join(rest[2:]) or None, **base)
    if head in ("pull", "issues") and len(rest) >= 2 and rest[1].isdigit():
        kind = "github_pr" if head == "pull" else "github_issue"
        return LinkTarget(kind=kind, number=int(rest[1]), **base)
    if head == "commit" and len(rest) >= 2:
        return LinkTarget(kind="github_commit", ref=rest[1], **base)
    # Releases, actions, wiki and the like: the repo is what it's about.
    return LinkTarget(kind="github_repo", **base)


def link_payload(link: Link) -> dict:
    return {
        "id": str(link.id),
        "url": link.url,
        "kind": link.kind,
        "title": link.title,
        "note": link.note,
        "source": link.source,
        "chat_id": str(link.chat_id) if link.chat_id else None,
        "run_id": str(link.run_id) if link.run_id else None,
        "project_id": str(link.project_id) if link.project_id else None,
        "preview": link.preview,
        "preview_at": link.preview_at.isoformat() if link.preview_at else None,
        "created_at": link.created_at.isoformat(),
        "updated_at": link.updated_at.isoformat(),
    }


async def save_link(
    db: AsyncSession,
    user_id: uuid.UUID,
    url: str,
    *,
    title: str | None = None,
    note: str | None = None,
    kind: str | None = None,
    source: str = "user",
    chat_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    preview: dict | None = None,
) -> tuple[Link, bool]:
    """Add a link, or update the one already saved for this address. Returns it and whether it
    is new. The caller commits."""
    target = parse_link(url)
    if kind is not None and kind not in LINK_KINDS:
        raise LinkError(f"{kind} isn't a kind of link.")
    title = (title or "").strip()[:TITLE_MAX]
    note = (note or "").strip()[:NOTE_MAX] or None
    link = await db.scalar(select(Link).where(Link.user_id == user_id, Link.url == target.url))
    created = link is None
    if created:
        link = Link(
            id=uuid.uuid4(),
            user_id=user_id,
            url=target.url,
            kind=kind or target.kind,
            title=title or target.default_title(),
            note=note,
            source=source,
            created_at=utcnow(),
        )
        db.add(link)
    else:
        if kind:
            link.kind = kind
        if title:
            link.title = title
        if note:
            link.note = note
    link.chat_id = chat_id or link.chat_id
    link.run_id = run_id or link.run_id
    link.project_id = project_id or link.project_id
    # A failed refresh keeps the last good preview.
    if preview is not None and not (
        preview.get("error") and link.preview and not link.preview.get("error")
    ):
        link.preview, link.preview_at = preview, utcnow()
    link.updated_at = utcnow()
    await db.flush()
    return link, created


async def announce(redis, link: Link) -> None:
    await publish(
        redis, link.user_id, Event(type="link.updated", data={"link": link_payload(link)})
    )


# --- GitHub previews -------------------------------------------------------------------------

# Fetches a GitHub API path (raw=True asks for the file itself rather than JSON). Returns None
# when GitHub refuses or can't be reached, which the preview reports instead of failing.
GitHubGet = Callable[[str, bool], Awaitable[httpx.Response | None]]


def _clip(text: str | None, limit: int = TEXT_MAX) -> tuple[str, bool]:
    text = text or ""
    return (text, False) if len(text) <= limit else (text[:limit], True)


async def github_preview(target: LinkTarget, get: GitHubGet) -> dict:
    """What the preview of a GitHub link shows. Never raises: when GitHub won't say (a private
    repo the caller can't see, a rate limit), the preview says so and keeps the address."""
    if target.kind not in GITHUB_KINDS:
        return {}
    repo = f"/repos/{target.owner}/{target.repo}"
    try:
        if target.kind == "github_repo":
            return await _repo_preview(repo, get)
        if target.kind in ("github_file", "github_folder"):
            return await _contents_preview(repo, target, get)
        if target.kind == "github_pr":
            return await _pr_preview(repo, target, get)
        if target.kind == "github_issue":
            return await _issue_preview(repo, target, get)
        return await _commit_preview(repo, target, get)
    except (KeyError, TypeError, ValueError):
        return {"error": "GitHub's answer didn't make sense, so there's no preview."}


UNREADABLE = (
    "GitHub didn't let Nexus read this (a private repo outside Nexus's organisation, or "
    "GitHub is busy). Open it on GitHub instead."
)


async def _json(get: GitHubGet, path: str):
    resp = await get(path, False)
    if resp is None or resp.status_code != 200:
        return None
    return resp.json()


async def _repo_preview(repo: str, get: GitHubGet) -> dict:
    data = await _json(get, repo)
    if data is None:
        return {"error": UNREADABLE}
    out = {
        "description": data.get("description"),
        "default_branch": data.get("default_branch"),
        "language": data.get("language"),
        "stars": data.get("stargazers_count"),
        "private": data.get("private"),
        "pushed_at": data.get("pushed_at"),
        "topics": (data.get("topics") or [])[:10],
    }
    readme = await get(f"{repo}/readme", True)
    if readme is not None and readme.status_code == 200:
        out["readme"], out["readme_truncated"] = _clip(readme.text)
    return out


async def _contents_preview(repo: str, target: LinkTarget, get: GitHubGet) -> dict:
    path = "/".join(p for p in (target.path or "").split("/") if p)
    data = await _json(get, f"{repo}/contents/{path}?ref={target.ref}")
    if data is None:
        return {"error": UNREADABLE}
    if isinstance(data, list):
        entries = sorted(
            ({"name": e["name"], "type": e["type"], "size": e.get("size", 0)} for e in data),
            key=lambda e: (e["type"] != "dir", e["name"].lower()),
        )
        return {"entries": entries[:LIST_MAX], "entries_total": len(entries)}
    out = {"name": data.get("name"), "size": data.get("size", 0)}
    if out["size"] > FILE_MAX or data.get("encoding") != "base64":
        out["note"] = "Too big to preview here; open it on GitHub."
        return out
    raw = base64.b64decode(data.get("content") or "")
    try:
        out["text"], out["truncated"] = _clip(raw.decode("utf-8"))
    except UnicodeDecodeError:
        out["note"] = "Not a text file; open it on GitHub."
    return out


def _person(data: dict | None) -> str | None:
    return (data or {}).get("login")


async def _pr_preview(repo: str, target: LinkTarget, get: GitHubGet) -> dict:
    data = await _json(get, f"{repo}/pulls/{target.number}")
    if data is None:
        return {"error": UNREADABLE}
    body, cut = _clip(data.get("body"))
    return {
        "title": data.get("title"),
        "state": "merged" if data.get("merged") else data.get("state"),
        "draft": data.get("draft", False),
        "author": _person(data.get("user")),
        "head": (data.get("head") or {}).get("ref"),
        "base": (data.get("base") or {}).get("ref"),
        "additions": data.get("additions"),
        "deletions": data.get("deletions"),
        "changed_files": data.get("changed_files"),
        "body": body,
        "body_truncated": cut,
        "updated": data.get("updated_at"),
    }


async def _issue_preview(repo: str, target: LinkTarget, get: GitHubGet) -> dict:
    data = await _json(get, f"{repo}/issues/{target.number}")
    if data is None:
        return {"error": UNREADABLE}
    body, cut = _clip(data.get("body"))
    return {
        "title": data.get("title"),
        "state": data.get("state"),
        "author": _person(data.get("user")),
        "comments": data.get("comments"),
        "labels": [lb.get("name") for lb in data.get("labels") or [] if isinstance(lb, dict)],
        "body": body,
        "body_truncated": cut,
        "updated": data.get("updated_at"),
    }


async def _commit_preview(repo: str, target: LinkTarget, get: GitHubGet) -> dict:
    data = await _json(get, f"{repo}/commits/{target.ref}")
    if data is None:
        return {"error": UNREADABLE}
    commit = data.get("commit") or {}
    files = [
        {
            "name": f.get("filename"),
            "status": f.get("status"),
            "additions": f.get("additions"),
            "deletions": f.get("deletions"),
        }
        for f in (data.get("files") or [])[:LIST_MAX]
    ]
    return {
        "message": commit.get("message"),
        "author": (commit.get("author") or {}).get("name"),
        "date": (commit.get("author") or {}).get("date"),
        "additions": (data.get("stats") or {}).get("additions"),
        "deletions": (data.get("stats") or {}).get("deletions"),
        "files": files,
    }


def public_github(api_url: str = "https://api.github.com", timeout: float = 10) -> GitHubGet:
    """Reads public GitHub without signing in (60 requests an hour per address), for links to
    repos outside Nexus's organisation."""
    base = api_url.rstrip("/")

    async def get(path: str, raw: bool) -> httpx.Response | None:
        accept = "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.get(base + path, headers=headers)
        except httpx.HTTPError:
            return None

    return get
