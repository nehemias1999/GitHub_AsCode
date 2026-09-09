# Testing strategy

**Purpose.** Say what is tested, how, and what each guard is protecting against.

**Scope.** `tests/`, `scripts/Invoke-Tests.ps1`, and `scripts/run_tests.py`.

**Audience.** Anyone adding a test, and anyone wondering why the suite refuses
something.

## Two gates, for as long as there are two implementations

`scripts/Invoke-Tests.ps1` is the PowerShell gate. `scripts/run_tests.py` is the Python
one. Both run in CI on every pull request, and every pull request leaves both green.

This is a cost, and it is temporary and bounded rather than a maintenance model. ADR
0006 keeps the PowerShell implementation alive because it is the only oracle for
"does the port produce the same answers?", and states the trigger for deleting it. The
PowerShell gate cannot serve both halves: it needs PowerShell, and requiring PowerShell
on a Linux agent to test a tool whose whole point is running there without it would be
an odd thing to write down. When the PowerShell goes, `Invoke-Tests.ps1` goes with it
and one gate is left.

The Python gate does **not** run the sensitive data scan, and says so on every run
rather than leaving it to be noticed. `scripts/Test-NoSensitiveData.ps1` is not ported
yet, so a green Python run is a narrower claim than a green PowerShell one.

## One definition of "passes"

`scripts/Invoke-Tests.ps1`. Parse check, PSScriptAnalyzer, Pester, sensitive data scan.
CI runs the identical command, so "it passed locally" and "it passed in CI" mean the
same thing. Every check that fails adds a line; the run reports all of them rather than
stopping at the first.

A missing analyser is a **failure**, not a skip: passing without analysing produces the
same output as analysing cleanly, and `-Skip Analyzer` already exists for anyone who
means to leave it out. The same reasoning applies to Pester finding no test files.

## Name a test after the failure it prevents

Not after the function it calls. `it finds the next page when the header carries more
than one link` says what breaks if it fails. `it parses Link headers` does not.

## Everything invented

`EXAMPLE-owner`, `EXAMPLE-repo`, `example.com`. A test that borrowed a real repository
name would turn the suite into another place sensitive data leaks from, and test files
are the last place anyone thinks to look. On an account with private repositories the
name alone is enough: that a given private repository exists is not public.

A test asserts every URL in every committed template resolves to a reserved
`example.*` host, and that every repository name in one is prefixed `EXAMPLE-`.

## The fixtures, and what each one is for

| Fixture | Exists to test |
| --- | --- |
| `repos.page1.json` | The shapes that matter: a licensed and fully declared repository, one carrying a hand-added topic the declaration does not know about, and one with nothing at all. Deliberately includes the URL-template properties a real payload is mostly made of, so the snapshot can be proven to drop them |
| `repos.page2.json` | Private and archived. Private is the entire point of the authenticated endpoint; archived must be skipped rather than planned against |
| `link-header-two-links.txt` | The regression: a comma separating two links leaking into the `rel` value |
| `link-header-comma-in-url.txt` | A comma **inside** a query value, which splitting the header on `,` would truncate |
| `link-header-last-page.txt` | No `next` relation - the loop's only termination condition |
| `graphql-errors.json` | HTTP 200 carrying `errors`. Committed in phase 1, before the code that must handle it exists, so the trap is written down first |

## Absence tests

`tests/automations/Automations.Tests.ps1`, read from the **parse tree** and not from the
text. A grep matches prose and misses a variable: `-Method $verb` is exactly how a write
would actually arrive, and no amount of string matching sees it.

