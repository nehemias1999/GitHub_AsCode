"""Loading declared state, and refusing to turn a configuration file into a program.

Ported case by case from GitHubAsCode.Foundation.Tests.ps1, with the protected-name
list rewritten rather than translated: the names that make a .env file dangerous are
the ones that steer the interpreter reading it, and that interpreter is no longer
PowerShell.
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from github_as_code import configuration

REPOSITORIES_SCHEMA = "../schemas/repositories.schema.json"


class EnvFileIsConfigurationAndNotCode(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.original = dict(os.environ)
        self.addCleanup(self._restore_environment)

    def _restore_environment(self):
        os.environ.clear()
        os.environ.update(self.original)

    def _write(self, text: str) -> Path:
        path = self.root / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_refuses_a_name_that_moves_where_python_finds_code(self):
        # The whole reason the name is constrained. A .env file is operator-edited,
        # unsigned and unhashed, and this function writes what it names into the
        # process environment - so PYTHONPATH=/somewhere/share would be honoured by
        # the next import, and the file stops being configuration.
        path = self._write("PYTHONPATH=/somewhere/share\n")
        with self.assertRaises(configuration.ConfigurationError) as caught:
            configuration.load_environment(str(path))
        self.assertIn("PYTHONPATH", str(caught.exception))

    def test_still_refuses_the_names_that_steer_the_other_interpreter(self):
        # Both implementations read the same .env during the transition, so a name that
        # is harmless to Python is not harmless to the PowerShell run reading the line
        # beside it. The list is a union, not a translation.
        for name in ("PSModulePath", "LD_PRELOAD", "DOTNET_STARTUP_HOOKS"):
            with self.subTest(name=name):
                path = self._write(f"{name}=anything\n")
                with self.assertRaises(configuration.ConfigurationError):
                    configuration.load_environment(str(path))

    def test_matches_a_protected_name_whatever_its_casing(self):
        # The Windows environment is case-insensitive, so a rule that PATH cannot be
        # set must not be satisfiable by writing Path.
        path = self._write("pYtHoNpAtH=/somewhere\n")
        with self.assertRaises(configuration.ConfigurationError):
            configuration.load_environment(str(path))

    def test_does_not_set_the_protected_variable_before_refusing(self):
        # Raising after the assignment would be a guard that reports the problem and
        # causes it anyway.
        os.environ.pop("PYTHONSTARTUP", None)
        path = self._write("PYTHONSTARTUP=/tmp/run-me.py\n")
        with self.assertRaises(configuration.ConfigurationError):
            configuration.load_environment(str(path))
        self.assertIsNone(os.environ.get("PYTHONSTARTUP"))

    def test_rejects_a_name_that_is_not_a_variable_name(self):
        path = self._write("not a name=value\n")
        with self.assertRaises(configuration.ConfigurationError):
            configuration.load_environment(str(path))

    def test_sets_ordinary_variables_and_reports_their_names_only(self):
        path = self._write("EXAMPLE_ONE=first\nEXAMPLE_TWO=second\n")
        names = configuration.load_environment(str(path))
        self.assertEqual(["EXAMPLE_ONE", "EXAMPLE_TWO"], names)
        self.assertEqual("first", os.environ["EXAMPLE_ONE"])
        # Names, never values: this list is printed by callers.
        self.assertNotIn("first", names)

    def test_strips_surrounding_quotes_so_a_trailing_space_can_be_expressed(self):
        path = self._write('EXAMPLE_QUOTED="  padded  "\n')
        configuration.load_environment(str(path))
        self.assertEqual("  padded  ", os.environ["EXAMPLE_QUOTED"])

    def test_ignores_comments_and_blank_lines(self):
        path = self._write("# a comment\n\nEXAMPLE_ONE=first\n")
        self.assertEqual(["EXAMPLE_ONE"], configuration.load_environment(str(path)))

    def test_fails_on_a_missing_file_and_skips_it_only_when_told_to(self):
        # A run that quietly proceeds without its credentials fails later with a
        # confusing message.
        missing = str(self.root / "absent.env")
        with self.assertRaises(configuration.ConfigurationError) as caught:
            configuration.load_environment(missing)
        self.assertIn("absent.env", str(caught.exception))
        self.assertEqual([], configuration.load_environment(missing, optional=True))

    def test_splits_one_value_carrying_several_comma_separated_paths(self):
        first = self.root / "a.env"
        second = self.root / "b.env"
        first.write_text("EXAMPLE_ONE=1\n", encoding="utf-8")
        second.write_text("EXAMPLE_TWO=2\n", encoding="utf-8")
        names = configuration.load_environment(f"{first},{second}")
        self.assertEqual(["EXAMPLE_ONE", "EXAMPLE_TWO"], names)


class ARequiredValueIsNamedAndNeverEchoed(unittest.TestCase):
    def setUp(self):
        self.original = dict(os.environ)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.clear()
        os.environ.update(self.original)

    def test_names_the_variable_and_never_its_value(self):
        os.environ.pop("EXAMPLE_TOKEN", None)
        with self.assertRaises(configuration.ConfigurationError) as caught:
            configuration.required_value("EXAMPLE_TOKEN")
        self.assertIn("EXAMPLE_TOKEN", str(caught.exception))

    def test_treats_a_whitespace_only_value_as_unset(self):
        os.environ["EXAMPLE_TOKEN"] = "   "
        with self.assertRaises(configuration.ConfigurationError):
            configuration.required_value("EXAMPLE_TOKEN")

    def test_trims_the_whitespace_a_pasted_value_arrives_with(self):
        # A trailing space on a base URL or a token produces a 401 that reads like bad
        # credentials rather than like a typo.
        os.environ["EXAMPLE_TOKEN"] = "  EXAMPLE-token\n"
        self.assertEqual("EXAMPLE-token", configuration.required_value("EXAMPLE_TOKEN"))


class DuplicatesAreFoundInOnePlace(unittest.TestCase):
    def test_reports_each_duplicated_value_once(self):
        # Two entries for one resource would each report their own verdict about it.
        self.assertEqual(["a"], configuration.duplicate_values(["a", "b", "a", "a"]))

    def test_reports_nothing_for_an_empty_input_rather_than_failing(self):
        self.assertEqual([], configuration.duplicate_values([]))

    def test_reports_nothing_when_every_value_is_distinct(self):
        self.assertEqual([], configuration.duplicate_values(["a", "b"]))


class TheDeclarationIsChosenByOneRule(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "config").mkdir()
        self.context = {
            "automations": {
                "repo-inventory": {
                    "configuration": "config/repositories.json",
                    "template": "config/repositories.example.json",
                }
            }
        }

    def test_falls_back_to_the_template_so_validate_runs_in_a_fresh_clone(self):
        declaration = configuration.resolve_declaration(self.context, "repo-inventory", self.root)
        self.assertTrue(declaration.used_template)
        self.assertEqual("repositories.example.json", declaration.path.name)

    def test_says_it_used_the_template_rather_than_leaving_it_to_be_noticed(self):
        # A run must never silently check the template while the operator believes it
        # checked their own declaration - a plan built from the template describes an
        # example and not an estate. The flag is what lets a caller say so.
        declaration = configuration.resolve_declaration(self.context, "repo-inventory", self.root)
        self.assertTrue(declaration.used_template)
        self.assertIsNotNone(declaration.active_path)

    def test_prefers_the_active_declaration_when_it_exists(self):
        active = self.root / "config" / "repositories.json"
        active.write_text("{}", encoding="utf-8")
        declaration = configuration.resolve_declaration(self.context, "repo-inventory", self.root)
        self.assertFalse(declaration.used_template)
        self.assertEqual(active.resolve(), declaration.path)

    def test_an_explicit_path_wins_over_everything(self):
        active = self.root / "config" / "repositories.json"
        active.write_text("{}", encoding="utf-8")
        declaration = configuration.resolve_declaration(
            self.context, "repo-inventory", self.root, configuration_path="elsewhere.json"
        )
        self.assertFalse(declaration.used_template)
        self.assertEqual("elsewhere.json", declaration.path.name)

    def test_names_the_automation_when_the_context_does_not_describe_it(self):
        with self.assertRaises(configuration.ConfigurationError) as caught:
            configuration.resolve_declaration(self.context, "repo-standards", self.root)
        self.assertIn("repo-standards", str(caught.exception))


class LoadingAConfigurationValidatesIt(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "config").mkdir()
        (self.root / "schemas").mkdir()
        (self.root / "schemas" / "repositories.schema.json").write_text(
            json.dumps(
                {
                    "type": "object",
                    "required": ["classes"],
                    "properties": {
                        "$schema": {"type": "string"},
                        "classes": {"type": "object", "minProperties": 1},
                    },
                    "additionalProperties": False,
                }
            ),
            encoding="utf-8",
        )

    def _write(self, document) -> Path:
        path = self.root / "config" / "declaration.json"
        path.write_text(
            document if isinstance(document, str) else json.dumps(document), encoding="utf-8"
        )
        return path

    def test_reports_a_missing_file_by_name(self):
        with self.assertRaises(configuration.ConfigurationError) as caught:
            configuration.load_configuration(self.root / "config" / "absent.json")
        self.assertIn("absent.json", str(caught.exception))

    def test_reports_a_file_that_is_not_json_as_such(self):
        path = self._write("{ not json")
        with self.assertRaises(configuration.ConfigurationError) as caught:
            configuration.load_configuration(path)
        self.assertIn("not valid JSON", str(caught.exception))

    def test_refuses_a_document_that_declares_no_schema(self):
        # Shipping a schema next to a file and never running it is common and
        # worthless. Every configuration file must point at what governs it.
        path = self._write({"classes": {"service": {}}})
        with self.assertRaises(configuration.ConfigurationError) as caught:
            configuration.load_configuration(path)
        self.assertIn("$schema", str(caught.exception))

    def test_resolves_the_schema_relative_to_the_document(self):
        path = self._write({"$schema": REPOSITORIES_SCHEMA, "classes": {"service": {}}})
        self.assertEqual({"service": {}}, configuration.load_configuration(path)["classes"])

    def test_lists_every_error_rather_than_the_first(self):
        # A half-corrected file costs another round trip.
        path = self._write({"$schema": REPOSITORIES_SCHEMA, "extra": 1, "other": 2})
        with self.assertRaises(configuration.ConfigurationError) as caught:
            configuration.load_configuration(path)
        message = str(caught.exception)
        self.assertIn("missing required property 'classes'", message)
        self.assertIn("undeclared property 'extra'", message)
        self.assertIn("undeclared property 'other'", message)

    def test_names_the_engine_that_ran(self):
        path = self._write({"$schema": REPOSITORIES_SCHEMA, "classes": {}})
        with self.assertRaises(configuration.ConfigurationError) as caught:
            configuration.load_configuration(path)
        self.assertIn("builtin validation", str(caught.exception))

    def test_skips_validation_only_when_asked(self):
        path = self._write({"anything": True})
        self.assertEqual(
            {"anything": True}, configuration.load_configuration(path, skip_validation=True)
        )


if __name__ == "__main__":
    unittest.main()
