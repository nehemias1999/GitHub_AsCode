<#
    GitHub.Repository - the repository rules, as pure functions.

    Every function in here takes values and returns values. Nothing reaches the
    network, which is not a testing convenience but the design: drift is defined
    against the PAYLOAD that would be sent, not against the declaration, so if the
    payload is a pure value then drift is a comparison of values and a second plan
    returning nothing pending is an assertion a test can make offline.

    Two rules in here are the ones that stop this repository destroying something.

    Topics are a replace-the-whole-collection API. PUT /repos/{o}/{r}/topics has no
    per-topic route, so sending the declared list deletes every topic somebody added
    and nobody declared. Get-GitHubTopicUnion is the answer: the payload is the union
    of live and declared, and the undeclared ones are reported as preserved. Removing
    one is reconcile's job, behind its own confirmation, and reconcile does not exist
    yet.

    Absence is not absence. A repository declared but not returned by the API might
    not exist, or might exist where this token cannot see it - GitHub answers 404 for
    both. So a missing declared repository is never reported as "create it"; it is
    reported as something a person has to resolve.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# The fields the inventory reads, and the only fields it reads. Listed once so the
# snapshot, the report and the schema cannot drift apart.
$script:SnapshotProperty = @(
    'name', 'full_name', 'private', 'visibility', 'archived', 'fork', 'is_template',
    'description', 'homepage', 'default_branch', 'language', 'topics',
    'has_issues', 'has_wiki', 'has_projects', 'has_discussions',
    'pushed_at', 'updated_at', 'created_at', 'size', 'open_issues_count'
)

function Get-GitHubSnapshotProperty {
    <#
    .SYNOPSIS
        Returns the repository fields the inventory reads.

    .DESCRIPTION
        Exported so the test suite and the configuration schema can assert against the
        same list the snapshot builds from, instead of restating it.

    .EXAMPLE
        Get-GitHubSnapshotProperty

    .OUTPUTS
        The property names, in report order.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    return @($script:SnapshotProperty)
}

function Format-GitHubTopicName {
    <#
    .SYNOPSIS
        Normalizes one topic to the form GitHub actually stores.

    .DESCRIPTION
        GitHub lowercases a topic, and accepts only letters, digits and hyphens, up to
        50 characters, starting with a letter or a digit.

        Normalizing here rather than trusting the declaration is what makes the union
        correct. A declaration saying "PowerShell" and a live topic "powershell" are
        the same topic; comparing them raw makes every plan report a change that a
        subsequent plan reports again, because the API stored the lowercase form. That
        is an idempotency failure, and idempotency is the acceptance criterion.

        An unusable topic is rejected rather than silently mangled: quietly turning
        "c#" into "c" gives the account a topic nobody chose.

    .PARAMETER Topic
        The declared topic.

    .EXAMPLE
        Format-GitHubTopicName -Topic 'PowerShell'

    .OUTPUTS
        The normalized topic.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Topic
    )

    $normalized = $Topic.Trim().ToLowerInvariant()

    if (-not $normalized) {
        throw 'A topic cannot be empty.'
    }
    if ($normalized.Length -gt 50) {
        throw "The topic '$normalized' is $($normalized.Length) characters. GitHub allows at most 50."
    }
    if ($normalized -notmatch '^[a-z0-9][a-z0-9-]*$') {
        throw "The topic '$normalized' is not a valid GitHub topic. Allowed: lowercase letters, digits and hyphens, starting with a letter or a digit. It is not normalized automatically, because turning 'c#' into 'c' would give the account a topic nobody chose."
    }

    return $normalized
}

