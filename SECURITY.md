# Security policy

## Reporting

This is a reference implementation, not a supported product. If you find a security
problem in it, open an issue describing the **class** of problem - not a working exploit
and not a step-by-step extraction path.

## What this repository can do to an account

Currently: **nothing.** There is no code path that writes.

That is not a convention. `github_as_code.http` has no `-Method` parameter, and
`tests/python/test_write_boundary.py` walks the parse tree asserting the word
`Method` appears as neither a command parameter nor a hashtable key anywhere in the
repository. Widening it is [ADR 0001](docs/adr/0001-write-boundary.md).

`DELETE` never appears at all, at any phase.

## The credential

Use a **fine-grained** personal access token. The reason is structural rather than a
matter of hygiene:

> There is no fine-grained permission equivalent to deleting a repository. Deleting
> requires a classic token with the `delete_repo` scope. Choosing the token type
> therefore removes the capability rather than restricting it.

Every other guard here is a statement about this code. That one is a statement about the
credential, and it holds against code nobody has written yet.
See [ADR 0004](docs/adr/0004-fine-grained-tokens-only.md).

Phase 1 needs one token, read-only: Metadata, Contents, Issues and Administration, all
`read`. Later phases add three more, one per role, so no automation can reach past its
job.

## Keeping credentials out of output

Four layers, at different points:

1. **A base URL carrying a credential is rejected.** `assert_base_url` refuses
   userinfo, so `https://token@host` cannot become the string every later message
   quotes.
2. **Redirects are not followed.** An `Authorization` header forwarded to whatever host
   a 30x points at is a credential disclosure driven by a response body. The usual cause
   is a wrong base URL, so a redirect is reported as the configuration problem it is.
3. **Every console line passes one funnel.** `Write-ModuleLog` applies
   `protect_secrets_in_text` before writing, so a log line added later cannot reintroduce a
   leak.
4. **The report writer redacts by value.** `remove_sensitive_values` walks the object
   before serialising, so a token that reached a nested property still does not reach the
   file.

A `Link` header pointing at another host is also refused, for the same reason as (2): it
is server-controlled input, and following it would send the `Authorization` header there.

## The sensitive data gate

`scripts/check_sensitive_data.py` runs as part of the quality gate, in two layers.

**Structural** - shapes that are credentials whatever they contain: private key blocks,
JWTs, cloud access keys, assigned secrets, and GitHub's token prefixes. Two rules cover
GitHub: `gh[pousr]_` for the classic shapes and `github_pat_` for the fine-grained one.

**Deny list** - terms from `.local/sensitive-terms.txt`, which is excluded from version
control. A fresh clone has none, so the layer is off unless `-RequireDenyTerms` is
passed: a check that cannot pass on a fresh clone is a check people learn to ignore. The
gate says which layers answered rather than reporting an unqualified pass.

## Files that must never be committed

`.gitignore` excludes them, and the maintenance contract forbids reading them:

| Path | Holds |
| --- | --- |
| `.env`, `.env.*` | Real tokens |
| `.local/` | Workstation scratch space, deny terms, credentials handed over out of band |
| `artifacts/` | Reports and run logs, which describe a real account |
| `automations/*/config/*.json` (not `*.example.json`) | Real repository names - and on an account with private repositories, the existence of a given private repository is not public |

The active configuration is produced by **renaming** its template, not by copying it.
The active name is what `.gitignore` excludes; a copy invites a differently named file
full of real values that Git happily tracks.

## Deliberately not implemented

Writing GitHub Actions secrets. It requires NaCl sealed-box encryption against the
repository public key; the standard library has neither X25519 nor XSalsa20-Poly1305, so there is no implementation without a
dependency, and it would violate *names, not values*.

Branch protection has no `apply` and never will, in part because
`enforce_admins: true` with one required review locks a single-owner account out of its
own default branch, recoverable only through the web interface. See
[docs/overview/scope-and-limits.md](docs/overview/scope-and-limits.md).
