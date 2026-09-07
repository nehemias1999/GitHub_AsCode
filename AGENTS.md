# Maintenance contract

Read this before changing anything here. It states the rules that are not negotiable,
what "finished" means, and the conventions that keep the code consistent.

## 1. Non-negotiable rules

Each of these exists because breaking it damages an account, and each is enforced by a
test rather than by trust.

1. **No write path exists in phases 1 and 2.** `GitHubAsCode.Http` has no `-Method`
   parameter. Adding one is [ADR 0001](docs/adr/0001-write-boundary.md) plus the four
   named changes it lists - not an edit.

2. **`DELETE` never appears, in any spelling, at any phase.** Not for a repository, a
   label, a topic, or a Projects v2 field. Every one destroys something whose blast
   radius is not in the plan.

3. **`Invoke-WebRequest` lives in exactly one file**, `GitHubAsCode.Http.psm1`. One
   place to audit, one place a write could ever be added.

4. **The account listing comes from `GET /user/repos`, never `GET /users/{user}/repos`.**
   The second returns public repositories only - measured as 15 against 24 on this
   account. No string literal anywhere may begin `users/`.

5. **A collection is written as a union, never a replacement.** `PUT /topics` replaces
   everything. Undeclared members are reported as `protected`, not removed.

6. **The configuration declares the NAME of a secret, never its value.** The account
   login too: it is identifying data and the config is committed.

7. **A 404 is never read as absence.** GitHub answers 404 both for something that does
   not exist and for something the token cannot see. Report `blocked`, not `create`.

8. **`ConvertTo-Json` always gets an explicit `-Depth`.** Windows PowerShell 5.1
   defaults to 2 and silently serialises nested objects as the name of their type.

9. **Never read `.env`, `.local/` or `artifacts/`.** They hold real credentials, real
   account data, and run output. `.gitignore` excludes them; do not work around it.

## 2. Definition of finished

A change is not done until all seven hold:

1. `.\scripts\Invoke-Tests.ps1` passes - including zero PSScriptAnalyzer **warnings**.
2. Every new or changed behaviour has a test **named after the failure it prevents**.
3. Every fixture is invented: `EXAMPLE-owner`, `EXAMPLE-repo`, `example.com`.
4. `validate` still passes offline, with no network and no token, for every automation.
5. A second `plan` over unchanged state reports the same thing. Idempotency is the
   acceptance criterion, not an optimisation.
6. Every document under `docs/` is linked from `docs/README.md`, and every link resolves.
7. `CHANGELOG.md` has an entry. Schemas are a **contract**: an incompatible change to
   one is a major version.

## 3. Where code goes

| Layer | Rule |
| --- | --- |
| `GitHubAsCode.*` | Cross-cutting. **Knows nothing about GitHub.** No URL, no endpoint, no permission name, no status code meaning |
| `GitHub.Rest` / `GitHub.GraphQL` | Protocol semantics: addressing, pagination, how a failure is recognised |
| `GitHub.Repository` and its successors | Domain rules, as **pure functions**. No network at all |
| `automations/*` | Orchestration and reporting only |

If the shared layer seems to need a special case for your module, the logic belongs in
your module. The shared layer holds the arithmetic; the domain client holds the meaning.

Pure functions are not a testing convenience. Drift is defined against **the payload
that would be sent**, so a pure payload makes drift a comparison of values and makes the
idempotency assertion testable offline.

## 4. Guards are absolute on purpose

The absence tests do not try to distinguish a legitimate use from a dangerous one,
because a guard with an exemption is a guard with a hole.

This has a cost, and it was paid during phase 1: the inventory's report carried a
`private = $privateCount` field - a count, not a request field - and the guard forbidding
a hashtable key named `private` rejected it. The field was renamed `privateCount`. That
is the guard working. **Rename your code, do not loosen the guard.**

## 5. Conventions

**Code and comments in English, ASCII only. Documentation in Spanish or English, chosen
per document and consistent within it.** The existing documents are in English to match
the sibling repositories.

- `Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'` at the top of
  every file.
- Comment-based help on every exported function, with `.SYNOPSIS`, `.DESCRIPTION`, a
  `.PARAMETER` for each parameter, `.EXAMPLE` and `.OUTPUTS`.
- **Comments say why, not what.** A comment restating the code is noise; a comment
  naming the failure the code prevents is the most valuable line in the file.
- Four-space indent, two for JSON and YAML. `.editorconfig` governs; do not fight it.
- **A closing brace must be in column 0.** `tests/automations/SensitiveDataGate.Tests.ps1`
  extracts a function from `scripts/Test-NoSensitiveData.ps1` with the regex
  `(?ms)^function Get-GitIgnoredPath \{.*?^\}`, and an indented closing brace breaks it
  with "Could not extract" - a failure that names the wrong thing.
- Exit codes: `0` nothing blocked, `2` something indeterminate, `1` the run failed.
- **`PSScriptAnalyzerSettings.psd1` has no exclusions, and adding one needs evidence
  measured HERE** - the finding count, the shapes flagged, and why changing the code
  would be worse. The six inherited exclusions were removed after measuring zero
  findings for each: an exclusion with somebody else's reason beside it is worse than
  one with no reason, because it looks answered.
- A pure function with a state-changing verb (`New-`, `Set-`) gets a
  `SuppressMessageAttribute` at the function, with the reason - never a settings-wide
  exclusion. Narrow the suppression to the thing you have justified.

## 6. Commits

Imperative mood, lower case, saying what changed and why. Prefix with the issue number
where one exists (`#42 read the account listing from the authenticated endpoint`).

## 7. Working with the sibling repositories

`Jenkins_AsCode` and `ADO_AsCode` are the source of the foundation, the command ladder
and the automation contract. Port from them rather than reinventing - but **port
critically**. Phase 1 found five real defects in the inherited code, and the pattern
running through them is worth naming: **a ported justification stops being evidence.**

- `Invoke-Tests.ps1` selected the highest installed Pester and imported it with
  `-MinimumVersion 5.0`, so on a machine with Pester 6 it ran the suite on an untested
  major and reported the result as if it were tested.
- The "network I/O in one place" absence test matched raw file **content**, so it fired
  on any file that merely mentioned `Invoke-WebRequest` in a comment - which meant the
  comment explaining the boundary would have had to be deleted to make the guard pass.
- The sensitive data gate's GitHub rule covered only the classic `gh*_` prefixes, not
  `github_pat_` - the fine-grained format this repository recommends.
- All six PSScriptAnalyzer exclusions had zero findings here, so six rules were switched
  off on another codebase's evidence.
- `.gitattributes` and `.editorconfig` justified real constraints by citing functions
  and test suites that do not exist in this repository.

All five were fixed here. If you fix something inherited, say so in the comment, so the next
reader knows the two repositories deliberately differ rather than accidentally drifted.
