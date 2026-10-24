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