function Format-GitHubRepositoryName {
    <#
    .SYNOPSIS
        Validates a declared repository name, and returns it unchanged.

    .DESCRIPTION
        The schema already constrains this with a pattern - and on Windows PowerShell
        5.1 that pattern is not enforced. There is no Test-Json -Schema on 5.1, so
        GitHubAsCode.Configuration falls back to a reduced validator that covers type,
        required, properties, additionalProperties, items, enum, const and $ref, and
        deliberately does not cover pattern, minLength or the numeric bounds. Its own
        help says so.

        5.1 is the declared support floor, so on the engine most likely to be running
        this, the only check on a repository name is this function.

        Today the consequence is bounded: a declared name is compared against hashtable
        keys and printed, never used to build a URL. The two request paths are the
        literals 'user/repos' and 'user'. But phase 3 builds
        repos/{owner}/{repo}/topics, and at that point the name becomes a path segment -
        and New-HttpUri escapes the query, not the path. What would stand between '../..'
        and a request is exactly this.

        So the name is validated in CODE, before it can matter, for the same reason
        Format-GitHubTopicName exists: the entry point calls both from its invariants
        loop, so an unusable value fails offline in a second rather than as a 422 or
        something stranger halfway through a run.

        Returns the name unchanged rather than normalising it. A repository name is
        case-sensitive on the way in and GitHub preserves it, so there is nothing safe
        to normalise - unlike a topic, which the API lowercases and which therefore has
        to be lowercased here to keep the comparison idempotent.

    .PARAMETER Name
        The declared repository name.

    .EXAMPLE
        Format-GitHubRepositoryName -Name 'EXAMPLE-service'

    .OUTPUTS
        The name, unchanged.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Name
    )

    if ([string]::IsNullOrWhiteSpace($Name)) {
        throw 'A repository name cannot be empty.'
    }
    if ($Name.Length -gt 100) {
        throw "The repository name is $($Name.Length) characters. GitHub allows at most 100."
    }
    # GitHub accepts letters, digits, hyphen, underscore and dot. Notably NOT the
    # slash - a value containing one is either owner/repo, which is the single most
    # common mistake in this field, or a traversal attempt.
    if ($Name -notmatch '^[A-Za-z0-9._-]+$') {
        $reason = if ($Name.Contains('/')) {
            "it contains '/', so it is probably owner/repo - declare the repository name alone, because the owner comes from the environment"
        }
        else {
            'allowed characters are letters, digits, hyphen, underscore and dot'
        }
        throw "The repository name '$Name' is not a valid GitHub repository name: $reason."
    }
    # '.' and '..' are valid against the character class above and are path traversal
    # once a name becomes a URL segment.
    if ($Name -eq '.' -or $Name -eq '..') {
        throw "The repository name '$Name' is a relative path segment, not a name."
    }

    return $Name
}

function Get-GitHubTopicUnion {
    <#
    .SYNOPSIS
        Builds the topic payload: everything live, plus everything declared.

    .DESCRIPTION
        THIS IS THE FUNCTION THAT STOPS TOPICS BEING DESTROYED.

        PUT /repos/{owner}/{repo}/topics replaces the entire collection and there is
        no per-topic route. The obvious implementation - send the declared topics -
        deletes every topic that was added by hand and never written down. That is a
        real risk rather than a hypothetical one on any account with older repositories:
        whatever is on a repository nobody has touched in months is exactly the kind of
        thing nobody remembers declaring.

        So the payload is the union, and the result also says which live topics were
        not declared, so the plan can report them as preserved rather than silently
        keeping them. Removing a topic is reconcile's job, behind its own
        confirmation.

        The order is sorted, because the API returns topics in an unspecified order
        and an unsorted payload makes the same declaration produce two different
        payloads between runs - which the drift comparison would then read as a
        change.

    .PARAMETER LiveTopic
        Topics currently on the repository.

    .PARAMETER DeclaredTopic
        Topics from the declaration.

    .EXAMPLE
        Get-GitHubTopicUnion -LiveTopic $repo.topics -DeclaredTopic $declared.topics

    .OUTPUTS
        An object with Payload, Added, Preserved and Changed.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [AllowNull()] [string[]] $LiveTopic,
        [AllowNull()] [string[]] $DeclaredTopic
    )

    $live = @()
    foreach ($topic in @($LiveTopic)) {
        if ($topic) { $live += $topic.Trim().ToLowerInvariant() }
    }

    $declared = @()
    foreach ($topic in @($DeclaredTopic)) {
        if ($topic) { $declared += Format-GitHubTopicName -Topic $topic }
    }

    $union = @($live + $declared | Sort-Object -Unique)
    $added = @($declared | Where-Object { $live -notcontains $_ } | Sort-Object -Unique)
    $preserved = @($live | Where-Object { $declared -notcontains $_ } | Sort-Object -Unique)

    return [pscustomobject]@{
        Payload   = $union
        Added     = $added
        Preserved = $preserved
        Changed   = ($added.Count -gt 0)
    }
}

