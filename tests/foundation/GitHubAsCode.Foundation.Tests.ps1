<#
    The shared layer, which had no tests at all.

    Three of the six foundation modules - GitHubAsCode.Http, .Report and
    .Configuration - shipped without a single test. That is not a coverage complaint:
    those modules hold the controls that security-model.md names, and the absence is
    why two real defects survived into a merged release. A token leak in an error
    message and a report that deleted its own evidence were both found by reading the
    code, because nothing would have gone red.

    The masking and URL-validation half is in GitHubAsCode.Secrets.Tests.ps1, written
    alongside those fixes. This file covers what is left and matters:

      - the retry policy, whose own docstring claims it is "testable offline instead
        of only observable during an outage" - a claim nothing tested
      - the .env loader, and specifically its refusal to set a variable that would
        turn a configuration file into a code-execution path
      - the closed vocabularies, which several modules assert against and which the
        exported accessors exist so a test can check rather than restate

    Every case is named after the failure it prevents.
#>

Set-StrictMode -Version Latest

BeforeAll {
    . (Join-Path $PSScriptRoot '../TestHelpers.ps1')
    . (Join-Path (Get-RepositoryRoot) 'foundation/Import-Foundation.ps1')
}

Describe 'Get-HttpRetryDecision' {

    It 'stops at the attempt limit instead of retrying forever' {
        # The bound that turns a transient outage into a finite run. Without it a 503
        # loop is indistinguishable from a hang.
        $decision = Get-HttpRetryDecision -StatusCode 503 -Attempt 3 -MaximumRetryCount 3

        $decision.ShouldRetry | Should -BeFalse
        $decision.DelaySeconds | Should -Be 0
    }

    It 'does not retry a status the server will answer the same way twice' {
        # A 401 or a 404 is not going to change on its own. Retrying one wastes the rate
        # limit budget and delays the real error by the backoff.
        foreach ($status in @(400, 401, 403, 404, 422)) {
            (Get-HttpRetryDecision -StatusCode $status -Attempt 1 -MaximumRetryCount 3).ShouldRetry |
                Should -BeFalse -Because "HTTP $status is not worth retrying"
        }
    }

    It 'retries a transient status, and a connection failure with no status at all' {
        # StatusCode 0 is the shape of a DNS failure or a dropped connection, which is
        # the most retryable thing there is and the easiest to forget.
        foreach ($status in @(0, 429, 500, 502, 503, 504)) {
            (Get-HttpRetryDecision -StatusCode $status -Attempt 1 -MaximumRetryCount 3).ShouldRetry |
                Should -BeTrue -Because "HTTP $status is transient"
        }
    }

    It 'honours Retry-After rather than its own backoff' {
        # GitHub sends Retry-After on a secondary rate limit, and it is the only number
        # that knows how long the block lasts. Backing off less than it says escalates
        # the block; the server's answer wins.
        (Get-HttpRetryDecision -StatusCode 429 -Attempt 1 -MaximumRetryCount 5 -RetryAfterSeconds 47).DelaySeconds |
            Should -Be 47
    }

    It 'caps an absurd Retry-After instead of sleeping for it' {
        # A server - or a proxy in front of one - can send a Retry-After measured in
        # hours. Obeying it turns a scheduled run into a hung process holding a
        # credential in memory.
        (Get-HttpRetryDecision -StatusCode 429 -Attempt 1 -MaximumRetryCount 5 -RetryAfterSeconds 86400 -RetryAfterCapSeconds 120).DelaySeconds |
            Should -Be 120
    }

    It 'backs off exponentially when the server says nothing' {
        $first = (Get-HttpRetryDecision -StatusCode 503 -Attempt 1 -MaximumRetryCount 5).DelaySeconds
        $second = (Get-HttpRetryDecision -StatusCode 503 -Attempt 2 -MaximumRetryCount 5).DelaySeconds
        $third = (Get-HttpRetryDecision -StatusCode 503 -Attempt 3 -MaximumRetryCount 5).DelaySeconds

        $second | Should -BeGreaterThan $first
        $third | Should -BeGreaterThan $second
    }

    It 'caps its own backoff too, so a high attempt limit cannot produce a long sleep' {
        (Get-HttpRetryDecision -StatusCode 503 -Attempt 20 -MaximumRetryCount 99 -RetryAfterCapSeconds 120).DelaySeconds |
            Should -BeLessOrEqual 120
    }

    It 'agrees with the list it exports for tests to check' {
        # Get-HttpRetryableStatusCode says in its own help that it is "exported so the
        # test suite asserts against the same list the module enforces, rather than
        # restating it and drifting from it". No test used it, so the claim was false.
        foreach ($status in (Get-HttpRetryableStatusCode)) {
            (Get-HttpRetryDecision -StatusCode $status -Attempt 1 -MaximumRetryCount 3).ShouldRetry |
                Should -BeTrue -Because "$status is on the exported retryable list"
        }
    }
}