| Guard | Protects against |
| --- | --- |
| Network I/O in exactly one file | Losing the single place a write could be added, and the single place to audit |
| No `Method` as a parameter or hashtable key, anywhere | Any write at all, in phases 1 and 2 |
| No `DELETE`, in any spelling | The one method this repository never acquires - not for a repository, a label, a topic or a project field |
| `delete_repo` named nowhere but the warning that refuses it | A token existing that can delete a repository |
| No hashtable key named `private`, `visibility`, `archived`, `is_template` or `default_branch` | A generic writer reaching the `PATCH /repos` fields that look ordinary and are not |
| No string literal beginning `users/` | Reading the account listing from the endpoint that hides private repositories |
| `ConvertTo-Json` always passes `-Depth` | The PowerShell 5.1 default of 2, which serialises nested objects as the name of their type |
| No `$ConfirmApply` while no verb writes | Promising a capability that does not exist, which is the first thing somebody reaches for |

> The `private` guard is absolute rather than context-aware, and it earned its keep
> during phase 1: the inventory's report detail had a `private = $privateCount` key -
> a count, not a request field. The guard cannot tell the two apart, and loosening it
> would have weakened the real protection, so the field was renamed `privateCount`
> instead. That is the guard working, not a false positive.

## Testing a writer without writing

Nothing writes yet, but the strategy is fixed now, because it is what the module
boundaries were drawn for. Four levels:

**1. The payload is a pure function.** `Get-GitHubTopicUnion` and its successors return
the value that would be sent, and it is compared against a fixture. This is not a
testing convenience: **drift is defined against the payload that would be sent**, not
against the declaration. If the payload is a pure value, drift is a comparison of
values.

**2. The transport is mocked.** `Mock Invoke-GitHubRequest -ModuleName GitHub.Repository`
records the calls, and the test asserts method, path and body. The assertion that
matters most is the negative one: under `plan`, or under `apply` without
`-ConfirmApply`, **not one call was made with a method other than GET**.

**3. Idempotency is executed, not promised.** A fixture of the state *after* an apply,
and a second `plan` over it that must report `pending = 0` and `blocked = 0`. Level 1
is what makes this possible offline - and it is already in place:
`GitHub.Repository.Tests.ps1` asserts that the union of a first payload with the same
declaration produces no change.

**4. End to end, offline, in CI.** The gate, then each automation's `validate` against
its own template, on both `powershell` and `pwsh`, with no network and no credential.

## What the Python gate checks, and what it cannot

`scripts/run_tests.py`. Parse, ruff, `unittest`. Same shape and same reasoning as the
PowerShell runner: increasing order of cost, every failure adds a line rather than
stopping the run, a missing linter is a **failure** and not a skip, and an empty test
discovery is a failure too - green from a run that tested nothing looks exactly like
green from a run that tested something.

`unittest` rather than a third-party runner is a choice, not an oversight: it is in the
standard library, so the Python gate needs exactly one development dependency instead
of two. ADR 0006 permits either.

### Three guards for one rule: no dependencies

In PowerShell "no dependencies" held because no package manager was in play. Python has
pip, so the rule needs teeth. Three layers, because they fail differently:

| Guard | Catches |
| --- | --- |
| `pyproject.toml` declares `dependencies = []` | A dependency declared and not yet imported - the state a repository is in for as long as it takes somebody to write the import |
| Every import under `src/` is stdlib or this package, read from the parse tree | One imported and never declared, which is what happens when the package is already installed on the machine that added it |
| The package imports under `python -I -S` | Both, by refusing to run with `site-packages` at all |

Each of them fails when it finds nothing to read, rather than passing over an empty
tree.

### The absence tests, ported to a different write vector

`tests/python/test_write_boundary.py`, read from the parse tree for the same reason the
PowerShell ones are. It is **not** a translation of the table above, because the way a
write would arrive is not the same. PowerShell had exactly one way in, `-Method`, and
every guard was shaped around that name. Python has two, and neither reads as a method:

