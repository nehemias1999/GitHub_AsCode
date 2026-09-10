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
board, and `project-board` cannot write a file. Only `repo-inventory` and
`repo-standards` exist today, and both read, so only the first one needs a value.

Phases 1 and 2 need `GITHUB_TOKEN_READ` and nothing else. Phase 2 is what makes
**Contents: read** load-bearing rather than merely listed: without it `repo-standards`
reports every repository as `blocked`, because on GitHub a 404 means either "no such
file" or "this token cannot see it" and reading the second as the first would produce a
plan claiming files are missing that are simply out of view.

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

**Classic tokens will be refused for any `apply` - phase 3.** A classic PAT answers with
`x-oauth-scopes`; a fine-grained one does not, so the header is a reliable test. When the
first writer exists, that check aborts the run with exit 1 before the first write if the
header is present or lists `delete_repo`.

What is live **now** is the reporting half: `repo-inventory` reads the header and warns,
because reading with a classic token is harmless. A refusal has nothing to refuse until
there is a write path - see [ADR 0001](../adr/0001-write-boundary.md).

**Expiry is warned about.** Fine-grained tokens send
`github-authentication-token-expiration`. `inventory` reports the days remaining and
`plan` warns below `tokenExpiryWarningDays`. It lands in the report under
`detail.authentication`, alongside whether the token is classic and which permissions it
carries - none of which is secret, and all of which is the evidence somebody needs when
a scheduled run starts failing. That block was named `detail.token` until the redaction
layer, which matches by property name, deleted it on every run. Without this, the first symptom of an
expired token is a 401 that reads exactly like revocation, which sends the reader
looking in the wrong place. `validate` is offline, cannot know, and does not pretend to.

## Keeping credentials out of output

Four layers, at different points:

1. **The URL is rejected if it carries a credential.** `assert_base_url` refuses
   userinfo in the base URL, so `https://token@host` cannot become the thing every
   later message quotes.
2. **Redirects are not followed.** `MaximumRedirection = 0`. An `Authorization` header
   forwarded to whatever host a 30x points at is a credential disclosure driven by a
   response body. The usual cause is a wrong base URL, so a redirect is reported as the
   configuration problem it is.
3. **Every console line passes one funnel.** `Write-ModuleLog` applies
   `protect_secrets_in_text` before writing, so a log line added later cannot reintroduce
   a leak. Masking at each call site would depend on remembering.
4. **The report writer redacts twice, by different keys.** `remove_sensitive_values`
   walks the object and redacts by property **name**; `protect_secrets_in_text` redacts by
   **value**, which is what catches a credential sitting under an innocent-looking name
   or inside free text.

   The earlier wording here credited `remove_sensitive_values` with redacting by value. It
   does not, and the distinction is the whole reason there are two layers: a name-based
   rule cannot see a token in a URL or in an error message, and a value-based rule cannot
   know that a field called `credentialsId` is only a reference.

## Where `.env` lives

`bootstrap.py` restricts the new `.env` to the current user - inheritance dropped, one
access rule - and says so in its output.

It is best-effort: a filesystem that will not take an ACL must not stop somebody setting
the repository up, so a failure warns and the run continues. The warning is the point,
because "the file is protected" and "the file inherits the directory" call for different
care about where the clone lives.

Under a user profile the inherited permissions are already restrictive. At the root of a
data disk, in a shared directory, or on a network share they are not - and a build agent
checkout is exactly where a clone ends up outside a profile.

Done with `icacls` rather than `Set-Acl`, and that is a measured choice: writing a
security descriptor through .NET needs the account's domain to be reachable, and it fails
with a trust-relationship error on a domain-joined machine that is offline. A hardening
step that only works on the network is not hardening.

## The sensitive data gate

`scripts/check_sensitive_data.py` runs as part of the quality gate, in two layers:

**Structural** - shapes that are credentials whatever they contain: private key blocks,
JWTs, cloud access keys, assigned secrets, and the GitHub token prefixes. Two rules
cover GitHub: `gh[pousr]_` for the classic shapes, and `github_pat_` for the
fine-grained one.

`protect_secrets_in_text` carries the same two prefixes plus a `Bearer` rule, for the same
reason from the other direction: the gate stops a credential being committed, and the
masker stops one being printed.

> The fine-grained rule is new here. The inherited version covered only the five `gh*_`
> prefixes, so the one credential a reader of *this* repository is most likely to be
> holding - the type it recommends - was the one shape the gate could not see.

**Deny list** - terms from `.local/sensitive-terms.txt`, which is excluded from version
control. A fresh clone has none, so this layer is off unless `-RequireDenyTerms` is
passed. A check that cannot pass on a fresh clone is a check people learn to ignore. CI
seeds the file from a secret and then requires it.

## What is deliberately not handled

Writing Actions secrets. It needs NaCl sealed-box encryption against the repository
public key. The standard library has neither X25519 nor XSalsa20-Poly1305, so doing it without a dependency means implementing curve25519 by hand - and it
would violate *names, not values*. See
[scope-and-limits.md](../overview/scope-and-limits.md).
