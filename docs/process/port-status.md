# How the port ran, and what it found

**Purpose.** Record how the port was carried out, what the evidence was, and what it
found - so a later reader can tell a decision from an accident.

**Scope.** The transition [ADR 0006](../adr/0006-python-and-the-standard-library.md)
describes.

**Audience.** Anyone wondering why something is the way it is, and anyone about to port
something else.

## The two implementations, while there were two

| | PowerShell | Python |
| --- | --- | --- |
| Transport | `github_as_code.http` | `github_as_code.http` |
| Configuration | `github_as_code.configuration` | `github_as_code.configuration` |
| Schema validation | reduced engine or `Test-Json -Schema` | `github_as_code.schema`, one engine |
| Plan model | `github_as_code.plan` | `github_as_code.plan` |
| Evidence | `github_as_code.report` | `github_as_code.report` |
| Protocol | `github_as_code.rest` | `github_as_code.rest` |
| Domain | `github_as_code.repository` | `github_as_code.repository` |
| Entry point | `inventory.py` | `automations/repo-inventory/inventory.py` |
| Gate | `scripts/run_tests.py` | `scripts/run_tests.py` |
| Secret scan | `scripts/check_sensitive_data.py` | `scripts/check_sensitive_data.py` |

Every pull request left both green until the removal landed. **The PowerShell
implementation was the oracle**: it is what "does the port produce the same answers?"
was answered against, and deleting it before that question was answered would have made
it unanswerable. That is the whole reason two were carried at all.

The right-hand column is what remains.

## The evidence has been produced

Run on 2026-09-09 against a live account, both implementations inside one rate-limit
window, reading the same declaration:

| | |
| --- | --- |
| `declarationFingerprint` | `sha256:7bf2e53fce4934ee048e87cbc3156f25999f233d07428fdfd421e99d51d632a5`, identical across all four runs |
| `ps-inventory.json` against `py-inventory.json` | exit 0 - no differences outside the normalised set |
| `ps-plan.json` against `py-plan.json` | exit 0 - no differences outside the normalised set |

**It did not agree on the first attempt, and what it found was worth finding.** The first
comparison reported 99 differences. Every one of them was list ORDER or a text format;
once the lists were matched by name, not a single snapshot field disagreed and the
summaries were identical. The verdicts were never in question.

The 81 ordering differences were a real defect, and in the **PowerShell** side rather
than the port: `Sort-Object` compares by the current culture and ignores case, so the
same account produced a different report order on a machine with a different locale.
That is the same class of defect as reading an HTTP-date under the current culture, which
this repository had already fixed once. Both sides now sort ordinally, and
`Get-OrdinalSortedString` carries the reason.

The rest were a timestamp written two ways - `.ToString('o')` gives seven fractional
digits, `isoformat()` gives a `+00:00` offset - now one shape through
`Format-ReportTimestamp` and `format_timestamp`; the per-run rate limit budget, now
normalised; and one sentence of prose the Python side words differently because the
`users/` guard will not let it name the endpoint, now worded the same way on both sides.

That leaves point 2 of the trigger satisfied. Points 1 and 3 are what CI does on every
pull request.

## What is deliberately different

Two things differ on purpose, and the comparison instrument normalises exactly these:

- **`schemaEngine`** is `builtin` in Python and `reduced` or `Test-Json` in PowerShell.
  ADR 0007 is the reason, and the Python side is strictly stronger:
  [testing-strategy.md](testing-strategy.md) records which keywords the reduced engine
  was silently not checking.
- **`runBy` and `runOn`** come from `getpass` and `socket` rather than from `USERNAME`
  and `COMPUTERNAME`, which were two of the genuinely Windows-bound things in the
  inherited code.
- **The `delete_repo` scope warning** names the scope in PowerShell and assembles it from
  parts in Python, because the Python absence guard carries no exemption for it. This one
  never reaches a report - it is a console warning - so the comparison does not see it.

Plus two fields that are per-run rather than per-implementation: `rateLimit.remaining` and
`rateLimit.resetUtc`. Two runs a second apart have spent different amounts of the budget.
`limit` and `resource` are NOT normalised: those describe the budget rather than the run.

Everything else must match, field for field, and now does.

