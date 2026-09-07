# Security model

**Purpose.** Say which token to use, why that type and not another, and what stops a
credential reaching a file or a report.

**Scope.** Authentication, secret handling, and the guards that enforce both.

**Audience.** Anyone setting this up, and anyone reviewing a change to it.

## Use fine-grained tokens

Not a preference. The reason is structural, and it is the strongest guarantee in this
repository:

> **There is no fine-grained permission equivalent to deleting a repository.**
> Deleting requires a classic token with the `delete_repo` scope. Choosing the token
> type therefore *removes the capability* rather than restricting it.

A convention says "we do not delete repositories". This says the credential cannot.
See [ADR 0004](../adr/0004-fine-grained-tokens-only.md).

The secondary reason: a classic token with `repo` is all-or-nothing. It includes admin
over your own repositories, which means branch protection. There is no way to narrow it
to `description` and `topics`.

## One token per role

| Variable | Permissions | Used by |
| --- | --- | --- |
| `GITHUB_TOKEN_READ` | Metadata: read · Contents: read · Issues: read · Administration: read | `repo-inventory`, `repo-protection` (all verbs), `repo-standards` validate and plan |
| `GITHUB_TOKEN_WRITE_METADATA` | the read set, plus Administration: write · Issues: write | `repo-metadata apply` only |
| `GITHUB_TOKEN_WRITE_CONTENT` | the read set, plus Contents: write | `repo-standards apply` only |
| `GITHUB_TOKEN_PROJECTS` | Projects: read (write from phase 5) | `project-board` only |

Splitting them is not ceremony: it means `repo-standards` **cannot** touch a project
board, and `project-board` cannot write a file. Only `repo-inventory` exists today, so
only the first one needs a value.

Phase 1 needs `GITHUB_TOKEN_READ` and nothing else.

## Names, not values

The configuration declares the **name** of every value the automations read. That is
what makes the whole configuration committable.

- `foundation/config/project-context.json` holds `readTokenEnv`, `ownerEnv` and so on -
  variable names.
- `.env.example` is the only file that names values, and it ships with all of them
  empty. An absence test asserts every variable whose name matches
  `TOKEN|SECRET|PASSWORD|KEY` has no value in it.
- **The account name is an environment variable too.** An account login is identifying
  data, and `project-context.json` is committed.

## Two preflights

Both read a response header, so both are facts rather than assumptions.

**Classic tokens are refused for any `apply`.** A classic PAT answers with
`x-oauth-scopes`; a fine-grained one does not. If that header is present when a write
is about to run - or if it lists `delete_repo` - the run aborts with exit 1 **before
the first write**. `repo-inventory` only warns, because reading with a classic token is
harmless.

**Expiry is warned about.** Fine-grained tokens send
`github-authentication-token-expiration`. `inventory` reports the days remaining and
`plan` warns below `tokenExpiryWarningDays`. Without this, the first symptom of an
expired token is a 401 that reads exactly like revocation, which sends the reader
looking in the wrong place. `validate` is offline, cannot know, and does not pretend to.

## Keeping credentials out of output

Four layers, at different points:

1. **The URL is rejected if it carries a credential.** `Assert-HttpBaseUrl` refuses
   userinfo in the base URL, so `https://token@host` cannot become the thing every
   later message quotes.
2. **Redirects are not followed.** `MaximumRedirection = 0`. An `Authorization` header
   forwarded to whatever host a 30x points at is a credential disclosure driven by a
   response body. The usual cause is a wrong base URL, so a redirect is reported as the
   configuration problem it is.
3. **Every console line passes one funnel.** `Write-ModuleLog` applies
   `Protect-SecretInText` before writing, so a log line added later cannot reintroduce
   a leak. Masking at each call site would depend on remembering.
4. **The report writer redacts by value.** `Remove-SensitiveValue` walks the object
   before serialising, so a token that reached a nested property still does not reach
   the file.

## The sensitive data gate

`scripts/Test-NoSensitiveData.ps1` runs as part of the quality gate, in two layers:

**Structural** - shapes that are credentials whatever they contain: private key blocks,
JWTs, cloud access keys, assigned secrets, and the GitHub token prefixes. Two rules
cover GitHub: `gh[pousr]_` for the classic shapes, and `github_pat_` for the
fine-grained one.

> The fine-grained rule is new here. The inherited version covered only the five `gh*_`
> prefixes, so the one credential a reader of *this* repository is most likely to be
> holding - the type it recommends - was the one shape the gate could not see.

**Deny list** - terms from `.local/sensitive-terms.txt`, which is excluded from version
control. A fresh clone has none, so this layer is off unless `-RequireDenyTerms` is
passed. A check that cannot pass on a fresh clone is a check people learn to ignore. CI
seeds the file from a secret and then requires it.

## What is deliberately not handled

Writing Actions secrets. It needs NaCl sealed-box encryption against the repository
public key, there is no pure PowerShell 5.1 implementation without a dependency, and it
would violate *names, not values*. See
[scope-and-limits.md](../overview/scope-and-limits.md).