| Guard | Protects against |
| --- | --- |
| No call passes `data`, and no `Request` takes a second positional argument | The Python write vector: urllib chooses POST from the presence of a body, so a write arrives with nothing at the call site that looks like one |
| Every `method=` is the literal `'GET'` | The obvious write, and the one a variable hides |
| `urllib.request` is imported by exactly one module, and that module is where it is expected to be | Losing the single place a write could be added - including by renaming the transport, which would otherwise leave the guard passing over nothing |
| No `delete`, in any spelling: string, name, attribute or function | The one method this repository never acquires |
| `delete_repo` appears nowhere | A token existing that can delete a repository |
| No string literal begins `users/` or contains `/users/` | Reading the account listing from the endpoint that hides private repositories |
| No dictionary key named `private`, `visibility`, `archived`, `is_template` or `default_branch` | A generic writer reaching the `PATCH /repos` fields that look ordinary and are not |

The `ConvertTo-Json -Depth` guard is **not** ported: `json.dumps` has no depth limit, so
it has nothing to test. Its entry in `github-notes.md` is rewritten rather than removed
when the PowerShell goes - that document is the ledger of why each guard exists, and an
entry deleted without trace loses the reason.

Two guards arrive that have no PowerShell counterpart at all, and both are in
`github-notes.md` as rows 15 and 16: the redirect refusal and the `data=` promotion.

### Every guard is checked by planting the failure it names

Not by reading it. A guard is a claim that something would be caught, and the only way
to know is to write the thing and watch it go red. Everything under `tests/python/` has
been through that: a third-party import, an unplaced module, a stale layer entry, a
sideways import, a `data=` body, a positional body, a non-GET method, a `delete` in four
spellings, a `delete_repo` scope, a `users/` path, and a `private` dictionary key.

It is not ceremony. The layering guard passed over a planted sideways import in silence
the first time it was run that way, because the import reader had a hole in it.

### Dependencies point downward, and now something checks

`docs/reference/architecture.md` has said this since phase 1 and nothing enforced it.
Python imports are statically enumerable, so `tests/python/test_layering.py` reads the
ladder from `pyproject.toml` under `[tool.github-as-code.layers]` and fails on an import
that points sideways or up. A module with no layer fails too: that is what keeps the
guard from growing quieter with every commit while the port is still mostly empty.

The reader those guards are built on has its own tests, because a reader that misses a
spelling makes both of them report a clean tree they never read. That is measured, not
theoretical: the first version missed `from package import module` - the ordinary way
somebody writes exactly the import the layer guard exists to catch - and a planted pair
of modules at the same layer went through in silence.

## The support floor is only half-testable locally

`PSUseCompatibleSyntax` is configured for 5.1 and 7.0, and warnings fail the gate, so
syntax incompatibility is caught statically wherever the suite runs.

Actually *executing* on both is often a CI-only property. A Windows machine may ship
Windows PowerShell 5.1 and nothing else, in which case a local run exercises the floor
alone. The CI matrix runs `powershell` and `pwsh`, and tests that spawn a child process
use `Get-PowerShellHostPath` - the host running the suite - so a run under 7 genuinely
tests 7 rather than shelling out to 5.1 and reporting a pass for both.

If you work on this repository, check which engines your machine actually has before
concluding that a green local run covers the declared support floor.

**On the Python side this is worse, and the loss is deliberate rather than unnoticed.**
`PSUseCompatibleSyntax` checked the 5.1/7.0 floor statically, on every machine that ran
the gate. Python has no equivalent. `ruff`'s `target-version = "py311"` catches the
subset that is syntax - a 3.12 generic is flagged, a 3.12 standard-library function is
not - and the rest is enforced only by the CI matrix running 3.11 and 3.14 on Linux and
Windows. A local run on one interpreter proves nothing about the floor. ADR 0006 records
this as a real downgrade rather than an even trade, which is why it is written here too:
a loss recorded only in a config value is a loss nobody reads.

## Documentation is tested

Every file under `docs/` must be reachable from `docs/README.md`. A document nobody can
reach from the index is one nobody reads, and it drifts from the code silently.
