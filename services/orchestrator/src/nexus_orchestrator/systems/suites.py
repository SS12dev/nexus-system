"""The dev team's house test setup, and the test runs Nexus does itself.

Teams kept breaking their own test wiring: Vitest picking up the Playwright spec, a second
Playwright version, pytest missing from the back end. So before the tester starts, plain code
gives the project a known-good setup (wire): Vitest and Playwright configs, npm scripts and
pinned dev dependencies in the front end, pytest in a FastAPI back end. After the tester, plain
code runs every suite (run_suites) and counts the results from the runners' own reports, so the
reviewer, the deploy gate and the final answer use measured numbers, not what an agent said.
"""

import logging
import re
from dataclasses import dataclass, field

from nexus_common.devteam import FOLDERS
from nexus_orchestrator.workbench import WorkbenchError

log = logging.getLogger(__name__)

YES = "NEXUS_YES"
COUNTS = re.compile(r"NEXUS_COUNTS (\d+) (\d+)")
NODE_PASS = re.compile(r"^#\s*pass\s+(\d+)", re.M)
NODE_FAIL = re.compile(r"^#\s*fail\s+(\d+)", re.M)
SETUP_TIMEOUT_S = 600
SUITE_TIMEOUT_S = 600
TAIL_CHARS = 1500
HOUSE = "Nexus house test setup: Nexus rewrites this file before each test run, so don't edit it."

VITEST_CONFIG = f"""// {HOUSE}
// Unit tests: src/**/*.test.ts(x) and tests/unit/. Playwright specs in tests/e2e/ never run here.
import {{ defineConfig, mergeConfig }} from 'vitest/config'
__IMPORT__
export default mergeConfig(
  __BASE__,
  defineConfig({{
    test: {{
      include: [
        'src/**/*.{{test,spec}}.{{ts,tsx,js,jsx}}',
        'tests/unit/**/*.{{test,spec}}.{{ts,tsx,js,jsx}}',
      ],
      exclude: ['tests/e2e/**', 'node_modules/**', 'dist/**'],
      environment: 'jsdom',
    }},
  }}),
)
"""

PREVIEW_CONFIG = f"""// {HOUSE}
// Serves the built app for the Playwright smoke test, with /api sent to the back end.
import {{ defineConfig, mergeConfig }} from 'vite'
__IMPORT__
export default mergeConfig(
  __BASE__,
  defineConfig({{
    preview: {{
      host: '127.0.0.1',
      port: 4173,
      strictPort: true,
      proxy: {{ '/api': 'http://127.0.0.1:8000' }},
    }},
  }}),
)
"""

PLAYWRIGHT_CONFIG = f"""// {HOUSE}
// The smoke test: tests/e2e/*.spec.ts against the built app (and the back end, if there is one).
import {{ defineConfig, devices }} from '@playwright/test'

export default defineConfig({{
  testDir: 'tests/e2e',
  retries: 0,
  use: {{ baseURL: 'http://127.0.0.1:4173', ...devices['Desktop Chrome'] }},
  webServer: [
__SERVERS__
  ],
}})
"""

APP_SERVER = """    {
      command: 'npm run build && npx vite preview --config preview.config.ts',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
      timeout: 180_000,
    },"""

BACKEND_SERVER = {
    "web_fastapi": "cd ../api && uv run uvicorn main:app --host 127.0.0.1 --port 8000",
    "node": "cd ../server && PORT=8000 npm start",
}

PYTEST_INI = f"""# {HOUSE}
[pytest]
testpaths = tests
pythonpath = .
"""

IGNORED = (".nexus/", "test-results/", "playwright-report/")


@dataclass
class Suite:
    name: str
    passed: int = 0
    failed: int = 0
    ran: bool = True
    note: str = ""


@dataclass
class Results:
    suites: list[Suite] = field(default_factory=list)

    @property
    def ran(self) -> list[Suite]:
        return [s for s in self.suites if s.ran]

    @property
    def passed(self) -> int:
        return sum(s.passed for s in self.ran)

    @property
    def failed(self) -> int:
        return sum(s.failed for s in self.ran)

    def line(self) -> str:
        """e.g. "unit 6 passed; smoke 1 failed; backend 3 passed"."""
        parts = []
        for s in self.suites:
            if not s.ran:
                parts.append(f"{s.name}: {s.note or 'none'}")
                continue
            got = f"{s.passed} passed" + (f", {s.failed} failed" if s.failed else "")
            parts.append(f"{s.name} {got}")
        return "; ".join(parts)

    def report(self) -> str:
        """For the reviewer and the builders: counts, then the output of each failing suite."""
        out = [f"Nexus ran the suites itself: {self.line() or 'nothing to run'}."]
        for s in self.suites:
            if s.ran and s.failed and s.note:
                out.append(f"\n{s.name} output (end):\n{s.note}")
        return "\n".join(out)


