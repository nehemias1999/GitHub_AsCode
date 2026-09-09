"""Every keyword the repository's schemas use is one the validator implements.

This is the guard ADR 0007 adds, and it is the reason one engine is better than two.
The reduced validator's principle - anything it cannot check it ignores rather than
guessing - is right on its own terms and is a hole without this: the day somebody adds
`oneOf` to a schema, validation silently stops covering it. Nothing fails. The schema
says one thing and the tool checks another, and the only symptom is a bad declaration
getting through.

With two engines the question "is this keyword covered?" has no single answer, which is
why this guard could not have existed before.
"""

import json
import unittest

from github_as_code import schema
from support import REPO_ROOT


def schema_files():
    """Every schema the repository ships, from both places they live."""
    found = sorted(REPO_ROOT.glob("foundation/schemas/*.schema.json"))
    found += sorted(REPO_ROOT.glob("automations/*/schemas/*.schema.json"))
    return found


class TheValidatorCoversWhatTheSchemasUse(unittest.TestCase):
    def test_has_schemas_to_read_at_all(self):
        # Without this the guard passes over an empty list and reports the same green
        # as a run that checked something.
        self.assertTrue(schema_files(), "No *.schema.json found, so this guard read nothing.")

    def test_uses_no_keyword_outside_the_implemented_set(self):
        offenders = []
        for path in schema_files():
            document = json.loads(path.read_text(encoding="utf-8"))
            unknown = schema.keywords_used(document) - schema.KNOWN_KEYWORDS
            for keyword in sorted(unknown):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {keyword}")

        self.assertEqual(
            [],
            offenders,
            "These schemas use keywords the built-in validator does not implement, so the "
            "part of the schema they govern is not being checked. Implement the keyword in "
            "src/github_as_code/schema.py, or add it to ANNOTATION_KEYWORDS with the reason "
            "it carries no constraint:\n  " + "\n  ".join(offenders),
        )

    def test_every_shipped_schema_is_itself_valid_json(self):
        # A schema that does not parse validates nothing, and the failure would surface
        # as an unrelated error inside a run rather than here.
        for path in schema_files():
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))


class TheRealSchemasAreEnforcedAndWereNot(unittest.TestCase):
    """Measured, not predicted.

    The shipped schemas use `pattern`, `minLength`, `maxLength`, `minItems`,
    `maxItems`, `uniqueItems`, `minimum`, `maximum` and `minProperties`. The reduced
    PowerShell validator implements none of those, so on Windows PowerShell 5.1 - the
    declared support floor - the part of the schema they govern is not checked.

    That was measured on this machine rather than inferred from the code. A declaration
    whose only fault is an empty `classes.<name>.description`, against a schema that
    says `minLength: 1`, passes `validate` on 5.1 and is reported as
    `Valid (reduced validation)`. The name check that catches `owner/repo` is a
    hand-written function in the entry point, not the schema - which is exactly the
    compensation ADR 0007 describes, and why it says the code-level checks stay but
    their justification changes.

    This case pins the difference so the port cannot quietly lose it.
    """

    def test_rejects_a_declaration_the_reduced_validator_reported_as_valid(self):
        declaration = json.dumps(
            {
                "$schema": "../schemas/repositories.schema.json",
                "classes": {"service": {"description": ""}},
                "repositories": [{"name": "EXAMPLE-repo", "class": "service"}],
            }
        )
        result = schema.validate_document(
            declaration,
            REPO_ROOT / "automations/repo-inventory/schemas/repositories.schema.json",
        )
        self.assertFalse(result.is_valid)
        self.assertTrue(any("minimum of 1 characters" in error for error in result.errors))

    def test_rejects_a_repository_name_written_as_owner_slash_repo(self):
        # Caught today by Format-GitHubRepositoryName in the entry point, after the
        # schema has already said the document is valid. Here the schema catches it,
        # and the code-level check stays for the better message.
        declaration = json.dumps(
            {
                "$schema": "../schemas/repositories.schema.json",
                "classes": {"service": {"description": "A service."}},
                "repositories": [{"name": "EXAMPLE-owner/EXAMPLE-repo", "class": "service"}],
            }
        )
        result = schema.validate_document(
            declaration,
            REPO_ROOT / "automations/repo-inventory/schemas/repositories.schema.json",
        )
        self.assertFalse(result.is_valid)
        self.assertTrue(any("pattern" in error for error in result.errors))

    def test_accepts_the_shipped_template_unchanged(self):
        # The other direction, and the one a stricter validator breaks: everything the
        # repository ships must still pass. A validator that gained keywords and started
        # rejecting the template would have been caught here rather than in CI.
        template = REPO_ROOT / "automations/repo-inventory/config/repositories.example.json"
        result = schema.validate_document(
            template.read_text(encoding="utf-8"),
            REPO_ROOT / "automations/repo-inventory/schemas/repositories.schema.json",
        )
        self.assertEqual([], result.errors)

    def test_accepts_the_project_context_unchanged(self):
        context = REPO_ROOT / "foundation/config/project-context.json"
        result = schema.validate_document(
            context.read_text(encoding="utf-8"),
            REPO_ROOT / "foundation/schemas/project-context.schema.json",
        )
        self.assertEqual([], result.errors)


class TheTwoKeywordSetsStaySeparate(unittest.TestCase):
    def test_no_keyword_is_both_enforced_and_annotation_only(self):
        # An overlap would make the guard's message wrong: it would say a keyword is
        # deliberately without effect while the validator enforces it, or the reverse.
        self.assertEqual(
            frozenset(), schema.IMPLEMENTED_KEYWORDS & schema.ANNOTATION_KEYWORDS
        )

    def test_the_composition_keywords_are_deliberately_absent(self):
        # If one of these is ever implemented, this test is what says the decision was
        # made rather than drifted into.
        for keyword in ("oneOf", "anyOf", "allOf", "not", "if", "format"):
            with self.subTest(keyword=keyword):
                self.assertNotIn(keyword, schema.KNOWN_KEYWORDS)


if __name__ == "__main__":
    unittest.main()
