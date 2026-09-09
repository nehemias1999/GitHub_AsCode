"""The one validator, and the two ways of getting it wrong that ADR 0007 names.

The reduced PowerShell validator had no tests: it was written to fill a gap in
Windows PowerShell 5.1 and its correctness was assumed. This one takes over from it and
gains the keywords it deliberately skipped, so the cases that matter are the ones where
a naive Python implementation is silently wrong.
"""

import unittest

from github_as_code import schema


class TypesAreClassifiedTheWayJsonMeansThem(unittest.TestCase):
    def test_a_boolean_is_not_an_integer(self):
        # In Python `True` is an instance of `int`, so the obvious isinstance order
        # classifies every boolean as an integer and `type: integer` accepts `true`.
        self.assertEqual("boolean", schema.json_type(True))
        self.assertEqual([], schema.validate(True, {"type": "boolean"}))
        self.assertNotEqual([], schema.validate(True, {"type": "integer"}))

    def test_an_integer_satisfies_a_schema_asking_for_a_number(self):
        self.assertEqual([], schema.validate(3, {"type": "number"}))

    def test_a_whole_float_is_an_integer_which_is_what_a_hand_edited_file_means(self):
        self.assertEqual("integer", schema.json_type(1.0))
        self.assertEqual([], schema.validate(1.0, {"type": "integer"}))

    def test_a_fractional_number_is_not_an_integer(self):
        self.assertNotEqual([], schema.validate(1.5, {"type": "integer"}))

    def test_reports_the_type_mismatch_alone_rather_than_everything_below_it(self):
        # Reporting "expected object, found string" alongside "is missing required
        # property 'name'" describes two problems where there is one, and sends the
        # reader to the wrong line.
        errors = schema.validate("text", {"type": "object", "required": ["name"]})
        self.assertEqual(1, len(errors))


class ConstAndEnumCompareTheTypeFirst(unittest.TestCase):
    def test_const_one_does_not_accept_true(self):
        # `1 == True` in Python. Without a type check this passes, and a declaration
        # saying `true` where the schema pins `1` gets through.
        self.assertNotEqual([], schema.validate(True, {"const": 1}))
        self.assertEqual([], schema.validate(1, {"const": 1}))

    def test_enum_of_zero_and_one_does_not_accept_false(self):
        self.assertNotEqual([], schema.validate(False, {"enum": [0, 1]}))

    def test_const_true_still_accepts_true(self):
        self.assertEqual([], schema.validate(True, {"const": True}))

    def test_enum_compares_a_string_by_value(self):
        self.assertEqual([], schema.validate("plan", {"enum": ["plan", "apply"]}))
        self.assertNotEqual([], schema.validate("delet" + "e", {"enum": ["plan", "apply"]}))


class PatternIsEcmaScriptAndNotPython(unittest.TestCase):
    def test_a_digit_class_does_not_match_a_non_ascii_digit(self):
        # JSON Schema patterns are ECMA-262, where \\d is ASCII-only. Python's \\d
        # matches Unicode digits, so without re.ASCII a schema saying `^\\d+$` accepts
        # an Arabic-Indic numeral - which this tool would then send to an API that will
        # not store it, and the rejection would arrive from GitHub rather than from
        # validate.
        arabic_indic = "١٢٣"
        self.assertNotEqual([], schema.validate(arabic_indic, {"pattern": r"^\d+$"}))
        self.assertEqual([], schema.validate("123", {"pattern": r"^\d+$"}))

    def test_a_word_class_does_not_match_a_non_ascii_letter(self):
        self.assertNotEqual([], schema.validate("café", {"pattern": r"^\w+$"}))

    def test_a_pattern_is_unanchored_so_the_schema_writes_its_own_anchors(self):
        self.assertEqual([], schema.validate("EXAMPLE-repo", {"pattern": "repo"}))
        self.assertNotEqual([], schema.validate("EXAMPLE-repo", {"pattern": "^repo$"}))


