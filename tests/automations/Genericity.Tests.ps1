<#
    The template stays generic.

    This repository is published as a GitHub template, and it was not written that way:
    it was written against one real account, and the first cleanup removed around 120
    references to that account's login, its repository names, its measured baseline, and
    the machine it was developed on.

    A cleanup is a one-time event. These tests are what make genericity a property.
    Without them the next commit written while looking at a real account re-introduces
    the problem, and nobody notices until somebody else generates a repository and finds
    a stranger's data in it.

    Same doctrine as the other guards in tests/automations/Automations.Tests.ps1: a
    contract nothing checks is a wish.
#>

Set-StrictMode -Version Latest

BeforeAll {
    . (Join-Path $PSScriptRoot '../TestHelpers.ps1')
    $script:RepositoryRoot = Get-RepositoryRoot

    # Every file Git tracks, which is the set that reaches somebody who generates a
    # repository from this template. Deliberately NOT a filesystem walk: artifacts/,
    # .env and the active declaration are excluded from version control and hold real
    # data by design, so scanning them would report findings that are not findings.
    $script:TrackedFile = @(
        & git -C $script:RepositoryRoot ls-files |
            Where-Object { $_ } |
            ForEach-Object { Join-Path $script:RepositoryRoot $_ } |
            Where-Object { Test-Path -LiteralPath $_ }
    )

    # LICENSE is the one file that names a person on purpose. The copyright holder of an
    # MIT licence is a legal fact about who wrote the code, and removing it would make
    # the template worse rather than more generic. Every other exemption needs the same
    # standard of justification, written here.
    $script:Exempt = @('LICENSE')

    function Get-ScannedFile {
        <#
        .SYNOPSIS
            Tracked files minus the documented exemptions.
        #>
        [CmdletBinding()]
        param()

        return @($script:TrackedFile | Where-Object {
            $name = Split-Path -Leaf $_
            $script:Exempt -notcontains $name
        })
    }

    function Find-InTrackedFile {
        <#
        .SYNOPSIS
            Returns 'path:line' for every tracked file matching a pattern.

        .DESCRIPTION
            Reads with -Raw and fails loudly on a file it cannot read, rather than
            skipping it. A genericity guard that silently passes over a file it could
            not open reports a clean result it did not establish - the same failure the
            sensitive data gate has and that this suite is not going to copy.

        .PARAMETER Pattern
            Regular expression to look for.
        #>
        [CmdletBinding()]
        [OutputType([string[]])]
        param(
            [Parameter(Mandatory)] [string] $Pattern
        )

        $hit = New-Object System.Collections.Generic.List[string]
        foreach ($file in Get-ScannedFile) {
            $content = $null
            try {
                $content = Get-Content -LiteralPath $file -Raw -ErrorAction Stop
            }
            catch {
                throw "Could not read tracked file '$file': $($_.Exception.Message). The genericity guard cannot report a clean result for a file it did not read."
            }
            if ($null -eq $content) { continue }

            $relative = $file.Substring($script:RepositoryRoot.Length).TrimStart('\', '/') -replace '\\', '/'
            $number = 0
            foreach ($line in ($content -split "\r?\n")) {
                $number++
                if ($line -match $Pattern) {
                    $hit.Add("${relative}:${number}: $($line.Trim())")
                }
            }
        }
        return $hit.ToArray()
    }
}

Describe 'The template names no particular account' {

    It 'has tracked files to scan at all' {
        # Guards the guard. If `git ls-files` returns nothing - wrong working directory,
        # git absent from PATH - every test below passes vacuously, which is the worst
        # possible outcome for a suite whose entire job is to find things.
        @(Get-ScannedFile).Count | Should -BeGreaterThan 40
    }

    It 'points no github.com URL at a real account' {
        # A link to somebody else's repository is worse than no link: it works, so
        # nobody questions it, and it sends the reader somewhere that is not their
        # project. Placeholders and the reserved example accounts are fine.
        $allowed = 'owner|OWNER|EXAMPLE|your-|octocat|<[^>]+>'
        $found = @(Find-InTrackedFile -Pattern "github\.com/(?!($allowed))[A-Za-z0-9][A-Za-z0-9-]{0,38}/")

        $found | Should -BeNullOrEmpty -Because "a template must not link to a particular account's repositories:`n$($found -join "`n")"
    }

    It 'contains no workstation path' {
        # C:\Users\<somebody> in a committed file says who built this and on what. It
        # also breaks for every reader, since the path does not exist on their machine.
        $found = @(Find-InTrackedFile -Pattern '[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9._-]+')

        $found | Should -BeNullOrEmpty -Because "a committed file names a developer's home directory:`n$($found -join "`n")"
    }

    It 'states no engine or tool version as a local measurement' {
        # "PowerShell 5.1.26100" and "Pester 6.1.0" are facts about one machine on one
        # day. They read as current, they expire on their own, and they tell the reader
        # nothing about their own environment. Version FLOORS - 5.1, 7.0, 5.5 - are
        # requirements and stay; four-part build numbers are measurements and go.
        $found = @(Find-InTrackedFile -Pattern '\b\d+\.\d+\.\d{3,}(\.\d+)?\b')

        $found | Should -BeNullOrEmpty -Because "a build-level version number is a measurement of one machine, not a requirement:`n$($found -join "`n")"
    }

    It 'reports a measurement as belonging to one account rather than to the reader' {
        # The baseline numbers are kept on purpose - they are the evidence that the API
        # traps are real. What they must not do is address the reader as though the
        # numbers were theirs. "on this account" in a document is that mistake;
        # "on the account where this was measured" is not.
        $found = @(Find-InTrackedFile -Pattern '(?i)\b(on|against) (this|the live) account\b')

        # The runtime log line in the entry point is the exception and is correct: when
        # a run prints "on this account" it is talking about the account it just read.
        $inDocumentation = @($found | Where-Object { $_ -notmatch '^automations/.*\.ps1:' })

        $inDocumentation | Should -BeNullOrEmpty -Because "documentation must not present one account's measurement as the reader's own:`n$($inDocumentation -join "`n")"
    }
}

Describe 'The template asks to be personalised' {

    It 'ships the author placeholder in every module manifest' {
        # The inverse assertion of the ones above, and the reason this file is not just
        # a blocklist. Six manifests carry Author and Copyright; if a real name ever
        # lands in them it will be because somebody personalised their generated
        # repository - which is what should happen - or because the template regressed.
        $manifest = @(Get-ChildItem (Join-Path $script:RepositoryRoot 'foundation/modules') -Recurse -Filter '*.psd1')
        @($manifest).Count | Should -BeGreaterThan 0

        foreach ($file in $manifest) {
            $content = Get-Content -LiteralPath $file.FullName -Raw
            $content | Should -Match "Author\s*=\s*'TEMPLATE-AUTHOR'" -Because "$($file.Name) must ship the placeholder, not a name"
            $content | Should -Match "Copyright\s*=\s*'\(c\) TEMPLATE-AUTHOR" -Because "$($file.Name) must ship the placeholder, not a name"
        }
    }

    It 'documents how to replace the placeholder' {
        # A placeholder with no instructions is a defect. This is the link between the
        # test above and the reader.
        $guide = Join-Path $script:RepositoryRoot 'docs/guides/using-this-template.md'
        Test-Path -LiteralPath $guide | Should -BeTrue

        (Get-Content -LiteralPath $guide -Raw) | Should -Match 'TEMPLATE-AUTHOR'
    }

    It 'keeps the licence holder out of the placeholder scheme' {
        # LICENSE is exempt from the scans above, so assert what it must contain rather
        # than leaving the exemption unchecked. An MIT licence with a placeholder holder
        # is not a licence.
        $license = Get-Content -LiteralPath (Join-Path $script:RepositoryRoot 'LICENSE') -Raw

        $license | Should -Match '(?i)MIT License'
        $license | Should -Not -Match 'TEMPLATE-AUTHOR' -Because 'the licence needs a real copyright holder, not a placeholder'
    }
}
