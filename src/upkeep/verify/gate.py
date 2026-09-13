"""The verification gate.

Nothing reaches a pull request unverified. This is both the differentiator and
the existential risk: a developer forgives a patch that fails CI and uninstalls
forever over one that passed CI and broke production.

v0 runs the two cheap checks — it compiles, and the existing test suite still
passes, unchanged. Contract replay against recorded fixtures is the next layer
and is deliberately not here yet; until it exists, Tier B stays unimplemented,
because a model-written patch without replay is exactly what this gate is for.

Note the third check: a patch that had to edit tests to go green is not a
success, it is a downgrade signal.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from upkeep.models import CheckResult, Confidence, GateResult, Tier, WorkItem

TIMEOUT_SECONDS = 120


def _run(command: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_SECONDS}s"
    except FileNotFoundError as exc:
        return False, str(exc)

    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode == 0, output[-2000:]


def check_compiles(repo: Path) -> CheckResult:
    passed, detail = _run(
        [sys.executable, "-m", "compileall", "-q", "."], repo
    )
    return CheckResult(name="compiles", passed=passed, detail="" if passed else detail)


def check_tests(repo: Path) -> CheckResult:
    passed, detail = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(repo),
        ],
        repo,
    )
    return CheckResult(name="tests", passed=passed, detail="" if passed else detail)


def check_tests_untouched(patched_files: list[str]) -> CheckResult:
    """A patch that rewrote tests to pass has not proven anything."""
    touched = [f for f in patched_files if Path(f).name.startswith(("test_", "conftest"))]
    return CheckResult(
        name="tests_untouched",
        passed=not touched,
        detail="" if not touched else f"patch modified test files: {touched}",
    )


def run_gate(
    repo: Path | str, items: list[WorkItem], patched_files: list[str]
) -> GateResult:
    repo = Path(repo)
    checks = [
        check_compiles(repo),
        check_tests(repo),
        check_tests_untouched(patched_files),
    ]
    result = GateResult(checks=checks)

    tiers = {item.tier for item in items if item.tier is not Tier.C}
    if not result.passed:
        result.confidence = Confidence.low
    elif Tier.B in tiers:
        result.confidence = Confidence.medium
    else:
        result.confidence = Confidence.high
    return result
