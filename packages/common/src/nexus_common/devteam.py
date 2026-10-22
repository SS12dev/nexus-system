"""The dev_project team: Nexus's fixed team for building, fixing or extending software in one
workbench project. The architect only names the project, states the goal and says whether it
continues an earlier project; the agents, their tools and the stages are always these:

    Brief (researcher) -> Plan (architect) -> Build (frontend, backend) -> Test (tester)
        -> Review (reviewer) -> back to Build with fixes, at most three rounds -> Release
"""

DEV_IDS = ("researcher", "architect", "frontend", "backend", "tester", "reviewer")
STAGES = {
    "researcher": "Brief",
    "architect": "Plan",
    "frontend": "Build",
    "backend": "Build",
    "tester": "Test",
    "reviewer": "Review",
}
# The house stacks the researcher picks from, and what each is called in the final reply.
STACKS = {
    "static": "static web app with Vite",
    "web_fastapi": "web app with a Vite front end and a FastAPI back end",
    "node": "web app with a Vite front end and a Node (Express) back end",
}
# Each stack's folders: the frontend builder's and the backend builder's (None: no backend).
FOLDERS = {
    "static": (".", None),
    "web_fastapi": ("web/", "api/"),
    "node": ("web/", "server/"),
}
MAX_ROUNDS = 3
MAX_STEPS = 40
# Tools every agent of the team needs; without ws_run (the exec capability) it can't work.
NEEDED_TOOLS = {"ws_tree", "ws_read", "ws_write", "ws_edit", "ws_run", "ws_diff", "ws_commit"}

_BUILD_TOOLS = ["ws_tree", "ws_read", "ws_write", "ws_edit", "ws_run", "ws_diff", "ws_commit"]

_STACK_LIST = (
    "static (Vite with TypeScript, no server: games, tools, pages without stored data), "
    "web_fastapi (Vite front end in web/, FastAPI back end in api/ run with uv: when it needs "
    "a server, stored data or secrets kept off the page), or node (Vite front end in web/, "
    "Express back end in server/)"
)


def dev_agents() -> list[dict]:
    """The team, as AgentSpec dicts."""
    return [
        {
            "id": "researcher",
            "name": "Researcher",
            "role": "researcher",
            "tier": "mini",
            "tools": ["web_search", "web_fetch", "ws_tree", "ws_read", "ws_write"],
            "max_steps": MAX_STEPS,
            "instructions": (
                "Work out what to build. If BRIEF.md already exists, this is a change to an "
                "existing project: read BRIEF.md and PLAN.md, keep the stack, and add the change "
                "to the brief. Otherwise look up how two or three similar apps work (a few "
                f"searches, no more) and pick one house stack: {_STACK_LIST}. Write BRIEF.md: "
                "what the app does, its key behaviours, the stack and why, and the references "
                "you used. Then call submit_brief."
            ),
        },
        {
            "id": "architect",
            "name": "Architect",
            "role": "planner",
            "tier": "pro",
            "tools": ["ws_tree", "ws_read", "ws_write"],
            "max_steps": MAX_STEPS,
            "instructions": (
                "Read BRIEF.md (and PLAN.md if there is one: then plan only the change, and keep "
                "the rest). Write PLAN.md with four sections: Features; File layout (static: the "
                "app at the project root; web_fastapi: web/ and api/; node: web/ and server/); "
                "Acceptance criteria, numbered and each one testable; Test plan: unit tests for "
                "the logic and one Playwright smoke test that loads the page, does the main "
                "interaction and checks there are no console errors. Keep it short enough to "
                "read in a minute; the user sees it. Then call submit_plan."
            ),
        },
        {
            "id": "frontend",
            "name": "Frontend builder",
            "role": "developer",
            "tier": "mini",
            "tools": [*_BUILD_TOOLS, "web_fetch"],
            "max_steps": MAX_STEPS,
            "instructions": (
                "Build the front end PLAN.md describes, in your folder only, with Vite and "
                "TypeScript (npm create vite@latest, then npm install). Make npm run build pass "
                "and write its output to dist/ before you stop; it is deployed from there. Call "
                "the back end on the same host under /api (fetch('/api/...')). Commit with "
                "ws_commit after each piece that works. When you are given review fixes, make "
                "exactly those. Reply with what you built and how to run it."
            ),
        },
        {
            "id": "backend",
            "name": "Backend builder",
            "role": "developer",
            "tier": "mini",
            "tools": [*_BUILD_TOOLS, "web_fetch"],
            "max_steps": MAX_STEPS,
            "instructions": (
                "Build the back end PLAN.md describes, in your folder only: FastAPI with uv "
                "(uv init, uv add fastapi uvicorn; app = FastAPI() in main.py, listening on 8000) "
                "for web_fastapi, Express for node (npm start runs it on process.env.PORT). Put "
                "every route under /api, with /api/health answering 200, and check it starts. "
                "Keep its dependencies installed (.venv or node_modules): deploys use them. Commit "
                "with ws_commit after each piece that works. When you are given review fixes, "
                "make exactly those. Reply with what you built and how to run it."
            ),
        },
        {
            "id": "tester",
            "name": "Tester",
            "role": "tester",
            "tier": "mini",
            "tools": list(_BUILD_TOOLS),
            "max_steps": MAX_STEPS,
            "instructions": (
                "Test the app against PLAN.md's test plan and acceptance criteria. Write unit "
                "tests for the logic (Vitest for TypeScript, pytest for Python) and one "
                "Playwright smoke test in tests/e2e/: add @playwright/test at the version in "
                "$PLAYWRIGHT_VERSION, build or start the app and serve it in the same ws_run "
                "command as the test, and check the page loads, the main interaction works and "
                "nothing is logged to the console as an error. Run everything. Fix test code, "
                "not app code: report app bugs instead. Write TEST_REPORT.md with what ran and "
                "what failed, commit, then call submit_tests with the counts."
            ),
        },
        {
            "id": "reviewer",
            "name": "Reviewer",
            "role": "reviewer",
            "tier": "pro",
            "tools": ["ws_tree", "ws_read", "ws_diff"],
            "max_steps": MAX_STEPS,
            "instructions": (
                "Review the work: read PLAN.md and TEST_REPORT.md, and the changes with ws_diff "
                "from the commit you are given. Approve when every acceptance criterion is met "
                "and the tests pass. Otherwise list specific fixes, numbered, each naming the "
                "file and who fixes it (frontend or backend); leave out style preferences. "
                "Call submit_review."
            ),
        },
    ]