function New-GitHubRepositorySnapshot {
    <#
    .SYNOPSIS
        Reduces an API repository object to the fields the inventory reports.

    .DESCRIPTION
        The API returns around 80 properties per repository, most of them URL
        templates. Carrying all of them into a report makes even a small inventory an
        unreadable megabyte, and makes a diff between two runs meaningless.

        A property the API did not send becomes $null rather than being absent, so
        every snapshot has the same shape and a report writer never has to test for a
        missing key. topics is the exception: it becomes an empty array, because a
        collection that is sometimes $null and sometimes an array is the shape that
        breaks a count.

    .PARAMETER Repository
        One repository object from the API.

    .EXAMPLE
        New-GitHubRepositorySnapshot -Repository $repo

    .OUTPUTS
        An ordered snapshot object.
    #>
    # Pure function: it reduces one object to another and changes no system state.
    # ShouldProcess would offer a confirmation prompt for something there is nothing
    # to confirm about, and would train people to answer yes.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [pscustomobject] $Repository
    )

    $snapshot = [ordered] @{}
    foreach ($property in $script:SnapshotProperty) {
        $value = $null
        if ($Repository.PSObject.Properties[$property]) {
            $value = $Repository.PSObject.Properties[$property].Value
        }
        $snapshot[$property] = $value
    }

    # topics is a collection, so it is normalized to an array. The rest may be $null.
    $topic = @()
    foreach ($item in @($snapshot['topics'])) {
        if ($item) { $topic += [string] $item }
    }
    $snapshot['topics'] = @($topic | Sort-Object)

    # The licence arrives as an object, and the only part worth reporting is the
    # identifier. A repository with no licence has $null here, not an empty object,
    # so "no licence" and "a licence with no name" cannot be confused.
    $license = $null
    if ($Repository.PSObject.Properties['license'] -and $Repository.license) {
        if ($Repository.license.PSObject.Properties['spdx_id'] -and $Repository.license.spdx_id -ne 'NOASSERTION') {
            $license = [string] $Repository.license.spdx_id
        }
    }
    $snapshot['license'] = $license

    return [pscustomobject] $snapshot
}

