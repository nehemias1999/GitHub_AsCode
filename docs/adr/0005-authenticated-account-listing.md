# 0005 - The account listing endpoint

**Status.** Accepted, phase 1.

## Context

Two endpoints list an account's repositories:

- `GET /users/{username}/repos` - takes the login in the path, needs no authentication
- `GET /user/repos` - takes no login, returns the repositories of whoever the token
  belongs to

The first reads more naturally when the owner is a value in a configuration file, and it
is the one an implementation reaches for.

**It returns public repositories only.**

Measured against one real account:

| Source | Repositories |
| --- | --- |
| Profile `public_repos` | 14 |
| Public repository search | 15 |
| **`GET /user/repos`, authenticated** | **24** |

Nine private repositories - a third of that account - are invisible to the first
endpoint. It does not report a partial result, does not warn, and does not differ in
shape. It returns a complete-looking array.

## Decision

**The account listing is read from `GET /user/repos` with `affiliation=owner`, and an
absence test asserts that no string literal anywhere in the repository begins with
`users/`.**

`affiliation=owner` rather than the default, because the default also returns
repositories the account collaborates on or reaches through an organisation, which are
not the account's to configure.

The rationale is recorded in the report itself, under `listing.rationale`, so a reader
of a report a year from now can see which endpoint produced the number and why.

## Consequences

Every command past `validate` needs a token, including `inventory`. That is a real cost
- there is no anonymous mode - and it is the correct one. An inventory that is complete
only sometimes, without saying which time, is not an inventory; and this one exists
specifically to be the input to a decision about which repositories to keep.

The absence test is worth more than the correct call site. A future automation could
reasonably build `users/{owner}/repos` for something else entirely and re-introduce the
gap in a different file. The test is repository-wide for that reason.

This is also the first row of [github-notes.md](../reference/github-notes.md), and one
of the two marked *measured* rather than *documented* - because the documentation states
the difference plainly, and it is still the trap that would have been walked into.