def _dir(folder: str) -> str:
    return "." if folder in (".", "") else folder.rstrip("/")


def _path(folder: str, name: str) -> str:
    d = _dir(folder)
    return name if d == "." else f"{d}/{name}"


def _with_base(template: str, vite_config: str | None) -> str:
    if vite_config:
        imp = f"import viteConfig from './{vite_config}'"
        base = (
            "typeof viteConfig === 'function'\n"
            "    ? viteConfig({ command: 'serve', mode: 'test' })\n"
            "    : viteConfig"
        )
    else:
        imp, base = "", "{}"
    return template.replace("__IMPORT__", imp).replace("__BASE__", base)


async def _yes(wb, user: str, project: str, command: str) -> str | None:
    """stdout after the marker when the command printed it, else None."""
    try:
        out = await wb.run(user, project, f"{command} && echo {YES}")
    except WorkbenchError:
        return None
    if YES not in out.stdout:
        return None
    return out.stdout.split(YES, 1)[0].strip()


async def wire(wb, user: str, project: str, stack: str) -> list[str]:
    """Give the project the house test setup. Returns what was set up, for the tester."""
    front, back = FOLDERS.get(stack, (".", None))
    done: list[str] = []
    d = _dir(front)
    found = await _yes(wb, user, project, f"cd {d} && test -f package.json && ls vite.config.*")
    if found is not None:
        names = [n for n in found.split() if n.startswith("vite.config.")]
        vite_config = next((n for n in names if n.endswith(".ts")), names[0] if names else None)
        servers = APP_SERVER
        if back and stack in BACKEND_SERVER:
            servers = (
                "    {\n"
                f"      command: '{BACKEND_SERVER[stack]}',\n"
                "      url: 'http://127.0.0.1:8000/api/health',\n"
                "      reuseExistingServer: false,\n"
                "      timeout: 120_000,\n"
                "    },\n" + APP_SERVER
            )
        files = {
            "vitest.config.ts": _with_base(VITEST_CONFIG, vite_config),
            "preview.config.ts": _with_base(PREVIEW_CONFIG, vite_config),
            "playwright.config.ts": PLAYWRIGHT_CONFIG.replace("__SERVERS__", servers),
        }
        try:
            for name, content in files.items():
                await wb.write(user, project, _path(front, name), content)
            setup = (
                f"cd {d} && npm pkg set 'scripts.test=vitest run' "
                "'scripts.test:e2e=playwright test' && "
                "{ [ -d node_modules/vitest ] && [ -d node_modules/jsdom ] && "
                '[ "$(node -p "require(\'@playwright/test/package.json\').version" 2>/dev/null)" '
                '= "$PLAYWRIGHT_VERSION" ] || npm install -D --no-fund --no-audit vitest jsdom '
                '"@playwright/test@$PLAYWRIGHT_VERSION"; }'
            )
            out = await wb.run(user, project, setup, timeout_s=SETUP_TIMEOUT_S)
            if out.exit_code == 0:
                done.append(
                    f"{d}: `npm test` runs Vitest (unit tests in src/**/*.test.ts(x)), "
                    "`npm run test:e2e` runs the Playwright smoke test in tests/e2e/ against "
                    "the built app"
                )
            else:
                log.warning("Test setup failed in %s/%s: %s", project, d, out.stderr[-500:])
        except WorkbenchError as exc:
            log.warning("Couldn't wire the front-end tests of %s: %s", project, exc)
    if back and stack == "web_fastapi":
        b = _dir(back)
        if await _yes(wb, user, project, f"cd {b} && test -f pyproject.toml") is not None:
            try:
                await wb.write(user, project, _path(back, "pytest.ini"), PYTEST_INI)
                out = await wb.run(
                    user,
                    project,
                    f"cd {b} && {{ uv run --quiet python -c 'import pytest, httpx' 2>/dev/null "
                    "|| uv add --dev pytest httpx; }",
                    timeout_s=SETUP_TIMEOUT_S,
                )
                if out.exit_code == 0:
                    done.append(f"{b}: `uv run pytest` runs the tests in {b}/tests/test_*.py")
            except WorkbenchError as exc:
                log.warning("Couldn't wire the back-end tests of %s: %s", project, exc)
    if done:
        ignore = " ".join(
            f"grep -qxF '{x}' .gitignore 2>/dev/null || echo '{x}' >> .gitignore;" for x in IGNORED
        )
        try:
            await wb.run(user, project, ignore)
        except WorkbenchError:
            pass
    return done