Describe 'Import-GitHubAsCodeEnvironment' {

    BeforeEach {
        $script:EnvDir = Join-Path ([System.IO.Path]::GetTempPath()) ("env-probe-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Force -Path $script:EnvDir | Out-Null
        $script:EnvFile = Join-Path $script:EnvDir 'probe.env'
    }

    AfterEach {
        foreach ($name in @('GHASCODE_PROBE_ONE', 'GHASCODE_PROBE_TWO', 'GHASCODE_PROBE_QUOTED')) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        Remove-Assertedly -Path $script:EnvDir -Recurse
    }

    It 'refuses to set a variable that would make .env a code-execution path' {
        # THE control this module exists for, and the strongest anti-execution guard in
        # the repository. A .env that could set PSModulePath turns a configuration file
        # into "import this module of mine on the next command", and one that could set
        # Path or DOTNET_STARTUP_HOOKS is worse.
        #
        # Nothing tested it. The module's own description explains the danger in a
        # paragraph, which is exactly the kind of rule that survives on prose until
        # somebody tidies it.
        foreach ($name in @('PSModulePath', 'Path', 'DOTNET_STARTUP_HOOKS', 'LD_PRELOAD')) {
            Set-Content -LiteralPath $script:EnvFile -Value "$name=/somewhere/attacker" -Encoding utf8

            { Import-GitHubAsCodeEnvironment -Path $script:EnvFile } |
                Should -Throw -Because "$name must be refused, not set"
        }
    }

    It 'does not set the protected variable before throwing' {
        # A refusal that happens after the assignment is not a refusal. Checked against
        # the real PSModulePath, which the loader itself depends on.
        $before = $env:PSModulePath
        Set-Content -LiteralPath $script:EnvFile -Value 'PSModulePath=/attacker/modules' -Encoding utf8

        { Import-GitHubAsCodeEnvironment -Path $script:EnvFile } | Should -Throw

        $env:PSModulePath | Should -Be $before
    }

    It 'sets ordinary variables and reports their names' {
        Set-Content -LiteralPath $script:EnvFile -Encoding utf8 -Value @(
            '# a comment',
            '',
            'GHASCODE_PROBE_ONE=first',
            'GHASCODE_PROBE_TWO=second'
        )

        $names = @(Import-GitHubAsCodeEnvironment -Path $script:EnvFile)

        $names | Should -Contain 'GHASCODE_PROBE_ONE'
        $env:GHASCODE_PROBE_ONE | Should -Be 'first'
        $env:GHASCODE_PROBE_TWO | Should -Be 'second'
    }

    It 'rejects a name that is not a valid variable name' {
        # A malformed line is a typo in a credential file, and the module's help says
        # that is "how a run proceeds without the credential it needed". Failing loudly
        # is the whole point.
        Set-Content -LiteralPath $script:EnvFile -Value 'not a name=value' -Encoding utf8

        { Import-GitHubAsCodeEnvironment -Path $script:EnvFile } | Should -Throw -ExpectedMessage '*Invalid variable name*'
    }

    It 'fails on a missing file, and skips it only when told to' {
        $absent = Join-Path $script:EnvDir 'not-there.env'

        { Import-GitHubAsCodeEnvironment -Path $absent } | Should -Throw -ExpectedMessage '*not found*'
        { Import-GitHubAsCodeEnvironment -Path $absent -Optional } | Should -Not -Throw
    }
}

Describe 'The plan vocabularies are closed' {

    It 'rejects a status or an action outside the vocabulary' {
        # Add-PlanOperation is the choke point every automation goes through, and the
        # closed list is what stops a typo becoming a status nothing counts. A plan with
        # a status called 'pendign' would summarise as zero pending.
        { New-PlanOperation -Resource 'repository' -Name 'x' -Action 'exists' -Status 'pendign' -Reason 'typo' } |
            Should -Throw -ExpectedMessage '*Unknown plan status*'

        { New-PlanOperation -Resource 'repository' -Name 'x' -Action 'creat' -Status 'ok' -Reason 'typo' } |
            Should -Throw -ExpectedMessage '*Unknown plan action*'
    }

    It 'keeps manual and skip as actions rather than statuses' {
        # The distinction the whole command model rests on, and the easiest thing to get
        # backwards - it was got backwards once while planning this repository. A test
        # is cheaper than rereading the module.
        Get-PlanActionName | Should -Contain 'manual'
        Get-PlanActionName | Should -Contain 'skip'
        Get-PlanStatusName | Should -Not -Contain 'manual'
        Get-PlanStatusName | Should -Not -Contain 'skip'
    }

    It 'includes protected, which is what "deliberately not changed" means' {
        Get-PlanStatusName | Should -Contain 'protected'
    }

    It 'counts a blocked operation as blocked' {
        # Test-PlanBlocked drives exit code 2. A scheduled run whose plan was entirely
        # blocked must not report success, and that was a real bug in the sibling
        # project: the answer was computed and thrown away in a log line.
        $plan = New-Plan -Command 'plan' -Target 'EXAMPLE-owner'
        Add-PlanOperation -Plan $plan -Operation (New-PlanOperation -Resource 'repository' -Name 'a' -Action 'exists' -Status 'ok' -Reason 'fine') | Out-Null

        Test-PlanBlocked -Plan $plan | Should -BeFalse

        Add-PlanOperation -Plan $plan -Operation (New-PlanOperation -Resource 'repository' -Name 'b' -Action 'resolve' -Status 'blocked' -Reason 'cannot tell') | Out-Null

        Test-PlanBlocked -Plan $plan | Should -BeTrue
    }
}

Describe 'Get-GitHubAsCodeRequiredValue' {

    It 'names the variable and never its value' {
        # The message a reader sees most often when setting this up. It has to identify
        # the variable without quoting whatever happens to be in it.
        [Environment]::SetEnvironmentVariable('GHASCODE_PROBE_SECRET', $null, 'Process')

        $message = ''
        try { Get-GitHubAsCodeRequiredValue -Name 'GHASCODE_PROBE_SECRET' | Out-Null }
        catch { $message = $_.Exception.Message }

        $message | Should -Match 'GHASCODE_PROBE_SECRET'
        $message | Should -Match '\.env'
    }

    It 'trims surrounding whitespace, which a pasted value arrives with' {
        [Environment]::SetEnvironmentVariable('GHASCODE_PROBE_SECRET', "  value  ", 'Process')
        try {
            Get-GitHubAsCodeRequiredValue -Name 'GHASCODE_PROBE_SECRET' | Should -Be 'value'
        }
        finally {
            [Environment]::SetEnvironmentVariable('GHASCODE_PROBE_SECRET', $null, 'Process')
        }
    }
}
