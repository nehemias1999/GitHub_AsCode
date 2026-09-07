<#
    Tests for the sensitive data gate itself.

    The gate is the one check whose failure mode is silence, so it gets its own tests.
    Get-GitIgnoredPath is extracted from the script and dot-sourced, the same way the
    pipeline-drift decision functions are.
#>

BeforeAll {
    . (Join-Path $PSScriptRoot '../TestHelpers.ps1')

    $script:GatePath = Join-Path (Get-RepositoryRoot) 'scripts/Test-NoSensitiveData.ps1'
    $source = Get-Content -LiteralPath $script:GatePath -Raw
    $match = [regex]::Match($source, '(?ms)^function Get-GitIgnoredPath \{.*?^\}')
    if (-not $match.Success) { throw "Could not extract Get-GitIgnoredPath from $script:GatePath." }
    . ([scriptblock]::Create($match.Value))
}

Describe 'Get-GitIgnoredPath' {

    It 'reports an ignored file as ignored' {
        # The probe is created by the test. An earlier version asserted against .env
        # and artifacts/, which exist only after bootstrap.ps1 has run - so the suite
        # passed on a developed working copy and failed on a fresh clone. A test that
        # depends on local state is a test that reports on the wrong thing.
        #
        # This one is matched by the *.tmp rule at the repository root, so it exercises
        # the exact-filename path rather than the directory one.
        # The name carries a guid because two runs of this suite at once would
        # otherwise fight over one file, and the loser would report on the other's
        # state - the same class of problem the comment above describes.
        $root = Get-RepositoryRoot
        $probe = Join-Path $root ("gitignored-probe-" + [guid]::NewGuid().ToString('N') + '.tmp')
        Set-Content -LiteralPath $probe -Value 'probe' -Encoding ascii
        try {
            @(Get-GitIgnoredPath -Root $root -Path @($probe)).Count | Should -Be 1
        }
        finally {
            Remove-Assertedly -Path $probe
        }
    }

    It 'does not report a tracked file as ignored' {
        $root = Get-RepositoryRoot
        @(Get-GitIgnoredPath -Root $root -Path @((Join-Path $root 'README.md'))).Count | Should -Be 0
    }

    It 'reports a file inside an ignored directory' {
        # git collapses an ignored directory into one entry with a trailing slash, so a
        # directory match has to be a prefix match rather than an equality test. The
        # directory is only removed again if this test is what created it.
        $root = Get-RepositoryRoot
        $directory = Join-Path $root 'artifacts'
        $createdHere = -not (Test-Path -LiteralPath $directory)
        $nested = Join-Path $directory ("reports/gitignored-probe-" + [guid]::NewGuid().ToString('N') + '.json')
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $nested) | Out-Null
        Set-Content -LiteralPath $nested -Value '{}' -Encoding ascii
        try {
            @(Get-GitIgnoredPath -Root $root -Path @($nested)).Count | Should -Be 1
        }
        finally {
            Remove-Assertedly -Path $nested
            if ($createdHere) { Remove-Assertedly -Path $directory -Recurse }
        }
    }

    It 'returns nothing for an empty input rather than failing' {
        @(Get-GitIgnoredPath -Root (Get-RepositoryRoot) -Path @()).Count | Should -Be 0
    }

    It 'returns nothing when the directory is not a working copy, so the scan covers everything' {
        # Failing loud: an unknown ignore state must widen the scan, never narrow it.
        @(Get-GitIgnoredPath -Root $env:TEMP -Path @((Join-Path $env:TEMP 'x.txt'))).Count | Should -Be 0
    }
}

Describe 'The gate covers the whole repository' {

    It 'passes on the working tree as it stands' {
        # Run as its own process so a failure is an exit code, not an exception here.
        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath 2>&1
        $LASTEXITCODE | Should -Be 0 -Because ($output -join [Environment]::NewLine)
    }

    It 'still reads the files that are not ignored' {
        # A filter bug that excluded everything would make the gate pass vacuously,
        # which looks identical to passing properly.
        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath 2>&1
        ($output -join ' ') | Should -Match 'across \d+ file'
    }

    It 'names the layers that ran, so a structural-only pass is not read as a full scan' {
        # The gate has two layers and only one of them can run without a local file.
        # "No findings" on its own is a clean bill of health for coverage that was
        # never obtained - and the deny list is the only layer that can match an
        # internal identifier with no recognisable shape, which is precisely the kind
        # that leaks. The success line has to say which layers answered.
        $missing = Join-Path ([System.IO.Path]::GetTempPath()) ("no-terms-" + [guid]::NewGuid().ToString() + '.txt')
        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath -TermsFile $missing 2>&1
        $LASTEXITCODE | Should -Be 0 -Because ($output -join [Environment]::NewLine)
        ($output -join ' ') | Should -Match 'structural rules only, no deny terms loaded'
    }

    It 'fails when the deny-list layer is required and did not run' {
        # -RequireTermsFile is how a caller that depends on the deny list says so.
        # Without a distinct exit code, a run with the layer silently absent is
        # indistinguishable from a run with it in force.
        $missing = Join-Path ([System.IO.Path]::GetTempPath()) ("no-terms-" + [guid]::NewGuid().ToString() + '.txt')
        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath -TermsFile $missing -RequireTermsFile 2>&1
        $LASTEXITCODE | Should -Be 2 -Because ($output -join [Environment]::NewLine)
    }

    It 'runs the deny-list layer when the file has terms, and says how many' {
        $terms = Join-Path ([System.IO.Path]::GetTempPath()) ("terms-" + [guid]::NewGuid().ToString() + '.txt')
        try {
            # The term is generated, not written literally, because a literal one
            # would sit in this very file and the gate would dutifully find it there.
            $term = 'absent-' + [guid]::NewGuid().ToString('N')
            Set-Content -LiteralPath $terms -Value @('# a comment is not a term', $term) -Encoding ASCII
            $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath -TermsFile $terms -RequireTermsFile 2>&1
            $LASTEXITCODE | Should -Be 0 -Because ($output -join [Environment]::NewLine)
            ($output -join ' ') | Should -Match 'structural rules \+ 1 deny term'
        }
        finally {
            if (Test-Path -LiteralPath $terms) { Remove-Item -LiteralPath $terms -Force }
        }
    }
}
