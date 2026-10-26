"""The dev_project graph: Nexus's software team over one workbench project.

    brief -> plan -> (plan_gate) -> build -> frontend + backend -> test -> review -> release
                                      ^______________fixes_______________|  (max_rounds builds)

The researcher picks a house stack and writes BRIEF.md; the architect writes PLAN.md, which is
posted in the chat. If the user asked to see the plan first, the run parks after it like a
paused run and carries on when they say go. Builders work side by side in their own folders
(no backend for a static app); the tester runs unit and Playwright tests; the reviewer approves
or sends numbered fixes back to the builders. Release is plain code: it commits what is left,
marks the project ready, saves it to the Library, pushes it to GitHub when the App is set up
(release.py). Then deploy runs it as an app (deploy.py) when the tests passed: approved work,
or work still unapproved after the last round, whose answer lists the open points. A deploy
that fails its health check goes back to the builders once, with the error and logs, before
the user hears about it. Other steps plug in through RELEASE_STEPS.
"""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from nexus_common.devteam import FOLDERS, STACKS
from nexus_orchestrator.deploy import deploy_project
from nexus_orchestrator.github import GitHubError
from nexus_orchestrator.systems import release as releasing
from nexus_orchestrator.systems import suites
from nexus_orchestrator.systems.checkup import checkup, uncommitted
from nexus_orchestrator.systems.compile import SystemState, run_agent, terminal
from nexus_orchestrator.systems.runtime import RunContext, build_agent, final_text
from nexus_orchestrator.workbench import WorkbenchError

log = logging.getLogger(__name__)

# The user wants to see the plan before anything is built.
PLAN_FIRST = re.compile(
    r"\b(plan (it )?first|wait for me|plan only|just (the|a) plan|show me the plan first|"
    r"don'?t (start )?build(ing)? (it )?yet|check (the plan )?with me (first|before))\b",
    re.I,
)
PLAN_CHARS = 6000
# Times a failed deploy goes back to the builders before the user is told.
DEPLOY_RETRIES = 1
SUMMARY_CHARS = 600


class _Brief(BaseModel):
    stack: Literal["static", "web_fastapi", "node"] = Field(description="The house stack")
    summary: str = Field(description="What will be built, in two or three sentences")


class _Plan(BaseModel):
    summary: str = Field(description="The plan in a few lines")


class _Tests(BaseModel):
    passed: int = Field(description="Tests that passed")
    failed: int = Field(description="Tests that failed")
    summary: str = Field(description="What ran and, for each failure, what went wrong")


class _Review(BaseModel):
    approved: bool = Field(description="True when it meets the plan and the tests pass")
    fixes: str = Field(default="", description="Numbered fixes, each with its file and owner")
    summary: str = Field(description="What the app does now, in two or three sentences")


# Release steps after the project is marked ready: each takes the run and what the team did and
# returns facts for the final answer, e.g. {"repo_url": ...} (GitHub) or {"live_url": ...}.
ReleaseStep = Callable[[RunContext, dict], Awaitable[dict]]
RELEASE_STEPS: list[ReleaseStep] = []


def plan_first(request: str) -> bool:
    return bool(PLAN_FIRST.search(request or ""))


def _args(state: dict) -> dict:
    return (state.get("done") or {}).get("args") or {}


def repo_line(release: dict) -> str:
    url = release.get("repo_url")
    if release.get("repo_error"):
        return f"not pushed: {release['repo_error']}"
    if not url:
        return "added here once GitHub is connected"
    if release.get("repo_note"):
        return f"{url} ({release['repo_note']})"
    if release.get("merged"):
        return f"{url} (on main)"
    return f"{url} (on branch {release.get('branch')}, not merged: {release.get('merge_note')})"


