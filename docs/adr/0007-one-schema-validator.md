# 0007 - One schema validator, built in, and complete against what the schemas use

**Status.** Accepted, alongside [ADR 0006](0006-python-and-the-standard-library.md).

## Context

`GitHubAsCode.Configuration` carries a hand-written JSON Schema validator of about 190
lines. It implements `type`, `const`, `enum`, `required`, `properties`,
`additionalProperties`, `items` and local `$ref`, and deliberately implements neither
`pattern`, `minLength`/`maxLength`, `minimum`/`maximum`, `minItems`/`uniqueItems`,
`format`, nor the `oneOf` family.

It exists for exactly one reason: **Windows PowerShell 5.1 has no `Test-Json -Schema`.**
`Get-GitHubAsCodeSchemaEngine` probes for the capability and picks an engine, and the
choice is threaded all the way through - into the provenance block, into a log line, into
every report, and into `docs/reference/automation-contract.md`. All of that is apology for
a constraint the language imposed.

The gap is patched by hand elsewhere: `Format-GitHubRepositoryName` and
`Format-GitHubTopicName` re-implement in code the `pattern` and length checks the schema
cannot enforce, and `Invoke-RepositoryInventory.ps1` calls them from `validate` for that
reason.

Python removes the constraint. Three options were open: keep one built-in validator, take
a dependency on `jsonschema`, or keep the capability probe and use `jsonschema` when it
happens to be importable.

## Decision

**One validator, always, written here, named `builtin`. Extended to cover every keyword
the repository's schemas actually use, and guarded so that stays true.**

`jsonschema` becomes a **development** dependency, used only by a differential test.

## Why not the other two

**Not a runtime dependency on `jsonschema`.** ADR 0004 refused an entire authentication
mechanism rather than take a dependency. `pattern` is `re.search` and `minLength` is
`len()`; a dependency for that would make ADR 0004 read as arbitrary rather than as a
rule.

**Not the capability probe** - and this is the option worth arguing against explicitly,
because it looks like the conservative choice and is the worst of the three. The probe
exists because PowerShell 5.1 *forced* two engines on the project, not because two engines
are good design. Reproducing it in Python would reintroduce them voluntarily, and with
them the property that a declaration can pass `validate` on a workstation where
`jsonschema` happens to be installed and fail in CI where it is not. A validator whose
strictness depends on what is installed is not a contract.

## What changes

`pattern`, `minLength`, `maxLength`, `minimum`, `maximum`, `exclusiveMinimum`,
`exclusiveMaximum`, `minItems`, `maxItems`, `uniqueItems`, `minProperties` and
`maxProperties` are implemented. Each is a few lines. The documented gap in
`automation-contract.md` closes, and the code-level name checks stay - their justification
changes from "because the validator cannot" to "because the error message is better".

Two details that are wrong if done the obvious way. JSON Schema `pattern` is ECMA-262,
where `\d` is ASCII-only, while Python's `\d` matches Unicode digits - so patterns compile
with `re.ASCII`, and a test covers a non-ASCII digit. And `enum`/`const` comparison must
check the type first, because `1 == True` is true in Python and `const: 1` would otherwise
accept `true`.

The `schemaEngine` field stays in the provenance block, pinned to the constant `builtin`
with a test asserting it never varies. Removing the field would break report-shape parity
during the transition; keeping it constant preserves the honesty property - a report never
claims coverage it did not have - while removing the variability that motivated it.

## The part that is new: a coverage guard

The current validator's stated principle is that **anything it cannot check it ignores
rather than guessing**. That is right - a validator that guesses produces false
rejections - but as it stands it is a hole. The day somebody adds `oneOf` to
`automations/*/schemas/*.schema.json`, validation silently stops covering it. Nothing
fails. The schema says one thing and the tool checks another, and the only symptom is a
bad declaration getting through.

So: a guard walks every `*.schema.json` under `foundation/schemas/` and
`automations/*/schemas/`, collects every keyword used, and **fails the gate if any keyword
falls outside the implemented set**. Then a schema that grows `oneOf` fails loudly, and
somebody either implements it or writes down the decision not to.

This is only possible because there is one engine to be complete *against*. With two, the
question "is this keyword covered?" has no single answer.

## Consequences

Coverage becomes a checked property instead of a comment. The engine-selection code, its
log line and its documentation paragraphs go away.

The assurance a real library would give is bought in the test suite instead of at runtime:
for every schema and document pair in the repository, plus a corpus of hand-written invalid
documents, the built-in validator and `jsonschema` must reach the same verdict. If
`jsonschema` is not installed that test **reports a skip** rather than passing silently -
a skipped conformance test and a passing one must not look alike, which is the same rule
the sensitive-data gate already follows when it names the layers that ran.
