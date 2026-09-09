"""The import reader every other guard is built on.

Two guards - the dependency one and the layering one - are only as good as what
support.imported_names sees, and a reader that misses a spelling makes both of them
report a clean tree they never actually read. That is not hypothetical: the first
version of this reader missed `from package import module`, which is the ordinary way
somebody writes exactly the import the layering guard exists to catch, and a planted
pair of modules at the same layer went through in silence.

So the reader gets its own tests, named after the spellings that hid something.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import support


class TheReaderSeesEverySpellingOfAnImport(unittest.TestCase):
    def names_in(self, source: str, relative: str = "github_as_code/probe.py") -> set[str]:
        """Parse a source file placed at a real path under a temporary src/.

        A real path, because imported_names resolves relative imports against the
        file's own package, and a fixture living somewhere else would resolve them
        against the wrong one - which is a way for a test to agree with a bug.
        """
        with TemporaryDirectory() as directory:
            src = Path(directory) / "src"
            path = src / relative
            path.parent.mkdir(parents=True)
            path.write_text(source, encoding="utf-8")

            original = support.SRC_ROOT
            support.SRC_ROOT = src
            try:
                return support.imported_names(path)
            finally:
                support.SRC_ROOT = original

    def test_sees_the_module_in_from_package_import_module(self):
        # The spelling that hid a sideways import.
        names = self.names_in("from github_as_code import http\n")
        self.assertIn("github_as_code.http", names)

    def test_sees_the_module_in_a_relative_from_import(self):
        names = self.names_in("from . import http\n")
        self.assertIn("github_as_code.http", names)

    def test_sees_a_plain_import(self):
        names = self.names_in("import urllib.request\n")
        self.assertIn("urllib.request", names)

    def test_sees_a_symbol_imported_from_a_module(self):
        # Resolved to its module by the layer guard's longest-prefix walk, and read
        # first-segment-only by the dependency guard. What matters here is that the
        # module it came from is not lost.
        names = self.names_in("from github_as_code.plan import PlanStatus\n")
        self.assertIn("github_as_code.plan", names)

    def test_sees_an_import_hidden_inside_a_function(self):
        # Read from the parse tree, so scope is irrelevant - and a function body is
        # exactly where somebody puts the import they know does not belong.
        names = self.names_in("def load():\n    import requests\n    return requests\n")
        self.assertIn("requests", names)

    def test_sees_through_an_alias(self):
        names = self.names_in("import urllib.request as web\n")
        self.assertIn("urllib.request", names)


class TheReaderNamesModulesTheWayAnImportWouldWriteThem(unittest.TestCase):
    def test_calls_a_package_by_its_package_name_rather_than_its_init(self):
        # github_as_code, never github_as_code.__init__: the layer map has to be
        # writable in the names imports actually use.
        path = support.SRC_ROOT / "github_as_code" / "__init__.py"
        self.assertEqual("github_as_code", support.module_name(path))


if __name__ == "__main__":
    unittest.main()
