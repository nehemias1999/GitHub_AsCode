# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Configuration schemas are a contract.** An incompatible change to a schema under
`automations/*/schemas/` is a major version, whatever it does to the code.

## [Unreleased]

## [0.1.0] - 2026-09-07

First release. Phase 1: the read-only account inventory.

### Added

- **`repo-inventory`** - reads every repository the account owns, public and private,
  and reports how live state differs from the declaration. Commands `validate`,
  `inventory`, `plan` and `smoke`. No `apply`, and no code path that could write.
- **`foundation/`** - six modules. `GitHubAsCode.Configuration`, `.Http`, `.Plan` and
  `.Report` are ported from `Jenkins_AsCode` with the namespace renamed and fresh
  GUIDs; `GitHub.Rest` and `GitHub.Repository` are new.
- `Get-HttpLinkHeaderTarget` - an RFC 5988 `Link` header parser, so the next page is
  read from the response rather than computed. Segmentation is driven by the angle
  brackets, not by splitting on commas, because a comma appears inside query values.
- `New-BearerAuthorizationHeader` - trims the token and rejects one containing
  whitespace, which is what a value pasted across a line break produces.
- `Get-GitHubTopicUnion` - the payload is the union of live and declared topics, and
  undeclared ones are reported as preserved. `PUT /topics` replaces the whole
  collection, so sending only the declared list would delete every topic added by hand.
- `Get-GitHubTokenShape` - tells a classic token from a fine-grained one by the
  `x-oauth-scopes` header, and reads the fine-grained expiry date.
- **Five ADRs**: the write boundary, one HTTP layer with two clients, additive by
  default, fine-grained tokens only, and the account listing endpoint.
- **Absence tests, read from the parse tree**: no write, no `DELETE`, no `delete_repo`,
  no `users/` path, no destructive `PATCH` field as a hashtable key, and no
  `ConvertTo-Json` without `-Depth`.
- `docs/reference/github-notes.md` - fourteen API behaviours where the obvious
  implementation destroys something, two of them marked *measured* rather than
  *documented*.
- CI: the quality gate on `windows-latest` under both `powershell` and `pwsh`, plus each
  automation's `validate` against its template. `permissions: contents: read`.

### Changed, relative to the inherited code

Five defects were found in the ported `Jenkins_AsCode` code and fixed here. Each is
noted in a comment at the site, so the difference between the two repositories reads as
deliberate rather than as drift.

- **`scripts/Invoke-Tests.ps1` no longer runs the suite on an untested Pester major.**
  The inherited version selected the highest installed version and imported it with
  `-MinimumVersion 5.0`, which was correct when 5.x was the only 5-or-later major. With
  Pester 6 installed - as it is on the development workstation - it silently ran the
  suite on an untested major and reported the result as if it were tested. It now
  prefers the newest version in `[5.5, 6.0)` and falls back to a newer major only with
  a warning saying so.
- **The "network I/O in exactly one place" absence test now reads the parse tree.** The
  inherited version matched raw file content, so it fired on any file that merely
  *mentioned* `Invoke-WebRequest` in a comment. `GitHub.Rest.psm1` explains the
  transport boundary in its header, so the guard would have had to be satisfied by
  deleting the explanation.
- **`scripts/Test-NoSensitiveData.ps1` gained a `github_pat_` rule.** The inherited
  `GitHubToken` rule covered only the five `gh*_` classic prefixes, so the fine-grained
  format - the token type this repository recommends - was the one shape the gate could
  not see.
- **`PSScriptAnalyzerSettings.psd1` now excludes nothing.** Six exclusions arrived with
  the port, each carrying a reason written against the other codebase. All six were
  measured here and found to have **zero findings**, so keeping them would have switched
  off six rules on borrowed evidence. The clearest case was
  `PSUseConsistentIndentation`, whose reason cited "every one of the 198 findings" - a
  count from a different repository, reading here as a measurement of this code. With
  the exclusions gone, a `Write-Host` or a plain-text `$Password` parameter added later
  fails the gate instead of passing under an exemption nobody re-examined.
- **Three stale citations corrected.** `.gitattributes` justified itself with
  `Get-TextFingerprint` and a Jenkinsfile, neither of which exists here (that function
  lives in `Scm.Git`, deliberately not ported). `.editorconfig` and `AGENTS.md` named
  two test suites as the reason for the column-0 brace rule, and only one of them
  exists here. The constraint is real, so the citations were corrected rather than
  dropped: a rule with no demonstrable reason is the first one somebody tidies away.
- **A dead condition removed from the entry point.** The token-shape probe was guarded
  by "if any repository came back", which is always true once the listing has
  succeeded. It read as caution and decided nothing - and the one case it appeared to
  protect, an empty listing, is precisely when "is this token expired, or scoped to
  nothing?" most needs asking.

### Security

- Fine-grained personal access tokens only. There is no fine-grained permission
  equivalent to deleting a repository, so the token type removes the capability rather
  than restricting it.
- One token per role, so `repo-standards` cannot touch a project board and
  `project-board` cannot write a file.
- The account login is an environment variable, not a configuration value: it is
  identifying data and the configuration is committed.

### Verified against the live account

Run on 2026-09-07 against `nehemias1999` (24 repositories):

- `plan` reports `ok 24, pending 0, warning 0, protected 0, blocked 0`, exit 0 -
  **identical across two consecutive runs**, which is the idempotency acceptance
  criterion rather than a summary.
- The listing reports 15 public and 9 private, matching the measured baseline. The
  endpoint guard holds.
- **Pagination was exercised for real**, not only against fixtures: forcing
  `pageSize: 5` made the run follow four `Link` headers over five pages and return the
  same 24 repositories with the same plan. Paginating does not change the answer.
- **Truncation was exercised for real**: forcing `maximumPageCount: 2` produced
  `blocked 15` and exit 2, with the `accountListing` operation naming the cause. It did
  not report the 10 repositories it had seen as a complete account.
- The classic-token warning fires on the `gh` CLI token, and the first run against the
  shipped template correctly reported the three `EXAMPLE-*` names as `resolve`/`blocked`
  rather than `create`.

### Known limitations

- **PowerShell 7 is exercised only in CI.** The development workstation has Windows
  PowerShell 5.1 only, so a local run tests the declared floor and nothing else. The CI
  matrix covers both engines.
- The deny-term layer of the sensitive data gate is off on a fresh clone, because it
  reads a file excluded from version control. CI seeds it from a secret and then
  requires it.

[Unreleased]: https://github.com/nehemias1999/GitHub_AsCode/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/nehemias1999/GitHub_AsCode/releases/tag/v0.1.0
