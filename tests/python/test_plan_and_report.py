"""The plan vocabulary and the evidence writer.

The masking cases are the ones that matter most here, and two of them exist because
redaction went wrong in a way that was worse than a leak: it destroyed the evidence the
report was written to carry. Both are pinned.
"""

import datetime
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from github_as_code import plan as plan_module
from github_as_code import report, schema

# Assembled rather than written, so a committed test file does not trip the secret gate.
FINE_GRAINED = ("github" + "_pat_") + ("EXAMPLE" * 6)
CLASSIC = ("gh" + "p_") + ("EXAMPLE" * 5)


class ThePlanVocabulariesAreClosed(unittest.TestCase):
    def test_rejects_a_status_or_an_action_outside_the_vocabulary(self):
        # A free-text status is how a plan turns into prose nothing can enforce - and
        # the exit code of a run is a caller that has to act on it.
        with self.assertRaises(plan_module.PlanVocabularyError):
            plan_module.new_operation("Repository", "x", "destroy", "ok", "")
        with self.assertRaises(plan_module.PlanVocabularyError):
            plan_module.new_operation("Repository", "x", "exists", "fine", "")

    def test_keeps_manual_and_skip_as_actions_rather_than_statuses(self):
        self.assertIn("manual", plan_module.PLAN_ACTION)
        self.assertIn("skip", plan_module.PLAN_ACTION)
        self.assertNotIn("manual", plan_module.PLAN_STATUS)
        self.assertNotIn("skip", plan_module.PLAN_STATUS)

    def test_includes_protected_which_is_what_deliberately_not_changed_means(self):
        self.assertIn("protected", plan_module.PLAN_STATUS)

    def test_counts_a_blocked_operation_as_blocked(self):
        plan = plan_module.new_plan("plan", "EXAMPLE-owner", generated_at="x")
        plan_module.add_operation(
            plan, "Repository", "x", {"action": "resolve", "status": "blocked", "reason": "r"}
        )
        self.assertEqual(1, plan_module.plan_summary(plan)["blocked"])
        self.assertTrue(plan_module.is_blocked(plan))

    def test_an_empty_plan_is_not_blocked(self):
        empty = plan_module.new_plan("plan", "t", generated_at="x")
        self.assertFalse(plan_module.is_blocked(empty))


class TheSummaryClosesWithWhatTheCommandActuallyDid(unittest.TestCase):
    def test_does_not_claim_a_comparison_that_inventory_never_ran(self):
        # inventory does not read the declaration at all, so a closing line about the
        # live state "already matching the declaration" announces a comparison that
        # never happened - and contradicts the per-operation reasons in the same run.
        lines = plan_module.format_plan_summary(
            plan_module.new_plan("inventory", "EXAMPLE-owner", generated_at="x")
        )
        self.assertIn("compares nothing", lines[-1])
        self.assertNotIn("matches the declaration", lines[-1])

    def test_still_says_a_plan_matched_the_declaration_because_that_one_did_compare(self):
        lines = plan_module.format_plan_summary(
            plan_module.new_plan("plan", "EXAMPLE-owner", generated_at="x")
        )
        self.assertIn("already matches the declaration", lines[-1])

    def test_never_hides_a_truncation(self):
        # A silent cap reads as full coverage.
        plan = plan_module.new_plan("plan", "t", generated_at="x")
        for index in range(20):
            plan_module.add_operation(
                plan,
                "Repository",
                f"EXAMPLE-repo-{index}",
                {"action": "update", "status": "pending", "reason": "r"},
            )
        lines = plan_module.format_plan_summary(plan, maximum_item=5)
        self.assertTrue(any("15 more pending operation(s) not listed" in line for line in lines))

    def test_counts_operations_that_are_ok_without_listing_them(self):
        # On an idempotent re-run they are almost the entire plan, and printing hundreds
        # of "already correct" lines trains people to stop reading the output.
        plan = plan_module.new_plan("plan", "t", generated_at="x")
        plan_module.add_operation(
            plan, "Repository", "EXAMPLE-repo", {"action": "exists", "status": "ok", "reason": "r"}
        )
        lines = plan_module.format_plan_summary(plan)
        self.assertIn("ok 1", lines[0])
        self.assertFalse(any("EXAMPLE-repo" in line for line in lines))


