<#
    The repository rules.

    All pure functions, so all of this runs offline. That is the property that makes
    the idempotency assertion at the bottom possible: drift is defined against the
    payload that would be sent, so a second plan can be tested without a second run.
#>

Set-StrictMode -Version Latest

BeforeAll {
    . (Join-Path $PSScriptRoot '../TestHelpers.ps1')
    . (Join-Path (Get-RepositoryRoot) 'foundation/Import-Foundation.ps1')
}

Describe 'Get-GitHubTopicUnion' {

    It 'keeps a topic that is live and undeclared' {
        # THE test of this repository's central guarantee. PUT /topics replaces the
        # whole collection, so an implementation that sends the declared list deletes
        # every topic added by hand. Whatever sits on a repository nobody has touched in
        # months is exactly what nobody remembers declaring.
        $union = Get-GitHubTopicUnion -LiveTopic @('added-by-hand') -DeclaredTopic @('ascode')

        $union.Payload | Should -Contain 'added-by-hand'
        $union.Payload | Should -Contain 'ascode'
        $union.Preserved | Should -Be @('added-by-hand')
    }

    It 'reports an undeclared topic as preserved rather than dropping it silently' {
        # Keeping it is not enough: the plan has to say it kept it, or the reader
        # cannot tell a preserved topic from one that was declared all along.
        $union = Get-GitHubTopicUnion -LiveTopic @('one', 'two') -DeclaredTopic @('one')

        $union.Preserved | Should -Be @('two')
        $union.Added | Should -BeNullOrEmpty
    }

    It 'treats a declared topic that differs only in case as already present' {
        # GitHub lowercases a topic on the way in. Comparing raw makes the plan report
        # a change, the apply store the lowercase form, and the next plan report the
        # same change - forever. That is an idempotency failure, which is the
        # acceptance criterion, not a cosmetic one.
        $union = Get-GitHubTopicUnion -LiveTopic @('powershell') -DeclaredTopic @('PowerShell')

        $union.Changed | Should -BeFalse
        $union.Added | Should -BeNullOrEmpty
    }

    It 'is a no-op the second time, given the payload the first time produced' {
        # Idempotency, executed rather than promised. A second plan over the state a
        # first apply would have left must find nothing to do.
        $declared = @('ascode', 'powershell')
        $first = Get-GitHubTopicUnion -LiveTopic @('added-by-hand') -DeclaredTopic $declared

        $second = Get-GitHubTopicUnion -LiveTopic $first.Payload -DeclaredTopic $declared

        $second.Changed | Should -BeFalse
        @($second.Payload) | Should -Be @($first.Payload)
    }

    It 'produces the same payload regardless of the order the API returned topics in' {
        # The API does not specify an order. An unsorted payload makes one declaration
        # produce two different payloads between runs, which the drift comparison then
        # reads as a change.
        $a = Get-GitHubTopicUnion -LiveTopic @('zeta', 'alpha') -DeclaredTopic @('mid')
        $b = Get-GitHubTopicUnion -LiveTopic @('alpha', 'zeta') -DeclaredTopic @('mid')

        @($a.Payload) | Should -Be @($b.Payload)
    }

    It 'does not duplicate a topic that is both live and declared' {
        $union = Get-GitHubTopicUnion -LiveTopic @('same') -DeclaredTopic @('same')

        @($union.Payload).Count | Should -Be 1
    }

    It 'handles an empty live collection, which is every repository on a fresh account' {
        $union = Get-GitHubTopicUnion -LiveTopic @() -DeclaredTopic @('first-topic')

        @($union.Payload) | Should -Be @('first-topic')
        $union.Changed | Should -BeTrue
    }
}

