# Getting started

**Purpose.** Get from a fresh clone to a plan that reports zero pending.

**Scope.** Phase 1 - `repo-inventory`.

**Audience.** Anyone running this for the first time.

## Prerequisites

PowerShell, and nothing else. Windows PowerShell 5.1 is the supported floor;
PowerShell 7 also works.

Running the **quality gate** needs two modules, which `bootstrap.ps1 -CheckOnly`
reports on:

```powershell
Install-Module Pester -MinimumVersion 5.5 -MaximumVersion 5.99.99 -Scope CurrentUser
Install-Module PSScriptAnalyzer -Scope CurrentUser
```

The upper bound on Pester is deliberate. `Invoke-Tests.ps1` prefers the newest version
inside that range and falls back to a newer major only with a line saying so - because
a suite running outside its tested range is not making the same claim as one running
inside it, and the two must not look alike.

## 1. Prepare the workstation

```powershell
.\scripts\bootstrap.ps1
```

Checks prerequisites and creates `.env` from `.env.example`.

## 2. Check the declaration, offline

Do this before setting up any credential. It contacts nothing and needs no token, so if
it fails, the problem is in the file and not in your setup.

```powershell
.\automations\repo-inventory\Invoke-RepositoryInventory.ps1 -Command validate
```

With no active declaration yet, it validates the shipped template and says so.

## 3. Create a token

Settings > Developer settings > Personal access tokens > **Fine-grained tokens**.

Not a classic token. The reason is in
[security-model.md](../reference/security-model.md), and it is structural: there is no
fine-grained permission equivalent to deleting a repository, so a fine-grained token
cannot delete one at all.

For phase 1, grant read-only:

- Repository access: **All repositories** (the inventory's job is to be complete)
- Repository permissions: **Metadata: read**, **Contents: read**, **Issues: read**,
  **Administration: read**

Set the shortest expiry you can live with. `inventory` reports the days remaining.

## 4. Fill in `.env`

```
GITHUB_OWNER=your-login
GITHUB_TOKEN_READ=github_pat_...
```

Leave the three write tokens empty. They belong to phases 3 to 5 and nothing reads them
yet.

## 5. Inventory the account

```powershell
.\automations\repo-inventory\Invoke-RepositoryInventory.ps1 -Command inventory
```

Read the summary. **The number that matters is the private count.** If your account has
private repositories and this reports zero, something is reading the public endpoint -
which is the exact failure this automation exists to prevent. Cross-check it:

```powershell
gh repo list your-login --limit 200 | Measure-Object
```

The report lands under `artifacts/repo-inventory/`, as JSON and as Markdown.

## 6. Derive the declaration from the report

Not by hand.

```powershell
Move-Item .\automations\repo-inventory\config\repositories.example.json `
          .\automations\repo-inventory\config\repositories.json
```

`Move-Item`, not `Copy-Item`. `repositories.json` is the name `.gitignore` excludes;
copying invites a differently named file full of real repository names that Git happily
tracks.

Then edit it to describe what the report found: one entry per repository, each with the
`class` it belongs to, and the `description` and `topics` it should carry.

## 7. Plan, and get to zero

```powershell
.\automations\repo-inventory\Invoke-RepositoryInventory.ps1 -Command plan
```

What you are aiming for is not "all ok":

```
pending   = 0
blocked   = 0
protected = non-zero, and the SAME number on the next run
```

`pending` items are what `repo-metadata` will change in phase 3. `blocked` items need a
person - most often a declared name that does not exist, or one the token cannot see.
`adopt`/`warning` items are repositories nothing declares yet.

**Run `plan` twice.** That is not a typo. A second run reporting the same operations is
what makes any later finding believable: it proves the inventory and the comparison
agree. If the two runs differ over an unchanged account, the difference is not on
GitHub.

## 8. Run the gate before committing anything

```powershell
.\scripts\Invoke-Tests.ps1
```

Parse check, PSScriptAnalyzer, Pester, and the sensitive data scan. CI runs the
identical command, so "it passed locally" and "it passed in CI" mean the same thing.

## When something goes wrong

[troubleshooting.md](troubleshooting.md).
