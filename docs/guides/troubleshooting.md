# Troubleshooting

**Purpose.** Symptom, cause, fix - for the failures that actually happen here.

**Scope.** Phase 1.

**Audience.** Anyone whose run just did something they did not expect.

## The report shows fewer repositories than the account has

**This is the failure this repository exists to prevent, so check it first.**

Symptom: the total looks plausible but is short, and `privateCount` is 0 on an account
that has private repositories.

Cause, in order of likelihood:

1. **The token cannot see them.** A fine-grained token scoped to *selected*
   repositories returns only those. The inventory's job is completeness, so scope it to
   **all repositories**.
2. **The listing was truncated.** Look for a `blocked` operation named
   `accountListing`. Raise `defaults.maximumPageCount` in
   `foundation/config/project-context.json`. It is reported as blocked rather than
   truncated quietly, precisely so this is visible.
3. **Something is reading the public endpoint.** `GET /users/{user}/repos` returns
   public repositories only, so it omits every private repository. There is an absence
   test asserting no code path builds a `users/` path, so this should be impossible; if it
   happens, that test has a hole.

Cross-check against a different tool:

```bash
gh repo list your-login --limit 200 | wc -l
gh api graphql -f query='{ viewer { repositories(first:1, ownerAffiliations:OWNER) { totalCount } } }'
```

## HTTP 401

The token was rejected. Three causes, and the third is the one people miss:

- it is not set - `load_environment` treats `.env` as optional, so a
  missing file is not an error
- it was revoked
- **it expired.** A fine-grained token has a fixed expiry date, and the resulting 401
  is indistinguishable from revocation. `inventory` reports the days remaining for
  exactly this reason; check the `token.daysUntilExpiry` field in the report.

## HTTP 403

Three unrelated causes share this status code, so read the headers before acting:

| `x-ratelimit-remaining` | Cause | Fix |
| --- | --- | --- |
| A healthy number | The token lacks the permission for this endpoint | The body names the missing fine-grained permission. See [security-model.md](../reference/security-model.md) |
| 0 | The primary limit is exhausted | Wait until `x-ratelimit-reset` |
| A healthy number, with `retry-after` | A **secondary** limit was tripped by a burst | Honour `retry-after`. Do not retry immediately - that escalates it into a longer block |

A 403 with no `User-Agent` on the request is also possible, but this repository always
sends one.

## A declared repository is reported blocked, not created

Working as intended. GitHub answers 404 both for a repository that does not exist and
for one the token cannot see, and the two are indistinguishable from the client. The
tool does not guess.

Check, in order: the spelling of the name; whether the token's repository access
includes it; whether it was renamed.

## Every repository is reported as adopt / warning

Also working as intended, and it is what a first run looks like. Nothing has declared
anything yet. Derive the declaration from the report - step 6 of
[getting-started.md](getting-started.md).

## Plan reports the same change on every run

An idempotency failure, and always one of these:

- **A description longer than 350 characters.** GitHub truncates it, so the declared
  value can never compare equal. `validate` catches this offline.
- **A topic differing only in case.** GitHub lowercases topics on the way in.
  `topic_union` normalises before comparing, so this should not happen; if it
  does, the normalisation has a hole.
- **A homepage declared as `null` rather than an empty string.** The API returns an
  unset homepage as `""` and an unset description as `null`, inconsistently.

## `validate` fails but the JSON looks fine

The declaration satisfies its schema and is still not executable. The message lists
each problem. Usually one of:

- a `class` that is not a key of the `classes` object - a cross-reference JSON Schema
  cannot express
- a topic that is not storable: uppercase is fine and normalised, but `c#` or a space
  is rejected rather than mangled into a topic nobody chose
- a `-RepositoryName` naming something not declared, which would silently narrow the
  run to nothing

## The gate fails on a lint finding

Every finding fails, deliberately. `pyproject.toml` declares
`Severity = @('Error','Warning')`, so they were already in scope; a runner that ignored
them made the whole documented exclusion list decorative. It matters specifically too:
no exclusions at all, so nothing here is switched off on another codebase's evidence.

Fix the finding, or add a `noqa` on the line **with its reason beside it**. A
project-wide exclusion needs evidence measured here: the finding count, the shapes
flagged, and why changing the code would be worse.

## The gate says a check was skipped

The differential schema conformance check needs `jsonschema`, a development dependency
that is not always installed. It skips rather than passing, and the run prints every skip
with its reason - a skipped conformance test and a passing one must not look alike:

```bash
python -m pip install "jsonschema>=4.0.0,<5.0.0"
```

The sensitive data gate reports the same way. With no `.local/sensitive-terms.txt` it
runs its structural rules only, and says so in its summary line rather than reporting an
unqualified pass.

## A GraphQL call returns empty rather than failing

Not reachable in phase 1 - there is no GraphQL client yet - but worth knowing now,
because it is the trap the whole design of `github_as_code.graphql` answers.

GraphQL reports failure with **HTTP 200** and an `errors` array. A client checking only
the status code reads `FORBIDDEN` or `RATE_LIMITED` as an empty result. That was
observed live: a `projectsV2` query with a token lacking `read:project` returned 200
carrying `INSUFFICIENT_SCOPES`. See row 9 of
[github-notes.md](../reference/github-notes.md).

## Where the evidence is

| What | Where |
| --- | --- |
| Report, machine-readable | `artifacts/repo-inventory/*.json` |
| Report, readable | `artifacts/repo-inventory/*.md` |
| Run transcript | the `runLog` path named in the report |

All of `artifacts/` is excluded from version control.
