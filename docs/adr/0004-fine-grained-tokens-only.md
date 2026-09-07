# 0004 - Fine-grained tokens, because they cannot delete

**Status.** Accepted, phase 1.

## Context

Three ways to authenticate against the GitHub API: a classic personal access token, a
fine-grained one, or a GitHub App.

The account this was written for currently authenticates with an OAuth token holding
`gist, read:org, repo, workflow`. It reads everything needed. It also carries admin over
every repository the account owns, which includes branch protection - the one thing
[scope-and-limits.md](../overview/scope-and-limits.md) says must never be written from
here.

## Decision

**Fine-grained personal access tokens, one per role.** Not classic, and not a GitHub
App.

The decisive reason is not least privilege in the abstract:

> **There is no fine-grained permission equivalent to deleting a repository.** Deleting
> requires a classic token with the `delete_repo` scope.

So choosing the token type **removes the capability** rather than restricting it. Every
other guard in this repository - the absence tests, the allowlists, the reviews - is a
statement about our code. This one is a statement about the credential, and it holds
even against code nobody has written yet.

Secondary: a classic token with `repo` is all-or-nothing and cannot be narrowed to
`description` and `topics`.

**GitHub App: no.** For a single-user account it adds a JWT-to-installation-token cycle,
and signing RS256 on Windows PowerShell 5.1 means hand-parsing a PKCS#1 PEM key through
ASN.1 - which breaks the rule that this repository depends on nothing beyond PowerShell
and git.

Revisit if either becomes true: the account becomes an organisation with several
people, or commits need attributing to a non-human actor.

## Consequences

Four environment variables instead of one, and four tokens to renew. Fine-grained
tokens expire on a fixed date, so `inventory` reports the days remaining and `plan`
warns below a threshold - without which the first symptom is a 401 that reads exactly
like revocation and sends the reader looking in the wrong place.

Two preflights become possible, both from a response header rather than an assumption.
A classic token answers with `x-oauth-scopes` and a fine-grained one does not, so any
`apply` run aborts before its first write if that header is present, and
`repo-inventory` warns when it sees `delete_repo` in the list - a scope that should not
exist, not merely one that should not be used.

The mapping from endpoint to fine-grained permission is verified against the
documentation as each writer is built, not from memory. Fine-grained tokens return a 403
**naming** the missing permission, and that is captured in the status message map.
