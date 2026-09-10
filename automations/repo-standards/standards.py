"""Entry point for repo-standards.

A thin runner: it puts src/ on sys.path and calls the module, so the logic lives in the
layer the architecture puts it in and can be imported by a test without running a
process.

Usage:
    python automations/repo-standards/standards.py validate
    python automations/repo-standards/standards.py plan
"""

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from github_as_code.automations import repo_standards  # noqa: E402

if __name__ == "__main__":
    sys.exit(repo_standards.main(repository_root=REPOSITORY_ROOT))
