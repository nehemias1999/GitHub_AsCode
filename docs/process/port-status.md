# Where the port is, and what deleting the PowerShell needs

**Purpose.** Say which implementation does what today, and give the exact procedure for
the one step that cannot be done from a keyboard alone.

**Scope.** The transition [ADR 0006](../adr/0006-python-and-the-standard-library.md)
describes.

**Audience.** Whoever runs the evidence, and whoever reviews the pull request that
removes 7,700 lines.

## Both implementations are complete and both are green

| | PowerShell | Python |
| --- | --- | --- |
| Transport | `GitHubAsCode.Http` | `github_as_code.http` |
| Configuration | `GitHubAsCode.Configuration` | `github_as_code.configuration` |
| Schema validation | reduced engine or `Test-Json -Schema` | `github_as_code.schema`, one engine |
| Plan model | `GitHubAsCode.Plan` | `github_as_code.plan` |
| Evidence | `GitHubAsCode.Report` | `github_as_code.report` |
| Protocol | `GitHub.Rest` | `github_as_code.rest` |
| Domain | `GitHub.Repository` | `github_as_code.repository` |
| Entry point | `Invoke-RepositoryInventory.ps1` | `automations/repo-inventory/inventory.py` |
| Gate | `scripts/Invoke-Tests.ps1` | `scripts/run_tests.py` |

Every pull request leaves both green, on `windows-latest` for the two PowerShell engines
and on `ubuntu-latest` and `windows-latest` for Python 3.11 and 3.14.

**The PowerShell implementation is the oracle.** It is what "does the port produce the
same answers?" is answered against, and deleting it before that question is answered
makes it unanswerable. That is the whole reason for the cost of carrying two.

## What is deliberately different

Three things differ on purpose, and the comparison instrument normalises exactly these:

- **`schemaEngine`** is `builtin` in Python and `reduced` or `Test-Json` in PowerShell.
  ADR 0007 is the reason, and the Python side is strictly stronger:
  [testing-strategy.md](testing-strategy.md) records which keywords the reduced engine
  was silently not checking.
- **`runBy` and `runOn`** come from `getpass` and `socket` rather than from `USERNAME`
  and `COMPUTERNAME`, which were two of the genuinely Windows-bound things in the
  inherited code.
- **The `delete_repo` scope warning** names the scope in PowerShell and assembles it from
  parts in Python, because the Python absence guard carries no exemption for it.

Everything else must match, field for field.

## The deletion trigger

From ADR 0006, in the order it must be checked:

1. The offline golden comparison green on every CI leg.
2. One live `inventory` and one live `plan` from **each** implementation, against the
   same account inside one rate-limit window, with an empty report diff outside the
   normalised set - and an identical `declarationFingerprint`, checked **first**, because
   if that differs they did not read the same declaration and nothing below it means
   anything.
3. The dual gate green on `ubuntu-latest` and `windows-latest`.

Points 1 and 3 are what CI already does. Point 2 needs a token and a live account, so it
is the operator's to run.

## Producing the evidence

Four runs, same account, same declaration, one sitting. Do not edit the declaration
between them: the fingerprint is what proves you did not.

```powershell
# 1. PowerShell, both rungs.
.\automations\repo-inventory\Invoke-RepositoryInventory.ps1 -Command inventory -ReportPath artifacts\parity\ps-inventory.json
.\automations\repo-inventory\Invoke-RepositoryInventory.ps1 -Command plan      -ReportPath artifacts\parity\ps-plan.json

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
  `scripts/Invoke-Tests.ps1`, `scripts/Test-NoSensitiveData.ps1`, `scripts/bootstrap.ps1`,
  `PSScriptAnalyzerSettings.psd1` and `Invoke-RepositoryInventory.ps1`;
- port the sensitive data gate first, or delete it last. `scripts/run_tests.py` says on
  every run that the scan is not part of it, and that line must stop being true before
  the PowerShell gate goes - otherwise the removal quietly drops a check;
- drop the `gate` job from `.github/workflows/ci.yml`;
- rewrite, rather than remove, the `ConvertTo-Json` row in
  [github-notes.md](../reference/github-notes.md). That document is the ledger of why each
  guard exists, and an entry deleted without trace loses the reason;
- record the evidence in `CHANGELOG.md`.

## Two implementations is not a maintenance model

It is a bounded window, and the argument against it is already written in the code being
replaced: a retry policy implemented twice is a retry policy that drifts. The longer both
live, the more likely a fix lands in one and not the other - so the evidence is worth
running sooner rather than at leisure.
