# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Configuration schemas are a contract.** An incompatible change to a schema under
`automations/*/schemas/` is a major version, whatever it does to the code.

## [Unreleased]

### Added

- **`scripts/check_sensitive_data.py` - the sensitive data gate, ported**, and wired into
  `scripts/run_tests.py`. Both gates now run it and they agree on this tree: 102 files
  scanned, 38 skipped as ignored, no findings. That was the one ordering constraint
  `docs/process/port-status.md` names before the PowerShell gate can be removed - port
  the scan first, or the removal quietly drops a check while every gate stays green.
  Unlike the original it is importable, so the suite calls the functions rather than
  extracting them from the file with a regular expression.
- **`docs/process/port-status.md`** - which implementation does what today, what differs
  on purpose, and the exact four runs plus two comparisons that produce the evidence ADR
  0006's deletion trigger requires. It also lists what the removal pull request has to do,
  including the one ordering constraint that is easy to get wrong: the sensitive data gate
  must be ported before the PowerShell gate goes, or the removal quietly drops a check.
- **`src/github_as_code/rest.py`** - the GitHub half of talking to the API: the status
  guidance map handed to the transport as data, the rate limit and token shape readers,
  the `Link`-driven pagination that never computes a page number, and the account listing
  that reads the authenticated endpoint.
- **`src/github_as_code/automations/repo_inventory.py`** and a thin entry point at
  `automations/repo-inventory/inventory.py`. The same four rungs, the same exit codes,
  and the same refusal to let `inventory` consult the declaration.
- **`scripts/compare_reports.py`** - the parity instrument ADR 0006's deletion trigger
  needs, with its own tests. It checks `declarationFingerprint` first and stops on a
  mismatch, because reporting forty field differences when the two runs read different
  declarations buries the only fact that matters.
- **`src/github_as_code/repository.py`** - the domain rules, as pure functions. The topic
  union that stops `PUT /topics` destroying what nobody declared, the topic and name
  validators, the snapshot reducer, and the four-case comparison whose most important
  case is that a declared repository the API did not return is `resolve`/`blocked` and
  never `create`.
- **`src/github_as_code/plan.py`** - the closed vocabularies, the plan model and the
  summary. `format_plan_summary` returns lines rather than writing to a stream, which is
  the one real change from the PowerShell version: a pure function is testable without
  capturing output.
- **`src/github_as_code/report.py`** - the evidence writer, with both masking layers.
  The `authentication` field name is now pinned by a test in both directions: it must
  survive the walk, and `token`, `tokenShape`, `credentialShape`, `auth` and
  `authorization` must still be redacted. The guard has not been loosened; the field was
  renamed, which is what happened the first time too.
- **`src/github_as_code/schema.py` - one JSON Schema validator, built in.** ADR 0007
  executed: the capability probe and the two engines are gone, the engine is `builtin` and
  a test asserts the name never varies. It gains every keyword the reduced validator
  skipped - `pattern`, the length, range, item and property bounds, and `uniqueItems` -
  and the two things a naive implementation gets wrong: patterns compile with `re.ASCII`
  because JSON Schema is ECMA-262 where `\d` is ASCII-only, and `const`/`enum` compare the
  type first because `1 == True` in Python.
- **A coverage guard, which is the part that is new.** Every `*.schema.json` the repository
  ships is walked, every keyword collected, and the gate fails on one outside the
  implemented set. Adding `oneOf` to a schema used to stop that part of the schema being
  checked with no symptom at all; now it fails loudly and somebody either implements it or
  writes the decision down.
- **A differential conformance test against `jsonschema`**, a development dependency. For
  every schema and document the repository ships, plus nine hand-written invalid
  documents, the built-in validator and the library must reach the same verdict. When
  `jsonschema` is absent it **skips loudly**: `scripts/run_tests.py` now prints every skip
  with its reason, and CI installs the library so the check runs somewhere.
- **`src/github_as_code/configuration.py`** - the .env loader, path resolution, the
  declaration-choosing rule, the duplicate check, the schema-validating configuration
  reader and the required-value reader.
