"""Before a dev team changes an app that already exists, plain code looks at it as it is now, so
the team fixes what is actually broken instead of guessing from the user's words: work an
earlier run left uncommitted, the project's own test suites run on the current code, and the
live app as a visitor sees it (the deployer opens it in a browser). The report goes to the
researcher, who writes the problems into BRIEF.md for the architect and the reviewer."""

import logging
from dataclasses import dataclass

from sqlalchemy import select

from nexus_common.deployer import DeployerError
from nexus_common.models import Project
from nexus_orchestrator.systems import suites
from nexus_orchestrator.workbench import WorkbenchError

log = logging.getLogger(__name__)

# Pages a web server shows when it has nothing of the app's to serve.
PLACEHOLDERS = ("welcome to nginx", "it works!", "index of /", "404 not found")
TEXT_CHARS = 300


@dataclass
class Checkup:
    report: str
    # The live app is down, shows something that isn't the app, or logs errors: a fixed version
    # may replace it even if the reviewer still has open points.
    live_broken: bool = False


async def uncommitted(wb, user: str, project: str) -> str:
    """git status of work a stopped run left behind; look before anything commits it."""
    try:
        out = await wb.run(user, project, "git status --short | head -30")
    except WorkbenchError as exc:
        log.warning("No git status for %s: %s", project, exc)
        return ""
    return out.stdout.strip()


async def checkup(rc, left: str = "") -> Checkup:
    """Never raises: a part that can't be checked says so in the report. left: what
    uncommitted() found before the run touched the folder."""
    wb, user, project = rc.deps.workbench, str(rc.user.id), rc.project
    async with rc.deps.sessionmaker() as db:
        row = await db.scalar(
            select(Project).where(Project.user_id == rc.user.id, Project.workbench_id == project)
        )
    stack = (row.stack if row else None) or "static"
    lines: list[str] = []
    if left:
        lines.append(
            "Changes an earlier run left uncommitted when it stopped before finishing (they are "
            f"kept; finish or undo them):\n{left}"
        )
    try:
        measured = await suites.run_suites(wb, user, project, stack)
        lines.append(
            measured.report()
            if measured.ran
            else "Tests: none of the project's suites could run as it is."
        )
    except WorkbenchError as exc:
        lines.append(f"Tests: couldn't run them ({exc}).")

    live_broken = False
    if row is None or not row.app_slug:
        lines.append("Live app: it has never been deployed.")
    else:
        where = row.live_url or row.app_slug
        if row.deploy_error:
            lines.append(f"Last deploy failed: {row.deploy_error[:500]}")
            live_broken = True
        found = None
        if rc.deps.deployer is not None:
            try:
                found = await rc.deps.deployer.check(row.app_slug, app_id=str(row.id))
            except DeployerError as exc:
                lines.append(f"Live app {where}: couldn't check it ({exc}).")
        if found is not None:
            live, broken = _live(where, found)
            lines.append(live)
            live_broken = live_broken or broken
    report = "\n\n".join(lines)
    return Checkup(report=report, live_broken=live_broken)


def _live(where: str, found: dict) -> tuple[str, bool]:
    """The deployer's check in words, and whether it shows the app broken."""
    if not found.get("running") or not found.get("loaded"):
        text = f"Live app {where}: {found.get('detail') or 'not running'}"
        if found.get("logs"):
            text += f"\nContainer logs (end):\n{found['logs'][-1500:]}"
        return text, True
    title = str(found.get("title") or "").strip()
    shown = " ".join(str(found.get("text") or "").split())[:TEXT_CHARS]
    errors = [str(e) for e in found.get("console_errors") or []]
    seen = f"{title} {shown}".lower()
    placeholder = any(p in seen for p in PLACEHOLDERS) or not (title or shown)
    text = f"Live app {where}: a browser opened it. Title: {title or '(none)'}. Shows: {shown}"
    if placeholder:
        text += (
            "\nThat isn't the app: the web server has nothing of the app's to serve (it is not "
            "where the deploy looks, or the build made no index.html)."
        )
    if errors:
        text += "\nConsole errors on load:\n" + "\n".join(f"- {e[:300]}" for e in errors[:10])
    return text, placeholder or bool(errors)