## The deletion trigger

From ADR 0006, in the order it must be checked:

1. The offline golden comparison green on every CI leg.
2. One live `inventory` and one live `plan` from **each** implementation, against the
   same account inside one rate-limit window, with an empty report diff outside the
   normalised set - and an identical `declarationFingerprint`, checked **first**, because
   if that differs they did not read the same declaration and nothing below it means
   anything.
3. The dual gate green on `ubuntu-latest` and `windows-latest`.

Points 1 and 3 were what CI did on every pull request. Point 2 needed a token and a live
account, and the result is above.

## Producing the evidence

Four runs, same account, same declaration, one sitting. Do not edit the declaration
between them: the fingerprint is what proves you did not.

```powershell
# 1. PowerShell, both rungs.
python automations/repo-inventory/inventory.py inventory -ReportPath artifacts\parity\ps-inventory.json
python automations/repo-inventory/inventory.py plan      -ReportPath artifacts\parity\ps-plan.json

# 2. Python, the same two.
python automations/repo-inventory/inventory.py inventory --report-path artifacts/parity/py-inventory.json
python automations/repo-inventory/inventory.py plan      --report-path artifacts/parity/py-plan.json

# 3. Compare each pair.
python scripts/compare_reports.py artifacts/parity/ps-inventory.json artifacts/parity/py-inventory.json
python scripts/compare_reports.py artifacts/parity/ps-plan.json      artifacts/parity/py-plan.json
```

Exit codes from the comparison:

| | |
| --- | --- |
| `0` | The two reports agree outside the normalised set. This is the evidence. |
| `1` | They disagree. The output names every field. This is a finding about the port, and it is what the exercise is for. |
| `2` | The two runs did not read the same declaration. A setup mistake, not a finding - fix it and run all four again. |

`artifacts/` is excluded from version control, so these files stay on the machine that
produced them. What goes into the pull request is the two **report file names** and the
`declarationFingerprint`, recorded in the `CHANGELOG.md` entry for the removal - which is
what ADR 0006 asks for.

## What the removal pull request does

Once the evidence is in hand:

- delete `foundation/`, `tests/foundation/`, `tests/automations/`, `tests/TestHelpers.ps1`,
  `scripts/run_tests.py`, `scripts/check_sensitive_data.py`, `scripts/bootstrap.py`,
  `PSScriptAnalyzerSettings.psd1` and `inventory.py`;
- ~~port the sensitive data gate first~~ **done.** `scripts/check_sensitive_data.py`
  runs in the Python gate, and the two agree on this tree: 102 files scanned, 38 skipped
  as ignored, no findings. That was the one ordering constraint, and it is satisfied;
- drop the `gate` job from `.github/workflows/ci.yml`;
- the `ConvertTo-Json` row in [github-notes.md](../reference/github-notes.md) was
  rewritten rather than removed. That document is the ledger of why each guard exists,
  and an entry deleted without trace loses the reason;
- `.editorconfig` lost the reason it gave for its closing-brace rule, and says so. The
  rule cited a Pester test extracting a function with a regular expression; both are
  gone. A configuration file justifying itself by citing something that does not exist is
  a failure this repository has already had twice, so the stale citation was replaced by
  an honest note rather than left to rot;
- the evidence is recorded in `CHANGELOG.md`.

## Two implementations was not a maintenance model

It was a bounded window, and the argument against it was written in the code being
replaced: a retry policy implemented twice is a retry policy that drifts. The window ran
from the first scaffolding commit to the removal, and every pull request in it left both
green.

## What to read before porting something else

The four findings that were not in anybody's plan, in the order they were found:

1. `urllib` forwards `Authorization` across a cross-host redirect. Proved with two
   servers rather than asserted, and the proof includes showing the stock opener does it.
2. The reduced schema validator was not checking nine of the keywords the shipped schemas
   use. Measured by running the same declaration through both.
3. `Sort-Object` orders by the machine's culture, so the oracle did not agree with
   itself across machines. Found only because the parity comparison failed.
4. Three sets of guards in the suite being deleted had nothing to do with the thing being
   deleted.

Every one of them was found by **running something and comparing**, not by reading the
code carefully. That is the method worth carrying to the next port.