class MaskingByValueCatchesWhatANameCannot(unittest.TestCase):
    def test_masks_a_bearer_credential_which_is_the_scheme_this_repository_sends(self):
        masked = report.protect_secrets_in_text(f"Authorization: Bearer {FINE_GRAINED}")
        self.assertNotIn(FINE_GRAINED, masked)

    def test_masks_whatever_follows_bearer_not_only_a_known_token_shape(self):
        # A rule matching only known prefixes would miss the next format GitHub
        # introduces.
        masked = report.protect_secrets_in_text("Bearer some-future-format-nobody-has-seen")
        self.assertEqual("Bearer [redacted]", masked)

    def test_masks_a_bare_token_in_free_text_under_no_header_at_all(self):
        for token in (FINE_GRAINED, CLASSIC):
            with self.subTest(shape=token[:6]):
                masked = report.protect_secrets_in_text(f"GITHUB_OWNER is '{token}'")
                self.assertNotIn(token, masked)

    def test_masks_userinfo_in_a_url_and_keeps_the_host(self):
        # The host is the diagnostically useful part: a reason that says which host
        # disagreed is the point of the message.
        masked = report.protect_secrets_in_text(f"failed for https://me:{CLASSIC}@api.example.com/x")
        self.assertNotIn(CLASSIC, masked)
        self.assertIn("api.example.com", masked)

    def test_leaves_text_with_no_credential_in_it_untouched(self):
        text = "Declared, but the API did not return it."
        self.assertEqual(text, report.protect_secrets_in_text(text))

    def test_returns_none_and_empty_unchanged_rather_than_failing(self):
        self.assertIsNone(report.protect_secrets_in_text(None))
        self.assertEqual("", report.protect_secrets_in_text(""))


class MaskingByNameMustNotDestroyTheEvidence(unittest.TestCase):
    def test_redacts_a_value_whose_name_says_credential(self):
        sanitized = report.remove_sensitive_values({"password": "hunter2"})
        self.assertEqual("[redacted]", sanitized["password"])

    def test_does_not_destroy_the_token_evidence_the_report_exists_to_carry(self):
        # THE regression, and it was live: the inventory named its block of evidence
        # about the token's shape 'token', and the walk replaced the whole object with
        # "[redacted]" on the way to the report. Nothing in it is secret - isClassic is
        # a fact about the TYPE, scope lists permission names, and the expiry fields are
        # dates - and security-model.md promises the report carries the days remaining.
        # The report promised the evidence and destroyed it.
        #
        # The field is named `authentication` for exactly that reason, and this test is
        # what stops somebody renaming it back to something the guard eats. `token`,
        # `tokenShape`, `credentialShape`, `auth` and `authorization` would all be
        # redacted; `authentication` is not, because the short segment `auth` only
        # matches as a whole segment.
        detail = {
            "authentication": {
                "isClassic": True,
                "scope": ["metadata:read"],
                "daysUntilExpiry": 5,
            }
        }
        sanitized = report.remove_sensitive_values(detail)
        self.assertNotIsInstance(sanitized["authentication"], str)
        self.assertEqual(5, sanitized["authentication"]["daysUntilExpiry"])
        self.assertEqual(["metadata:read"], sanitized["authentication"]["scope"])

    def test_would_redact_the_name_the_field_used_to_have(self):
        # The other half of the pair: the guard has not been loosened, the field was
        # renamed. If this ever stops holding, the guard has a hole.
        for name in ("token", "tokenShape", "credentialShape", "auth", "authorization"):
            with self.subTest(name=name):
                sanitized = report.remove_sensitive_values({name: {"daysUntilExpiry": 5}})
                self.assertEqual("[redacted]", sanitized[name])

    def test_does_not_fire_on_a_short_fragment_inside_an_innocent_word(self):
        # 'pat' unanchored matched 'reportPath', 'patch' and 'compatible', and an
        # inventory silently replaced the very data it exists to carry.
        detail = {"reportPath": "artifacts/reports/x.json", "patchCount": 3}
        sanitized = report.remove_sensitive_values(detail)
        self.assertEqual("artifacts/reports/x.json", sanitized["reportPath"])
        self.assertEqual(3, sanitized["patchCount"])

    def test_still_destroys_a_block_whose_name_really_is_credential_shaped(self):
        sanitized = report.remove_sensitive_values({"api_key": {"anything": 1}})
        self.assertEqual("[redacted]", sanitized["api_key"])

    def test_masks_a_string_by_value_even_where_the_name_is_innocent(self):
        url = f"https://u:{CLASSIC}@example.com"
        sanitized = report.remove_sensitive_values({"apiBaseUrl": url})
        self.assertNotIn(CLASSIC, sanitized["apiBaseUrl"])

    def test_keeps_a_one_item_list_a_list(self):
        # In PowerShell this needed a load-bearing comma: returning a single-element
        # array handed the caller the bare element, and a one-item inventory serialised
        # as a string instead of an array - silently changing the shape of the evidence.
        sanitized = report.remove_sensitive_values({"repositories": ["EXAMPLE-repo"]})
        self.assertIsInstance(sanitized["repositories"], list)

    def test_stops_at_the_depth_limit_rather_than_hanging_the_writer(self):
        deep = current = {}
        for _ in range(30):
            current["next"] = {}
            current = current["next"]
        sanitized = report.remove_sensitive_values(deep)
        self.assertIn("depth limit reached", json.dumps(sanitized))