- **`src/github_as_code/http.py` - the transport, ported.** `GitHubAsCode.Http.psm1` in
  Python: base URL validation that describes a bad value rather than echoing it, the URL
  builder, the Bearer and Basic headers, the `Link` header parser, the pure retry policy,
  the Retry-After reader and the JSON-or-explain-why-not decoder. Read-only by
  construction, as before: no parameter exists that could make it write.
- **A redirect-refusing opener, and two servers that prove it.** `urllib.request` follows
  a 30x by default and rebuilds the next request from the original headers, `Authorization`
  included - so a faithful-looking port hands an account-wide token to whatever host the
  redirect names, with no error and no log line. That inverts `MaximumRedirection = 0`,
  the most carefully reasoned decision in the PowerShell transport.
  `tests/python/test_redirect_refusal.py` runs two servers on the loopback interface and
  asserts the second is never contacted at all - and, in the same file, that the **stock
  opener does forward the token**. The hazard is measured, not assumed: if a future Python
  starts stripping the header, that test fails and the reasoning gets reread.
- **`tests/python/test_write_boundary.py` - the absence tests, reshaped for the language.**
  Not a translation: the write vector is different. `Request(data=...)` promotes a GET to a
  POST with nothing at the call site that reads like a write, so the guards assert on the
  shape of the call rather than on the presence of a name. Eight guards, each verified by
  planting the failure it names and watching it go red.
- `docs/reference/github-notes.md` gains rows 15 and 16, the two client traps Python
  introduces. Row 15 is marked *measured*.


- **The Python scaffolding, and the guards that come before any ported code.** `pyproject.toml`
  declaring `dependencies = []`, the `github_as_code` package under `src/`, and
  `scripts/run_tests.py` - the Python gate, with the same shape as the PowerShell one:
  parse, lint, tests, in increasing order of cost, every failure adding a line rather
  than stopping the run, and a missing linter or an empty test discovery counting as a
  failure rather than a skip. It says on every run that the sensitive data scan is not
  part of it, because a green line that covers less than the other gate must not look
  like one that covers the same.
- **Three guards for "no dependency beyond the standard library and git"**, because the
  rule needed teeth once pip was in play: the manifest declares none, no import under
  `src/` resolves outside `sys.stdlib_module_names`, and the package imports under
  `python -I -S` with no `site-packages` at all. Each fails when it finds nothing to
  read, rather than passing over an empty tree - which is the failure mode this
  repository has already been bitten by once, in a sibling project whose CI had never
  produced a job at all.
- **A guard for "dependencies point downward, never sideways."** The sentence has been in
  `docs/reference/architecture.md` since phase 1 with nothing enforcing it; Python imports
  are statically enumerable, so it is now read from a ladder in `pyproject.toml`. A module
  with no layer fails, and so does a layer naming a module that no longer exists.
- The import reader both guards are built on has its own tests, named after the spellings
  that hid something. The first version missed `from package import module` - the ordinary
  way somebody writes exactly the import the layer guard exists to catch - and a planted
  pair of modules at the same layer passed in silence. Found by planting the failure, not
  by reading the code.
- A `python-gate` CI job over `ubuntu-latest` and `windows-latest` on Python 3.11 and 3.14,
  beside the two existing PowerShell legs. The 3.11 leg is the only thing enforcing the
  declared floor: `PSUseCompatibleSyntax` checked it statically on every machine, and
  Python has no equivalent.

- **ADR 0006 - Python, and nothing but its standard library.** Records the decision to
  rewrite this tool in Python so it runs on Linux CI agents, in containers and on a Linux
  workstation, and restates the "no dependency beyond the interpreter and git" rule in the
  new language. It also records what was weighed against it: PowerShell 7 is
  cross-platform and this codebase is close to portable already, so the alternative was
  about half a day of work against a 7,911-line rewrite. Nothing is decided here about
  what the tool is *allowed to do* - ADRs 0001 through 0005 stand.
