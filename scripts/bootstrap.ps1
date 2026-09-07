<#
.SYNOPSIS
    Prepares a workstation to run the automations.

.DESCRIPTION
    Checks the prerequisites, creates the local folders that are excluded from
    version control, and creates .env from its template.

    The whole point is that there is very little to do: the automations have no
    runtime dependency beyond PowerShell itself - no modules to install, no SDK, no
    package manager. That was a design constraint, not a coincidence, because a tool
    that governs a platform has to run on a locked-down workstation and on a build
    agent without either being specially prepared.

.PARAMETER CheckOnly
    Report on the prerequisites without creating anything. Use it on a machine you do
    not want to leave files on, or in a pipeline that supplies its own environment.

.EXAMPLE
    .\scripts\bootstrap.ps1

    Checks the prerequisites and creates .env from the template if it is missing.

.EXAMPLE
    .\scripts\bootstrap.ps1 -CheckOnly

    Reports without writing anything.
#>
[CmdletBinding()]
param(
    [switch] $CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$problems = New-Object System.Collections.Generic.List[string]

function Write-BootstrapLog {
    <#
    .SYNOPSIS
        Writes a prefixed progress line.

    .PARAMETER Message
        Text to write.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Message)

    Write-Information "[bootstrap] $Message" -InformationAction Continue
}

# --- Prerequisites ---------------------------------------------------------

$powerShellVersion = $PSVersionTable.PSVersion
if ($powerShellVersion.Major -lt 5) {
    $problems.Add("PowerShell $powerShellVersion is too old. Windows PowerShell 5.1 or PowerShell 7 is required.")
}
else {
    Write-BootstrapLog "PowerShell $powerShellVersion."
    if ($powerShellVersion.Major -eq 5) {
        Write-BootstrapLog 'Note: on Windows PowerShell 5.1, configuration files are checked with a reduced schema validator. PowerShell 7 uses the full one. Both are enforced; the 5.1 one covers less.'
    }
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) { Write-BootstrapLog "git found at $($git.Source)." }
else { $problems.Add('git was not found on PATH. It is needed to clone and update this repository.') }

$pester = Get-Module -ListAvailable Pester | Sort-Object Version -Descending | Select-Object -First 1
if ($pester -and $pester.Version.Major -ge 5) {
    Write-BootstrapLog "Pester $($pester.Version) found (tests only)."
}
else {
    $found = if ($pester) { "$($pester.Version)" } else { 'none' }
    Write-BootstrapLog "Pester 5 not available (found: $found). Tests need it; the automations do not:"
    Write-BootstrapLog '  Install-Module Pester -MinimumVersion 5.5 -Scope CurrentUser -Force'
}

$analyzer = Get-Module -ListAvailable PSScriptAnalyzer | Select-Object -First 1
if ($analyzer) {
    Write-BootstrapLog "PSScriptAnalyzer $($analyzer.Version) found (tests only)."
}
else {
    Write-BootstrapLog 'PSScriptAnalyzer not available. Static analysis needs it; the automations do not:'
    Write-BootstrapLog '  Install-Module PSScriptAnalyzer -Scope CurrentUser -Force'
}

# --- Foundation modules ----------------------------------------------------

try {
    . (Join-Path $repoRoot 'foundation/Import-Foundation.ps1')
    $loaded = @(Get-Module | Where-Object { $_.Name -match '^(GitHubAsCode|GitHub)\.' })
    Write-BootstrapLog "Foundation modules loaded: $($loaded.Count) ($(($loaded.Name | Sort-Object) -join ', '))."
}
catch {
    $problems.Add("The foundation modules failed to load: $($_.Exception.Message)")
}

# --- Local folders and .env -----------------------------------------------

$localFolders = @('.local', 'artifacts/inventory', 'artifacts/reports', 'artifacts/logs')
$envPath = Join-Path $repoRoot '.env'
$envTemplatePath = Join-Path $repoRoot '.env.example'
$termsPath = Join-Path $repoRoot '.local/sensitive-terms.txt'

if ($CheckOnly) {
    foreach ($folder in $localFolders) {
        $exists = Test-Path -LiteralPath (Join-Path $repoRoot $folder)
        Write-BootstrapLog "$folder : $(if ($exists) { 'present' } else { 'would be created' })"
    }
    Write-BootstrapLog ".env : $(if (Test-Path -LiteralPath $envPath) { 'present' } else { 'would be created from .env.example' })"
    Write-BootstrapLog ".local/sensitive-terms.txt : $(if (Test-Path -LiteralPath $termsPath) { 'present' } else { 'would be created empty' })"
}
else {
    foreach ($folder in $localFolders) {
        $path = Join-Path $repoRoot $folder
        if (-not (Test-Path -LiteralPath $path)) {
            New-Item -ItemType Directory -Force -Path $path | Out-Null
            Write-BootstrapLog "created $folder"
        }
    }

    if (Test-Path -LiteralPath $envPath) {
        Write-BootstrapLog '.env already exists and was left untouched.'
    }
    elseif (Test-Path -LiteralPath $envTemplatePath) {
        Copy-Item -LiteralPath $envTemplatePath -Destination $envPath
        Write-BootstrapLog 'created .env from .env.example. Fill in GITHUB_OWNER and GITHUB_TOKEN_READ; that is everything repo-inventory needs. The write tokens stay empty until phase 3, and .env.example says which permissions each one wants.'
    }
    else {
        $problems.Add('.env.example is missing, so .env could not be created.')
    }

    # The deny-term layer of the sensitive data gate reads this file. Without it the gate
    # runs structural rules only - it still passes, which is the dangerous part - so it is
    # created here rather than left to be discovered missing.
    if (Test-Path -LiteralPath $termsPath) {
        Write-BootstrapLog '.local/sensitive-terms.txt already exists and was left untouched.'
    }
    else {
        # Created EMPTY on purpose. The terms are themselves sensitive, so seeding real ones
        # from this script would commit them to the repository that looks for them.
        # WriteAllLines writes UTF-8 with no BOM; a BOM would corrupt the first term read.
        [System.IO.File]::WriteAllLines($termsPath, [string[]] @(
            '# One literal deny term per line. Blank lines and lines starting with # are ignored.',
            '#',
            '# This is the second layer of scripts/Test-NoSensitiveData.ps1. The first layer',
            '# matches the SHAPE of a secret, so it cannot catch an internal identifier that has',
            '# no recognisable shape: a folder name, a job path, an opaque custom field id, a',
            '# project code name. Those are what leak by being unremarkable.',
            '#',
            '# Add yours below. .local/ is excluded from version control, which is why they go',
            '# here and never in a tracked file.'
        ))
        Write-BootstrapLog 'created .local/sensitive-terms.txt (empty). Add one literal term per line: folder names, job paths, field ids, project code names.'
    }
}

# --- Result ----------------------------------------------------------------

if ($problems.Count -gt 0) {
    $detail = ($problems | ForEach-Object { "  - $_" }) -join [Environment]::NewLine
    throw "Bootstrap found $($problems.Count) problem(s):$([Environment]::NewLine)$detail"
}

Write-BootstrapLog 'Ready.'
Write-BootstrapLog 'Next: fill in .env, then run an automation with -Command validate. That needs no credentials and proves the configuration is sound.'
