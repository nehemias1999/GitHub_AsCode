# Capabilities

**Purpose.** Say exactly what this repository does today, so nobody has to infer it
from the code or from an aspiration in the README.

**Scope.** Phase 1, as shipped.

**Audience.** Anyone about to run it, or deciding whether it covers their case.

## What exists now

One automation, `repo-inventory`, and it **cannot write**. Not "does not write by
convention": `github_as_code.http` has no `-Method` parameter, and
`tests/python/test_write_boundary.py` asserts from the parse tree that the word
`Method` appears as neither a parameter nor a hashtable key anywhere in the repository.

| Command | Reads live state | Writes | Needs a token |
| --- | --- | --- | --- |
| `validate` | No | No | **No** |
| `inventory` | Yes | No | Yes, read-only |
| `plan` | Yes | No | Yes, read-only |
| `smoke` | Yes | No | Yes, read-only |

## What it reports

Per repository, from `GET /user/repos?affiliation=owner` - public and private:

name, full name, private, visibility, archived, fork, template status, description,
homepage, default branch, language, topics, licence identifier, the four feature
toggles (`has_issues`, `has_wiki`, `has_projects`, `has_discussions`), the three
timestamps, size, and open issue count.

Around eighty properties come back per repository and most of them are URL templates.
The snapshot keeps the list above and drops the rest, because even a small report carrying
everything is an unreadable megabyte and a diff between two runs of it is meaningless.

Per account:

- the total, split public and private, and the number of pages it took
- whether the listing was **truncated** - which becomes a `blocked` operation, never a
  quiet short read
- the rate limit budget remaining
- whether the token is classic or fine-grained, its scopes if classic, and the days
  until it expires if fine-grained
- the four account-level findings: which repositories have no licence, which have no
  topics, how many have wikis enabled, how many have projects enabled

## What a plan says

Every repository becomes one operation, in the vocabulary
[command-model.md](../reference/command-model.md) defines:

| Situation | action | status |
| --- | --- | --- |
| Declared, live, matches | `exists` | `ok` |
| Declared, live, differs | `update` | `pending` |
| Declared, **not returned by the API** | `resolve` | `blocked` |
| Declared, live, archived | `skip` | `protected` |
| Live, **not declared** | `adopt` | `warning` |
| Account listing truncated | `resolve` | `blocked` |

On an account nobody has ever declared, every repository lands in the `adopt`/`warning`
row. That is the finding of a first run, not a fault in it.

## Exit codes

Because the result has a consumer that is not a person reading a screen.

| Code | Meaning |
| --- | --- |
| `0` | The run completed and nothing is blocked |
| `2` | The run completed and something could not be determined |
| `1` | The run itself failed |

## What is coming, and what never is

Phases 2 to 5 add `repo-standards`, `repo-metadata`, `repo-protection` and
`project-board`. What each will and will not do, and the several operations that are
**deliberately not implemented because they cannot be made safe**, are in
[scope-and-limits.md](scope-and-limits.md).
