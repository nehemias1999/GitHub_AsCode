<#
    Shared test helpers.

    Every fixture here is invented: EXAMPLE-owner, EXAMPLE-repo, example.com. A test
    that borrows a real repository name, host name or credential turns the suite into
    another place sensitive data leaks from, and test files are the last place anyone
    thinks to look. On an account with private repositories the name alone is enough:
    the fact that a given private repository exists is not public.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-RepositoryRoot {
    <#
    .SYNOPSIS
        Absolute path of the repository root.

    .EXAMPLE
        Get-RepositoryRoot

    .OUTPUTS
        The path.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
}

function Get-FixturePath {
    <#
    .SYNOPSIS
        Absolute path of a file under tests/fixtures.

    .PARAMETER Name
        File name.

    .EXAMPLE
        Get-FixturePath -Name 'repos.page1.json'

    .OUTPUTS
        The path.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $Name
    )

    $path = Join-Path (Join-Path $PSScriptRoot 'fixtures') $Name
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Fixture not found: $path"
    }
    return (Resolve-Path -LiteralPath $path).Path
}

function Get-FixtureText {
    <#
    .SYNOPSIS
        Content of a fixture, as text.

    .PARAMETER Name
        File name under tests/fixtures.

    .EXAMPLE
        Get-FixtureText -Name 'link-header-two-links.txt'

    .OUTPUTS
        The text.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $Name
    )

    return (Get-Content -LiteralPath (Get-FixturePath -Name $Name) -Raw)
}

function Get-FixtureObject {
    <#
    .SYNOPSIS
        A JSON fixture, parsed.

    .DESCRIPTION
        The API responses this suite works from are JSON, so the fixtures are too.
        Parsed here rather than in each test, so a malformed fixture fails with the
        file name instead of with an assertion about a property being absent.

    .PARAMETER Name
        File name under tests/fixtures.

    .EXAMPLE
        Get-FixtureObject -Name 'repos.page1.json'

    .OUTPUTS
        The parsed object.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Name
    )

    $path = Get-FixturePath -Name $Name
    try {
        return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)
    }
    catch {
        throw "Fixture '$Name' is not valid JSON: $($_.Exception.Message)"
    }
}

function New-FixtureHeader {
    <#
    .SYNOPSIS
        Builds a response header dictionary for a test.

    .DESCRIPTION
        Response headers are the answer on this API rather than metadata - Link
        carries pagination, x-ratelimit-remaining carries the budget, and
        x-oauth-scopes is how a classic token is told from a fine-grained one. So
        every header-reading function is tested from a dictionary built here.

        Case-insensitive, because a real response is: a test that only passes with
        the exact casing it wrote proves nothing about the function under test.

    .PARAMETER Header
        Name and value pairs.

    .EXAMPLE
        New-FixtureHeader -Header @{ Link = $link; 'x-ratelimit-remaining' = '4999' }

    .OUTPUTS
        A case-insensitive dictionary.
    #>
    # Pure function: it builds a dictionary and changes no system state.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    [OutputType([System.Collections.IDictionary])]
    param(
        [System.Collections.IDictionary] $Header = @{}
    )

    $dictionary = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($key in $Header.Keys) {
        $dictionary[[string] $key] = $Header[$key]
    }
    return $dictionary
}


function Get-PowerShellHostPath {
    <#
    .SYNOPSIS
        Path of the PowerShell executable running this suite.

    .DESCRIPTION
        Tests that need a child process used to name powershell.exe outright, and
        that had two consequences. The only cases that actually execute an entry
        point never ran under PowerShell 7, so half the declared support floor went
        unexercised by the very tests that claim to cover it. And they could not run
        at all on a host where that executable does not exist.

        Asking the current process means the child matches whichever host started
        the suite, so running the suite under 7 actually tests 7.

    .EXAMPLE
        & (Get-PowerShellHostPath) -NoProfile -File $script

    .OUTPUTS
        The executable path.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $path = (Get-Process -Id $PID).Path
    if (-not $path) {
        # A host that will not name its own executable is not one this suite can
        # drive a child process from, and guessing would put us back where we were.
        throw 'Could not determine the path of the PowerShell host running this suite.'
    }
    return $path
}
function Remove-Assertedly {
    <#
    .SYNOPSIS
        Removes a test artefact and says so when it cannot.

    .DESCRIPTION
        Cleanup used to run with -ErrorAction SilentlyContinue, which meant a file
        the suite created inside the working copy could survive the run with nobody
        told. The next thing to notice it would be the sensitive data gate scanning
        it, or a diff, long after the run that left it.

        It warns rather than throwing. This is called from a finally block, and
        throwing there would replace a real assertion failure with a cleanup error -
        hiding the thing the test was actually reporting.

    .PARAMETER Path
        Item to remove.

    .PARAMETER Recurse
        Remove a directory and its contents.

    .EXAMPLE
        try { ... } finally { Remove-Assertedly -Path $probe }
    #>
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [switch] $Recurse
    )

    if (-not (Test-Path -LiteralPath $Path)) { return }
    try { Remove-Item -LiteralPath $Path -Force -Recurse:$Recurse -ErrorAction Stop }
    catch { Write-Warning "Test cleanup could not remove '$Path': $($_.Exception.Message). It is still in the working copy." }
    if (Test-Path -LiteralPath $Path) {
        Write-Warning "Test cleanup left '$Path' behind."
    }
}