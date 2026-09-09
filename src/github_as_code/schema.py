"""One JSON Schema validator, built in, and complete against what the schemas use.

The PowerShell implementation carried two engines: `Test-Json -Schema` where the host
had it, and a reduced validator where it did not, because Windows PowerShell 5.1 has no
schema support. The choice was threaded into the provenance block, a log line, every
report and a page of documentation - all of it apology for a constraint the language
imposed.

Python removes the constraint, so ADR 0007 removes the apology. There is one engine, it
is this one, and it is named `builtin`. `jsonschema` stays a development dependency,
used by a differential test rather than at runtime: `pattern` is `re.search` and
`minLength` is `len()`, and taking a dependency for that would make ADR 0004 - which
refused an entire authentication mechanism rather than add one - read as arbitrary.

The reduced validator's principle was that **anything it cannot check it ignores rather
than guessing**. That is right, because a validator that guesses produces false
rejections. On its own it is also a hole: the day a schema grows `oneOf`, validation
silently stops covering it, and the only symptom is a bad declaration getting through.
So the keyword sets below are public, and a guard fails the gate when a schema uses
anything outside them. Coverage is a checked property, not a comment.

Two things here are wrong if done the obvious way, and both are in ADR 0007:

  - JSON Schema `pattern` is ECMA-262, where `\\d` is ASCII-only. Python's `\\d` matches
    Unicode digits, so a pattern of `^\\d+$` would accept an Arabic-Indic numeral that
    GitHub will not store. Patterns compile with `re.ASCII`.
  - `enum` and `const` must compare the type first, because `1 == True` in Python, and
    `const: 1` would otherwise accept `true`.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The engine name, pinned. It stays in the provenance block for report-shape parity
# during the transition, and a test asserts it never varies - which keeps the honesty
# property the field was added for (a report never claims coverage it did not have)
# while removing the variability that motivated it.
ENGINE = "builtin"

# Keywords this validator ENFORCES. A document violating one of these is rejected.
IMPLEMENTED_KEYWORDS = frozenset(
    {
        "$ref",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
    }
)

# Keywords that carry no constraint: they annotate, identify or hold definitions. They
# are listed rather than tolerated by accident, so that the coverage guard can tell
# "deliberately has no effect on validity" from "not implemented yet". Adding one here
# is a decision; leaving one out makes the gate fail, which is the point.
ANNOTATION_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "$defs",
        "definitions",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)

KNOWN_KEYWORDS = IMPLEMENTED_KEYWORDS | ANNOTATION_KEYWORDS

# How the walker gets from a schema node to the schemas underneath it. Written out
# rather than inferred, because inferring it is how a property NAME gets counted as a
# keyword - and a guard that reports `repositories` as an unimplemented keyword is a
# guard nobody trusts.
_SUBSCHEMA = (
    "additionalProperties",
    "items",
    "not",
    "if",
    "then",
    "else",
    "contains",
    "propertyNames",
)
_SUBSCHEMA_MAP = ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas")
_SUBSCHEMA_LIST = ("allOf", "anyOf", "oneOf", "prefixItems")


@dataclass(frozen=True)
class ValidationResult:
    """The verdict, every error rather than the first, and the engine that ran."""

    is_valid: bool
    errors: list[str]
    engine: str


def json_type(value: Any) -> str:
    """The JSON Schema type name for a parsed value.

    bool is checked before int on purpose: in Python `True` is an instance of `int`,
    so the obvious order classifies every boolean as an integer and a schema asking
    for `integer` would accept `true`.

    A float with no fractional part is an `integer`, which is what draft 2020-12 says
    and what `1.0` in a hand-edited JSON file means.

    Args:
        value: A value parsed from JSON.

    Returns:
        One of 'null', 'boolean', 'string', 'integer', 'number', 'array', 'object'.

    Example:
        >>> json_type(True)
        'boolean'
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "integer" if value.is_integer() else "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _equal(left: Any, right: Any) -> bool:
    """JSON equality, which is not Python equality.

    `1 == True` is true in Python, so `const: 1` would accept `true` and `enum: [0, 1]`
    would accept `false`. Booleans compare only with booleans; numbers compare across
    int and float, because `1` and `1.0` are the same JSON number.
    """
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(_equal(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    return left == right


def _canonical(value: Any) -> str:
    """A stable text form, for comparing items that are not hashable.

    json.dumps distinguishes `true` from `1`, which a set of Python values would not.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def resolve_reference(reference: str, root_schema: Any) -> Any | None:
    """Resolve a local $ref pointer against the schema document.

    Handles the one form this repository uses: '#/$defs/name', optionally nested. An
    external or remote reference returns None, and the caller reports that rather than
    pretending to have validated it - a validator that silently checks less than it
    appears to is worse than none.

    Args:
        reference: The $ref value.
        root_schema: The whole schema document.

    Returns:
        The referenced schema node, or None when it cannot be resolved locally.

    Example:
        >>> resolve_reference("#/$defs/repository", {"$defs": {"repository": {"type": "object"}}})
        {'type': 'object'}
    """
    if root_schema is None or not reference.startswith("#/"):
        return None

    node = root_schema
    for segment in reference[2:].split("/"):
        if not segment:
            continue
        name = segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or name not in node:
            return None
        node = node[name]
    return node


def validate(node: Any, schema: Any, root_schema: Any = None, path: str = "$") -> list[str]:
    """Validate a parsed document against a parsed schema.

    Every error is collected rather than the first, because a half-corrected file costs
    another round trip.

    Args:
        node: The value being validated.
        schema: The schema node.
        root_schema: The whole schema document, for resolving local $ref. Defaults to
            `schema`.
        path: Pointer-ish location used in messages.

    Returns:
        A list of error messages, empty when the document satisfies the schema.

    Example:
        >>> validate(5, {"type": "string"})
        ['$ expected type string but found integer']
    """
    if schema is None or schema is True:
        return []
    if schema is False:
        return [f"{path} is not allowed here"]
    if not isinstance(schema, dict):
        return []
    if root_schema is None:
        root_schema = schema

    if "$ref" in schema:
        resolved = resolve_reference(str(schema["$ref"]), root_schema)
        if resolved is None:
            return [
                f"{path} references '{schema['$ref']}', which could not be resolved "
                "in this schema"
            ]
        return validate(node, resolved, root_schema, path)

    errors: list[str] = []
    actual = json_type(node)

    if "type" in schema:
        expected = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if actual not in expected and not (actual == "integer" and "number" in expected):
            # Returning here rather than continuing: every keyword below assumes a type,
            # and reporting "expected string, found object" alongside "is missing
            # required property" describes two problems where there is one.
            return [f"{path} expected type {'|'.join(expected)} but found {actual}"]

    if "const" in schema and not _equal(node, schema["const"]):
        errors.append(f"{path} must be {_canonical(schema['const'])} but is {_canonical(node)}")

    if "enum" in schema and not any(_equal(node, option) for option in schema["enum"]):
        allowed = ", ".join(_canonical(option) for option in schema["enum"])
        errors.append(f"{path} value {_canonical(node)} is not one of: {allowed}")

    if actual == "string":
        errors.extend(_check_string(node, schema, path))
    elif actual in ("integer", "number"):
        errors.extend(_check_number(node, schema, path))
    elif actual == "array":
        errors.extend(_check_array(node, schema, root_schema, path))
    elif actual == "object":
        errors.extend(_check_object(node, schema, root_schema, path))

    return errors


def _check_string(node: str, schema: dict, path: str) -> list[str]:
    errors = []
    if "minLength" in schema and len(node) < schema["minLength"]:
        errors.append(f"{path} is shorter than the minimum of {schema['minLength']} characters")
    if "maxLength" in schema and len(node) > schema["maxLength"]:
        errors.append(f"{path} is longer than the maximum of {schema['maxLength']} characters")
    # re.ASCII, because JSON Schema patterns are ECMA-262 where \d, \w and \b are
    # ASCII-only. Without it, `^\d+$` accepts an Arabic-Indic numeral - a value this
    # tool would then send to an API that will not store it.
    #
    # re.search and not re.fullmatch: JSON Schema `pattern` is unanchored, and every
    # schema here writes its own ^ and $.
    if "pattern" in schema and not re.search(schema["pattern"], node, re.ASCII):
        errors.append(f"{path} does not match the required pattern {schema['pattern']}")
    return errors


def _check_number(node: float, schema: dict, path: str) -> list[str]:
    errors = []
    if "minimum" in schema and node < schema["minimum"]:
        errors.append(f"{path} is below the minimum of {schema['minimum']}")
    if "maximum" in schema and node > schema["maximum"]:
        errors.append(f"{path} is above the maximum of {schema['maximum']}")
    if "exclusiveMinimum" in schema and node <= schema["exclusiveMinimum"]:
        errors.append(f"{path} must be greater than {schema['exclusiveMinimum']}")
    if "exclusiveMaximum" in schema and node >= schema["exclusiveMaximum"]:
        errors.append(f"{path} must be less than {schema['exclusiveMaximum']}")
    return errors


def _check_array(node: list, schema: dict, root_schema: Any, path: str) -> list[str]:
    errors = []
    if "minItems" in schema and len(node) < schema["minItems"]:
        errors.append(
            f"{path} has {len(node)} item(s), fewer than the minimum of {schema['minItems']}"
        )
    if "maxItems" in schema and len(node) > schema["maxItems"]:
        errors.append(
            f"{path} has {len(node)} item(s), more than the maximum of {schema['maxItems']}"
        )
    if schema.get("uniqueItems"):
        seen = [_canonical(item) for item in node]
        if len(set(seen)) != len(seen):
            errors.append(f"{path} contains duplicate items where every item must be unique")
    if "items" in schema:
        for index, item in enumerate(node):
            errors.extend(validate(item, schema["items"], root_schema, f"{path}[{index}]"))
    return errors


def _check_object(node: dict, schema: dict, root_schema: Any, path: str) -> list[str]:
    errors = []
    if "minProperties" in schema and len(node) < schema["minProperties"]:
        errors.append(f"{path} has fewer than the minimum of {schema['minProperties']} properties")
    if "maxProperties" in schema and len(node) > schema["maxProperties"]:
        errors.append(f"{path} has more than the maximum of {schema['maxProperties']} properties")

    for required in schema.get("required", []):
        if required not in node:
            errors.append(f"{path} is missing required property '{required}'")

    declared = schema.get("properties", {})
    for name, value in node.items():
        if name in declared:
            errors.extend(validate(value, declared[name], root_schema, f"{path}.{name}"))
            continue
        if "additionalProperties" not in schema:
            continue
        allowed = schema["additionalProperties"]
        if isinstance(allowed, bool):
            if not allowed:
                errors.append(f"{path} has undeclared property '{name}'")
        else:
            errors.extend(validate(value, allowed, root_schema, f"{path}.{name}"))
    return errors


def keywords_used(schema: Any) -> set[str]:
    """Every schema keyword appearing anywhere in a schema document.

    The walker knows the structure explicitly rather than inferring it, because
    inferring it counts a property NAME as a keyword - and a guard reporting
    `repositories` as unimplemented is a guard nobody trusts.

    Args:
        schema: A parsed schema document.

    Returns:
        The set of keyword names used.

    Example:
        >>> sorted(keywords_used({"type": "object", "properties": {"a": {"type": "s"}}}))
        ['properties', 'type']
    """
    found: set[str] = set()
    if not isinstance(schema, dict):
        return found

    for key, value in schema.items():
        found.add(key)
        if key in _SUBSCHEMA_MAP and isinstance(value, dict):
            for child in value.values():
                found |= keywords_used(child)
        elif key in _SUBSCHEMA_LIST and isinstance(value, list):
            for child in value:
                found |= keywords_used(child)
        elif key in _SUBSCHEMA:
            found |= keywords_used(value)

    return found


def validate_document(json_text: str, schema_path: Path | str) -> ValidationResult:
    """Validate a JSON document, given as text, against a schema file.

    Args:
        json_text: The document.
        schema_path: Path to the schema file.

    Returns:
        A ValidationResult naming the engine that ran.

    Example:
        >>> validate_document('{}', "does-not-exist.json").is_valid
        False
    """
    schema_file = Path(schema_path)
    if not schema_file.is_file():
        return ValidationResult(False, [f"Schema file not found: {schema_file}"], "none")

    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    document = json.loads(json_text)
    errors = validate(document, schema, schema)
    return ValidationResult(not errors, errors, ENGINE)
