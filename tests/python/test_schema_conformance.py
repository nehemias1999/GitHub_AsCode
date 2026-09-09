"""The built-in validator agrees with a real one.

ADR 0007 refuses `jsonschema` at runtime and buys the assurance a real library would
give here instead: for every schema and document pair in the repository, plus a corpus
of hand-written invalid documents, the built-in validator and `jsonschema` must reach
the same verdict.

If `jsonschema` is not installed this **skips loudly** rather than passing silently. A
skipped conformance test and a passing one must not look alike - the same rule the
sensitive data gate follows when it names the layers that ran, and the reason
`scripts/run_tests.py` prints the skip count on every run.
"""

import json
import unittest

from github_as_code import schema
from support import REPO_ROOT

try:
    import jsonschema

    JSONSCHEMA_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by not having it installed
    JSONSCHEMA_AVAILABLE = False


def _is_valid_by_library(document, rule) -> bool:
    validator = jsonschema.validators.validator_for(rule)(rule)
    return not list(validator.iter_errors(document))


REPOSITORIES_SCHEMA = REPO_ROOT / "automations/repo-inventory/schemas/repositories.schema.json"
CONTEXT_SCHEMA = REPO_ROOT / "foundation/schemas/project-context.schema.json"

# Documents whose verdict both engines must agree on. Every value is invented.
def _declaration(**overrides):
    document = {
        "$schema": "../schemas/repositories.schema.json",
        "classes": {"service": {"description": "A service."}},
        "repositories": [{"name": "EXAMPLE-repo", "class": "service"}],
    }
    document.update(overrides)
    return document


INVALID_CORPUS = [
    ("an empty class description", _declaration(classes={"service": {"description": ""}})),
    ("no classes at all", _declaration(classes={})),
    ("an empty repository list", _declaration(repositories=[])),
    (
        "a name written as owner/repo",
        _declaration(repositories=[{"name": "EXAMPLE-owner/EXAMPLE-repo", "class": "service"}]),
    ),
    (
        "a name longer than GitHub stores",
        _declaration(repositories=[{"name": "E" * 101, "class": "service"}]),
    ),
    (
        "an undeclared property on a repository",
        _declaration(repositories=[{"name": "EXAMPLE-repo", "class": "service", "extra": 1}]),
    ),
    (
        "a class that is not a string",
        _declaration(repositories=[{"name": "EXAMPLE-repo", "class": 1}]),
    ),
    (
        "a missing required property",
        _declaration(repositories=[{"name": "EXAMPLE-repo"}]),
    ),
    ("a repositories value that is not an array", _declaration(repositories={})),
]


@unittest.skipUnless(
    JSONSCHEMA_AVAILABLE,
    "jsonschema is not installed, so the differential conformance check did NOT run. "
    'Install it with: python -m pip install "jsonschema>=4.0.0,<5.0.0"',
)
class TheBuiltinAgreesWithARealValidator(unittest.TestCase):
    def test_agrees_on_every_document_the_repository_ships(self):
        pairs = [
            (REPO_ROOT / "automations/repo-inventory/config/repositories.example.json",
             REPOSITORIES_SCHEMA),
            (REPO_ROOT / "foundation/config/project-context.json", CONTEXT_SCHEMA),
        ]
        for document_path, schema_path in pairs:
            with self.subTest(document=document_path.name):
                document = json.loads(document_path.read_text(encoding="utf-8"))
                rule = json.loads(schema_path.read_text(encoding="utf-8"))
                builtin = not schema.validate(document, rule, rule)
                self.assertEqual(_is_valid_by_library(document, rule), builtin)

    def test_agrees_on_every_document_that_should_be_rejected(self):
        rule = json.loads(REPOSITORIES_SCHEMA.read_text(encoding="utf-8"))
        for label, document in INVALID_CORPUS:
            with self.subTest(document=label):
                builtin = not schema.validate(document, rule, rule)
                self.assertEqual(
                    _is_valid_by_library(document, rule),
                    builtin,
                    f"The two validators disagree about {label}.",
                )

    def test_agrees_that_every_document_in_the_corpus_really_is_invalid(self):
        # Guards the corpus itself. A document that both engines accept agrees
        # perfectly and tests nothing, which is how a conformance suite rots into a
        # list of valid documents.
        rule = json.loads(REPOSITORIES_SCHEMA.read_text(encoding="utf-8"))
        for label, document in INVALID_CORPUS:
            with self.subTest(document=label):
                self.assertNotEqual([], schema.validate(document, rule, rule))


if __name__ == "__main__":
    unittest.main()
