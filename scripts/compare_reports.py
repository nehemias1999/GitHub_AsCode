"""Compare two reports, one from each implementation, and say where they differ.

This is the instrument ADR 0006's deletion trigger names. The trigger, in full:

  1. The offline golden comparison green on every CI leg.
  2. One live `inventory` and one live `plan` from each implementation, against the same
     account inside one rate-limit window, with an empty report diff outside the
     normalised set - and an identical `declarationFingerprint`, checked FIRST, because
     if that differs they did not read the same declaration and nothing below it means
     anything.
  3. The dual gate green on ubuntu-latest and windows-latest.

Point 2 needs a token and a live account, so it is the operator's to run. This script is
what turns the two files it produces into an answer.

**The fingerprint is checked before anything else, and a mismatch stops the comparison.**
Reporting forty field differences when the two runs read different declarations would
bury the only fact that matters under noise that follows from it.

What is normalised, and why each one is unavoidable rather than convenient:

  correlationId          a fresh UUID per run, by design
  generatedAt            the wall clock
  runBy / runOn          the user and host, which are the same here but need not be
  runLog / reportPath    paths naming a file that is unique per run
  declarationPath        absolute, so it differs by checkout location
  schemaEngine           'reduced' or 'Test-Json' against 'builtin' - the whole point of
                         ADR 0007 is that these differ, and deliberately

Everything else must match. Usage:

    python scripts/compare_reports.py powershell-report.json python-report.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Fields whose difference carries no information about whether the two implementations
# agree. Matched by their dotted path in the report, so a field of the same name
# somewhere meaningful is still compared.
NORMALISED = frozenset(
    {
        "detail.provenance.correlationId",
        "detail.provenance.generatedAt",
        "detail.provenance.runBy",
        "detail.provenance.runOn",
        "detail.provenance.declarationPath",
        "detail.provenance.schemaEngine",
        "detail.runLog",
        "detail.declarationPath",
        "generatedAt",
        # The budget as each run observed it. Two runs a second apart have spent
        # different amounts of it, and the reset window can roll over between them, so
        # these say nothing about whether the implementations agree. `limit` and
        # `resource` are NOT here: those describe the budget rather than the run, and a
        # difference in either would be a real one.
        "detail.rateLimit.remaining",
        "detail.rateLimit.resetUtc",
    }
)

FINGERPRINT = "detail.provenance.declarationFingerprint"


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Every leaf of a report, keyed by its dotted path.

    A list is keyed by index rather than compared whole, so a diff names the operation
    that differs instead of saying that two lists of forty are not equal.
    """
    flat: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            flat.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            flat.update(flatten(item, f"{prefix}[{index}]"))
    else:
        flat[prefix] = value
    return flat


def _normalised_path(path: str) -> str:
    """The path with list indices removed, for matching against NORMALISED."""
    result = []
    for part in path.split("."):
        result.append(part.split("[")[0] if "[" in part else part)
    return ".".join(result)


def compare(left: dict, right: dict) -> tuple[list[str], list[str]]:
    """Compare two reports.

    Returns:
        (fatal, differences). `fatal` is non-empty only when the two runs did not read
        the same declaration, in which case `differences` is not computed at all.
    """
    left_flat = flatten(left)
    right_flat = flatten(right)

    left_fingerprint = left_flat.get(FINGERPRINT)
    right_fingerprint = right_flat.get(FINGERPRINT)
    if left_fingerprint != right_fingerprint:
        return (
            [
                "The two runs did not read the same declaration, so nothing below this is "
                f"meaningful.\n  left:  {left_fingerprint}\n  right: {right_fingerprint}"
            ],
            [],
        )
    if not left_fingerprint:
        return (
            [
                "Neither report carries a declaration fingerprint, so there is nothing to "
                "anchor the comparison to. Both files must come from a run that read a "
                "declaration."
            ],
            [],
        )

    differences = []
    for path in sorted(set(left_flat) | set(right_flat)):
        if _normalised_path(path) in NORMALISED:
            continue
        left_value = left_flat.get(path, "<absent>")
        right_value = right_flat.get(path, "<absent>")
        if left_value != right_value:
            differences.append(f"{path}\n  left:  {left_value!r}\n  right: {right_value!r}")

    return [], differences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", help="A report from one implementation.")
    parser.add_argument("right", help="A report from the other.")
    arguments = parser.parse_args(argv)

    left = json.loads(Path(arguments.left).read_text(encoding="utf-8"))
    right = json.loads(Path(arguments.right).read_text(encoding="utf-8"))

    fatal, differences = compare(left, right)

    if fatal:
        for line in fatal:
            print(f"FATAL: {line}", file=sys.stderr)
        return 2

    print(f"declarationFingerprint matches: {flatten(left)[FINGERPRINT]}")

    if not differences:
        print("No differences outside the normalised set. The two reports agree.")
        return 0

    print(f"{len(differences)} difference(s) outside the normalised set:", file=sys.stderr)
    for difference in differences:
        print(f"  {difference}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
