# Testing strategy

**Purpose.** Say what is tested, how, and what each guard is protecting against.

**Scope.** `tests/`, and `scripts/Invoke-Tests.ps1`.

**Audience.** Anyone adding a test, and anyone wondering why the suite refuses
something.

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

## The support floor is only half-testable locally

`PSUseCompatibleSyntax` is configured for 5.1 and 7.0, and warnings fail the gate, so
syntax incompatibility is caught statically wherever the suite runs.

Actually *executing* on both is a CI-only property on this workstation: PowerShell 7 is
not installed here, so a local run exercises the 5.1 floor and nothing else. The CI
matrix runs `powershell` and `pwsh`, and tests that spawn a child process use
`Get-PowerShellHostPath` - the host running the suite - so a run under 7 genuinely
tests 7 rather than shelling out to 5.1 and reporting a pass for both.

## Documentation is tested

Every file under `docs/` must be reachable from `docs/README.md`. A document nobody can
reach from the index is one nobody reads, and it drifts from the code silently.
