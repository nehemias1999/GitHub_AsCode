"""The command ladder, end to end and offline.

`validate` runs for real against the shipped template. The rungs that need an account
run against a fake transport answering from the committed fixtures - the same fixtures
the Pester suite uses, so both implementations are being asked about the same bytes.

The assertions that matter most are the negative ones: `inventory` must not consult the
declaration, a filtered run must not answer about repositories nobody asked about, and
nothing anywhere may reach the transport asking for something other than a read.
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from github_as_code import http
from github_as_code.automations import repo_inventory
from support import REPO_ROOT

FIXTURES = REPO_ROOT / "tests" / "fixtures"
TEMPLATE = REPO_ROOT / "automations/repo-inventory/config/repositories.example.json"


class _FakeTransport:
    """Answers the two paths a run makes, from the committed fixtures."""

    def __init__(self, pages=None, user_headers=None):
        self.pages = pages
        self.user_headers = user_headers or {}
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        path = kwargs["path"]
        if path == "user":
            return http.Response(
                content=(FIXTURES / "user.json").read_text(encoding="utf-8"),
                headers=self.user_headers,
                status_code=200,
            )
        if self.pages is None:
            body, link = (FIXTURES / "repos.page1.json").read_text(encoding="utf-8"), ""
        else:
            body, link = self.pages.pop(0)
        headers = {"Link": link, "x-ratelimit-remaining": "4900", "x-ratelimit-limit": "5000"}
        return http.Response(content=body, headers=headers, status_code=200)


class ValidateIsOfflineAndComplete(unittest.TestCase):
    def test_passes_against_the_shipped_template(self):
        # The one end-to-end path that needs no network and no credential, which is why
        # CI runs it on every leg.
        code = repo_inventory.main(
            ["validate", "--configuration-path", str(TEMPLATE)], repository_root=REPO_ROOT
        )
        self.assertEqual(0, code)

    def test_accepts_a_repository_root_given_as_a_string(self):
        # A caller building an argument list out of sys.argv hands over strings. The
        # first version of main() took the value as given and failed three functions
        # later with a TypeError about str and str.
        code = repo_inventory.main(
            ["validate", "--configuration-path", str(TEMPLATE)], repository_root=str(REPO_ROOT)
        )
        self.assertEqual(0, code)

    def test_rejects_a_declaration_naming_a_class_that_is_not_defined(self):
        # A cross-reference between two parts of one document, which JSON Schema cannot
        # express at all - so it is checked here, offline, where a typo costs a second.
        problems = repo_inventory._declaration_problems(
            {
                "classes": {"service": {"description": "A service."}},
                "repositories": [{"name": "EXAMPLE-repo", "class": "tooling"}],
            },
            [],
        )
        self.assertTrue(any("is not defined" in problem for problem in problems))

    def test_rejects_a_duplicate_declaration(self):
        problems = repo_inventory._declaration_problems(
            {
                "classes": {"service": {"description": "A service."}},
                "repositories": [
                    {"name": "EXAMPLE-repo", "class": "service"},
                    {"name": "EXAMPLE-repo", "class": "service"},
                ],
            },
            [],
        )
        self.assertTrue(any("Duplicate repository name" in problem for problem in problems))

    def test_rejects_a_description_github_would_truncate(self):
        # A longer declared value could never compare equal, so every plan would report
        # the same change forever. That is an idempotency failure, catchable offline.
        problems = repo_inventory._declaration_problems(
            {
                "classes": {"service": {"description": "A service."}},
                "repositories": [
                    {"name": "EXAMPLE-repo", "class": "service", "description": "E" * 351}
                ],
            },
            [],
        )
        self.assertTrue(any("at most 350" in problem for problem in problems))

    def test_rejects_a_requested_name_nothing_declares(self):
        # A typo that silently narrows the run to nothing is how "pending 0" becomes a
        # lie.
        problems = repo_inventory._declaration_problems(
            {
                "classes": {"service": {"description": "A service."}},
                "repositories": [{"name": "EXAMPLE-repo", "class": "service"}],
            },
            ["EXAMPLE-typo"],
        )
        self.assertTrue(any("EXAMPLE-typo" in problem for problem in problems))

    def test_rejects_an_unusable_topic_before_it_becomes_a_422(self):
        problems = repo_inventory._declaration_problems(
            {
                "classes": {"service": {"description": "A service."}},
                "repositories": [{"name": "EXAMPLE-repo", "class": "service", "topics": ["c#"]}],
            },
            [],
        )
        self.assertTrue(any("unusable topic" in problem for problem in problems))


class ARunAgainstAnAccount(unittest.TestCase):
    """The rungs that read, driven by a fake transport."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "artifacts").mkdir()

        self.original = dict(os.environ)
        self.addCleanup(self._restore)
        os.environ["EXAMPLE_API_URL"] = "https://api.example.com"
        os.environ["EXAMPLE_OWNER"] = "EXAMPLE-owner"
        os.environ["EXAMPLE_TOKEN"] = "EXAMPLE-token"

        # Both files declare a schema, because the loader refuses one that does not -
        # "shipping a JSON Schema next to a configuration file and never running it is
        # common and worthless" cuts both ways, and a fixture that skipped it would be
        # testing a path production never takes. The schemas are permissive on purpose:
        # what is under test here is the ladder, not the validator.
        (self.root / "any.schema.json").write_text(
            json.dumps({"type": "object"}), encoding="utf-8"
        )

        self.context_path = self.root / "project-context.json"
        self.context_path.write_text(
            json.dumps(
                {
                    "$schema": "any.schema.json",
                    "version": "0.1.0",
                    "github": {
                        "apiBaseUrlEnv": "EXAMPLE_API_URL",
                        "ownerEnv": "EXAMPLE_OWNER",
                        "readTokenEnv": "EXAMPLE_TOKEN",
                        "apiVersion": "2022-11-28",
                    },
                    "defaults": {
                        "requestTimeoutSeconds": 60,
                        "maximumRetryCount": 3,
                        "retryAfterCapSeconds": 120,
                        "pageSize": 100,
                        "maximumPageCount": 50,
                    },
                    "automations": {
                        "repo-inventory": {
                            "configuration": "declaration.json",
                            "template": "declaration.json",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        self.declaration_path = self.root / "declaration.json"
        self.declaration_path.write_text(
            json.dumps(
                {
                    "$schema": "any.schema.json",
                    "classes": {"service": {"description": "A service."}},
                    "repositories": [{"name": "EXAMPLE-tool", "class": "service"}],
                }
            ),
            encoding="utf-8",
        )

    def _restore(self):
        os.environ.clear()
        os.environ.update(self.original)

    def _run(self, command, transport, extra=()):
        report_path = self.root / f"{command}.json"
        code = repo_inventory.main(
            [
                command,
                "--project-context-path",
                str(self.context_path),
                "--configuration-path",
                str(self.declaration_path),
                "--report-path",
                str(report_path),
                "--env-file",
                str(self._env_file()),
                *extra,
            ],
            repository_root=self.root,
        )
        written = json.loads(report_path.read_text(encoding="utf-8"))
        return code, written

    def _env_file(self) -> Path:
        path = self.root / "empty.env"
        path.write_text("# nothing to load; the variables are already set\n", encoding="utf-8")
        return path

    def _patched(self, transport):
        # rest.request and rest.paged_result take the transport as an argument, but the
        # entry point calls them without one. Patching the module attribute is what lets
        # the whole run be exercised without a network, and without the entry point
        # growing a parameter that exists only for tests.
        from github_as_code import rest

        original_owned, original_request = rest.owned_repositories, rest.request

        def owned(context, **_):
            return original_owned(context, transport=transport)

        def request(context, path, query=None, allow_not_found=False, **_):
            return original_request(
                context, path, query, allow_not_found, transport=transport
            )

        rest.owned_repositories = owned
        rest.request = request
        self.addCleanup(lambda: setattr(rest, "owned_repositories", original_owned))
        self.addCleanup(lambda: setattr(rest, "request", original_request))

    def test_inventory_does_not_consult_the_declaration(self):
        # The rung that exists to be run BEFORE a declaration does. A first run against
        # the shipped template used to produce blocked operations about EXAMPLE-* names
        # nobody had ever heard of, and exited 2.
        transport = _FakeTransport()
        self._patched(transport)
        code, written = self._run("inventory", transport)

        self.assertEqual(0, code)
        names = {operation["name"] for operation in written["operations"]}
        self.assertNotIn("EXAMPLE-declared-but-absent", names)
        self.assertTrue(all(op["status"] == "ok" for op in written["operations"]))

    def test_plan_reports_a_declared_repository_the_api_did_not_return_as_blocked(self):
        self.declaration_path.write_text(
            json.dumps(
                {
                    "$schema": "any.schema.json",
                    "classes": {"service": {"description": "A service."}},
                    "repositories": [{"name": "EXAMPLE-absent", "class": "service"}],
                }
            ),
            encoding="utf-8",
        )
        transport = _FakeTransport()
        self._patched(transport)
        code, written = self._run("plan", transport)

        # blocked means the run completed and something could not be determined.
        self.assertEqual(2, code)
        blocked = [op for op in written["operations"] if op["status"] == "blocked"]
        self.assertEqual(1, len(blocked))
        self.assertEqual("resolve", blocked[0]["action"])

    def test_plan_reports_every_undeclared_repository_as_adopt(self):
        transport = _FakeTransport()
        self._patched(transport)
        _, written = self._run("plan", transport)

        adopted = [op for op in written["operations"] if op["action"] == "adopt"]
        self.assertEqual({"EXAMPLE-handmade", "EXAMPLE-bare"}, {op["name"] for op in adopted})

    def test_a_filtered_run_does_not_answer_about_anything_else(self):
        transport = _FakeTransport()
        self._patched(transport)
        _, written = self._run("plan", transport, extra=["--repository-name", "EXAMPLE-tool"])

        self.assertEqual(["EXAMPLE-tool"], [op["name"] for op in written["operations"]])
        # And the scope is recorded, because a filtered run and a whole one otherwise
        # differ only in a total.
        self.assertIn("EXAMPLE-tool", written["detail"]["provenance"]["scope"])

    def test_the_report_carries_the_snapshot_the_declaration_is_derived_from(self):
        transport = _FakeTransport()
        self._patched(transport)
        _, written = self._run("inventory", transport)

        self.assertEqual(3, len(written["detail"]["repository"]))
        self.assertIn("EXAMPLE-tool", [item["name"] for item in written["detail"]["repository"]])

    def test_the_token_evidence_survives_the_report_writer(self):
        # The regression that destroyed the only durable record of the expiry.
        transport = _FakeTransport(
            user_headers={"github-authentication-token-expiration": "2099-12-31 23:59:59 UTC"}
        )
        self._patched(transport)
        _, written = self._run("inventory", transport)

        authentication = written["detail"]["authentication"]
        self.assertNotIsInstance(authentication, str)
        self.assertIsNotNone(authentication["daysUntilExpiry"])

    def test_a_truncated_listing_blocks_rather_than_truncating_quietly(self):
        # An inventory that reports fewer repositories than exist, while claiming to be
        # complete, is worse than one that fails.
        pages = [
            (
                (FIXTURES / "repos.page1.json").read_text(encoding="utf-8"),
                (FIXTURES / "link-header-two-links.txt").read_text(encoding="utf-8"),
            )
        ]
        transport = _FakeTransport(pages=pages * 3)
        self._patched(transport)
        self.context_path.write_text(
            self.context_path.read_text(encoding="utf-8").replace(
                '"maximumPageCount": 50', '"maximumPageCount": 1'
            ),
            encoding="utf-8",
        )
        code, written = self._run("inventory", transport)

        self.assertEqual(2, code)
        self.assertTrue(written["detail"]["listing"]["truncated"])
        self.assertTrue(any(op["resource"] == "accountListing" for op in written["operations"]))

    def test_every_list_in_the_report_is_ordered_ordinally(self):
        # Where the 81 differences actually were. Repository names keep their case, so
        # a culture-aware sort orders them differently from an ordinal one - and a
        # report whose order depends on the machine locale cannot be compared with one
        # produced anywhere else.
        transport = _FakeTransport()
        self._patched(transport)
        _, written = self._run("inventory", transport)

        for field in ("withoutLicense", "withoutTopics"):
            with self.subTest(field=field):
                values = written["detail"]["finding"][field]
                self.assertEqual(sorted(values), values)

        names = [item["name"] for item in written["detail"]["repository"]]
        self.assertEqual(sorted(names), names)
        self.assertEqual(sorted(op["name"] for op in written["operations"]),
                         [op["name"] for op in written["operations"]])

    def test_never_asks_the_transport_for_anything_but_a_read(self):
        # The negative assertion that matters most in the whole suite.
        transport = _FakeTransport()
        self._patched(transport)
        self._run("plan", transport)

        for call in transport.calls:
            self.assertNotIn("method", call)
            self.assertNotIn("data", call)
            self.assertFalse(call["path"].startswith("users/"))

    def test_a_second_plan_over_unchanged_state_reports_the_same_thing(self):
        # Idempotency is the acceptance criterion, not an optimisation.
        first_transport = _FakeTransport()
        self._patched(first_transport)
        _, first = self._run("plan", first_transport)

        second_transport = _FakeTransport()
        self._patched(second_transport)
        _, second = self._run("plan", second_transport)

        self.assertEqual(first["operations"], second["operations"])
        self.assertEqual(first["summary"], second["summary"])


if __name__ == "__main__":
    unittest.main()
