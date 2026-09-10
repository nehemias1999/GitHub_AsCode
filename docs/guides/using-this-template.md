# Using this template

**Purpose.** The steps between generating a repository from this template and a `plan`
that reports zero pending.

**Scope.** First-time setup of a generated repository.

**Audience.** Whoever just clicked *Use this template*.

## What you are getting

A read-only inventory of a GitHub account, plus the scaffolding four more automations are
meant to be built on. It **cannot write to GitHub** — `github_as_code.http` has no
`-Method` parameter, and a test asserts the word appears nowhere in the repository.
Widening that is [ADR 0001](../adr/0001-write-boundary.md), and it names the four files
the change must touch together.

The documentation is the larger half of the deliverable. Read
[github-notes.md](../reference/github-notes.md) before writing any code against the
GitHub API — it is fourteen behaviours where the obvious implementation destroys
something.

## 1. Make it yours

Six things carry a placeholder or somebody else's name. The genericity test
(`tests/python/`) fails on the first two until you fix them.

| What | Where | Do |
| --- | --- | --- |
| `TEMPLATE-AUTHOR` | `pyproject.toml` and `LICENSE` | Replace with your name or handle |
| Copyright holder | `LICENSE` | Put your name and the current year |
| `<owner>` | `CHANGELOG.md`, the two link definitions at the bottom | Your account, or delete the links |
| Version | `pyproject.toml` and `src/github_as_code/__init__.py` | Leave at `0.1.0`, or reset if you are restarting the history. The two are kept in step by hand |
| `CHANGELOG.md` | the `[0.1.0]` entry | It describes the template's own first release. Keep it as provenance, or replace it with your own first entry |
| Repository description and topics | your new repository on GitHub | It has neither. Ironically, that is one of the findings this tool reports |

Nothing else needs renaming. `GitHubAsCode.*` and `GitHub.*` are the product's module
namespaces, not the author's.

## 2. Define your repository classes

This is the one design decision the template cannot make for you.

`automations/repo-inventory/config/repositories.example.json` ships four example classes
— `service`, `library`, `tool`, `archived`. They are a starting point, not a
prescription. Replace them with the groups your account actually has.

Two rules, both load-bearing:

- **A class is declared, never inferred from the name.** A rule that guesses which class
  a repository is in will one day guess wrong about the one repository where it matters.
- **The schema does not constrain the class names** (`classes` is
  `additionalProperties`), so you can use anything. The cross-check that a repository's
  `class` exists happens in `validate`, offline, because JSON Schema cannot express a
  reference between two parts of one document.

## 3. Create a token

Settings → Developer settings → Personal access tokens → **Fine-grained tokens**.

Not a classic token, and the reason is structural rather than hygienic: **there is no
fine-grained permission equivalent to deleting a repository.** Choosing the token type
removes the capability instead of restricting it. See
[ADR 0004](../adr/0004-fine-grained-tokens-only.md).

For phase 1, read-only:

- Repository access: **All repositories** — the inventory's job is completeness
- Permissions: **Metadata: read**, **Contents: read**, **Issues: read**,
  **Administration: read**

Set the shortest expiry you can live with; `inventory` reports the days remaining.

## 4. Bootstrap and go

```bash
python scripts/bootstrap.py

# Fill in GITHUB_OWNER and GITHUB_TOKEN_READ in .env.
# The three write tokens stay empty: nothing reads them until phase 3.

python automations/repo-inventory/inventory.py validate    # offline
python automations/repo-inventory/inventory.py inventory   # reads
```

**Check the private count in the summary.** If your account has private repositories and
it reports zero, something is reading the public endpoint — the exact failure this
automation exists to prevent.

Then derive your declaration from the report rather than writing it by hand:

```bash
Move-Item .\automations\repo-inventory\config\repositories.example.json `
          .\automations\repo-inventory\config\repositories.json
```

Move it, do not copy it. The active name is what `.gitignore` excludes; a copy
invites a differently named file full of real repository names that Git happily tracks —
and on an account with private repositories, the names alone are worth excluding.

Edit it to describe what the report found, then:

```bash
python automations/repo-inventory/inventory.py plan
python automations/repo-inventory/inventory.py plan
```

Twice, deliberately. A second run reporting the same operations is what proves the
inventory and the comparison agree. Full walkthrough:
[getting-started.md](getting-started.md).

## 5. Before you commit anything

```bash
python scripts/run_tests.py
```

Parse check, ruff (**any finding fails**), the suite, and a sensitive data scan. CI runs
the identical command on Linux and Windows, on the oldest and newest supported
interpreter.

`pyproject.toml` excludes **no lint rule**, on purpose: six inherited
exclusions were measured at zero findings and removed. If a rule fires on your code, add
the exclusion **with a reason measured in your repository** — a finding count and the
shapes it flagged. An exclusion carrying somebody else's reason is worse than one with no
reason, because it looks answered.

## Building the next phase

`AGENTS.md` is the maintenance contract: nine non-negotiable rules and a seven-point
definition of finished. Read it before adding anything.

[automation-contract.md](../reference/automation-contract.md) lists the seven things
every automation must ship, and step 2 of *Adding one* is the one people skip: **write
the template and its schema first.** The shape of the declaration is the design.

The phases the documentation refers to:

| Phase | Automation | Writes |
| --- | --- | --- |
| 1 | `repo-inventory` | No — this is what you have |
| 2 | `repo-standards`, read-only | No — this is what you have |
| 3 | `repo-metadata` | **Yes** — the first writer, and the ADR 0001 boundary change |
| 4 | `repo-standards apply`, `repo-protection` | Create-only / never |
| 5 | `project-board` | Additive, over GraphQL |

A mitigation in `github-notes.md` prefixed with a phase describes code that does not
exist yet.
