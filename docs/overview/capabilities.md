# Capabilities

**Purpose.** Say exactly what this repository does today, so nobody has to infer it
from the code or from an aspiration in the README.

**Scope.** Phases 1 and 2, as shipped.

**Audience.** Anyone about to run it, or deciding whether it covers their case.

## What exists now

Two automations - `repo-inventory` and `repo-standards` - and **neither can write**. Not
"does not write by convention": `github_as_code.http` has no `-Method` parameter, and
`tests/python/test_write_boundary.py` asserts from the parse tree that the word
`Method` appears as neither a parameter nor a hashtable key anywhere in the repository.

Both expose the same ladder, and `repo-standards` has no `apply` at all - not a disabled
one, no such subcommand.

| Command | Reads live state | Writes | Needs a token |
| --- | --- | --- | --- |
| `validate` | No | No | **No** |
| `inventory` | Yes | No | Yes, read-only |
| `plan` | Yes | No | Yes, read-only |
| `smoke` | Yes | No | Yes, read-only |

## repo-inventory: what it reports

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

## repo-inventory: what a plan says

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

## repo-standards: what it reports

Which files each repository is missing, judged against the standard for its **class**.

The class of a repository is **not** declared here. `standards.json` names classes and
the files each requires; the repository-to-class mapping is read from the `repo-inventory`
declaration, where it is already stated. Restating it would be two files that must agree
about which class a repository is in, and the disagreement would be silent - a repository
checked against the wrong standard produces a plan that looks entirely reasonable. The
consequence is enforced offline: every class the inventory declaration uses must have a
standard, and `validate` says which do not.

| Situation | action | status |
| --- | --- | --- |
| Every required file present | `exists` | `ok` |
| Some required file missing | `add` | `pending` |
| The class requires no files (`archived`) | `validate` | `ok` |
| The class has no standard | `resolve` | `blocked` |
| **The contents could not be read** | `resolve` | `blocked` |

That last row is the whole difficulty of the phase. GitHub answers 404 both for a file
that does not exist and for one the token cannot see, so "the README is missing" and
"this token has no Contents: read" arrive identically. A token with `Metadata: read` and
without `Contents: read` lists every repository perfectly well and then 404s on every
path inside them - which, read as absence, would report twenty repositories lacking a
README with nothing anywhere saying the run could not look.

So the repository root is listed **first**, once, and it decides everything below it: if
the root is refused the repository is `blocked` and nothing at all is said about its
files; only once the root succeeds is a 404 beneath it read as absence. Read
`finding.unreadableCount` in the report before any file verdict - anything above zero
means the verdicts below it are absent rather than wrong.

Per account it also reports `finding.missingByPath`, how many repositories are missing
each declared file, which is the number that decides what to fix first.

The cost is one request per repository plus one per distinct directory the standards
name - not one per declared file, because files are checked against a listing already
fetched.

## Exit codes

Because the result has a consumer that is not a person reading a screen.

| Code | Meaning |
| --- | --- |
| `0` | The run completed and nothing is blocked |
| `2` | The run completed and something could not be determined |
| `1` | The run itself failed |

## What is coming, and what never is

Phases 3 to 5 add `repo-metadata`, `repo-protection` and `project-board`, and phase 4
adds `repo-standards apply` - which creates a missing file and never overwrites or
removes one. What each will and will not do, and the several operations that are
**deliberately not implemented because they cannot be made safe**, are in
[scope-and-limits.md](scope-and-limits.md).