def live_line(release: dict) -> str:
    if release.get("live_url"):
        note = " (with the open points below still to fix)" if release.get("preview") else ""
        if release.get("public"):
            return release["live_url"] + note
        return (
            f"{release['live_url']} (private: only you can open it, signed in to Nexus; say "
            f'"make it public" to share it){note}'
        )
    if release.get("live_note"):
        return release["live_note"]
    return "added here once deploys are switched on"


def answer(name: str, dev: dict) -> str:
    """The final chat reply: the answer first, then the facts."""
    tests = dev.get("tests") or {}
    passed, failed = int(tests.get("passed") or 0), int(tests.get("failed") or 0)
    if passed or failed:
        result = f"{passed} passing" + (f", {failed} failing" if failed else "")
        if tests.get("line"):
            result += f" ({tests['line']})"
    else:
        result = "none reported"
    rounds = dev.get("rounds", 1)
    if dev.get("approved"):
        review = f"approved in round {rounds}"
    else:
        review = f"not approved after {rounds} rounds; open points are below"
    release = dev.get("release") or {}
    summary = (dev.get("summary") or dev.get("plan_summary") or "").strip()[:SUMMARY_CHARS]
    lines = [
        f"{name} is built and ready to release."
        if dev.get("approved")
        else f"{name} is built, but the reviewer still wants changes.",
        summary,
        f"- Stack: {STACKS.get(dev.get('stack', ''), 'not chosen')}",
        f"- Tests: {result}",
        f"- Review: {review}",
        f"- Repo: {repo_line(release)}",
        f"- Live link: {live_line(release)}",
    ]
    if not dev.get("approved") and dev.get("fixes"):
        lines.append(f"\nStill to fix:\n{dev['fixes'].strip()[:1500]}")
    return "\n".join(x for x in lines if x)


# What stays at the project root when a front end moves into its own folder.
ROOT_KEEP = ".git .gitignore .nexus BRIEF.md PLAN.md README.md TEST_REPORT.md web api server"


# A static app a builder made in a folder of its own (npm create vite@latest my-app) instead of
# at the project root, where the test setup and the deploy look for it.
HOIST = (
    "test -f package.json || test -f index.html || { "
    'set -- $(for d in */; do d=${d%/}; case "$d" in node_modules|web|api|server|dist|tests) ;; '
    '*) if test -f "$d/package.json" || test -f "$d/index.html"; then echo "$d"; fi ;; '
    "esac; done); test $# -eq 1 && sub=$1 && "
    'ls -A "$sub" | while IFS= read -r f; do case "$f" in '
    ".git|.nexus|BRIEF.md|PLAN.md|TEST_REPORT.md) ;; "
    '*) if test -e "$f"; then git rm -rq --cached "$f" >/dev/null 2>&1; rm -rf "$f"; fi; '
    'if git ls-files --error-unmatch "$sub/$f" >/dev/null 2>&1; then git mv "$sub/$f" "$f"; '
    'else mv "$sub/$f" "$f"; fi ;; esac; done && rm -rf "$sub" && echo "NEXUS_HOISTED $sub"; }'
)


async def hoist(wb, user: str, project: str) -> str | None:
    """Move a static app out of its own subfolder to the project root, where the builders,
    the test setup and the deploy all look. The folder it came from, when it moved."""
    try:
        out = await wb.run(user, project, HOIST)
        if "NEXUS_HOISTED" not in out.stdout:
            return None
        sub = out.stdout.split("NEXUS_HOISTED", 1)[1].strip()
        await wb.commit(user, project, f"Move the app from {sub}/ to the project root")
    except WorkbenchError as exc:
        log.warning("Couldn't move the app of %s to the project root: %s", project, exc)
        return None
    return sub


