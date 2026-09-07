<#
.SYNOPSIS
    Makes the foundation modules importable and imports them.

.DESCRIPTION
    The foundation ships as real PowerShell modules with manifests, so they declare
    their dependencies through RequiredModules. That only resolves when the folder
    holding them is on PSModulePath, which is what this script arranges.

    Dot-source it from an automation entry point or a test:

        . (Join-Path $repoRoot 'foundation/Import-Foundation.ps1')

    It is idempotent: repeated dot-sourcing neither duplicates the path entry nor
    reloads the modules, unless -Force is requested.

.PARAMETER FoundationForce
    Reimport the modules even when they are already loaded. Used while developing a
    module, where a stale copy in the session is the usual source of confusion.

.PARAMETER FoundationModule
    Import only the named modules instead of all of them.

.NOTES
    Both parameters are prefixed for the same reason every variable in the body is:
    this script is DOT-SOURCED, so its param block declares variables in the CALLER's
    scope. Inherited from a sibling project, where an unprefixed -Name gave every caller
    a [string[]] typed $Name, and a caller then assigning a string to it silently got
    back a one-element array - failing three layers away with "Cannot convert value
    to type System.String".

.EXAMPLE
    . (Join-Path $repoRoot 'foundation/Import-Foundation.ps1')

.EXAMPLE
    . (Join-Path $repoRoot 'foundation/Import-Foundation.ps1') -FoundationForce

    Reloads every foundation module in the current session.

    Passed as an argument, not assigned beforehand. Setting $FoundationForce first
    and then dot-sourcing does NOT work: the param block below runs in the caller's
    scope and rebinds the switch to its default, so the assignment is overwritten and
    nothing is reloaded - silently, which is exactly the stale-copy confusion the
    switch exists to resolve.
#>

[CmdletBinding()]
param(
    [switch] $FoundationForce,
    [string[]] $FoundationModule = @(
        'GitHubAsCode.Configuration',
        'GitHubAsCode.Http',
        'GitHubAsCode.Plan',
        'GitHubAsCode.Report',
        'GitHub.Rest',
        'GitHub.Repository'
    )
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$foundationModuleRoot = Join-Path $PSScriptRoot 'modules'
if (-not (Test-Path -LiteralPath $foundationModuleRoot)) {
    throw "Foundation module folder not found: $foundationModuleRoot"
}

$foundationPathSeparator = [System.IO.Path]::PathSeparator
$foundationCurrentPaths = @($env:PSModulePath -split $foundationPathSeparator | Where-Object { $_ })
if ($foundationCurrentPaths -notcontains $foundationModuleRoot) {
    $env:PSModulePath = $foundationModuleRoot + $foundationPathSeparator + $env:PSModulePath
}

# Import in dependency order: the modules that depend on nothing come first.
# GitHubAsCode.Configuration and .Plan have no dependencies at all, so nothing can
# cycle through them; GitHub.Rest requires Configuration and Http, and
# GitHub.Repository requires Plan. Loading out of order makes RequiredModules report
# a resolution error that names the wrong module.
#
# The GraphQL half (GitHub.GraphQL, GitHub.Projects) is absent on purpose: it arrives
# in phase 5, and a module listed here before it exists fails the import for every
# automation rather than only for the one that needs it.
$foundationImportOrder = @(
    'GitHubAsCode.Configuration',
    'GitHubAsCode.Http',
    'GitHubAsCode.Plan',
    'GitHubAsCode.Report',
    'GitHub.Rest',
    'GitHub.Repository'
)

# Every variable in here is prefixed, because this script is DOT-SOURCED: it shares
# the caller's scope, so a loop variable named $moduleName would silently overwrite
# the caller's own $moduleName.
foreach ($foundationModuleName in $foundationImportOrder) {
    if ($FoundationModule -notcontains $foundationModuleName) { continue }
    if (-not $FoundationForce -and (Get-Module -Name $foundationModuleName)) { continue }
    Import-Module -Name $foundationModuleName -Force:$FoundationForce -ErrorAction Stop
}
Remove-Variable -Name foundationModuleName -ErrorAction SilentlyContinue