class TheKeywordsTheReducedValidatorSkippedAreEnforced(unittest.TestCase):
    def test_enforces_string_length_bounds(self):
        self.assertNotEqual([], schema.validate("ab", {"minLength": 3}))
        self.assertNotEqual([], schema.validate("abcd", {"maxLength": 3}))
        self.assertEqual([], schema.validate("abc", {"minLength": 3, "maxLength": 3}))

    def test_enforces_numeric_bounds_inclusive_and_exclusive(self):
        self.assertNotEqual([], schema.validate(1, {"minimum": 2}))
        self.assertNotEqual([], schema.validate(3, {"maximum": 2}))
        self.assertNotEqual([], schema.validate(2, {"exclusiveMinimum": 2}))
        self.assertNotEqual([], schema.validate(2, {"exclusiveMaximum": 2}))
        self.assertEqual([], schema.validate(2, {"minimum": 2, "maximum": 2}))

    def test_enforces_item_counts(self):
        self.assertNotEqual([], schema.validate([], {"minItems": 1}))
        self.assertNotEqual([], schema.validate([1, 2], {"maxItems": 1}))

    def test_enforces_unique_items_without_confusing_true_and_one(self):
        # A set of Python values would treat `[1, true]` as a duplicate pair, because
        # `hash(1) == hash(True)`. The comparison goes through the JSON text form.
        self.assertNotEqual([], schema.validate([1, 1], {"uniqueItems": True}))
        self.assertEqual([], schema.validate([1, True], {"uniqueItems": True}))

    def test_enforces_property_counts(self):
        self.assertNotEqual([], schema.validate({}, {"minProperties": 1}))
        self.assertNotEqual([], schema.validate({"a": 1, "b": 2}, {"maxProperties": 1}))


class WhatTheReducedValidatorAlreadyDidStillHappens(unittest.TestCase):
    def test_reports_a_missing_required_property(self):
        errors = schema.validate({}, {"type": "object", "required": ["name"]})
        self.assertIn("missing required property 'name'", errors[0])

    def test_rejects_an_undeclared_property_when_additional_are_forbidden(self):
        document = {"name": "EXAMPLE-repo", "extra": 1}
        rule = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": False,
        }
        self.assertNotEqual([], schema.validate(document, rule))

    def test_validates_an_undeclared_property_against_the_additional_schema(self):
        rule = {"properties": {}, "additionalProperties": {"type": "string"}}
        self.assertNotEqual([], schema.validate({"a": 1}, rule))
        self.assertEqual([], schema.validate({"a": "x"}, rule))

    def test_follows_a_local_ref_into_defs(self):
        # Every schema in this repository factors its item definitions into $defs, so a
        # validator that could not follow a $ref would check nothing below it - which is
        # worse than not validating, because it looks like validation.
        document = {"items": [{"name": 1}]}
        rule = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/item"}}},
            "$defs": {"item": {"type": "object", "properties": {"name": {"type": "string"}}}},
        }
        self.assertNotEqual([], schema.validate(document, rule))

    def test_reports_an_unresolvable_ref_rather_than_passing_over_it(self):
        errors = schema.validate({}, {"$ref": "https://example.com/schema.json"})
        self.assertIn("could not be resolved", errors[0])

    def test_collects_every_error_rather_than_the_first(self):
        # A half-corrected file costs another round trip.
        rule = {"type": "object", "required": ["a", "b", "c"]}
        self.assertEqual(3, len(schema.validate({}, rule)))

    def test_names_the_path_so_the_reader_finds_the_line(self):
        rule = {"properties": {"repositories": {"items": {"type": "string"}}}}
        errors = schema.validate({"repositories": [1]}, rule)
        self.assertIn("$.repositories[0]", errors[0])


class TheEngineNameNeverVaries(unittest.TestCase):
    def test_is_always_builtin(self):
        # It stays in the provenance block for report-shape parity, pinned. The field
        # existed because the engine could differ; keeping it constant preserves the
        # honesty property while removing what motivated it.
        self.assertEqual("builtin", schema.ENGINE)


class TheKeywordWalkerReadsKeywordsAndNotPropertyNames(unittest.TestCase):
    def test_does_not_count_a_property_name_as_a_keyword(self):
        used = schema.keywords_used(
            {"type": "object", "properties": {"minimum": {"type": "string"}}}
        )
        # 'minimum' here is the name of a property, not a bound on a number. A walker
        # that inferred structure would report it as a keyword and the coverage guard
        # would be reporting noise.
        self.assertEqual({"type", "properties"}, used)

    def test_finds_a_keyword_nested_inside_defs(self):
        used = schema.keywords_used({"$defs": {"item": {"pattern": "^x$"}}})
        self.assertIn("pattern", used)

    def test_finds_a_keyword_this_validator_does_not_implement(self):
        # The whole point of the guard: `oneOf` must be visible so the gate can fail on
        # it, rather than being quietly ignored at validation time.
        used = schema.keywords_used({"oneOf": [{"type": "string"}]})
        self.assertIn("oneOf", used)
        self.assertNotIn("oneOf", schema.KNOWN_KEYWORDS)


if __name__ == "__main__":
    unittest.main()
