"""The repository rules, ported case by case from GitHub.Repository.Tests.ps1.

These are the pure functions, so the whole suite runs offline - which is not a
convenience. Drift is defined against the payload that would be sent, so if the payload
is a pure value then drift is a comparison of values, and "a second plan reports the
same thing" is an assertion rather than a promise.
"""

import unittest

from github_as_code import plan as plan_module
from github_as_code import repository


class TopicsAreNeverDestroyed(unittest.TestCase):
    """PUT /topics replaces the whole collection. This is the function that stops it."""

    def test_keeps_a_topic_that_is_live_and_undeclared(self):
        union = repository.topic_union(["kept-by-hand"], ["declared"])
        self.assertIn("kept-by-hand", union.payload)

    def test_reports_an_undeclared_topic_as_preserved_rather_than_dropping_it_silently(self):
        union = repository.topic_union(["kept-by-hand"], ["declared"])
        self.assertEqual(["kept-by-hand"], union.preserved)

    def test_treats_a_declared_topic_differing_only_in_case_as_already_present(self):
        # The API stores the lowercase form, so comparing raw makes every plan report a
        # change that the next plan reports again. That is an idempotency failure, and
        # idempotency is the acceptance criterion.
        union = repository.topic_union(["powershell"], ["PowerShell"])
        self.assertFalse(union.changed)
        self.assertEqual([], union.added)

    def test_is_a_no_op_the_second_time_given_the_payload_the_first_time_produced(self):
        first = repository.topic_union(["live"], ["declared"])
        second = repository.topic_union(first.payload, ["declared"])
        self.assertFalse(second.changed)
        self.assertEqual(first.payload, second.payload)

    def test_produces_the_same_payload_whatever_order_the_api_returned_topics_in(self):
        one = repository.topic_union(["b", "a"], ["c"])
        other = repository.topic_union(["a", "b"], ["c"])
        self.assertEqual(one.payload, other.payload)

    def test_does_not_duplicate_a_topic_that_is_both_live_and_declared(self):
        union = repository.topic_union(["shared"], ["shared"])
        self.assertEqual(["shared"], union.payload)

    def test_handles_an_empty_live_collection_which_is_every_repository_on_a_fresh_account(self):
        union = repository.topic_union(None, ["declared"])
        self.assertEqual(["declared"], union.payload)
        self.assertTrue(union.changed)


class ATopicIsRejectedRatherThanMangled(unittest.TestCase):
    def test_lowercases_because_that_is_what_the_api_stores(self):
        self.assertEqual("powershell", repository.format_topic_name("PowerShell"))

    def test_rejects_a_topic_it_cannot_store_rather_than_turning_it_into_another_one(self):
        # Quietly turning 'c#' into 'c' gives the account a topic nobody chose.
        with self.assertRaises(repository.DeclarationError):
            repository.format_topic_name("c#")

    def test_rejects_a_topic_longer_than_the_50_characters_github_accepts(self):
        with self.assertRaises(repository.DeclarationError):
            repository.format_topic_name("a" * 51)

    def test_rejects_an_empty_topic(self):
        with self.assertRaises(repository.DeclarationError):
            repository.format_topic_name("   ")

    def test_accepts_the_forms_that_are_legal(self):
        for topic in ("infrastructure", "dot-net", "python3"):
            with self.subTest(topic=topic):
                self.assertEqual(topic, repository.format_topic_name(topic))


class ARepositoryNameIsCheckedBeforeItCanBecomeAPathSegment(unittest.TestCase):
    def test_rejects_owner_slash_repo_which_is_the_most_common_mistake_in_this_field(self):
        with self.assertRaises(repository.DeclarationError) as caught:
            repository.format_repository_name("EXAMPLE-owner/EXAMPLE-repo")
        self.assertIn("owner/repo", str(caught.exception))

    def test_rejects_a_relative_path_segment(self):
        # Valid against the character class, and path traversal once a name becomes a
        # URL segment - which phase 3 makes it.
        for name in (".", ".."):
            with self.subTest(name=name), self.assertRaises(repository.DeclarationError):
                repository.format_repository_name(name)

    def test_rejects_a_name_github_would_not_store(self):
        with self.assertRaises(repository.DeclarationError):
            repository.format_repository_name("has space")

    def test_rejects_a_name_longer_than_the_100_characters_github_allows(self):
        with self.assertRaises(repository.DeclarationError):
            repository.format_repository_name("E" * 101)

    def test_accepts_the_forms_that_are_legal_and_changes_nothing(self):
        # Case-sensitive on the way in, and GitHub preserves it, so there is nothing
        # safe to normalise - unlike a topic.
        for name in ("EXAMPLE-repo", "example_repo", "example.repo", "Example"):
            with self.subTest(name=name):
                self.assertEqual(name, repository.format_repository_name(name))


