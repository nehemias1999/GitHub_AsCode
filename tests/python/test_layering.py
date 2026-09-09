"""Dependencies point downward, never sideways.

docs/reference/architecture.md has said this since phase 1, and PowerShell could not
check it: module load order is a runtime property there, and RequiredModules is a
declaration nothing compares against the imports actually written. Python imports are
statically enumerable, so the sentence stops being a convention.

The ladder lives in pyproject.toml under [tool.github-as-code.layers], next to the
other thing this repository declares about itself, and is read from there rather than
restated here - two copies of a rule are two rules.
"""

import unittest

from support import (
    REPO_ROOT,
    SRC_ROOT,
    imported_names,
    load_pyproject,
    module_name,
    shipped_modules,
)

PACKAGE = "github_as_code"


def layer_map() -> dict[str, int]:
    return load_pyproject()["tool"]["github-as-code"]["layers"]


class EveryModuleIsPlaced(unittest.TestCase):
    def test_has_shipped_code_to_read_at_all(self):
        self.assertTrue(
            shipped_modules(),
            f"No Python file found under {SRC_ROOT}, so the layering guard read nothing.",
        )

    def test_assigns_a_layer_to_every_module(self):
        # An unplaced module fails rather than being waved through, and that is what
        # gives this guard teeth while the port is still mostly empty: the next module
        # to land cannot land without somebody deciding where it sits. A guard that
        # ignored what it did not recognise would grow quieter with every commit.
        declared = layer_map()
        unplaced = [
            str(path.relative_to(REPO_ROOT))
            for path in shipped_modules()
            if module_name(path) not in declared
        ]
        self.assertEqual(
            [],
            unplaced,
            "These modules have no layer in pyproject.toml [tool.github-as-code.layers]:\n  "
            + "\n  ".join(unplaced),
        )

    def test_places_no_module_that_does_not_exist(self):
        # The other direction, and the one that rots silently: a layer left behind for
        # a module that was renamed or removed reads as coverage the guard is not
        # providing.
        present = {module_name(path) for path in shipped_modules()}
        stale = sorted(name for name in layer_map() if name not in present)
        self.assertEqual(
            [],
            stale,
            "These layers name a module that does not exist:\n  " + "\n  ".join(stale),
        )


class NothingImportsSideways(unittest.TestCase):
    def test_imports_only_modules_on_a_lower_layer(self):
        declared = layer_map()
        offenders = []

        for path in shipped_modules():
            own = module_name(path)
            if own not in declared:
                # Reported by test_assigns_a_layer_to_every_module. Failing twice for
                # one cause makes the second failure look like a second problem.
                continue
            own_layer = declared[own]

            for name in imported_names(path):
                if name.split(".")[0] != PACKAGE or name == own:
                    continue
                # An import of a package reaches its __init__, which is what the map
                # names; anything deeper is matched by its own longest declared prefix.
                target = name
                while target and target not in declared:
                    target = target.rsplit(".", 1)[0] if "." in target else ""
                if not target:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)} imports {name}, which has no layer"
                    )
                    continue
                if declared[target] >= own_layer:
                    direction = "sideways" if declared[target] == own_layer else "upward"
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)} (layer {own_layer}) imports "
                        f"{target} (layer {declared[target]}) - {direction}"
                    )

        self.assertEqual(
            [],
            offenders,
            "Dependencies must point downward. See docs/reference/architecture.md:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
