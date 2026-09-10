# Contributing

## Before you start

Read [AGENTS.md](AGENTS.md). It is the maintenance contract: nine non-negotiable rules,
a seven-point definition of finished, and the conventions. This file is the shorter,
practical version.

## Setup

```bash
python scripts/bootstrap.py

python -m pip install "ruff>=0.6.0,<1.0.0" "jsonschema>=4.0.0,<5.0.0"
```

The upper bound on ruff is deliberate - see the note in
[docs/guides/getting-started.md](docs/guides/getting-started.md).

## The one command

```bash
python scripts/run_tests.py
```

Parse check, ruff, the suite, sensitive data scan. CI runs exactly this, so
there is no second definition of "pass". **Warnings fail**, because
`pyproject.toml` excludes no lint rule - and because
`PSUseCompatibleSyntax` emits Warning, so ignoring warnings would mean nothing enforces
the declared 5.1 and 7.0 support floor.

Narrow it while iterating:

```bash
python scripts/run_tests.py -Skip Analyzer -Path tests/foundation
```

## Adding an automation

The full list is in
[docs/reference/automation-contract.md](docs/reference/automation-contract.md). The order
matters:

1. **Write the template and its schema first.** The shape of the declaration is the
   design; writing the code first produces a declaration shaped like the implementation.
2. Write the entry point. Reuse the foundation; add nothing GitHub-specific to the
   cross-cutting modules.
3. Register it in `foundation/config/project-context.json`.
4. Add its active configuration file name to `.gitignore`.
5. Write the guide, **including a rollback section**, and link it from `docs/README.md`.
6. Give the module a layer in `[tool.github-as-code.layers]`, or the guard fails.
7. Add a `CHANGELOG.md` entry.

## Writing tests

**Name a test after the failure it prevents**, not the function it calls.

- `it finds the next page when the header carries more than one link` - good, says what
  breaks.
- `it parses Link headers` - bad, says nothing.

**Every fixture is invented**: `EXAMPLE-owner`, `EXAMPLE-repo`, `example.com`. A test
that borrows a real repository name turns the suite into another place sensitive data
leaks from, and test files are the last place anyone thinks to look.

**Put the dangerous logic in a pure function.** Not for convenience: drift is defined
against the payload that would be sent, so a pure payload makes drift a comparison of
values and makes idempotency assertable offline from a fixture.

## Do not loosen a guard

The absence tests are absolute, and they will occasionally reject something harmless.
That happened during phase 1: the report detail had a field named `private`, which is a
count, and the guard forbidding a dictionary key of that name rejected it. It was renamed
`privateCount`.

**Rename your code. Do not add an exemption.** A guard with an exemption is a guard with
a hole, and the hole is in the check that stops this tool detaching a fork network.

If a guard is genuinely wrong - as two inherited ones were - fix the guard properly, say
so in a comment at the site, and record it in the changelog. Do not weaken it.

## Comments

Say **why**, not what. A comment restating the code is noise. A comment naming the
failure the code prevents is often the most valuable line in the file, and it is what
makes a guard survive the next person who finds it inconvenient.

## Style

Code and comments in English, ASCII only. `Set-StrictMode -Version Latest` at the top of
every file. Comment-based help on every exported function.

**A closing brace goes in column 0.** Two suites extract functions with the regex
`^function <name> \{ .*? ^\}`, and an indented closing brace breaks them with "Could not
extract".

## Commits

Imperative, lower case, saying what changed and why. Prefix with an issue number where
one exists:

```
#42 read the account listing from the authenticated endpoint
```
