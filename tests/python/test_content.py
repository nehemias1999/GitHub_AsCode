"""The standards rules, and the 404 problem they are built around.

The case that matters most is the first one: a token without `Contents: read` lists every
repository perfectly well and then 404s on every path inside them. Reading those as "the
file is missing" would produce a plan saying twenty repositories lack a README, with
nothing anywhere saying the run could not look. That is not a hypothetical - it is what
the recommended fine-grained token does when the Contents permission is left off.
"""

import unittest

from github_as_code import content, http, rest
from github_as_code import plan as plan_module


def a_context() -> rest.GitHubContext:
    return rest.GitHubContext(
        base_url="https://api.example.com",
        owner="EXAMPLE-owner",
        headers={"Authorization": "Bearer EXAMPLE-token"},
        token_environment_name="GITHUB_TOKEN_READ",
    )


class _FakeTransport:
    """Answers a scripted map of path -> listing, or 404 for anything absent."""

    def __init__(self, listings: dict[str, list[str]]):
        self.listings = listings
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        path = kwargs["path"]
        if path not in self.listings:
            if kwargs.get("allow_not_found"):
                return None
            raise http.TransportError(f"unexpected {path}")
        import json

        body = json.dumps([{"name": name, "type": "file"} for name in self.listings[path]])
        return http.Response(content=body, headers={}, status_code=200)


ROOT = "repos/EXAMPLE-owner/EXAMPLE-repo/contents"


class ARefusedRootIsNeverReadAsAnEmptyRepository(unittest.TestCase):
    def test_reports_unreadable_rather_than_every_file_missing(self):
        # THE case. A 404 on the root is the token, not the contents.
        transport = _FakeTransport({})
        contents = content.fetch_contents(
            a_context(), "EXAMPLE-owner", "EXAMPLE-repo", [""], transport=transport
        )
        self.assertFalse(contents.readable)
        self.assertIn("Contents: read", contents.detail)

    def test_a_blocked_repository_reports_no_file_verdicts_at_all(self):
        standard = content.Standard(
            "service", "", (content.RequiredFile("README.md"), content.RequiredFile("LICENSE"))
        )
        status = content.standards_status(standard, content.RepositoryContents(readable=False))
        self.assertEqual("blocked", status.status)
        self.assertEqual("resolve", status.action)
        self.assertEqual((), status.missing)

    def test_stops_asking_about_subdirectories_once_the_root_is_refused(self):
        # Every later call would 404 for the same reason, and each one spends rate limit
        # to learn nothing.
        transport = _FakeTransport({})
        content.fetch_contents(
            a_context(), "EXAMPLE-owner", "EXAMPLE-repo", ["", ".github"], transport=transport
        )
        self.assertEqual(1, len(transport.calls))

    def test_a_missing_subdirectory_is_absence_because_the_root_was_readable(self):
        # The root listing already established that contents can be read, so a 404 below
        # it really is "not there".
        transport = _FakeTransport({ROOT: ["README.md"]})
        contents = content.fetch_contents(
            a_context(), "EXAMPLE-owner", "EXAMPLE-repo", ["", ".github"], transport=transport
        )
        self.assertTrue(contents.readable)
        self.assertFalse(contents.has(".github/workflows.yml"))
        self.assertTrue(contents.has("README.md"))

    def test_a_path_that_names_a_file_is_not_read_as_an_empty_directory(self):
        # The API answers with an object rather than an array for a file. Treating that
        # as a listing would report every file under it as missing.
        class FileShaped(_FakeTransport):
            def __call__(self, **kwargs):
                self.calls.append(kwargs)
                if kwargs["path"] == ROOT:
                    return http.Response(
                        content='[{"name": "docs", "type": "file"}]', headers={}, status_code=200
                    )
                return http.Response(
                    content='{"name": "docs", "type": "file"}', headers={}, status_code=200
                )

        transport = FileShaped({})
        contents = content.fetch_contents(
            a_context(), "EXAMPLE-owner", "EXAMPLE-repo", ["", "docs"], transport=transport
        )
        self.assertTrue(contents.readable)
        self.assertEqual(frozenset(), contents.listings["docs"])