Describe 'Format-GitHubTopicName' {

    It 'lowercases, because that is what the API stores' {
        Format-GitHubTopicName -Topic 'PowerShell' | Should -Be 'powershell'
    }

    It 'rejects a topic it cannot store rather than mangling it into a different one' {
        # Quietly turning 'c#' into 'c' gives the account a topic nobody chose, on a
        # repository nobody was looking at.
        { Format-GitHubTopicName -Topic 'c#' } | Should -Throw
        { Format-GitHubTopicName -Topic 'has space' } | Should -Throw
        { Format-GitHubTopicName -Topic '-leading-hyphen' } | Should -Throw
        { Format-GitHubTopicName -Topic '' } | Should -Throw
    }

    It 'rejects a topic longer than the 50 characters GitHub accepts' {
        { Format-GitHubTopicName -Topic ('a' * 51) } | Should -Throw -ExpectedMessage '*at most 50*'
    }

    It 'accepts the forms that are legal' {
        Format-GitHubTopicName -Topic 'as-code' | Should -Be 'as-code'
        Format-GitHubTopicName -Topic '3d' | Should -Be '3d'
        Format-GitHubTopicName -Topic ('a' * 50) | Should -Be ('a' * 50)
    }
}

Describe 'New-GitHubRepositorySnapshot' {

    BeforeAll {
        $script:page1 = Get-FixtureObject -Name 'repos.page1.json'
        $script:tool = $script:page1 | Where-Object { $_.name -eq 'EXAMPLE-tool' }
        $script:bare = $script:page1 | Where-Object { $_.name -eq 'EXAMPLE-bare' }
    }

    It 'drops the URL templates a real payload is mostly made of' {
        # Around eighty properties come back per repository. Carrying them all makes
        # even a small report an unreadable megabyte and a diff between two runs
        # meaningless.
        $snapshot = New-GitHubRepositorySnapshot -Repository $script:tool

        $snapshot.PSObject.Properties.Name | Should -Not -Contain 'hooks_url'
        $snapshot.PSObject.Properties.Name | Should -Not -Contain 'clone_url'
        $snapshot.PSObject.Properties.Name | Should -Not -Contain 'owner'
    }

    It 'keeps every field the inventory reports, and no others' {
        $snapshot = New-GitHubRepositorySnapshot -Repository $script:tool
        $expected = @(Get-GitHubSnapshotProperty) + 'license'

        @($snapshot.PSObject.Properties.Name) | Should -Be @($expected)
    }

    It 'reduces the licence object to its identifier' {
        (New-GitHubRepositorySnapshot -Repository $script:tool).license | Should -Be 'MIT'
    }

    It 'reports no licence as $null, not as an empty object' {
        # "No licence" and "a licence with no name" have to be distinguishable: the
        # first is the most common finding on a real account, the second is a bug.
        (New-GitHubRepositorySnapshot -Repository $script:bare).license | Should -BeNullOrEmpty
    }

    It 'always gives topics an array, so a count never breaks' {
        # A field that is sometimes $null and sometimes an array is the shape that
        # makes @($x).Count read 1 for "none".
        $snapshot = New-GitHubRepositorySnapshot -Repository $script:bare

        @($snapshot.topics).Count | Should -Be 0
    }

    It 'gives every snapshot the same shape, so a report writer never tests for a missing key' {
        $shapes = @($script:page1 | ForEach-Object { (New-GitHubRepositorySnapshot -Repository $_).PSObject.Properties.Name -join ',' } | Sort-Object -Unique)

        @($shapes).Count | Should -Be 1
    }
}