async def relayout(wb, user: str, project: str, stack: str) -> bool:
    """A static app that gains a back end has its front end at the root, where the new stack's
    builders, test setup and deploy template don't look. Move it into the stack's front-end
    folder first, so every step finds it. True when it moved."""
    front = FOLDERS[stack][0].rstrip("/")
    if front in (".", ""):
        return False
    keep = "|".join(ROOT_KEEP.split())
    move = (
        f"test -f package.json && ! test -e {front}/package.json && mkdir -p {front} && "
        'ls -A | while IFS= read -r f; do case "$f" in '
        f"{keep}) ;; "
        '*) if git ls-files --error-unmatch "$f" >/dev/null 2>&1; '
        f'then git mv "$f" {front}/; else mv "$f" {front}/; fi ;; esac; done && echo NEXUS_MOVED'
    )
    try:
        out = await wb.run(user, project, move)
        if "NEXUS_MOVED" not in out.stdout:
            return False
        await wb.commit(user, project, f"Move the front end into {front}/ for the back end")
    except WorkbenchError as exc:
        log.warning("Couldn't move the front end of %s into %s/: %s", project, front, exc)
        return False
    return True


def add_dev_project(rc: RunContext, g: StateGraph, task: str, request: str) -> None:
    spec = rc.spec
    user, project = str(rc.user.id), rc.project
    wb = rc.deps.workbench
    graphs = {
        "researcher": build_agent(
            rc,
            spec.agent("researcher"),
            [terminal("submit_brief", "Hand over the brief once BRIEF.md is written.", _Brief)],
            final_hint="Call submit_brief.",
        ),
        # A change to an existing project: BRIEF.md already says what the app is, so no web
        # research, only the brief brought up to date.
        "researcher_change": build_agent(
            rc,
            spec.agent("researcher").model_copy(
                update={
                    "tools": [
                        *[t for t in spec.agent("researcher").tools if not t.startswith("web_")],
                        "ws_run",
                    ]
                }
            ),
            [terminal("submit_brief", "Hand over the brief once BRIEF.md is written.", _Brief)],
            final_hint="Call submit_brief.",
        ),
        "architect": build_agent(
            rc,
            spec.agent("architect"),
            [terminal("submit_plan", "Hand over the plan once PLAN.md is written.", _Plan)],
            final_hint="Call submit_plan.",
        ),
        "frontend": build_agent(rc, spec.agent("frontend")),
        "backend": build_agent(rc, spec.agent("backend")),
        "tester": build_agent(
            rc,
            spec.agent("tester"),
            [terminal("submit_tests", "Report the test results.", _Tests)],
            final_hint="Call submit_tests.",
        ),
        "reviewer": build_agent(
            rc,
            spec.agent("reviewer"),
            [terminal("submit_review", "Approve the work or send fixes back.", _Review)],
            final_hint="Call submit_review.",
        ),
    }
    header = f"{task}\n\n<project>Workbench project {project}; all ws_ tools work in it.</project>"

    def stage(n: int, name: str, text: str) -> list[HumanMessage]:
        return [HumanMessage(f"{header}\n\nStage {n} of 6: {name}.\n{text}")]

    async def brief(state: SystemState) -> dict:
        if wb is None:
            raise WorkbenchError("The workbench isn't available.")
        await releasing.set_identity(rc)
        # Where the run started, so the reviewer sees only this run's changes.
        head = await wb.run(user, project, "git rev-parse --short HEAD")
        found = await wb.run(user, project, "test -f BRIEF.md && echo NEXUS_CHANGE")
        change = "NEXUS_CHANGE" in found.stdout
        live_broken = False
        if change:
            left = await uncommitted(wb, user, project)
            moved = await hoist(wb, user, project)
            # What is broken now, measured, before anyone changes anything.
            now = await checkup(rc, left)
            live_broken = now.live_broken
            todo = (
                "This app exists already. First, how it is now, as Nexus found it:\n"
                + (f"(Nexus moved the app from {moved}/ to the project root.)\n" if moved else "")
                + f"{now.report}\n\n"
                "Look at the code and reproduce what the user says is wrong (ws_run: build it, "
                "run its tests). Then bring BRIEF.md up to date for this change only, with a "
                "section 'Problems found' listing what is broken now and must work after this "
                "run, each with its cause when you found it. Keep the stack unless the change "
                "needs a back end. No web research."
            )
        else:
            todo = "Write BRIEF.md and pick the stack."
        researcher = {"researcher": graphs["researcher_change" if change else "researcher"]}
        st = await run_agent(rc, researcher, "researcher", stage(1, "Brief", todo))
        args = _args(st)
        stack = args.get("stack") if args.get("stack") in STACKS else "static"
        summary = str(args.get("summary") or final_text(st))
        if await relayout(wb, user, project, stack):
            summary += f"\n(Nexus moved the existing front end into {FOLDERS[stack][0]}.)"
        return {
            "outputs": {"researcher": summary},
            "dev": {
                "stack": stack,
                "brief": summary,
                "base": head.stdout.strip() or "HEAD",
                "change": change,
                "live_broken": live_broken,
            },
        }

    async def plan(state: SystemState) -> dict:
        dev = state.get("dev") or {}
        st = await run_agent(
            rc,
            graphs,
            "architect",
            stage(
                2,
                "Plan",
                f"The researcher chose the {dev['stack']} stack: {dev.get('brief', '')}\n"
                "Write PLAN.md.",
            ),
        )
        summary = str(_args(st).get("summary") or final_text(st))
        try:
            text = (await wb.read(user, project, "PLAN.md"))["content"].strip()
        except WorkbenchError:
            text = summary
        if len(text) > PLAN_CHARS:
            text = text[:PLAN_CHARS] + "\n\n… (the rest is in PLAN.md)"
        wait = plan_first(request)
        tail = (
            "Reply **go** when you're happy with it, or tell me what to change."
            if wait
            else "Building it now; I'll post the result here."
        )
        await rc.post(f"**Plan for {spec.name}**\n\n{text}\n\n{tail}")
        if wait:
            await rc.pause()
        return {"outputs": {"architect": summary}, "dev": {"plan_summary": summary, "wait": wait}}

    async def plan_gate(state: SystemState) -> dict:
        # Its own node, so resuming re-runs only this and not the architect.
        if (state.get("dev") or {}).get("wait"):
            interrupt({"kind": "pause"})
        return {}

    async def build(state: SystemState) -> dict:
        """Start a build round; the builders then run as their own nodes, so an approval or a
        pause in one of them parks and resumes just that builder."""
        dev = state.get("dev") or {}
        rounds = dev.get("rounds", 0) + 1
        if rounds == 1 and FOLDERS[dev["stack"]][1] is None:
            await rc.agent_status("backend", "skipped")
        return {"dev": {"rounds": rounds}}

    def builders(state: SystemState) -> list[str]:
        stack = (state.get("dev") or {})["stack"]
        return ["frontend", "backend"] if FOLDERS[stack][1] else ["frontend"]

    def builder(agent_id: str):
        async def node(state: SystemState) -> dict:
            dev = state.get("dev") or {}
            rounds = dev["rounds"]
            folder = FOLDERS[dev["stack"]][0 if agent_id == "frontend" else 1]
            fixes = dev.get("fixes", "") if rounds > 1 else ""
            todo = (
                f"Round {rounds}: the reviewer asked for these fixes; make the ones for you:\n"
                f"{fixes}"
                if fixes
                else "Build your part of PLAN.md."
            )
            tests = dev.get("tests") or {}
            if fixes and int(tests.get("failed") or 0):
                todo += f"\n\nThe last test run:\n{tests.get('summary', '')[:3000]}"
            st = await run_agent(
                rc,
                graphs,
                agent_id,
                stage(3, "Build", f"Your folder: {folder}\n{todo}"),
                item=f"round {rounds}",
            )
            return {"outputs": {agent_id: final_text(st)}}

        return node

    async def test(state: SystemState) -> dict:
        dev = state.get("dev") or {}
        built = "\n\n".join(
            f'<output from="{a}">\n{(state.get("outputs") or {}).get(a, "")}\n</output>'
            for a in ("frontend", "backend")
            if (state.get("outputs") or {}).get(a)
        )
        if dev["stack"] == "static":
            await hoist(wb, user, project)
        # The house test setup first, so the tester writes tests rather than fixing runners.
        wired = await suites.wire(wb, user, project, dev["stack"])
        setup = (
            "The test runners are set up and installed (don't change their configs or "
            "versions):\n" + "\n".join(f"- {x}" for x in wired) + "\n"
            if wired
            else ""
        )
        if dev.get("rounds", 1) > 1 and dev.get("fixes"):
            setup += (
                "The reviewer's fixes from the last round; make the ones for the tester or about "
                f"test code:\n{dev['fixes']}\n"
            )
        st = await run_agent(
            rc,
            graphs,
            "tester",
            stage(4, "Test", f"What the builders said:\n{built}\n{setup}Test it."),
            item=f"round {dev.get('rounds', 1)}",
        )
        args = _args(st)
        tests = {
            "passed": int(args.get("passed") or 0),
            "failed": int(args.get("failed") or 0),
            "summary": str(args.get("summary") or final_text(st)),
        }
        # Then Nexus runs every suite itself: these counts are what the reviewer, the deploy
        # gate and the answer go by. The tester's own counts stand only when nothing ran.
        measured = await suites.run_suites(wb, user, project, dev["stack"])
        if measured.ran:
            tests.update(
                passed=measured.passed,
                failed=measured.failed,
                missing=[x.name for x in measured.suites if not x.ran],
                line=measured.line(),
                summary=f"{measured.report()}\n\nThe tester said: {tests['summary']}",
            )
        return {"outputs": {"tester": tests["summary"]}, "dev": {"tests": tests}}

    async def review(state: SystemState) -> dict:
        dev = state.get("dev") or {}
        rounds = dev.get("rounds", 1)
        tests = dev.get("tests") or {}
        st = await run_agent(
            rc,
            graphs,
            "reviewer",
            stage(
                5,
                "Review",
                f"Review round {rounds} of {spec.max_rounds}. See the changes with "
                f'ws_diff(ref="{dev.get("base", "HEAD")}"). Test report: {tests.get("passed", 0)} '
                f"passed, {tests.get('failed', 0)} failed. {tests.get('summary', '')}"
                + (
                    "\nThis run changes an existing app: every item under 'Problems found' in "
                    "BRIEF.md must work now; send back any that doesn't."
                    if dev.get("change")
                    else ""
                ),
            ),
            item=f"round {rounds}",
        )
        args = _args(st)
        approved = bool(args.get("approved", (st.get("done") or {}).get("tool") != "submit_review"))
        fixes = str(args.get("fixes") or "")
        summary = str(args.get("summary") or final_text(st))
        return {
            "outputs": {"reviewer": summary if approved else fixes or summary},
            "dev": {"approved": approved, "fixes": fixes, "summary": summary},
        }

    def after_review(state: SystemState) -> str:
        dev = state.get("dev") or {}
        if dev.get("approved") or dev.get("rounds", 1) >= spec.max_rounds:
            return "release"
        return "build"

    async def release(state: SystemState) -> dict:
        dev = dict(state.get("dev") or {})
        commit = None
        try:
            done = await wb.commit(user, project, f"Ready for release: {spec.name}")
            commit = done.get("commit")
            if not commit:
                # The builders already committed everything: the release is what's there.
                head = await wb.run(user, project, "git rev-parse --short HEAD")
                commit = head.stdout.strip() or None
        except WorkbenchError as exc:
            log.warning("Couldn't commit the release of run %s: %s", rc.run_id, exc)
        await rc.record(
            "project.ready",
            None,
            project=project,
            stack=dev.get("stack"),
            commit=commit,
            approved=bool(dev.get("approved")),
        )
        facts: dict = {"project": project, "commit": commit}
        row = await releasing.save_project(rc, dev)
        facts["project_id"] = str(row.id)
        if rc.deps.github is not None:
            try:
                facts.update(await releasing.publish(rc, dev))
            except (GitHubError, WorkbenchError) as exc:
                log.warning("Couldn't publish run %s to GitHub: %s", rc.run_id, exc)
                facts["repo_error"] = str(exc)
        for step in RELEASE_STEPS:
            try:
                facts.update(await step(rc, dev) or {})
            except Exception:
                log.exception("Release step %s failed for run %s", step.__name__, rc.run_id)
        return {"dev": {"release": facts}}

    async def deploy(state: SystemState) -> dict:
        dev = dict(state.get("dev") or {})
        facts = dict(dev.get("release") or {})
        # Green tests put the app up: every suite Nexus expects ran and passed. Work the reviewer
        # hasn't approved only gets here after the last round; it goes up so the user can try
        # it, but never over an app that's already live and working, which keeps the last good
        # version. A live app the checkup found broken is no good version to keep.
        # main still moves only on approval (release.py).
        tests = dev.get("tests") or {}
        missing = list(tests.get("missing") or [])
        if rc.deps.deployer is None:
            pass  # Deploys are off here; the answer says the link comes later.
        elif not releasing.passed(dev):
            facts["live_note"] = (
                "not deployed, as not every test passed"
                if int(tests.get("failed") or 0)
                else "not deployed, as no tests ran"
            )
        elif missing:
            facts["live_note"] = f"not deployed, as these tests didn't run: {', '.join(missing)}"
        elif (
            not dev.get("approved")
            and not dev.get("live_broken")
            and await releasing.is_live(rc, uuid.UUID(facts["project_id"]))
        ):
            facts["live_note"] = (
                "not updated: the live app keeps the last version, since this one still has "
                "open points"
            )
        else:
            result = await deploy_project(
                rc.deps, uuid.UUID(facts["project_id"]), run_id=rc.run_id, chat_id=rc.chat_id
            )
            if result.ok:
                facts["live_url"] = result.url
                facts["public"] = await releasing.is_public(rc, uuid.UUID(facts["project_id"]))
                facts["preview"] = not dev.get("approved")
            elif dev.get("deploy_retries", 0) < DEPLOY_RETRIES and result.logs:
                # Back to the builders with what went wrong; the user hears only the outcome.
                fixes = (
                    f"The deploy failed: {result.error}\nFix the app so it builds and starts "
                    f"with the house template for the {dev.get('stack')} stack.\n"
                    f"Logs:\n{result.logs[-3000:]}"
                )
                return {
                    "dev": {
                        "deploy_retries": dev.get("deploy_retries", 0) + 1,
                        "approved": False,
                        "fixes": fixes,
                        "redeploy": True,
                    }
                }
            else:
                facts["live_note"] = f"not deployed: {result.error}"
        dev["release"] = facts
        return {"dev": {"release": facts, "redeploy": False}, "final": answer(spec.name, dev)}

    def after_deploy(state: SystemState) -> str:
        return "build" if (state.get("dev") or {}).get("redeploy") else END

    for name, fn in [
        ("brief", brief),
        ("plan", plan),
        ("plan_gate", plan_gate),
        ("build", build),
        ("frontend", builder("frontend")),
        ("backend", builder("backend")),
        ("test", test),
        ("review", review),
        ("release", release),
        ("deploy", deploy),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "brief")
    g.add_edge("brief", "plan")
    g.add_edge("plan", "plan_gate")
    g.add_edge("plan_gate", "build")
    g.add_conditional_edges("build", builders, ["frontend", "backend"])
    # Builders that ran side by side finish in the same step, so the tester runs once.
    g.add_edge("frontend", "test")
    g.add_edge("backend", "test")
    g.add_edge("test", "review")
    g.add_conditional_edges("review", after_review, ["build", "release"])
    g.add_edge("release", "deploy")
    g.add_conditional_edges("deploy", after_deploy, ["build", END])