function Get-GitHubRepositoryStatus {
    <#
    .SYNOPSIS
        Compares one declared repository against its live state.

    .DESCRIPTION
        Returns the action, status and reason for one repository, in the vocabulary
        GitHubAsCode.Plan enforces. The reason is written for the person approving the
        plan, not for a log parser: "pending" on its own is not reviewable.

        The four cases that matter, and why each gets the status it gets:

        Live but not declared -> adopt / warning. It exists and nothing says what it
        should look like. Not an error, but the reason this inventory is being run.

        Declared but not live -> resolve / blocked. NOT create. GitHub answers 404
        both for a repository that does not exist and for one this token cannot see,
        so the tool genuinely does not know which it is, and blocked is what "could
        not be determined" means.

        Archived -> skip / protected. An archived repository is read-only and every
        write against it fails. Reporting it as pending would produce a plan whose
        apply cannot succeed.

        Declared, live, and different -> update / pending. A change is required and it
        is safe, which is exactly what pending means. The detail of what differs is in
        the Difference property, so the approver reads the fields rather than the word.

    .PARAMETER Declaration
        The declared entry: name, and optionally class, description, homepage, topics.

    .PARAMETER Snapshot
        The snapshot from New-GitHubRepositorySnapshot, or $null when absent.

    .EXAMPLE
        Get-GitHubRepositoryStatus -Declaration $declared -Snapshot $snapshot

    .OUTPUTS
        An object with Action, Status, Reason and Difference.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [pscustomobject] $Declaration,
        [AllowNull()] [pscustomobject] $Snapshot
    )

    if ($null -eq $Snapshot) {
        return [pscustomobject]@{
            Action     = 'resolve'
            Status     = 'blocked'
            Reason     = "Declared, but the API did not return it. On GitHub that means either it does not exist or this token cannot see it, and the two are indistinguishable from here. Check the name, then check the token's repository access."
            Difference = @()
        }
    }

    if ($Snapshot.archived) {
        return [pscustomobject]@{
            Action     = 'skip'
            Status     = 'protected'
            Reason     = 'Archived, so it is read-only and every write against it would fail. Unarchiving is deliberately not automated; do it in the web interface if the repository is coming back into use.'
            Difference = @()
        }
    }

    $difference = New-Object System.Collections.Generic.List[object]

    foreach ($field in @('description', 'homepage')) {
        if (-not $Declaration.PSObject.Properties[$field]) { continue }

        $declaredValue = $Declaration.PSObject.Properties[$field].Value
        if ($null -eq $declaredValue) { continue }

        # The API returns an unset description as $null and an unset homepage as an
        # empty string, inconsistently. Both mean "nothing there", so both normalize
        # to an empty string before comparison - otherwise the plan reports a change
        # that the apply cannot make, forever.
        $liveValue = [string] $Snapshot.$field
        if ([string] $declaredValue -ne $liveValue) {
            $difference.Add([pscustomobject]@{
                Field    = $field
                Live     = $liveValue
                Declared = [string] $declaredValue
            })
        }
    }

    if ($Declaration.PSObject.Properties['topics'] -and $null -ne $Declaration.topics) {
        $union = Get-GitHubTopicUnion -LiveTopic $Snapshot.topics -DeclaredTopic $Declaration.topics
        if ($union.Changed) {
            $difference.Add([pscustomobject]@{
                Field    = 'topics'
                Live     = @($Snapshot.topics) -join ', '
                Declared = @($union.Added) -join ', '
            })
        }
    }

    if ($difference.Count -eq 0) {
        return [pscustomobject]@{
            Action     = 'exists'
            Status     = 'ok'
            Reason     = 'Live state already matches the declaration.'
            Difference = @()
        }
    }

    $fieldList = @($difference | ForEach-Object { $_.Field }) -join ', '
    return [pscustomobject]@{
        Action     = 'update'
        Status     = 'pending'
        Reason     = "Differs from the declaration in: $fieldList. repo-metadata would change these; this inventory only reports them."
        Difference = $difference.ToArray()
    }
}

function Get-GitHubUndeclaredStatus {
    <#
    .SYNOPSIS
        Returns the status for a live repository nothing declares.

    .DESCRIPTION
        Separate from Get-GitHubRepositoryStatus because it is the opposite question,
        and because it is the finding the first run of this inventory exists to
        produce: on an account nobody has ever declared, every repository lands here.

        adopt rather than create: the resource exists and is being brought under
        management as it is. warning rather than pending: there is nothing to change
        until somebody writes down what it should look like.

    .PARAMETER Snapshot
        The snapshot of the undeclared repository.

    .EXAMPLE
        Get-GitHubUndeclaredStatus -Snapshot $snapshot

    .OUTPUTS
        An object with Action, Status and Reason.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [pscustomobject] $Snapshot
    )

    $note = @()
    if (-not $Snapshot.license) { $note += 'no licence' }
    if (@($Snapshot.topics).Count -eq 0) { $note += 'no topics' }
    if ($Snapshot.private) { $note += 'private' }

    $detail = ''
    if ($note.Count -gt 0) { $detail = ' Currently: ' + ($note -join ', ') + '.' }

    return [pscustomobject]@{
        Action = 'adopt'
        Status = 'warning'
        Reason = "Present on the account and not declared, so nothing states how it should be configured.$detail Add it to the configuration to bring it under management."
    }
}

Export-ModuleMember -Function @(
    'Get-GitHubSnapshotProperty',
    'Format-GitHubTopicName',
    'Format-GitHubRepositoryName',
    'Get-GitHubTopicUnion',
    'New-GitHubRepositorySnapshot',
    'Get-GitHubRepositoryStatus',
    'Get-GitHubUndeclaredStatus'
)
