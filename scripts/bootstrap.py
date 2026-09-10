"""Prepare a workstation to run the automations.

Checks the prerequisites, creates the local folders that are excluded from version
control, and creates `.env` from its template.

The whole point is that there is very little to do: the automations have no runtime
dependency beyond the interpreter and git - no package to install, no SDK, no package
manager. That is a design constraint rather than a coincidence, because a tool that
governs a platform has to run on a locked-down workstation and on a build agent without
either being specially prepared. Three guards in the suite prove it, one of them by
running `validate` with no site-packages at all.

Usage:
    python scripts/bootstrap.py
    python scripts/bootstrap.py --check-only
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

MINIMUM_PYTHON = (3, 11)
LOCAL_FOLDERS = (".local", "artifacts/inventory", "artifacts/reports", "artifacts/logs")


def log(message: str, level: str = "info") -> None:
    stream = sys.stderr if level == "warning" else sys.stdout
    prefix = "WARNING: " if level == "warning" else ""
    print(f"{prefix}[bootstrap] {message}", file=stream)


def restrict_to_current_user(path: Path) -> tuple[bool, str]:
    """Restrict a file to the current user, as far as the platform allows.

    `.env` is about to hold a personal access token, and a copy inherits whatever the
    directory grants. Under a user profile that is already restrictive; at the root of a
    data disk, on a share, or in a build agent checkout, it is not - and a checkout is
    exactly where a clone ends up outside a profile.

    Best-effort on purpose: this is a convenience script, and a filesystem that will not
    take the change - a mapped drive, a container mount - must not stop somebody setting
    the repository up. It reports which of the two happened rather than staying silent,
    because "the file is protected" and "the file inherits the directory" call for
    different care about where the clone lives.

    On Windows this shells out to icacls rather than using os.chmod, which on that
    platform only toggles the read-only attribute and grants nothing. The PowerShell
    version reached the same conclusion from the other direction and recorded the
    measurement: Set-Acl needs the account's domain to be reachable and fails with a
    broken trust relationship on a domain-joined machine that is offline, while icacls
    against the same file succeeds. A hardening step that only works on the network is
    not hardening.

    Returns:
        (restricted, detail) - detail explains the failure when restricted is False.
    """
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError as error:
            return False, str(error)
        return True, "0600"

    identity = os.environ.get("USERNAME", "")
    if not identity:
        return False, "the current user could not be determined"
    try:
        completed = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{identity}:(F)"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return False, str(error)
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout).strip()
    return True, "current user only"


def check_prerequisites() -> list[str]:
    """Report what is present, and return what is missing."""
    problems: list[str] = []

    version = sys.version_info
    if version[:2] < MINIMUM_PYTHON:
        problems.append(
            f"Python {version.major}.{version.minor} is too old. "
            f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or later is required - see "
            "docs/adr/0006-python-and-the-standard-library.md for why the floor is there."
        )
    else:
        log(f"Python {version.major}.{version.minor}.{version.micro}.")

    git = shutil.which("git")
    if git:
        log(f"git found at {git}.")
    else:
        problems.append("git was not found on PATH. It is needed to clone and update this "
                        "repository, and the sensitive data gate asks it what is ignored.")

    # Development tools. The automations need neither, and saying so is the point: a
    # reader who cannot install anything can still run the tool.
    for module, purpose, install in (
        ("ruff", "linting", 'python -m pip install "ruff>=0.6.0,<1.0.0"'),
        ("jsonschema", "the differential schema conformance check",
         'python -m pip install "jsonschema>=4.0.0,<5.0.0"'),
    ):
        try:
            __import__(module)
        except ImportError:
            log(f"{module} not available. The gate needs it for {purpose}; the automations "
                f"do not:")
            log(f"  {install}")
        else:
            log(f"{module} found (tests only).")

    # Prove the package imports from the working copy, which is the only copy there is.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    try:
        import github_as_code

        log(f"github_as_code {github_as_code.__version__} imports from src/.")
    except ImportError as error:
        problems.append(f"The package failed to import: {error}")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Report on the prerequisites without creating anything. Use it on a machine "
            "you do not want to leave files on, or in a pipeline that supplies its own "
            "environment."
        ),
    )
    arguments = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    problems = check_prerequisites()

    env_path = root / ".env"
    template_path = root / ".env.example"
    terms_path = root / ".local" / "sensitive-terms.txt"

    if arguments.check_only:
        for folder in LOCAL_FOLDERS:
            state = "present" if (root / folder).exists() else "would be created"
            log(f"{folder} : {state}")
        log(f".env : {'present' if env_path.exists() else 'would be created from .env.example'}")
        log(
            ".local/sensitive-terms.txt : "
            f"{'present' if terms_path.exists() else 'would be created empty'}"
        )
    else:
        for folder in LOCAL_FOLDERS:
            path = root / folder
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                log(f"created {folder}")

        if env_path.exists():
            log(".env already exists and was left untouched.")
        elif template_path.is_file():
            shutil.copyfile(template_path, env_path)
            restricted, detail = restrict_to_current_user(env_path)
            if restricted:
                log(f"restricted .env to the current user only ({detail}).")
            else:
                log(
                    f"could not restrict permissions on .env ({detail}). It inherits the "
                    "directory's permissions, so check who can read them if this clone is "
                    "not inside your user profile.",
                    "warning",
                )
            log(
                "created .env from .env.example. Fill in GITHUB_OWNER and "
                "GITHUB_TOKEN_READ; that is everything repo-inventory needs. The write "
                "tokens stay empty until phase 3, and .env.example says which permissions "
                "each one wants."
            )
        else:
            problems.append(".env.example is missing, so .env could not be created.")

        if not terms_path.exists() and terms_path.parent.exists():
            terms_path.write_text(
                "# One literal term per line. Organization names, host names, project code\n"
                "# names. This file is excluded from version control on purpose: the terms\n"
                "# are themselves sensitive, which is why they must not live in the script\n"
                "# that looks for them.\n",
                encoding="utf-8",
            )
            log("created .local/sensitive-terms.txt (empty; the gate says so on every run)")

    if problems:
        log(f"{len(problems)} problem(s) to fix:", "warning")
        for problem in problems:
            log(f"  - {problem}", "warning")
        return 1

    log("Ready. Next: python automations/repo-inventory/inventory.py validate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
