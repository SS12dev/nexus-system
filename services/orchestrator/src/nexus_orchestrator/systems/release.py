"""The release of a software project: its row in the Library (one card, never loose files) and
its GitHub repo.

Each project gets a private repo in GITHUB_APPS_ORG named after the app ("2048"), with the
user's name in front for everyone but the owner ("priya-2048"). The run's work is pushed to
nexus/<run id>, or to the project's branch that isn't merged yet when a follow-up continues it,
and main moves to it only when the reviewer approved and every test passed.
"""

import logging
import re
from datetime import datetime

from sqlalchemy import select

from nexus_common.models import Project, RoleName, User, utcnow
from nexus_orchestrator.github import GitHubError
from nexus_orchestrator.links import keep_release_link
from nexus_orchestrator.systems.runtime import RunContext
from nexus_orchestrator.workbench import WorkbenchError

log = logging.getLogger(__name__)

NOT_SLUG = re.compile(r"[^a-z0-9]+")
NAME_TRIES = 20


def slug(text: str, limit: int = 60) -> str:
    return NOT_SLUG.sub("-", (text or "").lower()).strip("-")[:limit].strip("-")


def base_repo_name(user: User, app: str) -> str:
    name = slug(app) or "app"
    if user.role == RoleName.owner:
        return name
    who = slug((user.name or "").split(" ")[0]) or slug(user.email.split("@")[0]) or "user"
    return f"{who[:30]}-{name}"


def passed(dev: dict) -> bool:
    tests = dev.get("tests") or {}
    return int(tests.get("passed") or 0) > 0 and not int(tests.get("failed") or 0)


async def last_commit(rc: RunContext) -> tuple[str | None, str | None, datetime | None]:
    """(sha, subject, time) of the project's HEAD."""
    try:
        out = await rc.deps.workbench.run(
            str(rc.user.id), rc.project, "git log -1 --format=%H%x1f%s%x1f%cI"
        )
        sha, subject, when = out.stdout.strip().split("\x1f")
        return sha, subject, datetime.fromisoformat(when)
    except (WorkbenchError, ValueError):
        return None, None, None


async def save_project(rc: RunContext, dev: dict) -> Project:
    """Add or update the Library's row for this project."""
    sha, subject, when = await last_commit(rc)
    async with rc.deps.sessionmaker() as db:
        row = await db.scalar(
            select(Project).where(Project.user_id == rc.user.id, Project.workbench_id == rc.project)
        )
        if row is None:
            row = Project(user_id=rc.user.id, workbench_id=rc.project, created_at=utcnow())
            db.add(row)
        row.name = rc.spec.name
        row.stack = dev.get("stack") or row.stack
        row.chat_id = rc.chat_id or row.chat_id
        row.system_id, row.run_id = rc.system_id, rc.run_id
        if sha:
            row.last_commit, row.last_commit_message, row.last_commit_at = sha, subject, when
        row.updated_at = utcnow()
        await db.commit()
        return row


async def set_identity(rc: RunContext) -> None:
    """Commits from here on are by the GitHub App's bot, so GitHub shows them as Nexus's."""
    if rc.deps.github is None:
        return
    try:
        name, email = await rc.deps.github.identity()
        await rc.deps.workbench.identity(str(rc.user.id), rc.project, name, email)
    except (GitHubError, WorkbenchError) as exc:
        log.warning("Couldn't set the bot identity for run %s: %s", rc.run_id, exc)


async def _repo_name(rc: RunContext, row: Project) -> str:
    """The first free name: not on GitHub already, and not another project's."""
    gh = rc.deps.github
    base = base_repo_name(rc.user, rc.spec.name)
    for n in range(1, NAME_TRIES + 1):
        name = base if n == 1 else f"{base}-{n}"
        async with rc.deps.sessionmaker() as db:
            taken = await db.scalar(
                select(Project.id).where(
                    Project.repo_owner == gh.org, Project.repo_name == name, Project.id != row.id
                )
            )
        if taken is None and await gh.get_repo(name) is None:
            return name
    raise GitHubError(f"Couldn't find a free repo name for {base}.")


async def publish(rc: RunContext, dev: dict) -> dict:
    """Push the project to its GitHub repo, creating the repo the first time. Returns facts
    for the final answer: repo_url, branch, merged and why not."""
    gh, wb = rc.deps.github, rc.deps.workbench
    async with rc.deps.sessionmaker() as db:
        row = await db.scalar(
            select(Project).where(Project.user_id == rc.user.id, Project.workbench_id == rc.project)
        )
    if row.transferred_to:
        return {
            "repo_url": row.repo_url,
            "repo_note": f"moved to {row.transferred_to}'s GitHub, so Nexus no longer pushes "
            "to it; the changes are in the workbench",
        }
    repo = await gh.get_repo(row.repo_name) if row.repo_name else None
    if repo is None:
        repo = await gh.create_repo(await _repo_name(rc, row), rc.spec.goal)
    # A follow-up keeps working on the branch that isn't merged yet, so the fixes land on the
    # same branch (and the user sees one line of work) instead of a new branch per run.
    branch = row.branch if row.branch and not row.merged else f"nexus/{rc.run_id}"
    token = await gh.push_token(repo.name)
    pushed = await wb.push(str(rc.user.id), rc.project, repo.clone_url, branch, token)
    merged, note = False, ""
    if not dev.get("approved"):
        note = "the reviewer hasn't approved it"
    elif not passed(dev):
        note = "not every test passed"
    else:
        try:
            note = await gh.update_main(repo, branch, pushed["commit"])
            merged = True
        except GitHubError as exc:
            note = str(exc)
    async with rc.deps.sessionmaker() as db:
        row = await db.get(Project, row.id)
        row.repo_owner, row.repo_name, row.repo_url = repo.owner, repo.name, repo.url
        row.branch, row.merged = branch, merged
        row.updated_at = utcnow()
        await db.commit()
    # The code lives on GitHub; the library keeps a link to it, shown on the team's answer.
    await keep_release_link(
        rc.deps,
        rc.user.id,
        repo.url,
        title=rc.spec.name,
        note=f"The code of {rc.spec.name}, on GitHub.",
        kind="github_repo",
        chat_id=rc.chat_id,
        run_id=rc.run_id,
        project_id=row.id,
    )
    return {"repo_url": repo.url, "branch": branch, "merged": merged, "merge_note": note}


async def is_public(rc: RunContext, project_id) -> bool:
    async with rc.deps.sessionmaker() as db:
        row = await db.get(Project, project_id)
        return bool(row and row.public)