class TheWalkIsOneCallPerDirectory(unittest.TestCase):
    def test_lists_the_root_first_and_every_named_directory_once(self):
        standards = {
            "service": content.Standard(
                "service",
                "",
                (
                    content.RequiredFile("README.md"),
                    content.RequiredFile(".github/CODEOWNERS"),
                    content.RequiredFile(".github/dependabot.yml"),
                ),
            )
        }
        self.assertEqual(["", ".github"], content.required_directories(standards))

    def test_always_includes_the_root_even_when_no_file_lives_there(self):
        # The root is what establishes readability, so it is fetched whether or not a
        # standard names a file in it.
        standards = {
            "s": content.Standard("s", "", (content.RequiredFile("docs/index.md"),))
        }
        self.assertEqual(["", "docs"], content.required_directories(standards))

    def test_asks_once_per_directory_rather_than_once_per_file(self):
        transport = _FakeTransport({ROOT: ["README.md"], f"{ROOT}/.github": ["CODEOWNERS"]})
        content.fetch_contents(
            a_context(), "EXAMPLE-owner", "EXAMPLE-repo", ["", ".github"], transport=transport
        )
        self.assertEqual(2, len(transport.calls))

    def test_never_asks_the_transport_for_anything_but_a_read(self):
        transport = _FakeTransport({ROOT: []})
        content.fetch_contents(
            a_context(), "EXAMPLE-owner", "EXAMPLE-repo", [""], transport=transport
        )
        for call in transport.calls:
            self.assertNotIn("method", call)
            self.assertNotIn("data", call)


class TheVerdictNamesTheFilesAndTheReason(unittest.TestCase):
    def contents(self, names: list[str]) -> content.RepositoryContents:
        return content.RepositoryContents(readable=True, listings={"": frozenset(names)})

    def test_reports_everything_present_as_ok(self):
        standard = content.Standard("service", "", (content.RequiredFile("README.md"),))
        status = content.standards_status(standard, self.contents(["README.md"]))
        self.assertEqual("ok", status.status)
        self.assertEqual("exists", status.action)

    def test_reports_a_missing_file_as_add_and_pending(self):
        standard = content.Standard(
            "service", "", (content.RequiredFile("README.md"), content.RequiredFile("LICENSE"))
        )
        status = content.standards_status(standard, self.contents(["README.md"]))
        self.assertEqual("pending", status.status)
        self.assertEqual("add", status.action)
        self.assertEqual(("LICENSE",), status.missing)
        self.assertEqual(("README.md",), status.present)

    def test_prints_the_reason_so_an_approver_reads_why_and_not_only_what(self):
        standard = content.Standard(
            "library", "", (content.RequiredFile("LICENSE", "a dependency with no licence"),)
        )
        status = content.standards_status(standard, self.contents([]))
        self.assertIn("a dependency with no licence", status.reason)

    def test_a_class_requiring_nothing_is_a_decision_and_reports_ok(self):
        # An archived class requires nothing on purpose: an archived repository refuses
        # every write, so a missing file would be a pending operation that can never be
        # applied.
        standard = content.Standard("archived", "kept for the record", ())
        status = content.standards_status(standard, self.contents([]))
        self.assertEqual("ok", status.status)
        self.assertEqual("validate", status.action)
        self.assertIn("requires no files", status.reason)

    def test_a_class_no_standard_describes_is_blocked_not_ok(self):
        # Reporting "nothing missing" would be a lie by omission: the run did not check,
        # it had nothing to check against.
        status = content.undeclared_class_status("mystery", ["service"])
        self.assertEqual("blocked", status.status)
        self.assertIn("mystery", status.reason)

    def test_every_verdict_is_in_the_closed_vocabulary(self):
        standard = content.Standard("s", "", (content.RequiredFile("a"),))
        verdicts = [
            content.standards_status(standard, content.RepositoryContents(readable=False)),
            content.standards_status(standard, self.contents(["a"])),
            content.standards_status(standard, self.contents([])),
            content.standards_status(content.Standard("s", ""), self.contents([])),
            content.undeclared_class_status("x", []),
        ]
        for verdict in verdicts:
            with self.subTest(action=verdict.action):
                self.assertIn(verdict.action, plan_module.PLAN_ACTION)
                self.assertIn(verdict.status, plan_module.PLAN_STATUS)


class ADeclaredPathCannotBecomeATraversal(unittest.TestCase):
    def test_rejects_a_relative_segment_before_it_becomes_a_url_segment(self):
        # build_uri escapes the query, not the path - the same reasoning that makes
        # format_repository_name validate in code rather than trusting the schema.
        for path in ("../secrets", "a/../../b", "a//b", "./x"):
            with self.subTest(path=path), self.assertRaises(content.ContentError):
                content.normalise_path(path)

    def test_rejects_an_absolute_path(self):
        with self.assertRaises(content.ContentError):
            content.normalise_path("/etc/passwd")

    def test_rejects_a_backslash_because_the_api_addresses_with_forward_slashes(self):
        with self.assertRaises(content.ContentError):
            content.normalise_path("docs" + chr(92) + "index.md")

    def test_rejects_an_empty_path(self):
        with self.assertRaises(content.ContentError):
            content.normalise_path("   ")

    def test_accepts_the_forms_that_are_legal_and_changes_nothing(self):
        for path in ("README.md", ".gitignore", ".github/workflows/ci.yml", "docs/a-b_c.md"):
            with self.subTest(path=path):
                self.assertEqual(path, content.normalise_path(path))


if __name__ == "__main__":
    unittest.main()
