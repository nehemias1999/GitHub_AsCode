<#
.SYNOPSIS
    Runs the full quality gate: static analysis, unit tests, and the sensitive data
    scan.

.DESCRIPTION
    One command, three checks, in increasing order of cost. Having a single entry
    point is not cosmetic: the previous generation of this codebase had test scripts
    with no runner, and by the time anyone looked, several assertions had been false
    for weeks. A suite nobody can run in one step is a suite nobody runs.

    The same command is what CI executes, so "it passed locally" and "it passed in CI"
    mean the same thing.

.PARAMETER Skip
    Checks to skip: Analyzer, Pester, Secrets.

.PARAMETER Path
    Restrict the Pester run to one path.

.PARAMETER RequireDenyTerms
    Fail when the sensitive data gate cannot run its deny-list layer. Off by
    default: that layer reads a file excluded from version control, so a fresh
    clone has none, and a check that cannot pass on a fresh clone is a check
    people learn to ignore.

.EXAMPLE
    .\scripts\Invoke-Tests.ps1

    Runs everything.

.EXAMPLE
    .\scripts\Invoke-Tests.ps1 -Skip Analyzer -Path tests/foundation

    Runs only the foundation tests and the secret scan.
#>
[CmdletBinding()]
param(
    [ValidateSet('Analyzer', 'Pester', 'Secrets')]
    [string[]] $Skip = @(),

    [string] $Path,

    [switch] $RequireDenyTerms
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$failures = New-Object System.Collections.Generic.List[string]

function Write-TestLog {
    <#
    .SYNOPSIS
        Writes a prefixed progress line.

    .PARAMETER Message
        Text to write.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Message,
        [ValidateSet('info', 'warning')] [string] $Level = 'info'
    )

    if ($Level -eq 'warning') {
        Write-Warning "[tests] $Message"
        return
    }
    Write-Information "[tests] $Message" -InformationAction Continue
}

# --- 1. Parse check --------------------------------------------------------
# Cheapest possible check, and it catches the class of mistake that makes every
# other check report something confusing instead of the real problem.

Write-TestLog 'Parsing every script...'
# Filtering on the extension after enumeration, not with -Include: -Include is
# silently ignored when -Path carries no wildcard, which made this check try to
# parse every JSON and Markdown file in the repository.
$scriptFiles = @(
    Get-ChildItem -LiteralPath $repoRoot -Recurse -File |
        Where-Object { $_.Extension -in '.ps1', '.psm1', '.psd1' } |
        Where-Object { $_.FullName -notmatch '[\\/](\.git|artifacts|\.local)[\\/]' }
)
foreach ($file in $scriptFiles) {
    $parseErrors = $null
    [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw -LiteralPath $file.FullName), [ref]$parseErrors) | Out-Null
    if ($parseErrors -and $parseErrors.Count -gt 0) {
        $failures.Add("$($file.Name): $($parseErrors.Count) parse error(s) - first at line $($parseErrors[0].Token.StartLine)")
    }
}
Write-TestLog "Parsed $($scriptFiles.Count) script(s)."

# --- 2. Static analysis ----------------------------------------------------

if ($Skip -notcontains 'Analyzer') {
    if (Get-Module -ListAvailable PSScriptAnalyzer) {
        Import-Module PSScriptAnalyzer -Force
        Write-TestLog 'Running PSScriptAnalyzer...'

        $settingsPath = Join-Path $repoRoot 'PSScriptAnalyzerSettings.psd1'
        $findings = @(Invoke-ScriptAnalyzer -Path $repoRoot -Recurse -Settings $settingsPath -ExcludeRule @() |
            Where-Object { $_.ScriptPath -notmatch '[\\/](\.git|artifacts|\.local)[\\/]' })

        $errors = @($findings | Where-Object { $_.Severity -eq 'Error' })
        $warnings = @($findings | Where-Object { $_.Severity -eq 'Warning' })

        foreach ($finding in ($findings | Sort-Object Severity, ScriptName | Select-Object -First 40)) {
            Write-TestLog ("  {0,-8} {1}:{2} {3}" -f $finding.Severity, $finding.ScriptName, $finding.Line, $finding.RuleName)
        }
        if ($findings.Count -gt 40) {
            Write-TestLog "  ... $($findings.Count - 40) more finding(s) not listed."
        }

        Write-TestLog "PSScriptAnalyzer: $($errors.Count) error(s), $($warnings.Count) warning(s)."
        if ($errors.Count -gt 0) {
            $failures.Add("PSScriptAnalyzer reported $($errors.Count) error(s).")
        }
        # Warnings fail as well. PSScriptAnalyzerSettings.psd1 declares
        # Severity = @('Error','Warning'), so they were already in scope by
        # declaration and this runner simply did not act on it - which made the whole
        # carefully documented exclusion list in that file decorative. It matters
        # specifically too: PSUseCompatibleSyntax emits Warning, so the declared
        # 5.1/7.0 support floor was not being enforced by anything.
        if ($warnings.Count -gt 0) {
            $failures.Add("PSScriptAnalyzer reported $($warnings.Count) warning(s). The settings file declares Warning in scope: fix them, or add an exclusion there with its reason.")
        }
    }
    else {
        # A missing analyser is a failure, not a skip. Passing without analysing
        # produces the same output as analysing cleanly, and -Skip Analyzer already
        # exists for anyone who means to leave it out.
        $failures.Add('PSScriptAnalyzer is not installed, so static analysis did not run. Install-Module PSScriptAnalyzer -Scope CurrentUser, or pass -Skip Analyzer to leave it out deliberately.')
    }
}

