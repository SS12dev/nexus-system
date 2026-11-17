"""Counting test results from the runners' own output."""

from nexus_orchestrator.systems import suites
from nexus_orchestrator.workbench import CommandResult


def out(stdout: str, code: int = 0, timed_out: bool = False) -> CommandResult:
    return CommandResult(code, stdout, "", timed_out, 1)


def test_counts_from_the_marker_then_node_test():
    assert suites._counts(out("Tests 2 passed\nNEXUS_COUNTS 2 1\n")) == (2, 1)
    assert suites._counts(out("# tests 3\n# pass 2\n# fail 1\n")) == (2, 1)
    assert suites._counts(out("Error: Cannot find module 'vitest'")) is None


async def test_a_suite_that_breaks_before_counting_is_a_failure():
    class Wb:
        async def run(self, user, project, command, timeout_s=None):
            if "NEXUS_YES" in command:
                return out("NEXUS_YES\n")
            return out("Error: Playwright Test did not expect test() to be called here.", 1)

    suite = await suites._suite(Wb(), "u", "p", "smoke", "true", "npx playwright test")
    assert (suite.passed, suite.failed, suite.ran) == (0, 1, True)
    assert "test() to be called here" in suite.note


def test_results_line_and_report():
    r = suites.Results(
        [
            suites.Suite("unit", 5),
            suites.Suite("smoke", 0, 1, note="timeout"),
            suites.Suite("backend", ran=False, note="no tests"),
        ]
    )
    assert (r.passed, r.failed) == (5, 1)
    assert r.line() == "unit 5 passed; smoke 0 passed, 1 failed; backend: no tests"
    assert "smoke output (end):\ntimeout" in r.report()


class GateWb:
    """A web_fastapi project: answers the guards, then each check with its tool's summary."""

    def __init__(self, outputs: dict[str, str]):
        self.outputs = outputs
        self.commands: list[str] = []

    async def run(self, user, project, command, timeout_s=None):
        self.commands.append(command)
        if "NEXUS_YES" in command:
            return out("NEXUS_YES\n")
        for key, text in self.outputs.items():
            if key in command:
                return out(text)
        return out("")


async def test_gates_count_problems_from_each_tool():
    wb = GateWb(
        {
            "tsc --noEmit": "src/a.ts(1,7): error TS2322: nope\nNEXUS_GATE 1\n",
            "biome lint": "src/a.ts:3 warning Unexpected any\nNEXUS_GATE 0 1\n",
            "npm audit": "NEXUS_GATE 0 2\n",
            "ruff check": "main.py:5:5: S602 shell=True\nNEXUS_GATE 1\n",
            "pip-audit": "jinja2: PYSEC-1\nNEXUS_GATE 1\n",
        }
    )
    gates = await suites.run_gates(wb, "u", "p", "web_fastapi")
    assert [g.name for g in gates.ran] == [
        "types", "lint", "dependency audit", "backend lint", "backend dependency audit",
    ]  # fmt: skip
    assert gates.problems == 3
    assert gates.line() == (
        "types 1 problem; lint 1 warning; dependency audit 2 warnings; backend lint 1 problem; "
        "backend dependency audit 1 problem"
    )
    report = gates.report()
    assert "error TS2322" in report and "S602" in report and "PYSEC-1" in report


async def test_a_check_that_cannot_run_is_left_out_not_failed():
    class Wb:
        async def run(self, user, project, command, timeout_s=None):
            if "NEXUS_YES" in command:
                return out("")  # no guard passes: nothing to check
            raise AssertionError("no check should run")

    gates = await suites.run_gates(Wb(), "u", "p", "static")
    assert gates.ran == [] and gates.problems == 0 and gates.report() == ""
    # A tool that crashed before printing its count is left out too.
    broken = await suites._gate(GateWb({"x": "Error: boom"}), "u", "p", "lint", "true", "x")
    assert not broken.ran


async def test_screenshots_list_the_pictures_for_the_reviewer():
    class Wb:
        async def run(self, user, project, command, timeout_s=None):
            assert "shots.config.ts" in command
            return out(".nexus/shots/laptop.jpg\n.nexus/shots/phone.jpg\nNEXUS_YES\n")

    shots = await suites.screenshots(Wb(), "u", "p", "web_fastapi")
    assert shots == ["web/.nexus/shots/laptop.jpg", "web/.nexus/shots/phone.jpg"]
