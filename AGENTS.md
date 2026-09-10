# Maintenance contract

Read this before changing anything here. It states the rules that are not negotiable,
what "finished" means, and the conventions that keep the code consistent.

## 1. Non-negotiable rules

Each of these exists because breaking it damages an account, and each is enforced by a
test rather than by trust.

1. **No write path exists in phases 1 and 2.** `github_as_code.http` sends `GET` and
   nothing else. Adding a write is [ADR 0001](docs/adr/0001-write-boundary.md) plus the
   four named changes it lists - not an edit.

   The vector is not the one the PowerShell guards were shaped around. There is no
   `-Method` to add: `urllib.request.Request(url, data=...)` promotes a GET to a POST
   from the presence of a body alone, with nothing at the call site that reads like a
   write. The guards assert on the shape of the call.

2. **`DELETE` never appears, in any spelling, at any phase.** Not for a repository, a
   label, a topic, or a Projects v2 field. Every one destroys something whose blast
   radius is not in the plan.

3. **`urllib.request` is imported by exactly one module**, `github_as_code.http`. One
   place to audit, one place a write could ever be added - and a guard checks the module
   is still where it is expected to be, because renaming it would otherwise turn that
   check into one that passes over nothing.

4. **The account listing comes from `GET /user/repos`, never `GET /users/{user}/repos`.**
   The second returns public repositories only, so it omits every private repository.
   No string literal anywhere may begin `users/`.

5. **A collection is written as a union, never a replacement.** `PUT /topics` replaces
   everything. Undeclared members are reported as `protected`, not removed.

6. **The configuration declares the NAME of a secret, never its value.** The account
   login too: it is identifying data and the config is committed.

7. **A 404 is never read as absence.** GitHub answers 404 both for something that does
   not exist and for something the token cannot see. Report `blocked`, not `create`.

8. **Redirects are refused, and the token never travels to a second host.**
   `urllib.request` follows a 30x by default and rebuilds the next request from the
   original headers, `Authorization` included. Two servers on the loopback interface
   prove the refusal holds, and prove the stock opener would not have.

9. **Never read `.env`, `.local/` or `artifacts/`.** They hold real credentials, real
   account data, and run output. `.gitignore` excludes them; do not work around it.

## 2. Definition of finished

A change is not done until all seven hold:

1. **`python scripts/run_tests.py` passes** - parse, ruff, the suite, and the sensitive
   data scan, with no ruff findings at all. There were two gates while there were two
   implementations; the evidence that closed that window is in
   [port-status.md](docs/process/port-status.md).
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
| `github_as_code.configuration` / `.plan` / `.report` | Cross-cutting. **Knows nothing about GitHub.** No URL, no endpoint, no permission name, no status code meaning |
| `github_as_code.rest` / `github_as_code.graphql` | Protocol semantics: addressing, pagination, how a failure is recognised |
| `github_as_code.repository` and its successors | Domain rules, as **pure functions**. No network at all |
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
`private` field - a count, not a request field - and the guard forbidding a dictionary key
of that name rejected it. The field was renamed `privateCount`. That is the guard working.
**Rename your code, do not loosen the guard.**

## 5. Conventions

**Code and comments in English, ASCII only. Documentation in Spanish or English, chosen
per document and consistent within it.** The existing documents are in English.

ASCII only is enforced by review rather than by a test, and it has bitten once: a literal
byte order mark written into a `lstrip` call is invisible in a diff. Write `"\ufeff"`.

- A docstring on every module and every public function, with what it does, `Args:`,
  `Returns:`, `Raises:` where it raises, and an `Example:`.
- **Comments say why, not what.** A comment restating the code is noise; a comment
  naming the failure the code prevents is the most valuable line in the file.
- Four-space indent, two for JSON and YAML, 100 columns. `.editorconfig` and
  `pyproject.toml` govern; do not fight either.
- Type hints on every public signature. They are not checked by a tool here, so they are
  documentation that happens to be machine-readable - which means a wrong one is worse
  than none.
- **A test imports the module it tests.** It sounds obvious and was not available
  before: the PowerShell secret-gate suite had to extract a function out of the script
  with a regular expression anchored on a closing brace in column 0, so an indented brace
  broke it with "Could not extract" - a failure that names the wrong thing and sends the
  reader to the wrong file. If a check ever needs to read source as text again, ask first
  whether it can import it instead.
- Exit codes: `0` nothing blocked, `2` something indeterminate, `1` the run failed.
- **`pyproject.toml` excludes no lint rule, and adding an exclusion needs evidence
  measured HERE** - the finding count, the shapes flagged, and why changing the code
  would be worse. The PowerShell settings file inherited six exclusions and dropped all
  six after measuring zero findings for each: an exclusion carrying somebody else's
  reason is worse than one with no reason, because it looks answered.
- A `noqa` goes on the line, names the rule, and carries the reason - never a file-wide
  or project-wide exclusion. Narrow the suppression to the thing you have justified.

## 6. Commits

Imperative mood, lower case, saying what changed and why. Prefix with the issue number
where one exists (`#42 read the account listing from the authenticated endpoint`).

## 7. Working with the sibling repositories

`Jenkins_AsCode` and `ADO_AsCode` are the source of the shared layer, the command ladder
and the automation contract. Port from them rather than reinventing - but **port
critically**. Phase 1 found six real defects in the inherited code, and the port to
Python found more, all of the same shape. The pattern is worth naming: **a ported
justification stops being evidence.**

The port itself is the longest worked example. Every module was translated with its
comments, and in four places the comment survived while the hazard it described did not -
`ConvertTo-Json` depth, the invariant-culture date parse - while two new hazards arrived
that no comment mentioned, because they belong to the new language. Read
[port-status.md](docs/process/port-status.md) before assuming a carried-over reason still
applies.

- The PowerShell test runner selected the highest installed Pester and imported it
  with `-MinimumVersion 5.0`, so once Pester 6 shipped it ran the suite on an untested
  major and reported the result as if it were tested. The Python gate carries the same
  shape of bound on `ruff`, for the same reason.
- The "network I/O in one place" absence test matched raw file **content**, so it fired
  on any file that merely mentioned `Invoke-WebRequest` in a comment - which meant the
  comment explaining the boundary would have had to be deleted to make the guard pass.
- The sensitive data gate's GitHub rule covered only the classic `gh*_` prefixes, not
  `github_pat_` - the fine-grained format this repository recommends.
- All six static-analysis exclusions had zero findings here, so six rules were switched
  off on another codebase's evidence.
- `.gitattributes` and `.editorconfig` justified real constraints by citing functions
  and test suites that do not exist in this repository.
- `.github/workflows/ci.yml` set `shell: ${{ matrix.shell }}` on each step, which fails
  at workflow-parse time. `Jenkins_AsCode` has therefore **never had a green CI run**,
  while four of its documents state that the gate runs automatically. Worth dwelling on:
  a startup failure yields no job and no log, so it renders as nothing rather than as a
  failure. If you claim a check runs, go and look at a passing run of it.

All six were fixed here. If you fix something inherited, say so in the comment, so the next
reader knows the two repositories deliberately differ rather than accidentally drifted.