class AMarkdownCellCannotBreakTheTable(unittest.TestCase):
    def test_escapes_a_pipe_so_the_row_does_not_grow_a_column(self):
        self.assertEqual("a \\| b", report.format_markdown_cell("a | b"))

    def test_flattens_a_newline_so_one_value_does_not_become_two_rows(self):
        self.assertEqual("a b", report.format_markdown_cell("a\r\nb"))

    def test_escapes_a_bracket_pair_so_a_value_cannot_inject_a_link(self):
        self.assertEqual("\\[x\\](y)", report.format_markdown_cell("[x](y)"))

    def test_escapes_a_backslash_first_so_later_steps_do_not_double_it(self):
        self.assertEqual("\\\\", report.format_markdown_cell("\\"))

    def test_renders_none_as_an_empty_cell_rather_than_the_word_none(self):
        self.assertEqual("", report.format_markdown_cell(None))


class TheProvenanceBlockAnswersHowToReproduceTheRun(unittest.TestCase):
    def test_fingerprints_the_declaration_rather_than_storing_it(self):
        block = report.provenance("plan", "d.json", '{"a": 1}', "all", ".", "0.1.0")
        self.assertTrue(block["declarationFingerprint"].startswith("sha256:"))
        self.assertNotIn('{"a": 1}', json.dumps(block))

    def test_fingerprints_the_same_declaration_identically_across_line_endings(self):
        # The fingerprint is the FIRST thing ADR 0006's deletion trigger compares
        # between the two implementations. One that varied by platform would make every
        # comparison below it meaningless.
        crlf = report.provenance("plan", "d", "a\r\nb\r\n", "all", ".", "0.1.0")
        lf = report.provenance("plan", "d", "a\nb", "all", ".", "0.1.0")
        self.assertEqual(crlf["declarationFingerprint"], lf["declarationFingerprint"])

    def test_records_that_the_run_read_the_template_rather_than_a_declaration(self):
        # It changes what the report is ABOUT: a plan built from the template describes
        # an example, not an estate.
        block = report.provenance("plan", "t.json", "", "all", ".", "0.1.0", used_template=True)
        self.assertTrue(block["declarationIsTemplate"])

    def test_records_the_scope_because_pending_zero_can_mean_nothing_was_examined(self):
        self.assertEqual("all", report.provenance("plan", "d", "", "", ".", "0.1.0")["scope"])
        self.assertEqual("one", report.provenance("plan", "d", "", "one", ".", "0.1.0")["scope"])

    def test_pins_the_schema_engine_to_the_one_engine(self):
        block = report.provenance("plan", "d", "", "all", ".", "0.1.0")
        self.assertEqual(schema.ENGINE, block["schemaEngine"])

    def test_survives_a_checkout_with_no_git_directory(self):
        # A tarball or a vendored copy is a legitimate way to run this, and a report
        # with an empty commit field is better than no report.
        with TemporaryDirectory() as directory:
            block = report.provenance("plan", "d", "", "all", directory, "0.1.0")
            self.assertEqual("", block["repositoryCommit"])