class ASnapshotHasOneShape(unittest.TestCase):
    def test_drops_the_url_templates_a_real_payload_is_mostly_made_of(self):
        snapshot = repository.new_snapshot(
            {"name": "EXAMPLE-repo", "archive_url": "https://example.com/{a}", "keys_url": "x"}
        )
        self.assertNotIn("archive_url", snapshot)
        self.assertNotIn("keys_url", snapshot)

    def test_keeps_every_field_the_inventory_reports_and_no_others(self):
        snapshot = repository.new_snapshot({"name": "EXAMPLE-repo"})
        self.assertEqual(
            set(repository.snapshot_properties()) | {"license"}, set(snapshot.keys())
        )

    def test_reduces_the_licence_object_to_its_identifier(self):
        snapshot = repository.new_snapshot(
            {"name": "EXAMPLE-repo", "license": {"spdx_id": "MIT", "name": "MIT License"}}
        )
        self.assertEqual("MIT", snapshot["license"])

    def test_reports_no_licence_as_none_not_as_an_empty_object(self):
        # "no licence" and "a licence with no name" must not be confusable.
        self.assertIsNone(repository.new_snapshot({"name": "x", "license": None})["license"])

    def test_treats_noassertion_as_no_identifier(self):
        # GitHub's way of saying it found a licence file it could not identify.
        snapshot = repository.new_snapshot({"license": {"spdx_id": "NOASSERTION"}})
        self.assertIsNone(snapshot["license"])

    def test_always_gives_topics_a_list_so_a_count_never_breaks(self):
        self.assertEqual([], repository.new_snapshot({"name": "x"})["topics"])
        self.assertEqual([], repository.new_snapshot({"topics": None})["topics"])

    def test_gives_every_snapshot_the_same_shape_so_a_writer_never_tests_for_a_missing_key(self):
        sparse = repository.new_snapshot({"name": "x"})
        full = repository.new_snapshot({name: "v" for name in repository.snapshot_properties()})
        self.assertEqual(set(sparse.keys()), set(full.keys()))


class TheFourCasesThatMatter(unittest.TestCase):
    def test_reports_a_declared_repository_the_api_did_not_return_as_blocked_never_as_create(self):
        # GitHub answers 404 both for a repository that does not exist and for one this
        # token cannot see. blocked is what "could not be determined" means.
        status = repository.repository_status({"name": "EXAMPLE-repo"}, None)
        self.assertEqual("blocked", status.status)
        self.assertEqual("resolve", status.action)
        self.assertNotIn("create", status.action)

    def test_reports_an_archived_repository_as_protected_and_plans_nothing_against_it(self):
        # Every write against an archived repository fails, so pending would produce a
        # plan whose apply cannot succeed.
        status = repository.repository_status(
            {"name": "EXAMPLE-repo", "description": "changed"},
            {"archived": True, "description": "old", "topics": []},
        )
        self.assertEqual("protected", status.status)
        self.assertEqual("skip", status.action)
        self.assertEqual([], list(status.difference))

    def test_reports_a_matching_repository_as_ok(self):
        status = repository.repository_status(
            {"name": "EXAMPLE-repo", "description": "A service."},
            {"archived": False, "description": "A service.", "topics": []},
        )
        self.assertEqual("ok", status.status)

    def test_treats_an_unset_description_and_an_empty_declared_one_as_the_same_thing(self):
        # The API returns an unset description as null and an unset homepage as an empty
        # string. Both mean "nothing there", and comparing them raw makes the plan report
        # a change the apply cannot make, forever.
        status = repository.repository_status(
            {"name": "x", "description": ""}, {"archived": False, "description": None, "topics": []}
        )
        self.assertEqual("ok", status.status)

    def test_names_the_fields_that_differ_so_the_approver_reads_fields_and_not_a_word(self):
        status = repository.repository_status(
            {"name": "x", "description": "new", "homepage": "https://example.com"},
            {"archived": False, "description": "old", "homepage": "", "topics": []},
        )
        self.assertEqual("pending", status.status)
        self.assertIn("description", status.reason)
        self.assertIn("homepage", status.reason)
        self.assertEqual({"description", "homepage"}, {d.field for d in status.difference})

    def test_does_not_report_a_change_when_the_declaration_only_repeats_live_topics(self):
        status = repository.repository_status(
            {"name": "x", "topics": ["alpha"]}, {"archived": False, "topics": ["alpha"]}
        )
        self.assertEqual("ok", status.status)

    def test_does_not_compare_a_field_the_declaration_is_silent_about(self):
        status = repository.repository_status(
            {"name": "x"}, {"archived": False, "description": "whatever", "topics": ["a"]}
        )
        self.assertEqual("ok", status.status)