def _counts(out) -> tuple[int, int] | None:
    m = COUNTS.findall(out.stdout or "")
    if m:
        return int(m[-1][0]), int(m[-1][1])
    text = f"{out.stdout}\n{out.stderr}"
    p, f = NODE_PASS.findall(text), NODE_FAIL.findall(text)
    if p or f:
        return int(p[-1]) if p else 0, int(f[-1]) if f else 0
    return None


def _tail(out) -> str:
    text = "\n".join(x for x in (out.stdout, out.stderr) if x).strip()
    return text[-TAIL_CHARS:]


async def _suite(wb, user: str, project: str, name: str, guard: str, command: str) -> Suite:
    """Run one suite when its guard passes; a suite that broke before counting is 1 failure."""
    if await _yes(wb, user, project, guard) is None:
        return Suite(name, ran=False, note="no tests")
    try:
        out = await wb.run(user, project, command, timeout_s=SUITE_TIMEOUT_S)
    except WorkbenchError as exc:
        return Suite(name, failed=1, note=str(exc))
    counts = _counts(out)
    passed, failed = counts or (0, 0)
    if out.timed_out or (out.exit_code != 0 and failed == 0):
        failed = max(failed, 1)
    return Suite(name, passed, failed, note=_tail(out) if failed else "")


async def run_suites(wb, user: str, project: str, stack: str) -> Results:
    front, back = FOLDERS.get(stack, (".", None))
    d = _dir(front)
    results = Results()
    unit_json = (
        "node -e \"try{const r=require('./.nexus/unit.json');"
        "console.log('NEXUS_COUNTS',r.numPassedTests,r.numFailedTests)}catch(e){}\""
    )
    results.suites.append(
        await _suite(
            wb,
            user,
            project,
            "unit",
            f"cd {d} && test -f vitest.config.ts && test -d node_modules/vitest",
            f"cd {d} && rm -f .nexus/unit.json; npm test --silent -- --reporter=default "
            f"--reporter=json --outputFile.json=.nexus/unit.json 2>&1; code=$?; {unit_json}; "
            "exit $code",
        )
    )
    e2e_json = (
        "node -e \"try{const s=require('./.nexus/e2e.json').stats;"
        "console.log('NEXUS_COUNTS',s.expected+s.flaky,s.unexpected)}catch(e){}\""
    )
    results.suites.append(
        await _suite(
            wb,
            user,
            project,
            "smoke",
            f"cd {d} && test -f playwright.config.ts && ls tests/e2e/*.spec.* >/dev/null 2>&1",
            f"cd {d} && mkdir -p .nexus && rm -f .nexus/e2e.json; "
            "PLAYWRIGHT_JSON_OUTPUT_NAME=.nexus/e2e.json npx playwright test "
            f"--reporter=line,json 2>&1; code=$?; {e2e_json}; exit $code",
        )
    )
    if back and stack == "web_fastapi":
        b = _dir(back)
        xml = (
            'uv run --quiet python -c "import xml.etree.ElementTree as E;'
            "r=E.parse('.nexus/pytest.xml').getroot();s=r if r.tag=='testsuite' else r[0];"
            "g=lambda k:int(s.get(k) or 0);f=g('failures')+g('errors');"
            "print('NEXUS_COUNTS',g('tests')-f-g('skipped'),f)\" 2>/dev/null"
        )
        results.suites.append(
            await _suite(
                wb,
                user,
                project,
                "backend",
                f"cd {b} && test -f pyproject.toml && ls tests/test_*.py >/dev/null 2>&1",
                f"cd {b} && mkdir -p .nexus && rm -f .nexus/pytest.xml; uv run pytest -q "
                f"-p no:cacheprovider --junitxml=.nexus/pytest.xml 2>&1; code=$?; {xml}; "
                "exit $code",
            )
        )
    elif back:
        b = _dir(back)
        results.suites.append(
            await _suite(
                wb,
                user,
                project,
                "backend",
                f"cd {b} && test -f package.json && npm pkg get scripts.test | grep -q '\"'",
                f"cd {b} && npm test --silent 2>&1",
            )
        )
    return results
