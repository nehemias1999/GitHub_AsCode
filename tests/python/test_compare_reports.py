"""The parity instrument, tested before it is trusted with the decision it feeds.

`scripts/compare_reports.py` is what turns two live reports into the answer ADR 0006's
deletion trigger needs. A comparison tool that reports agreement because it is not
looking is the worst possible failure here: it would authorise removing the only oracle
the port has.

So every case below is about the ways this could say "they agree" when they do not.
"""

import json
import sys
import unittest
from pathlib import Path

from support import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import compare_reports  # noqa: E402


def a_report(**overrides):
    report = {
        "module": "repo-inventory",
        "command": "plan",
        "target": "EXAMPLE-owner",
        "generatedAt": "2026-01-01T00:00:00Z",
        "summary": {"total": 1, "ok": 1, "pending": 0, "warning": 0, "protected": 0, "blocked": 0},
        "operations": [
            {
                "resource": "repository",
                "name": "EXAMPLE-repo",
                "action": "exists",
                "status": "ok",
                "reason": "Live state already matches the declaration.",
            }
        ],
        "detail": {
            "runLog": "artifacts/logs/one.log",
            "declarationPath": "/somewhere/declaration.json",
            "provenance": {
                "correlationId": "11111111-1111-1111-1111-111111111111",
                "runBy": "someone",
                "runOn": "a-machine",
                "declarationPath": "/somewhere/declaration.json",
                "declarationFingerprint": "sha256:abc",
                "schemaEngine": "builtin",
            },
        },
    }
    report.update(overrides)
    return report


class TheFingerprintIsCheckedFirstAndStopsEverything(unittest.TestCase):
    def test_refuses_to_compare_two_runs_that_read_different_declarations(self):
        # Reporting forty field differences here would bury the only fact that matters
        # under noise that follows from it.
        left = a_report()
        right = a_report()
        right["detail"]["provenance"]["declarationFingerprint"] = "sha256:def"

        fatal, differences = compare_reports.compare(left, right)
        self.assertTrue(fatal)
        self.assertEqual([], differences)
        self.assertIn("did not read the same declaration", fatal[0])

    def test_refuses_a_report_with_no_fingerprint_at_all(self):
        # Two reports that both lack it would otherwise compare equal on that field and
        # the comparison would proceed with nothing anchoring it.
        left = a_report()
        right = a_report()
        del left["detail"]["provenance"]["declarationFingerprint"]
        del right["detail"]["provenance"]["declarationFingerprint"]

        fatal, _ = compare_reports.compare(left, right)
        self.assertTrue(fatal)
        self.assertIn("nothing to anchor", fatal[0])


class OnlyTheUnavoidableDifferencesAreIgnored(unittest.TestCase):
    def test_ignores_the_fields_that_are_unique_to_a_run_by_design(self):
        left = a_report()
        right = a_report(generatedAt="2026-06-06T06:06:06Z")
        right["detail"]["provenance"]["correlationId"] = "22222222-2222-2222-2222-222222222222"
        right["detail"]["provenance"]["runBy"] = "somebody-else"
        right["detail"]["runLog"] = "artifacts/logs/two.log"

        fatal, differences = compare_reports.compare(left, right)
        self.assertEqual([], fatal)
        self.assertEqual([], differences)

    def test_ignores_the_schema_engine_because_adr_0007_makes_them_differ(self):
        left = a_report()
        right = a_report()
        left["detail"]["provenance"]["schemaEngine"] = "reduced"
        _, differences = compare_reports.compare(left, right)
        self.assertEqual([], differences)

    def test_reports_a_difference_in_a_verdict(self):
        # The thing the comparison exists to find.
        left = a_report()
        right = a_report()
        right["operations"][0]["status"] = "pending"
        _, differences = compare_reports.compare(left, right)
        self.assertEqual(1, len(differences))
        self.assertIn("operations[0].status", differences[0])

    def test_reports_a_difference_in_a_reason_and_not_only_in_a_verdict(self):
        # Two implementations agreeing on the word and disagreeing on the explanation is
        # exactly the drift a reviewer would never notice.
        left = a_report()
        right = a_report()
        right["operations"][0]["reason"] = "Something else entirely."
        _, differences = compare_reports.compare(left, right)
        self.assertEqual(1, len(differences))

    def test_reports_an_operation_present_on_one_side_only(self):
        left = a_report()
        right = a_report()
        right["operations"].append(dict(left["operations"][0], name="EXAMPLE-extra"))
        _, differences = compare_reports.compare(left, right)
        self.assertTrue(differences)
        self.assertTrue(any("<absent>" in difference for difference in differences))

    def test_names_the_operation_that_differs_rather_than_the_whole_list(self):
        left = a_report()
        right = a_report()
        right["operations"][0]["name"] = "EXAMPLE-other"
        _, differences = compare_reports.compare(left, right)
        self.assertIn("operations[0].name", differences[0])

    def test_does_not_normalise_a_field_that_merely_shares_a_name(self):
        # `declarationPath` is normalised under detail and under provenance. A field
        # called generatedAt inside an operation, if one ever existed, is not the report
        # timestamp and must still be compared.
        left = a_report()
        right = a_report()
        left["operations"][0]["reason"] = "runBy"
        right["operations"][0]["reason"] = "runOn"
        _, differences = compare_reports.compare(left, right)
        self.assertEqual(1, len(differences))


class TheFlattenerNamesEveryLeaf(unittest.TestCase):
    def test_keys_a_nested_value_by_its_dotted_path(self):
        flat = compare_reports.flatten({"a": {"b": 1}})
        self.assertEqual({"a.b": 1}, flat)

    def test_keys_a_list_item_by_its_index(self):
        flat = compare_reports.flatten({"a": [{"b": 1}, {"b": 2}]})
        self.assertEqual({"a[0].b": 1, "a[1].b": 2}, flat)

    def test_keeps_none_and_empty_apart(self):
        # "no licence" and "a licence with no name" must not be confusable, and the same
        # is true of the report fields that carry them.
        self.assertNotEqual(
            compare_reports.flatten({"a": None}), compare_reports.flatten({"a": ""})
        )


class TheScriptAnswersWithAnExitCode(unittest.TestCase):
    def test_exits_zero_when_the_reports_agree(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            left = Path(directory) / "left.json"
            right = Path(directory) / "right.json"
            left.write_text(json.dumps(a_report()), encoding="utf-8")
            right.write_text(json.dumps(a_report()), encoding="utf-8")
            self.assertEqual(0, compare_reports.main([str(left), str(right)]))

    def test_exits_two_when_the_declarations_differ(self):
        # A different code from "they disagree", because it means something different: a
        # setup mistake, not a finding about the port.
        with __import__("tempfile").TemporaryDirectory() as directory:
            left = Path(directory) / "left.json"
            right = Path(directory) / "right.json"
            other = a_report()
            other["detail"]["provenance"]["declarationFingerprint"] = "sha256:def"
            left.write_text(json.dumps(a_report()), encoding="utf-8")
            right.write_text(json.dumps(other), encoding="utf-8")
            self.assertEqual(2, compare_reports.main([str(left), str(right)]))

    def test_exits_one_when_they_disagree(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            left = Path(directory) / "left.json"
            right = Path(directory) / "right.json"
            other = a_report()
            other["operations"][0]["status"] = "pending"
            left.write_text(json.dumps(a_report()), encoding="utf-8")
            right.write_text(json.dumps(other), encoding="utf-8")
            self.assertEqual(1, compare_reports.main([str(left), str(right)]))


if __name__ == "__main__":
    unittest.main()
