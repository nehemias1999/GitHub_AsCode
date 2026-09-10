"""Every automation ships what the contract requires, and the docs stay reachable.

Ported from the PowerShell suite for the same reason as the genericity guards: none of
this is about PowerShell. It is about an automation being a coherent unit - an entry
point, a versioned template, a schema, a guide, a registration, and a documented
rollback - and about the documentation index not drifting from the documents.

`docs/reference/automation-contract.md` states the contract. This file is what makes it
a contract rather than a wish.
"""

import json
import re
import unittest

from github_as_code import configuration
from support import REPO_ROOT

CONTEXT_PATH = REPO_ROOT / "foundation/config/project-context.json"
AUTOMATIONS = ("repo-inventory", "repo-standards")


def project_context() -> dict:
    return json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))


class EveryAutomationShipsWhatTheContractRequires(unittest.TestCase):
    def test_there_is_an_automation_to_check_at_all(self):
        # Guards the guard: an empty list would make every case below pass vacuously.
        self.assertTrue(AUTOMATIONS)

    def test_has_an_entry_point_a_versioned_template_a_schema_and_a_guide(self):
        for name in AUTOMATIONS:
            with self.subTest(automation=name):
                root = REPO_ROOT / "automations" / name
                self.assertTrue((root / "README.md").is_file(), "the guide")
                self.assertTrue(
                    any(root.glob("*.py")), "an entry point"
                )
                self.assertTrue(
                    any(root.glob("config/*.example.json")), "a versioned template"
                )
                self.assertTrue(any(root.glob("schemas/*.schema.json")), "a schema")

    def test_excludes_its_active_configuration_from_version_control(self):
        # The active declaration names real repositories, and on an account with private
        # ones the name alone is not public.
        ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for name in AUTOMATIONS:
            with self.subTest(automation=name):
                for template in (REPO_ROOT / "automations" / name / "config").glob(
                    "*.example.json"
                ):
                    active = template.name.replace(".example", "")
                    self.assertTrue(
                        any(active in line for line in ignore.splitlines()),
                        f"{active} must be excluded from version control",
                    )

    def test_declares_the_schema_that_governs_its_template(self):
        # The pairing lives next to the data instead of in a lookup table that drifts.
        for name in AUTOMATIONS:
            for template in (REPO_ROOT / "automations" / name / "config").glob(
                "*.example.json"
            ):
                with self.subTest(template=template.name):
                    document = json.loads(template.read_text(encoding="utf-8"))
                    self.assertIn("$schema", document)
                    resolved = configuration.resolve_path(
                        document["$schema"], template.resolve().parent
                    )
                    self.assertTrue(resolved.is_file(), f"{resolved} does not exist")

    def test_is_registered_in_the_project_context(self):
        automations = project_context()["automations"]
        for name in AUTOMATIONS:
            with self.subTest(automation=name):
                self.assertIn(name, automations)
                self.assertIn("configuration", automations[name])
                self.assertIn("template", automations[name])

    def test_documents_rollback_in_its_guide(self):
        # Nothing writes yet, so rollback is currently "delete the report". Saying that
        # is still the contract: an automation whose guide is silent about undoing it is
        # one nobody can approve.
        for name in AUTOMATIONS:
            with self.subTest(automation=name):
                guide = (REPO_ROOT / "automations" / name / "README.md").read_text(
                    encoding="utf-8"
                )
                self.assertRegex(guide, r"(?i)rollback|undo|revert")


class TheCommandSurfaceIsTheLadderTheContractDescribes(unittest.TestCase):
    def entry_point(self, name: str) -> str:
        module = REPO_ROOT / "src/github_as_code/automations" / f"{name.replace('-', '_')}.py"
        return module.read_text(encoding="utf-8")

    def test_exposes_validate_inventory_plan_and_smoke(self):
        for name in AUTOMATIONS:
            with self.subTest(automation=name):
                source = self.entry_point(name)
                self.assertIn('choices=("validate", "inventory", "plan", "smoke")', source)

    def test_exposes_no_verb_that_writes(self):
        # apply, reconcile and their relatives do not exist, and the ladder is the place
        # somebody would add one first.
        for name in AUTOMATIONS:
            with self.subTest(automation=name):
                source = self.entry_point(name)
                choices = re.search(r"choices=\(([^)]*)\)", source).group(1)
                for verb in ("apply", "reconcile", "create", "remove", "rename"):
                    self.assertNotIn(f'"{verb}"', choices)

    def test_has_no_confirmation_switch_because_nothing_here_needs_confirming(self):
        # Promising a capability that does not exist is the first thing somebody reaches
        # for. There is no write to gate, so there is no gate.
        for name in AUTOMATIONS:
            with self.subTest(automation=name):
                source = self.entry_point(name).lower()
                for switch in ("--confirm", "confirm_apply", "--force"):
                    self.assertNotIn(switch, source)


class TheEnvironmentTemplateDeclaresNamesAndHoldsNoValue(unittest.TestCase):
    def test_leaves_every_token_empty(self):
        # .env.example is committed. A value in it is a committed credential.
        for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            if "TOKEN" in name.upper() or "SECRET" in name.upper():
                with self.subTest(variable=name):
                    self.assertEqual("", value.strip(), f"{name} must ship empty")

    def test_declares_every_token_the_project_context_names(self):
        # The context names the variables; the template is where an operator fills them
        # in. A name in one and not the other is a run that fails on a missing value with
        # nothing telling the reader where to put it.
        template = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        github = project_context()["github"]
        for key, value in github.items():
            if not key.endswith("Env"):
                continue
            with self.subTest(variable=value):
                self.assertIn(value, template)


class DocumentationIsIndexed(unittest.TestCase):
    def test_links_every_document_from_the_documentation_index(self):
        # A document nobody can reach from the index is one nobody reads, and it drifts
        # from the code silently.
        index = (REPO_ROOT / "docs/README.md").read_text(encoding="utf-8")
        missing = []
        for path in sorted((REPO_ROOT / "docs").rglob("*.md")):
            if path.name == "README.md" and path.parent.name == "docs":
                continue
            relative = path.relative_to(REPO_ROOT / "docs").as_posix()
            if relative not in index:
                missing.append(relative)
        self.assertEqual([], missing, "\n".join(missing))


class TheProjectContextDeclaresNothingItSilentlyIgnores(unittest.TestCase):
    def test_either_reads_every_default_or_says_in_the_schema_that_it_does_not(self):
        # A setting nobody reads is a promise the tool does not keep, and it is worse
        # than a missing one: somebody changes it and nothing happens.
        #
        # The exception is declared rather than assumed. A default reserved for a later
        # phase says so in its own schema description, so a reader of the context finds
        # out from the context instead of by grepping the source and finding nothing.
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in (REPO_ROOT / "src").rglob("*.py")
        )
        schema = json.loads(
            (REPO_ROOT / "foundation/schemas/project-context.schema.json").read_text(
                encoding="utf-8"
            )
        )
        described = schema["properties"]["defaults"]["properties"]

        unread = []
        for key in project_context()["defaults"]:
            if key in source:
                continue
            if "read by nothing yet" in described.get(key, {}).get("description", "").lower():
                continue
            unread.append(key)

        self.assertEqual(
            [],
            unread,
            f"declared, read by nothing, and not marked as reserved in the schema: {unread}",
        )


if __name__ == "__main__":
    unittest.main()
