"""No code path can write to GitHub.

The port of the absence tests in tests/automations/Automations.Tests.ps1, reshaped for
the language. They are read from the parse tree and not from the text, for the reason
that suite gives: a grep matches a mention in a comment and misses a variable, and the
variable is how a write would actually arrive.

The guard set is not a translation, because the write vector is not the same one.
PowerShell had exactly one way in - `-Method` - and every guard was shaped around it.
Python has two, and neither looks like a method parameter:

  - `urllib.request.Request(url, data=...)` silently promotes a GET to a POST. There is
    no method argument involved and nothing reads as a write at the call site.
  - `Request(..., method="PATCH")`, which is the obvious one.

So the guards below assert on the shape of the call, not on the presence of a name.

These guards are absolute on purpose. AGENTS.md section 4 is worth rereading before
loosening one: a guard with an exemption is a guard with a hole, and the cost of that
was already paid once here, when a report field named `private` had to be renamed
rather than the guard taught to tell a count from a request field.
"""

import ast
import unittest

from support import REPO_ROOT, imported_names, shipped_modules

# The one file allowed to perform network I/O. One place to audit, and one place a
# write could ever be added.
TRANSPORT_MODULE = "github_as_code/http.py"


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _called_name(node: ast.Call) -> str:
    """The dotted name of what is being called, as written."""
    parts = []
    current = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _parsed_sources():
    for path in shipped_modules():
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class ThereIsSomethingToRead(unittest.TestCase):
    def test_has_shipped_code_to_read_at_all(self):
        # Every guard below is vacuously true over an empty tree, and a vacuous pass
        # reports the same green as a real one.
        self.assertTrue(shipped_modules())


class NetworkIoHappensInExactlyOnePlace(unittest.TestCase):
    def test_only_the_transport_module_imports_urllib_request(self):
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in shipped_modules()
            if path.as_posix().endswith(TRANSPORT_MODULE) is False
            and any(name.startswith("urllib.request") for name in imported_names(path))
        ]
        self.assertEqual(
            [],
            offenders,
            "urllib.request belongs in one file only, so there is one place to audit and "
            "one place a write could ever be added:\n  " + "\n  ".join(offenders),
        )

    def test_the_transport_module_is_where_it_is_expected_to_be(self):
        # Without this, renaming http.py turns the guard above into a check that
        # nothing imports urllib.request anywhere - which passes, loudly, while the
        # transport sits somewhere nobody is auditing.
        self.assertTrue(
            any(path.as_posix().endswith(TRANSPORT_MODULE) for path in shipped_modules()),
            f"{TRANSPORT_MODULE} does not exist, so the network I/O guard is checking nothing.",
        )


class NoRequestCarriesABody(unittest.TestCase):
    def test_no_call_passes_data_which_would_promote_a_get_to_a_post(self):
        # THE Python write vector, and the one with no PowerShell counterpart. urllib
        # decides the method from the presence of a body, so `data=` is a write with
        # nothing at the call site that reads like one.
        offenders = []
        for path, tree in _parsed_sources():
            for node in _calls(tree):
                if any(keyword.arg == "data" for keyword in node.keywords):
                    where = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
                    offenders.append(f"{where} {_called_name(node)}(data=...)")
        self.assertEqual(
            [],
            offenders,
            "A request carrying a body is a write:\n  " + "\n  ".join(offenders),
        )

    def test_no_request_is_built_with_a_positional_body(self):
        # Request(url, data) - the same write, spelled without the keyword.
        offenders = []
        for path, tree in _parsed_sources():
            for node in _calls(tree):
                if _called_name(node).endswith("Request") and len(node.args) > 1:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        self.assertEqual([], offenders, "\n  ".join(offenders))


class EveryMethodIsTheLiteralGet(unittest.TestCase):
    def test_no_method_argument_is_anything_but_get(self):
        # A literal, never a variable. `method=verb` is exactly how a write arrives,
        # and it is unreadable to anything that matches on text.
        offenders = []
        for path, tree in _parsed_sources():
            for node in _calls(tree):
                for keyword in node.keywords:
                    if keyword.arg != "method":
                        continue
                    value = keyword.value
                    if isinstance(value, ast.Constant) and value.value == "GET":
                        continue
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno} method is not the "
                        "literal 'GET'"
                    )
        self.assertEqual([], offenders, "\n  ".join(offenders))


class DeleteAppearsNowhere(unittest.TestCase):
    def test_contains_no_delete_in_any_spelling(self):
        # The one method this repository never acquires, at any phase - not for a
        # repository, a label, a topic or a project field. Every one destroys something
        # whose blast radius is not in the plan.
        #
        # Absolute rather than context-aware: it fires on an identifier and on a string
        # alike. If it ever fires on something innocent, rename the innocent thing.
        offenders = []
        for path, tree in _parsed_sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if "delete" in node.value.lower():
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} in a string")
                elif isinstance(node, ast.Name) and "delete" in node.id.lower():
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} as a name")
                elif isinstance(node, ast.Attribute) and "delete" in node.attr.lower():
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} as an attribute")
                elif isinstance(node, ast.FunctionDef) and "delete" in node.name.lower():
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} as a function")
        self.assertEqual([], offenders, "\n  ".join(offenders))

    def test_never_names_the_delete_repo_scope(self):
        # A token holding it must not exist, so the string must not be reachable from
        # anything that builds a scope list or a piece of guidance.
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in shipped_modules()
            if "delete" + "_repo" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual([], offenders, "\n  ".join(offenders))


class TheAccountListingComesFromTheAuthenticatedEndpoint(unittest.TestCase):
    def test_no_string_literal_begins_with_users_slash(self):
        # GET /users/{user}/repos returns public repositories only, so it omits every
        # private one and the inventory would report a smaller account than exists -
        # with no error. ADR 0005.
        offenders = []
        for path, tree in _parsed_sources():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and (node.value.startswith("users/") or "/users/" in node.value)
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        self.assertEqual(
            [],
            offenders,
            "The account listing reads GET /user/repos, never /users/{user}/repos:\n  "
            + "\n  ".join(offenders),
        )


class NoDestructivePatchFieldIsBuilt(unittest.TestCase):
    def test_no_dictionary_key_names_a_field_that_looks_ordinary_and_is_not(self):
        # visibility, archived, is_template and default_branch reach PATCH /repos and
        # each one changes something that is not recoverable by re-running a plan.
        # `private` is on the list too, and it is the guard that already cost
        # something: a report field named `private` - a count, not a request field -
        # was renamed `privateCount` rather than the guard loosened.
        forbidden = {"private", "visibility", "archived", "is_template", "default_branch"}
        offenders = []
        for path, tree in _parsed_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key in node.keys:
                    if isinstance(key, ast.Constant) and key.value in forbidden:
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.lineno} key '{key.value}'"
                        )
        self.assertEqual(
            [],
            offenders,
            "Rename your field; do not loosen the guard. See AGENTS.md section 4:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