class AnUndeclaredRepositoryIsAdopted(unittest.TestCase):
    def test_reports_adopt_and_warning_not_create_and_pending(self):
        # The resource exists and is being brought under management as it is, and there
        # is nothing to change until somebody writes down what it should look like.
        status = repository.undeclared_status({"topics": [], "license": None, "private": False})
        self.assertEqual("adopt", status.action)
        self.assertEqual("warning", status.status)

    def test_says_what_is_missing_because_that_is_the_finding_of_the_first_run(self):
        status = repository.undeclared_status({"topics": [], "license": None, "private": False})
        self.assertIn("no licence", status.reason)
        self.assertIn("no topics", status.reason)

    def test_marks_a_private_repository_as_private_in_its_reason(self):
        status = repository.undeclared_status({"topics": ["a"], "license": "MIT", "private": True})
        self.assertIn("private", status.reason)


class EveryVerdictIsInTheClosedVocabulary(unittest.TestCase):
    def test_returns_only_statuses_and_actions_the_plan_vocabulary_defines(self):
        verdicts = [
            repository.repository_status({"name": "x"}, None),
            repository.repository_status({"name": "x"}, {"archived": True, "topics": []}),
            repository.repository_status({"name": "x"}, {"archived": False, "topics": []}),
            repository.repository_status(
                {"name": "x", "description": "new"},
                {"archived": False, "description": "old", "topics": []},
            ),
            repository.undeclared_status({"topics": [], "license": None, "private": False}),
        ]
        for verdict in verdicts:
            with self.subTest(action=verdict.action):
                self.assertIn(verdict.action, plan_module.PLAN_ACTION)
                self.assertIn(verdict.status, plan_module.PLAN_STATUS)

    def test_refuses_to_build_a_status_outside_the_vocabulary(self):
        # Checked where the status is built, not three layers away at the plan.
        with self.assertRaises(plan_module.PlanVocabularyError):
            repository.Status(action="destroy", status="ok", reason="", difference=[])


class ListOrderDoesNotDependOnTheMachine(unittest.TestCase):
    """Measured against a live account: 81 of 99 report differences were list ORDER.

    PowerShell's Sort-Object compares case-insensitively and by current culture, so it
    ordered 'therapist...' before 'TUP_...'. Python's sorted() is ordinal and does not.
    The PowerShell side was changed to ordinal - it was the one whose output varied by
    machine locale - and these cases pin the ordering both now produce.
    """

    def test_a_topic_is_lowercased_first_so_case_never_arises_there(self):
        # Worth stating, because it is why the topic payload was NOT among the 81
        # differing fields: the API stores topics lowercase, so format_topic_name
        # lowercases them and there is no case left to order by. The names that DID
        # differ - repository names - are case-sensitive on the way in and preserved.
        union = repository.topic_union(["therapist"], ["TUP"])
        self.assertEqual(["therapist", "tup"], union.payload)

    def test_orders_a_snapshot_topic_list_ordinally(self):
        snapshot = repository.new_snapshot({"name": "x", "topics": ["therapist", "TUP"]})
        self.assertEqual(["TUP", "therapist"], snapshot["topics"])

    def test_is_stable_so_the_same_input_twice_produces_the_same_order(self):
        first = repository.topic_union(["b", "A"], ["c"]).payload
        second = repository.topic_union(["b", "A"], ["c"]).payload
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
