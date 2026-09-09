"""Entry point for repo-inventory, the Python half.

A thin runner, on purpose. Everything it does is put src/ on sys.path and call the
module, so the automation's logic lives in the layer the architecture puts it in and can
be imported by a test without running a process.

The PowerShell entry point beside this one stays until parity is proven - see
docs/adr/0006-python-and-the-standard-library.md.

Usage:
    python automations/repo-inventory/inventory.py validate
    python automations/repo-inventory/inventory.py inventory
    python automations/repo-inventory/inventory.py plan
"""

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from github_as_code.automations import repo_inventory  # noqa: E402

if __name__ == "__main__":
    sys.exit(repo_inventory.main(repository_root=REPOSITORY_ROOT))
