"""The quality gate for the Python half of the port.

One command, the same one CI runs, in increasing order of cost: parse, lint, tests.
The reasoning is scripts/Invoke-Tests.ps1's and is not repeated here - a suite nobody
can run in one step is a suite nobody runs.

Two gates exist during the port, not one, and that is a deliberate and temporary cost.
ADR 0006 keeps both implementations alive until parity is proven, and the PowerShell
gate cannot be the Python one: it needs PowerShell, and requiring PowerShell on a Linux
agent to test a tool whose whole point is running there without it would be an odd
thing to write down. When the PowerShell implementation is deleted, Invoke-Tests.ps1
goes with it and this file is the gate.

What this gate does NOT run, so that a green line here is not read as more than it is:
the sensitive data scan. That lives in scripts/Test-NoSensitiveData.ps1, is 525 lines
of PowerShell, and is not ported yet. The run says so rather than leaving it to be
noticed.

Usage:
    python scripts/run_tests.py
    python scripts/run_tests.py --skip lint
"""

import argparse
import ast
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_TESTS = REPO_ROOT / "tests" / "python"
SOURCE_ROOTS = ("src", "tests/python", "scripts")


def log(message: str) -> None:
    print(f"[tests] {message}", flush=True)


def python_files() -> list[Path]:
    found: list[Path] = []
    for root in SOURCE_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        found.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(found)


def check_parse(failures: list[str]) -> None:
    """The cheapest check, and the one that stops every later failure naming the wrong file."""
    log("Parsing every Python file...")
    files = python_files()
    if not files:
        failures.append("No Python file found, so the parse check read nothing.")
        return
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            failures.append(f"{path.relative_to(REPO_ROOT)}:{error.lineno}: {error.msg}")
    log(f"Parsed {len(files)} file(s).")


def check_lint(failures: list[str]) -> None:
    """ruff, configured in pyproject.toml.

    A missing linter is a failure and not a skip, for the reason the PowerShell runner
    gives about PSScriptAnalyzer: passing without linting produces the same output as
    linting cleanly, and --skip lint already exists for anyone who means to leave it
    out deliberately.
    """
    executable = shutil.which("ruff")
    command = [executable] if executable else [sys.executable, "-m", "ruff"]

    version = subprocess.run(
        [*command, "--version"], capture_output=True, text=True, check=False
    )
    if version.returncode != 0:
        failures.append(
            "ruff is not installed, so nothing was linted. "
            'python -m pip install "ruff>=0.6.0,<1.0.0", or pass --skip lint.'
        )
        return

    # Printed rather than assumed: the bound in pyproject.toml is a range, and a lint
    # result means something different depending on which release inside it ran.
    log(f"Running {version.stdout.strip()}...")
    result = subprocess.run([*command, "check", str(REPO_ROOT)], check=False)
    if result.returncode != 0:
        failures.append("ruff reported findings.")


def check_tests(failures: list[str]) -> None:
    log("Running the Python suite...")
    # top_level_dir is tests/python so the suite's own support module is importable by
    # name, and src/ is added because the package is never installed - it is read from
    # the working copy, which is the only copy there is.
    sys.path.insert(0, str(PYTHON_TESTS))
    sys.path.insert(0, str(REPO_ROOT / "src"))

    suite = unittest.defaultTestLoader.discover(
        start_dir=str(PYTHON_TESTS), top_level_dir=str(PYTHON_TESTS)
    )
    if suite.countTestCases() == 0:
        # An empty run is green, and green from an empty run looks exactly like green
        # from a run that tested something. Same reasoning as the Pester branch of
        # Invoke-Tests.ps1.
        failures.append(f"No test found under {PYTHON_TESTS}, so no test ran.")
        return

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    # testsRun counts a skipped test, so subtracting only failures and errors reports
    # a skip as a pass - which is the exact thing the skip is meant to be visible
    # against.
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    log(
        f"Python suite: {passed} passed, {len(result.failures)} failed, "
        f"{len(result.errors)} errored, {len(result.skipped)} skipped."
    )

    # Skips are named, not counted away. A skipped conformance test and a passing one
    # must not look alike: the differential check against jsonschema is exactly the
    # assurance ADR 0007 trades a runtime dependency for, and it is worth nothing if
    # nobody notices the day it stops running.
    for case, reason in result.skipped:
        log(f"  SKIPPED {case}: {reason}")
    if not result.wasSuccessful():
        failures.append(
            f"{len(result.failures) + len(result.errors)} Python test(s) did not pass."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip",
        action="append",
        choices=["parse", "lint", "tests"],
        default=[],
        help="A check to leave out deliberately.",
    )
    arguments = parser.parse_args()

    # Every check that fails adds a line, and the run reports all of them rather than
    # stopping at the first.
    failures: list[str] = []
    if "parse" not in arguments.skip:
        check_parse(failures)
    if "lint" not in arguments.skip:
        check_lint(failures)
    if "tests" not in arguments.skip:
        check_tests(failures)

    log(
        "The sensitive data scan is NOT part of this gate: it is still PowerShell only. "
        "Run scripts/Invoke-Tests.ps1 for it."
    )

    if failures:
        print("\nQuality gate failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    log("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