- **ADR 0007 - One schema validator, built in.** The reduced validator exists only
  because Windows PowerShell 5.1 has no `Test-Json -Schema`; Python removes the
  constraint, so the capability probe and the two engines go away rather than being
  reproduced. The validator gains the keywords it currently ignores - `pattern`,
  the length, range and item bounds - and gains a guard that fails the gate if a schema
  ever uses a keyword outside the implemented set. Today that gap is silent: adding
  `oneOf` to a schema would stop it being checked with no symptom.

### Changed

- `scripts/run_tests.py` no longer says the sensitive data scan is missing from it,
  because it is not. A test asserts that sentence stays gone: if it comes back, a check
  has been dropped.
- The Python CI legs seed the deny-term layer from the same `SENSITIVE_TERMS` secret the
  PowerShell job uses, and pass `--require-deny-terms` when it is set.
- `docs/guides/getting-started.md` - both implementations, how the arguments map between
  them, and both gates. Which one to use is stated rather than left to be inferred: Python
  on Linux or in a container, since that is the requirement the port exists for.
- The isolated-execution guard now RUNS `validate` under `python -I -S` instead of
  importing the package. Upgrading it immediately found a real defect: `main()` took
  `repository_root` as given, so a string - which the guard builds out of `sys.argv` -
  reached `root / "foundation"` and failed with a `TypeError` about str and str. The
  weaker version passed over it.
- The Python CI legs gain the offline `validate` step the PowerShell job has run since
  phase 1.
- `scripts/run_tests.py` parses and lints `automations/` too. The Python entry points live
  there beside the PowerShell ones, and left out they would be neither parsed nor linted
  by anything.
- The protected environment-variable list is a **union, not a translation**. `PYTHONPATH`,
  `PYTHONHOME` and `PYTHONSTARTUP` are the names that make a .env file a code-execution
  path for the interpreter now reading it; the PowerShell and .NET names stay because both
  implementations read the same file during the transition, and a name harmless to Python
  is not harmless to the PowerShell run reading the line beside it.
- `scripts/run_tests.py` counts a skipped test as skipped rather than as passed.
  `unittest`'s `testsRun` includes skips, so subtracting only failures and errors reported
  a skip as a pass - against exactly the check the skip is meant to be visible for.
- `docs/process/testing-strategy.md` records what the reduced validator was not checking,
  measured on this machine rather than inferred.
- `docs/process/testing-strategy.md` - the Python absence-test table, why it is not a
  translation of the PowerShell one, and a section on checking every guard by planting the
  failure it names. That is written down because it earned its place: the layering guard
  added last week passed over a planted sideways import in silence, and only planting it
  found the hole in the import reader.


- `AGENTS.md` - "finished" now means **both** gates pass, for as long as both
  implementations exist.
- `docs/process/testing-strategy.md` - records the two gates and the bounded window they
  live in, what the Python gate checks and what it cannot, and the loss of
  `PSUseCompatibleSyntax` as a real downgrade rather than an even trade.
- `docs/reference/architecture.md` - the layer diagram gains the four edges the module
  manifests always declared and it never drew: `Report` on `Plan` and `Configuration`,
  `GitHub.Rest` on `Configuration`, `GitHub.Repository` on `Plan`. A guard that
  contradicted the diagram would be worse than either alone.
- `docs/adr/0004-fine-grained-tokens-only.md` carries a status note saying which of its
  supporting arguments ADR 0006 supersedes. The body is untouched: an ADR records what was
  decided and why at the time, and editing it would erase the reasoning rather than update
  it. The decision itself is unchanged.

### Fixed

- **Report list order no longer depends on the machine's locale.** `Sort-Object` compares
  by the current culture and ignores case, so the same account produced a different report
  order on a machine with a different locale - the same class of defect as reading an
  HTTP-date under the current culture, which this repository had already fixed once. Both
  implementations now sort ordinally, through `Get-OrdinalSortedString` on the PowerShell
  side. Found by comparing two live runs, not by reading the code: 81 of 99 report
  differences were ordering.
- **One timestamp shape in both implementations.** `.ToString('o')` writes seven
  fractional digits and `isoformat()` writes a `+00:00` offset; both are ISO 8601 and
  neither is wrong, which is why the shape had to be chosen rather than inherited.
  `Format-ReportTimestamp` and `format_timestamp` write seconds and a literal `Z`.
