"""Saving links from the orchestrator: Nexus's save_link tool, and the repo and live app a dev
team's release adds to the library on its own. GitHub links in Nexus's organisation are read
as the GitHub App, so their previews work although the repos are private."""

import logging
import uuid

from nexus_common.links import (
    GITHUB_KINDS,
    GitHubGet,
    LinkError,
    LinkTarget,
    announce,
    github_preview,
    parse_link,
    public_github,
    save_link,
)
from nexus_common.models import Link, utcnow

log = logging.getLogger(__name__)


def reader(deps, target: LinkTarget) -> GitHubGet:
    gh = getattr(deps, "github", None)
    if gh is not None and (target.owner or "").lower() == (gh.org or "").lower():
        return gh.read
    return public_github(deps.settings.github_api_url)


async def preview_for(deps, target: LinkTarget) -> dict | None:
    if target.kind not in GITHUB_KINDS:
        return None
    return await github_preview(target, reader(deps, target))


async def keep_link(
    deps,
    user_id: uuid.UUID,
    url: str,
    *,
    title: str | None = None,
    note: str | None = None,
    kind: str | None = None,
    source: str = "jarvis",
    chat_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> Link:
    """Add or update a link in the user's library, with a fresh preview. Raises LinkError for
    an address that can't be saved."""
    target = parse_link(url)
    preview = await preview_for(deps, target)
    async with deps.sessionmaker() as db:
        link, _ = await save_link(
            db,
            user_id,
            target.url,
            title=title,
            note=note,
            kind=kind,
            source=source,
            chat_id=chat_id,
            run_id=run_id,
            project_id=project_id,
            preview=preview,
        )
        await db.commit()
        await db.refresh(link)
    await announce(deps.redis, link)
    return link


async def refresh_preview(deps, link_id: uuid.UUID) -> None:
    """Fetch a link's preview again: for links the user added in the Library (the API can't read
    the organisation's private repos; only the orchestrator holds the App's key) and for the
    Refresh button."""
    async with deps.sessionmaker() as db:
        link = await db.get(Link, link_id)
        if link is None:
            return
        try:
            target = parse_link(link.url)
        except LinkError:
            return
        preview = await preview_for(deps, target)
        if preview is None:
            return
        if not (preview.get("error") and link.preview and not link.preview.get("error")):
            link.preview = preview
        link.preview_at = utcnow()
        await db.commit()
    await announce(deps.redis, link)


async def keep_release_link(deps, user_id: uuid.UUID, url: str | None, **kw) -> Link | None:
    """keep_link for a release step: a link that can't be saved is logged, never fatal."""
    if not url:
        return None
    try:
        return await keep_link(deps, user_id, url, source="team", **kw)
    except Exception:
        log.warning("Couldn't save the link %s", url, exc_info=True)
        return None