# --- 3. Unit tests ---------------------------------------------------------

if ($Skip -notcontains 'Pester') {
    # Version selection is explicit, and it is not "the newest one installed".
    #
    # Jenkins_AsCode takes the highest available Pester and imports it with
    # -MinimumVersion 5.0. That was written when 5.x was the only 5-or-later major
    # in existence. Pester 6 has since shipped, so on a machine that has it - this
    # one does, 6.1.0 - that gate silently runs the suite on a major nobody tested
    # it against, and reports the result as if it had.
    #
    # So: prefer the newest version inside the range this suite is written for, and
    # fall back to a newer major only with a line saying that is what happened. A
    # test run outside its tested range is not a failure, but it is not the same
    # claim as a run inside it, and the two must not look alike.
    $testedMinimum = [version] '5.5.0'
    $testedBelow = [version] '6.0.0'

    $available = @(Get-Module -ListAvailable Pester | Sort-Object Version -Descending)
    $pester = $available | Where-Object { $_.Version -ge $testedMinimum -and $_.Version -lt $testedBelow } | Select-Object -First 1
    $outsideTestedRange = $false

    if (-not $pester) {
        $pester = $available | Where-Object { $_.Version -ge $testedBelow } | Select-Object -First 1
        if ($pester) { $outsideTestedRange = $true }
    }

    if ($pester) {
        Import-Module Pester -RequiredVersion $pester.Version -Force
        if ($outsideTestedRange) {
            Write-TestLog "Running Pester $($pester.Version), which is OUTSIDE the tested range (>= $testedMinimum, < $testedBelow). No Pester 5.5+ is installed. If the suite fails in a way that looks like Pester itself, install the tested major: Install-Module Pester -MinimumVersion 5.5 -MaximumVersion 5.99.99 -Scope CurrentUser -Force" -Level warning
        }
        else {
            Write-TestLog "Running Pester $($pester.Version)..."
        }

        $testPath = if ($Path) { $Path } else { Join-Path $repoRoot 'tests' }

        # Pester throws when it finds no *.Tests.ps1, and the message reads like a
        # broken invocation rather than like an empty directory. A repository state
        # with no tests yet - the first commit of a series, or -Path pointed at a
        # directory whose tests have not been written - is not a failure of this gate,
        # and turning it into one makes the gate impossible to pass at exactly the
        # moment somebody is setting it up.
        $testFiles = @()
        if (Test-Path -LiteralPath $testPath) {
            $testFiles = @(Get-ChildItem -LiteralPath $testPath -Recurse -File -Filter '*.Tests.ps1' -ErrorAction SilentlyContinue)
        }
        if ($testFiles.Count -eq 0) {
            # Reported and passed over until now, which meant a mistyped -Path
            # produced an empty green run: the same output as a suite that ran and
            # succeeded. -Skip Pester is how to say you meant to leave it out.
            $failures.Add("Pester found no *.Tests.ps1 under $testPath, so no test ran. Check the path, or pass -Skip Pester to leave the suite out deliberately.")
            $skipPester = $true
        }
        else {
            $skipPester = $false
        }

        $configuration = New-PesterConfiguration
        $configuration.Run.Path = $testPath
        $configuration.Run.PassThru = $true
        $configuration.Output.Verbosity = 'Detailed'
        $configuration.Should.ErrorAction = 'Continue'

        if (-not $skipPester) {
            $result = Invoke-Pester -Configuration $configuration
            Write-TestLog "Pester: $($result.PassedCount) passed, $($result.FailedCount) failed, $($result.SkippedCount) skipped."
            if ($result.FailedCount -gt 0) {
                $failures.Add("$($result.FailedCount) test(s) failed.")
            }
        }
    }
    else {
        $found = if ($available.Count -gt 0) { @($available | ForEach-Object { $_.Version.ToString() }) -join ', ' } else { 'none' }
        # Windows ships Pester 3.4, whose syntax is incompatible with these tests.
        # Say so plainly rather than letting it fail with confusing parse errors.
        $failures.Add("Pester 5.5 or later is required and nothing usable is installed (found: $found). Install-Module Pester -MinimumVersion 5.5 -MaximumVersion 5.99.99 -Scope CurrentUser -Force")
    }
}

# --- 4. Sensitive data gate ------------------------------------------------

if ($Skip -notcontains 'Secrets') {
    Write-TestLog 'Running the sensitive data gate...'
    if ($RequireDenyTerms) {
        & (Join-Path $PSScriptRoot 'Test-NoSensitiveData.ps1') -RequireTermsFile
    }
    else {
        & (Join-Path $PSScriptRoot 'Test-NoSensitiveData.ps1')
    }
    # Two failure modes, and telling them apart matters: findings mean something was
    # found, exit 2 means the deny-list layer was required and never ran. Reporting
    # the second as "reported findings" would send someone looking for a match that
    # does not exist.
    $gateExitCode = $LASTEXITCODE
    if ($gateExitCode -eq 2) {
        $failures.Add('The sensitive data gate could not run its deny-list layer, and it was required.')
    }
    elseif ($gateExitCode -ne 0) {
        $failures.Add('The sensitive data gate reported findings.')
    }
}

# --- Result ----------------------------------------------------------------

if ($failures.Count -gt 0) {
    $detail = ($failures | ForEach-Object { "  - $_" }) -join [Environment]::NewLine
    Write-Warning "Quality gate failed:$([Environment]::NewLine)$detail"
    exit 1
}

Write-TestLog 'All checks passed.'
exit 0
