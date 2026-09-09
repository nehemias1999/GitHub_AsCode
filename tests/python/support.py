"""Shared helpers for the Python suite.

Deliberately small. The PowerShell suite's TestHelpers.ps1 grew into a place where
behaviour hid from the tests that were supposed to be reading it; this file holds
locations and file enumeration, and nothing that could be mistaken for a rule.
"""

import ast
import tomllib
from pathlib import Path

# tests/python/support.py -> tests/python -> tests -> the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def load_pyproject() -> dict:
    """Read pyproject.toml with tomllib.

    tomllib rather than a regular expression, and 3.11 rather than 3.9, is the whole
    reason ADR 0006 puts the floor where it does: the guard that proves this project
    has no dependencies must not need one to read the file that says so.
    """
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def shipped_modules() -> list[Path]:
    """Every Python file under src/, which is everything the tool actually ships.

    Nothing under tests/ or scripts/ is included. Those may use the development
    dependencies; the shipped package may not, and conflating the two would make the
    dependency guard pass for the wrong reason.
    """
    return sorted(path for path in SRC_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def module_name(path: Path) -> str:
    """The dotted import name a file under src/ is reached by.

    `src/github_as_code/__init__.py` is `github_as_code`, not `github_as_code.__init__`,
    because that is the name an import statement elsewhere in the tree would write.
    """
    relative = path.relative_to(SRC_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_names(path: Path) -> set[str]:
    """Every module name imported by a file, read from the parse tree.

    From the parse tree and not from the text, for the reason
    docs/process/testing-strategy.md gives about the PowerShell absence tests: a grep
    matches a mention in a comment and misses the real thing. It also sees imports
    inside a function body, which is exactly where somebody would put the one they
    knew did not belong.

    A relative import (`from . import x`) is resolved against the file's own package,
    so the layer guard reads it as the absolute name it actually refers to.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    own = module_name(path)
    package = own.rsplit(".", 1)[0] if "." in own else own
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                # A relative import: level 1 is this file's package, level 2 its parent.
                base_parts = package.split(".")
                trimmed = base_parts[: len(base_parts) - (node.level - 1)] or base_parts[:1]
                base = ".".join(trimmed)
                if node.module:
                    base = f"{base}.{node.module}"
            if not base:
                continue

            names.add(base)
            # The alias list matters, and leaving it out was a real hole rather than a
            # theoretical one: `from github_as_code import http` puts the package in
            # node.module and the MODULE in the alias list, so recording only
            # node.module saw an import of the package and missed the edge entirely -
            # and that spelling is the ordinary way somebody would write exactly the
            # import this guard exists to catch. Measured here, on a planted pair of
            # modules at the same layer, which the guard passed over in silence.
            #
            # An alias that is a class or a function rather than a module produces a
            # name no layer declares; the layer guard resolves it to its longest
            # declared prefix, and the dependency guard only ever reads the first
            # segment. Neither is harmed by the extra name.
            for alias in node.names:
                names.add(f"{base}.{alias.name}")

    return names