Describe 'Get-GitHubRepositoryStatus' {

    BeforeAll {
        $script:snapshots = @{}
        foreach ($name in @('repos.page1.json', 'repos.page2.json')) {
            foreach ($repository in (Get-FixtureObject -Name $name)) {
                $script:snapshots[$repository.name] = New-GitHubRepositorySnapshot -Repository $repository
            }
        }
    }

    It 'reports a declared repository the API did not return as blocked, never as create' {
        # THE 404 test. GitHub answers 404 both for a repository that does not exist
        # and for one this token cannot see, so the tool genuinely does not know
        # which. Reporting create would be a plan to make something that may already
        # exist; blocked is what "could not be determined" means.
        $declaration = [pscustomobject]@{ name = 'EXAMPLE-absent'; class = 'tool' }

        $status = Get-GitHubRepositoryStatus -Declaration $declaration -Snapshot $null

        $status.Status | Should -Be 'blocked'
        $status.Action | Should -Be 'resolve'
        $status.Action | Should -Not -Be 'create'
        $status.Reason | Should -Match 'cannot see it'
    }

    It 'reports an archived repository as protected and plans nothing against it' {
        # An archived repository is read-only: every write fails. A plan whose apply
        # cannot succeed is not a plan.
        $declaration = [pscustomobject]@{ name = 'EXAMPLE-archived'; class = 'archived'; description = 'something else entirely' }

        $status = Get-GitHubRepositoryStatus -Declaration $declaration -Snapshot $script:snapshots['EXAMPLE-archived']

        $status.Status | Should -Be 'protected'
        $status.Action | Should -Be 'skip'
    }

    It 'reports a matching repository as ok' {
        $declaration = [pscustomobject]@{
            name        = 'EXAMPLE-tool'
            class       = 'tool'
            description = 'EXAMPLE - a tool.'
            topics      = @('powershell', 'ascode')
        }

        $status = Get-GitHubRepositoryStatus -Declaration $declaration -Snapshot $script:snapshots['EXAMPLE-tool']

        $status.Status | Should -Be 'ok'
        $status.Action | Should -Be 'exists'
    }

    It 'treats an unset description and an empty declared one as the same thing' {
        # The API returns an unset description as null and an unset homepage as an
        # empty string, inconsistently. Comparing them raw makes the plan report a
        # change the apply cannot make, on every run, forever.
        $declaration = [pscustomobject]@{ name = 'EXAMPLE-bare'; class = 'service'; description = ''; homepage = '' }

        $status = Get-GitHubRepositoryStatus -Declaration $declaration -Snapshot $script:snapshots['EXAMPLE-bare']

        $status.Status | Should -Be 'ok'
    }

    It 'names the fields that differ, so the approver reads fields and not a word' {
        # "pending" on its own is not reviewable. The reason exists for the person
        # approving the plan, not for a log parser.
        $declaration = [pscustomobject]@{
            name        = 'EXAMPLE-bare'
            class       = 'service'
            description = 'EXAMPLE - a new description.'
            topics      = @('newly-declared')
        }

        $status = Get-GitHubRepositoryStatus -Declaration $declaration -Snapshot $script:snapshots['EXAMPLE-bare']

        $status.Status | Should -Be 'pending'
        $status.Action | Should -Be 'update'
        $status.Reason | Should -Match 'description'
        $status.Reason | Should -Match 'topics'
        @($status.Difference).Count | Should -Be 2
    }

    It 'does not report a change when the declaration only repeats topics already live' {
        # The union is additive, so declaring what is already there is not a change.
        # Reporting one would mean plan never converges.
        $declaration = [pscustomobject]@{ name = 'EXAMPLE-handmade'; class = 'archived'; topics = @('added-by-hand') }

        $status = Get-GitHubRepositoryStatus -Declaration $declaration -Snapshot $script:snapshots['EXAMPLE-handmade']

        $status.Status | Should -Be 'ok'
    }

    It 'does not compare a field the declaration is silent about' {
        # ok asserts the live state was checked and matched. Claiming that about
        # something never compared is how a report becomes confidently wrong - so an
        # absent declared field yields no difference rather than a difference against
        # empty.
        $declaration = [pscustomobject]@{ name = 'EXAMPLE-tool'; class = 'tool' }

        $status = Get-GitHubRepositoryStatus -Declaration $declaration -Snapshot $script:snapshots['EXAMPLE-tool']

        $status.Status | Should -Be 'ok'
        @($status.Difference).Count | Should -Be 0
    }

    It 'returns only statuses and actions the plan vocabulary defines' {
        # The vocabulary is closed, and Add-PlanOperation throws on an unknown value.
        # Catching it here names the function instead of failing four layers away.
        $validStatus = @(Get-PlanStatusName)
        $validAction = @(Get-PlanActionName)

        foreach ($name in @('EXAMPLE-tool', 'EXAMPLE-bare', 'EXAMPLE-archived', 'EXAMPLE-private')) {
            $declaration = [pscustomobject]@{ name = $name; class = 'tool'; description = 'forces a difference' }
            $status = Get-GitHubRepositoryStatus -Declaration $declaration -Snapshot $script:snapshots[$name]

            $validStatus | Should -Contain $status.Status
            $validAction | Should -Contain $status.Action
        }
    }
}

