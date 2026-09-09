"""The shipped tool depends on nothing but the standard library and git.

Three layers, because they fail differently and each catches what the others miss:

1. The manifest check catches a dependency that has been declared and not yet
   imported - the state a repository is in for exactly as long as it takes somebody
   to write the import.
2. The import check catches one that has been imported and never declared, which is
   what happens when a package is already installed on the machine that added it.
3. The execution check catches both, by refusing to run with site-packages at all.

In PowerShell this rule held because no package manager was in play. Python has pip,
so the rule needs teeth. See docs/adr/0006-python-and-the-standard-library.md.
"""

import subprocess
import sys
import unittest

from support import REPO_ROOT, SRC_ROOT, imported_names, load_pyproject, shipped_modules


class ThereIsNothingToInstall(unittest.TestCase):
    """The manifest layer."""

    def test_declares_no_runtime_dependency(self):
        manifest = load_pyproject()
        declared = manifest["project"]["dependencies"]
        self.assertEqual(
            declared,
            [],
            "pyproject.toml declares a runtime dependency. ADR 0006 restates ADR 0004's "
            "rule in this language: nothing beyond the standard library and git. Widening "
            "it is an ADR, not an edit.",
        )

    def test_keeps_the_development_tools_out_of_the_runtime_list(self):
        # ruff belongs in the dev extra and nowhere else. The failure this prevents is
        # the quiet one: a linter moved into `dependencies` to make an editor happy
        # would satisfy every check above, because that list would no longer be empty
        # for a reason anybody reads as wrong.
        manifest = load_pyproject()
        extras = manifest["project"]["optional-dependencies"]
        self.assertIn("dev", extras)
        self.assertNotEqual(extras["dev"], [], "The dev extra is empty, so it pins nothing.")
        self.assertEqual(manifest["project"]["dependencies"], [])


class NothingOutsideTheStandardLibraryIsImported(unittest.TestCase):
    """The import layer, read from the parse tree."""

    def test_has_shipped_code_to_read_at_all(self):
        # Without this, every assertion below passes over an empty tree and reports
        # the same green as a run that checked something. scripts/Invoke-Tests.ps1
        # fails when Pester finds no test files for the same reason.
        self.assertTrue(
            shipped_modules(),
            f"No Python file found under {SRC_ROOT}, so the dependency guard read nothing.",
        )

    def test_imports_only_the_standard_library_and_this_package(self):
        own_package = "github_as_code"
        offenders = []

        for path in shipped_modules():
            for name in imported_names(path):
                root = name.split(".")[0]
                if root == own_package or root in sys.stdlib_module_names:
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {name}")

        self.assertEqual(
            [],
            offenders,
            "Shipped code imports something outside the standard library:\n  "
            + "\n  ".join(offenders),
        )


class ItRunsWithNoSitePackagesAtAll(unittest.TestCase):
    """The execution layer."""

    def test_imports_under_an_isolated_interpreter(self):
        # -S drops site-packages, so anything pip installed is simply not there; -I
        # additionally ignores PYTHONPATH and the current directory, which is why src/
        # has to be named explicitly in the program text rather than passed in the
        # environment. Naming it there is the point: the path is one directory of this
        # repository and nothing else.
        #
        # It RUNS `validate`, not just an import. An import proves the module graph
        # loads; a command proves the code paths a run actually takes do, which is the
        # end-to-end form ADR 0006 describes. validate is the one rung that needs no
        # network and no token, so it is the one that can run here.
        #
        # Upgrading this from an import to a command immediately found a real defect:
        # main() took repository_root as given, so a string - which this test builds out
        # of sys.argv - reached `root / "foundation"` and failed with a TypeError about
        # str and str. The weaker version of this guard passed over it.
        program = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from github_as_code.automations import repo_inventory; "
            "sys.exit(repo_inventory.main("
            "['validate', '--configuration-path', sys.argv[2]], repository_root=sys.argv[3]))"
        )
        template = REPO_ROOT / "automations/repo-inventory/config/repositories.example.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                program,
                str(SRC_ROOT),
                str(template),
                str(REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            0,
            completed.returncode,
            "validate does not run without site-packages, which means something in it "
            f"needs an installed dependency.\nstdout:\n{completed.stdout}"
            f"\nstderr:\n{completed.stderr}",
        )
        self.assertIn("offline and complete", completed.stdout)


if __name__ == "__main__":
    unittest.main()