- `scripts/compare_reports.py` normalises `detail.rateLimit.remaining` and
  `detail.rateLimit.resetUtc`, which are per-run observations. `limit` and `resource`
  stay compared: they describe the budget rather than the run.
- The `listing.rationale` field is worded the same way in both implementations. The Python
  one cannot name the endpoint - the `users/` guard forbids it - so the PowerShell one
  adopted the wording that both can write, rather than the difference being normalised
  away.

- `Invoke-RepositoryInventory.ps1` no longer prints the seven environment variable names
  that `Import-GitHubAsCodeEnvironment` returns. The function returns them by design; the
  call site did not capture the result, and in PowerShell an uncaptured return value is
  written to the output stream rather than discarded, so every authenticated run ended by
  emitting them after its report paths. Names only, never values.
- `Write-PlanSummary` no longer closes an `inventory` run with "the live state already
  matches the declaration". `inventory` does not read the declaration at all, so that
  sentence announced a comparison that never ran - and contradicted the per-operation
  reasons printed in the same run. `plan`, which does compare, is unchanged.

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

Six defects were found in the ported `Jenkins_AsCode` code and fixed here. Each is
noted in a comment at the site, so the difference between the two repositories reads as
deliberate rather than as drift.

- **`scripts/Invoke-Tests.ps1` no longer runs the suite on an untested Pester major.**
  The inherited version selected the highest installed version and imported it with
  `-MinimumVersion 5.0`, which was correct when 5.x was the only 5-or-later major. With
  Pester 6 installed - as any current machine is - it silently ran the
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
- **The CI workflow now starts at all.** The ported file set
  `shell: ${{ matrix.shell }}` on each step. GitHub validates a step's `shell` when it
  parses the workflow, before any expression is evaluated, so an expression there is not
  a shell name and the run fails at startup: zero seconds, no job, no log, and the run
  labelled by its file path instead of its name. **This is why `Jenkins_AsCode` has
  never had a green CI run** - every one of its runs failed this way while four of its
  documents claimed the gate ran automatically. A startup failure looks like nothing
  rather than like a failure, which is how it went unnoticed. The shell is now a
  job-level `defaults.run.shell`, which is evaluated later and does accept the matrix
  context; verified empirically, the two legs report `engine=Desktop` and
  `engine=Core`.
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

### Verified against a live account

Run against a real account:

- `plan` reports every declared repository as `ok`, with `pending 0` and `blocked 0`,
  exit 0 - **identical across two consecutive runs**, which is the idempotency
  acceptance criterion rather than a summary.
- The listing reports both public and private repositories, and the private count is
  non-zero. The endpoint guard holds.
- **Pagination was exercised for real**, not only against fixtures: forcing a small
  `pageSize` made the run follow several `Link` headers over multiple pages and return
  the same repositories with the same plan. Paginating does not change the answer.
- **Truncation was exercised for real**: forcing a low `maximumPageCount` produced a
  blocked plan and exit 2, with the `accountListing` operation naming the cause. It did
  not report the repositories it had already seen as a complete account.
- The classic-token warning fires on a classic token, and the first run against the
  shipped template correctly reported its `EXAMPLE-*` names as `resolve`/`blocked` rather
  than `create`.

### Known limitations

- **PowerShell 7 may be exercised only in CI.** A Windows machine can ship Windows
  PowerShell 5.1 and nothing else, in which case a local run tests the declared floor
  alone. The CI matrix covers both engines, which is why it is not optional.
- The deny-term layer of the sensitive data gate is off on a fresh clone, because it
  reads a file excluded from version control. CI seeds it from a secret and then
  requires it.

<!-- Replace <owner> with your account when you generate a repository from this
     template. Left as a placeholder on purpose: a link to somebody else's repository
     is worse than no link. -->
[Unreleased]: https://github.com/<owner>/GitHub_AsCode/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<owner>/GitHub_AsCode/releases/tag/v0.1.0