Describe 'Get-GitHubUndeclaredStatus' {

    BeforeAll {
        $script:bare = New-GitHubRepositorySnapshot -Repository ((Get-FixtureObject -Name 'repos.page1.json') | Where-Object { $_.name -eq 'EXAMPLE-bare' })
    }

    It 'reports adopt and warning, not create and pending' {
        # It exists already, so nothing is created; and there is nothing to change
        # until somebody writes down what it should look like, so nothing is pending.
        $status = Get-GitHubUndeclaredStatus -Snapshot $script:bare

        $status.Action | Should -Be 'adopt'
        $status.Status | Should -Be 'warning'
    }

    It 'says what is missing, because that is the finding of the first run' {
        # On an account nobody has ever declared, every repository lands here. A
        # reason that only said "not declared" twenty-four times would waste the run.
        $status = Get-GitHubUndeclaredStatus -Snapshot $script:bare

        $status.Reason | Should -Match 'no licence'
        $status.Reason | Should -Match 'no topics'
    }

    It 'marks a private repository as private in its reason' {
        $private = New-GitHubRepositorySnapshot -Repository ((Get-FixtureObject -Name 'repos.page2.json') | Where-Object { $_.name -eq 'EXAMPLE-private' })

        (Get-GitHubUndeclaredStatus -Snapshot $private).Reason | Should -Match 'private'
    }
}

Describe 'Format-GitHubRepositoryName' {

    It 'rejects owner/repo, which is the most common mistake in this field' {
        # The schema forbids a slash with a pattern, and on Windows PowerShell 5.1 that
        # pattern is not enforced: there is no Test-Json -Schema, so the reduced
        # validator runs and it does not cover `pattern`. 5.1 is the declared support
        # floor, so on the engine most likely to be running this, THIS is the only check.
        $message = ''
        try { Format-GitHubRepositoryName -Name 'EXAMPLE-owner/EXAMPLE-repo' | Out-Null }
        catch { $message = $_.Exception.Message }

        $message | Should -Match 'owner/repo'
        $message | Should -Match 'owner comes from the environment'
    }

    It 'rejects a relative path segment' {
        # '.' and '..' pass the character class - they are made of allowed characters -
        # and are path traversal the moment a name becomes a URL segment. Phase 3 builds
        # repos/{owner}/{repo}/topics, and New-HttpUri escapes the query, not the path.
        foreach ($name in @('.', '..')) {
            { Format-GitHubRepositoryName -Name $name } |
                Should -Throw -ExpectedMessage '*relative path segment*' -Because "$name must not be usable as a name"
        }
    }

    It 'rejects a name GitHub would not store' {
        foreach ($name in @('has space', 'x#y', 'a?b', '', '   ')) {
            { Format-GitHubRepositoryName -Name $name } |
                Should -Throw -Because "'$name' is not a valid repository name"
        }
    }

    It 'rejects a name longer than the 100 characters GitHub allows' {
        { Format-GitHubRepositoryName -Name ('a' * 101) } | Should -Throw -ExpectedMessage '*at most 100*'
    }

    It 'accepts the forms that are legal, and changes nothing' {
        # Returned unchanged rather than normalised. A repository name is case-sensitive
        # on the way in and GitHub preserves it, so there is nothing safe to normalise -
        # unlike a topic, which the API lowercases and which therefore must be lowercased
        # here to keep the comparison idempotent.
        foreach ($name in @('EXAMPLE-service', 'a.b_c-1', 'MixedCase', ('a' * 100))) {
            Format-GitHubRepositoryName -Name $name | Should -Be $name
        }
    }
}
