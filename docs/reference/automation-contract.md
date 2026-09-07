# The automation contract

**Purpose.** State the seven things every automation must provide, so a new one is
consistent with the existing ones without anyone having to read them.

**Scope.** Any module under `automations/`.

**Audience.** Anyone adding or reviewing an automation.

Adapted from a sibling project that applies the same pattern to Azure DevOps, where this
document is the most reusable artefact in the repository and is not specific to that
platform. It is not specific to GitHub either.

## The seven items

| # | Requirement |
| --- | --- |
| 1 | A complete configuration **template**, versioned, plus the active file name it is renamed to. |
| 2 | The active file **excluded from version control** - created by *renaming* the template, never by copying its contents. |
| 3 | An isolated **JSON Schema**, validated at run time. Not documentation. |
| 4 | A single **entry point** `Invoke-<Module>.ps1` exposing at least `validate` and `plan`. |
| 5 | A **guide** covering purpose, configuration, commands, permissions, output and **rollback**. |
| 6 | **Tests** using fixtures that contain no real data. |
| 7 | Its own **workflow definition**, only if it runs from GitHub Actions. |

Items 1 to 6 are enforced by `tests/automations/Automations.Tests.ps1`. A contract
nothing checks is a wish.

## Item by item

### 1 and 2 - the template, and the rename

The template is versioned and complete: somebody can read it and see the whole shape,
with placeholder values. The active file is produced by **renaming** it.

```text
repositories.example.json  ->  repositories.json    (excluded)
```

Renaming rather than copying is not a style preference. The active name is the one
`.gitignore` excludes; a copy invites a file with a *new* name, full of real values,
that Git happily tracks.

The test here is whether the file would still make sense in somebody else's account. A
list of repository names would not - and on an account with private repositories it is
worse than untidy, because the fact that a given private repository exists is not
public. Every name in a committed template is prefixed `EXAMPLE-`, and a test asserts
it.

### 3 - a schema that runs

Every configuration file points at its schema with a relative `$schema` property, and
`Get-GitHubAsCodeConfiguration` resolves it and validates before returning.

Shipping a schema and never running it is common and worthless: the schema documents an
intention while the loader accepts anything, and the two drift apart with nobody
noticing. Here `validate` actually validates, **offline**, so a malformed declaration
fails in a second instead of halfway through a run.

On PowerShell 5.1 there is no `Test-Json -Schema`, so a reduced validator runs. The
result **names the engine that ran**, so a report never claims more coverage than it
had - the reduced engine ignores `pattern`, `minimum`, `minItems` and the `oneOf`
family.

What a schema cannot express goes in the entry point's `validate` section: a
cross-reference between two parts of one document (a repository's `class` must be a key
of `classes`), and anything needing a function the schema has no access to (a topic
must survive `Format-GitHubTopicName`).

### 4 - one entry point

One file, one command surface, one `-Command` parameter with a closed value list. See
[command-model.md](command-model.md).

`inventory` and `smoke` are strongly recommended. Additional verbs are fine when they
name a distinct operational intent, and each carries its own confirmation. What must
**not** happen is a generic verb loaded with selectors that change what it means.

### 5 - a guide, including rollback

Purpose, configuration field by field, commands, the token permissions needed, where
output goes, and how to reverse what was done.

Rollback is the section people skip and the one that matters at two in the morning. It
is allowed to say *"nothing to reverse, because nothing was written"* - which is the
honest answer for every automation in phase 1 - but it is not allowed to be absent. The
contract test checks the word is there; a reviewer checks it means something.

### 6 - tests with invented fixtures

Every fixture is invented: `EXAMPLE-owner`, `EXAMPLE-repo`, `example.com`. A test that
borrows a real repository name, host name or credential turns the suite into another
place sensitive data leaks from, and test files are the last place anyone thinks to
look.

Name a test after the failure it prevents, not the function it calls.

### 7 - a workflow, if it runs from Actions

`workflow_dispatch` only, a closed command list, `permissions: contents: read`, secrets
materialised at run time, artefacts uploaded with `if: always()`.

No parameter belonging to one automation may appear in another's definition. And a verb
whose blast radius is not bounded by its plan - `reconcile`, `rename` - is absent from
every workflow, because it is not something to trigger from a form.

## Rules that cut across all seven

| Rule | Why |
| --- | --- |
| The declaration names a secret, never holds one. | It is what makes the whole configuration committable. |
| Nothing is deleted. | The blast radius of a delete is not in the declaration. |
| A collection is written as a union, not a replacement. | `PUT /topics` replaces everything. See [github-notes.md](github-notes.md). |
| `apply` refuses a plan with any blocked operation. | Partial application of an approved plan produces a state nobody declared. |
| Every operation carries a reason written for the approver. | `pending` on its own is not reviewable. |
| A second run changes nothing. | Idempotency is the acceptance criterion, not an optimisation. |
| An interrupted run leaves a receipt. | The next run is a resume, not a guess. |

## Adding one

1. Create `automations/<name>/` with `config/` and `schemas/`.
2. Write the template and its schema **first**. The shape of the declaration is the
   design.
3. Write the entry point. Reuse the foundation; add nothing GitHub-specific to
   `GitHubAsCode.*`.
4. Register the module in `foundation/config/project-context.json`.
5. Add its active configuration file name to `.gitignore`.
6. Write the guide and link it from `docs/README.md`.
7. Add a row to `$script:Automation` in the contract test.
8. Add a `CHANGELOG.md` entry.

Step 3 is where the pressure appears. If the shared layer seems to need a special case
for your module, the logic belongs in your module - see
[architecture.md](architecture.md).