class TheTwoArtefactsCannotDisagree(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.plan = plan_module.new_plan(
            "plan", "EXAMPLE-owner", generated_at="2026-01-01T00:00:00Z"
        )
        plan_module.add_operation(
            self.plan,
            "Repository",
            "EXAMPLE-repo",
            {"action": "update", "status": "pending", "reason": "Differs in: description."},
        )

    def test_writes_both_files_from_the_same_sanitized_object(self):
        paths = report.write_report(self.plan, self.root / "r.json", "repo-inventory")
        self.assertTrue(paths.json_path.is_file())
        self.assertTrue(paths.markdown_path.is_file())
        self.assertEqual(".md", paths.markdown_path.suffix)

    def test_writes_json_with_no_byte_order_mark(self):
        # A JSON document starting with a BOM is rejected by strict parsers - Python
        # among them. A report nobody can parse is not evidence.
        paths = report.write_report(self.plan, self.root / "r.json", "repo-inventory")
        self.assertFalse(paths.json_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_masks_a_credential_that_reached_the_detail(self):
        paths = report.write_report(
            self.plan, self.root / "r.json", "repo-inventory", detail={"note": f"Bearer {CLASSIC}"}
        )
        self.assertNotIn(CLASSIC, paths.json_path.read_text(encoding="utf-8"))
        self.assertNotIn(CLASSIC, paths.markdown_path.read_text(encoding="utf-8"))

    def test_creates_the_directory_rather_than_failing_on_a_fresh_clone(self):
        paths = report.write_report(self.plan, self.root / "a" / "b" / "r.json", "repo-inventory")
        self.assertTrue(paths.json_path.is_file())

    def test_the_generated_name_is_utc_and_unique(self):
        # Local time in the name while the content records UTC meant a directory's
        # lexicographic order was not its chronological one, and one second of
        # granularity collided on its own.
        first = report.report_path(self.root, "repo-inventory", "plan")
        second = report.report_path(self.root, "repo-inventory", "plan")
        self.assertNotEqual(first.name, second.name)
        self.assertIn("Z-", first.name)

    def test_an_explicit_path_wins(self):
        self.assertEqual(
            "chosen.json", report.report_path(self.root, "m", "c", explicit="chosen.json").name
        )


class ARunTranscriptNeverTakesTheRunDown(unittest.TestCase):
    def test_writes_a_line_with_a_timestamp_and_a_level(self):
        with TemporaryDirectory() as directory:
            path = report.start_run_log(directory, "repo-inventory", "plan")
            self.assertIsNotNone(path)
            report.add_run_log_line(path, "warning", "could not read EXAMPLE-repo")
            text = path.read_text(encoding="utf-8")
            self.assertIn("[warning]", text)
            self.assertIn("could not read EXAMPLE-repo", text)

    def test_masks_the_line_because_a_transcript_outlives_a_scrollback_buffer(self):
        with TemporaryDirectory() as directory:
            path = report.start_run_log(directory, "repo-inventory", "plan")
            report.add_run_log_line(path, "info", f"sent Bearer {CLASSIC}")
            self.assertNotIn(CLASSIC, path.read_text(encoding="utf-8"))

    def test_ignores_an_absent_transcript_so_a_run_without_one_keeps_working(self):
        report.add_run_log_line(None, "info", "anything")


class TheReportTimestampIsOneShape(unittest.TestCase):
    """The other half of the parity fix, and the reason it exists.

    `.ToString('o')` writes seven fractional digits; `isoformat()` writes a +00:00
    offset. Both are ISO 8601 and neither is wrong, which is exactly why the shape has
    to be chosen rather than inherited from whichever library each side reaches for.
    """

    def test_writes_the_one_shape_both_implementations_write(self):
        when = datetime.datetime(2027, 9, 7, 3, 0, 0, tzinfo=datetime.UTC)
        self.assertEqual("2027-09-07T03:00:00Z", report.format_timestamp(when))

    def test_carries_no_fractional_part(self):
        when = datetime.datetime(2027, 9, 7, 3, 0, 0, 123456, tzinfo=datetime.UTC)
        self.assertNotIn(".", report.format_timestamp(when))

    def test_converts_to_utc_so_a_local_value_cannot_be_written_as_utc(self):
        offset = datetime.timezone(datetime.timedelta(hours=-3))
        when = datetime.datetime(2027, 9, 7, 0, 0, 0, tzinfo=offset)
        self.assertEqual("2027-09-07T03:00:00Z", report.format_timestamp(when))

    def test_treats_a_naive_value_as_utc_rather_than_as_local_time(self):
        # A naive datetime read as local time would shift the instant by the machine's
        # offset, which is the same class of machine-dependence the ordinal sort fixed.
        when = datetime.datetime(2027, 9, 7, 3, 0, 0)
        self.assertEqual("2027-09-07T03:00:00Z", report.format_timestamp(when))


if __name__ == "__main__":
    unittest.main()
