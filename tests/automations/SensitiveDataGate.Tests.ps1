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

Describe 'The gate actually finds a secret' {

    # Until this block existed, every case in this file asserted that the gate PASSES,
    # or checked a line in its summary. Not one planted a secret and required the gate
    # to find it - so `$rules = @()` would have left the whole suite green, and so did
    # the real defect these cases now cover: the gate read only an allowlist of text
    # extensions, which meant .pem, .netrc, .sh and the rest were never opened at all.
    #
    # Measured before the fix, against exactly the files below: "no findings across
    # 0 file(s)", exit 0. Not a missed rule - it read nothing and reported success.

    BeforeEach {
        $script:Probe = Join-Path ([System.IO.Path]::GetTempPath()) ("gate-probe-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Force -Path $script:Probe | Out-Null

        # Assembled from parts, so this test file is not itself credential-shaped - the
        # same reason as in tests/foundation/GitHubAsCode.Secrets.Tests.ps1.
        $script:Token = ('gh' + 'p_') + ('EXAMPLE' * 5)
    }

    AfterEach {
        Remove-Assertedly -Path $script:Probe -Recurse
    }

    It 'finds a private key block in a file whose extension it once ignored' {
        # The PrivateKeyBlock rule existed all along. It could not fire, because .pem
        # was not on the allowlist - so the rule guarding the most obviously fatal
        # thing to commit had never run against the file type that carries it.
        # Assembled, like the token above: a literal key header in this file is itself
        # a finding, and the gate is right to say so. It caught the first version of
        # this test, which is the guard working rather than an obstacle.
        $fence = ('-' * 5) + 'BEGIN OPENSSH PRIVATE KEY' + ('-' * 5)
        $content = $fence + [Environment]::NewLine +
                   'b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAA' + [Environment]::NewLine +
                   (($fence -replace 'BEGIN', 'END'))
        Set-Content -LiteralPath (Join-Path $script:Probe 'deploy.pem') -Value $content -Encoding utf8

        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath -Path $script:Probe 2>&1

        $LASTEXITCODE | Should -Be 1 -Because ($output -join [Environment]::NewLine)
        ($output -join "`n") | Should -Match 'PrivateKeyBlock'
    }

    It 'finds a token in a credential file with no extension' {
        # .netrc and .npmrc have no extension, and the old rule for extensionless files
        # was an allowlist of five friendly names - LICENSE, README, CHANGELOG, AGENTS,
        # Dockerfile - so every extensionless file that actually holds a credential was
        # excluded by name.
        Set-Content -LiteralPath (Join-Path $script:Probe '.netrc') -Value "machine api.example.com login someone password $script:Token" -Encoding utf8

        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath -Path $script:Probe 2>&1

        $LASTEXITCODE | Should -Be 1 -Because ($output -join [Environment]::NewLine)
        ($output -join "`n") | Should -Match 'GitHubToken'
    }

    It 'finds a token in a shell script' {
        Set-Content -LiteralPath (Join-Path $script:Probe 'deploy.sh') -Value "export GITHUB_TOKEN=$script:Token" -Encoding utf8

        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath -Path $script:Probe 2>&1

        $LASTEXITCODE | Should -Be 1 -Because ($output -join [Environment]::NewLine)
    }

    It 'reports a file it could not read instead of counting it as clean' {
        # The other half of the same defect. The loop read with -ErrorAction
        # SilentlyContinue and skipped on empty content, having already counted the file
        # - so an unreadable file was reported as scanned and clean.
        #
        # A read failure is hard to provoke portably, so this asserts the mechanism that
        # replaced it: the count in the summary is of files actually READ, not of files
        # considered. A zero-byte file is opened successfully and has nothing in it, so
        # it must not be counted.
        #
        # WriteAllText with an empty string, not Set-Content -Value '' - the latter
        # writes a newline, so the file is not empty and is legitimately counted. The
        # first version of this test asserted otherwise and was simply wrong about the
        # cmdlet.
        [System.IO.File]::WriteAllText((Join-Path $script:Probe 'zero-one.txt'), '')
        [System.IO.File]::WriteAllText((Join-Path $script:Probe 'zero-two.txt'), '')

        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath -Path $script:Probe 2>&1

        $LASTEXITCODE | Should -Be 0 -Because ($output -join [Environment]::NewLine)
        ($output -join "`n") | Should -Match 'across 0 file'
    }

    It 'does not read a binary file, whatever the rules would have matched in it' {
        # The NUL-byte check was written as a backstop for "a binary that slips through
        # the allowlist". With the allowlist inverted it is the actual mechanism, so it
        # is worth a test of its own: bytes that happen to spell a token must not be
        # reported out of a file nothing can read as text.
        $bytes = [byte[]] (0, 1, 2, 0) + [Text.Encoding]::ASCII.GetBytes($script:Token) + [byte[]] (0, 0)
        [System.IO.File]::WriteAllBytes((Join-Path $script:Probe 'blob.dat'), $bytes)

        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $script:GatePath -Path $script:Probe 2>&1

        $LASTEXITCODE | Should -Be 0 -Because ($output -join [Environment]::NewLine)
    }
}
