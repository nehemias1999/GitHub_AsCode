<#
.SYNOPSIS
    Inventories every repository the account owns and reports how live state differs
    from the declaration.

.DESCRIPTION
    Command ladder. Nothing here writes to GitHub:

      validate   Offline. The declaration against its schema, plus the invariants a
                 schema cannot express. No network, no token.
      inventory  Reads every repository the account owns - public AND private - and
                 records it. Makes no reference to the declaration, so it is the
                 honest starting point.
      plan       Compares the declaration against live state and classifies every
                 difference.
      smoke      Plan plus the manual verification checklist.

    There is no apply, and no code path that could write: GitHubAsCode.Http has no
    -Method parameter at all in phases 1 and 2. Widening that is an ADR, not an edit.
    See docs/adr/0001-write-boundary.md.

    THE POINT OF THIS AUTOMATION. It reads GET /user/repos, not
    GET /users/{owner}/repos. The second one returns public repositories only, so every
    private repository is absent from an inventory that reports itself complete. An
    inventory whose job is to be the input to a decision is worse than useless when it is
    quietly short.

    How the declaration is produced: not by hand. Run inventory first, read the
    snapshot, and derive the declaration from what was actually found. Then plan
    against the same account must report zero pending - and that zero is the proof
    that the inventory and the comparison agree, which is what makes any later
    finding believable.

    Three things this deliberately does not report as a problem:

    An undeclared repository is reported as adopt/warning and never altered. It
    exists and nothing states how it should look; that is the finding, not an error.

    An archived repository is skip/protected. It is read-only, so every write against
    it would fail, and a plan whose apply cannot succeed is not a plan.

    A declared repository the API did not return is resolve/blocked, NOT create.
    GitHub answers 404 both for a repository that does not exist and for one this
    token cannot see, and the two are indistinguishable from here.

.PARAMETER Command
    Which rung of the ladder to run.

.PARAMETER RepositoryName
    Restrict the run to the named repositories. The scope is recorded in the report,
    because a filtered run and a whole one otherwise differ only in a total - and
    "pending 0" then reads as "everything is aligned" when it could equally mean "one
    repository was examined".

.PARAMETER EnvFile
    Environment files to load. Defaults to .env at the repository root.

.PARAMETER ProjectContextPath
    Override the project context path.

.PARAMETER ConfigurationPath
    Override the declaration path.

.PARAMETER ReportPath
    Override where the report is written.

.EXAMPLE
    .\Invoke-RepositoryInventory.ps1 -Command validate

    Offline. Checks the declaration and contacts nothing.

.EXAMPLE
    .\Invoke-RepositoryInventory.ps1 -Command inventory

    Reads the account and writes the snapshot the declaration is derived from.

.EXAMPLE
    .\Invoke-RepositoryInventory.ps1 -Command plan

    Compares the declaration against live state.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('validate', 'inventory', 'plan', 'smoke')]
    [string] $Command,

    [string[]] $RepositoryName = @(),
    [string[]] $EnvFile,
    [string] $ProjectContextPath,
    [string] $ConfigurationPath,
    [string] $ReportPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$moduleName = 'repo-inventory'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $repositoryRoot 'foundation/Import-Foundation.ps1')

# Opened before the first line of progress, so the transcript holds the whole run and
# not just the part after some later setup step succeeded.
$usedTemplate = $false
$runLogPath = Start-GitHubAsCodeRunLog -RepositoryRoot $repositoryRoot -Module $moduleName -Command $Command

function Write-ModuleLog {
    <#
    .SYNOPSIS
        Writes a prefixed progress line.

    .DESCRIPTION
        Progress goes through here and nowhere else, so the value masker is applied
        here too. Console output does not pass through the report writer, and a token
        can reach a message by more routes than anyone enumerates - an error body, a
        URL with userinfo. Masking at the funnel rather than at each call site means a
        log line added later cannot reintroduce the leak.

    .PARAMETER Message
        Text to write.

    .PARAMETER Level
        info for progress, warning for something a person needs to read.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Message,
        [ValidateSet('info', 'warning')] [string] $Level = 'info'
    )

    $masked = Protect-SecretInText -Text $Message
    Add-GitHubAsCodeRunLogLine -Path $runLogPath -Level $Level -Message $Message
    if ($Level -eq 'warning') {
        Write-Warning "[$moduleName] $masked"
        return
    }
    Write-Information "[$moduleName] $masked" -InformationAction Continue
}

# --- Declaration ----------------------------------------------------------

$projectContextPathResolved = if ($ProjectContextPath) { $ProjectContextPath } else { 'foundation/config/project-context.json' }
$projectContextPathResolved = Resolve-GitHubAsCodePath -Path $projectContextPathResolved -RootPath $repositoryRoot
$projectContext = Get-GitHubAsCodeConfiguration -Path $projectContextPathResolved

$declarationChoice = Resolve-GitHubAsCodeDeclaration -ProjectContext $projectContext -Module $moduleName -RepositoryRoot $repositoryRoot -ConfigurationPath $ConfigurationPath
$configurationPath = $declarationChoice.Path
$usedTemplate = $declarationChoice.UsedTemplate
if ($usedTemplate) {
    Write-ModuleLog "No active declaration at $($declarationChoice.ActivePath). Using the versioned template instead: $configurationPath. The report will describe the example, not an account." -Level warning
}

$declaration = Get-GitHubAsCodeConfiguration -Path $configurationPath
Write-ModuleLog "Declaration: $configurationPath"

# --- validate -------------------------------------------------------------

$validationProblem = New-Object System.Collections.ArrayList

$declaredNames = @($declaration.repositories | ForEach-Object { $_.name })
$duplicateNames = @(Get-GitHubAsCodeDuplicateValue -Value $declaredNames)
if ($duplicateNames.Count -gt 0) {
    $null = $validationProblem.Add("Duplicate repository name(s): $($duplicateNames -join ', '). Two declarations for one repository would each report their own verdict about it.")
}

# The class must be one the classes object defines. JSON Schema cannot express a
# cross-reference between two parts of the same document, so it is checked here -
# offline, where a typo costs a second instead of failing halfway through a run.
$knownClass = @($declaration.classes.PSObject.Properties.Name)
foreach ($repository in $declaration.repositories) {
    if ($knownClass -notcontains $repository.class) {
        $null = $validationProblem.Add("Repository '$($repository.name)' declares class '$($repository.class)', which is not defined. Defined classes: $($knownClass -join ', ').")
    }

    # Validated in code, not only by the schema. On PowerShell 5.1 - the declared
    # support floor - there is no Test-Json -Schema, so the reduced validator runs and
    # it does not enforce `pattern`. The name's shape is therefore unchecked on the
    # engine most likely to be running this, and phase 3 will turn a declared name into
    # a URL path segment.
    try {
        $null = Format-GitHubRepositoryName -Name $repository.name
    }
    catch {
        $null = $validationProblem.Add("Repository declaration: $($_.Exception.Message)")
    }

    # Format-GitHubTopicName is the same function the payload is built from, so a
    # topic that cannot be stored fails here rather than as a 422 mid-run.
    if ($repository.PSObject.Properties['topics'] -and $repository.topics) {
        foreach ($topic in @($repository.topics)) {
            try {
                $null = Format-GitHubTopicName -Topic $topic
            }
            catch {
                $null = $validationProblem.Add("Repository '$($repository.name)' declares an unusable topic: $($_.Exception.Message)")
            }
        }
    }

    # GitHub truncates a description beyond 350 characters, so a longer declared
    # value could never compare equal and every plan would report the same change
    # forever. That is an idempotency failure, and it is catchable offline.
    if ($repository.PSObject.Properties['description'] -and $repository.description) {
        $length = ([string] $repository.description).Length
        if ($length -gt 350) {
            $null = $validationProblem.Add("Repository '$($repository.name)' declares a description of $length characters. GitHub stores at most 350, so this would never compare equal and every plan would report the same change again.")
        }
    }
}

# A name in -RepositoryName that nothing declares is a typo, and a typo that silently
# narrows the run to nothing is how "pending 0" becomes a lie.
foreach ($requested in $RepositoryName) {
    if ($declaredNames -notcontains $requested) {
        $null = $validationProblem.Add("-RepositoryName '$requested' is not declared. Declared: $($declaredNames -join ', ').")
    }
}

if ($validationProblem.Count -gt 0) {
    $detailText = ($validationProblem | ForEach-Object { "  - $_" }) -join [Environment]::NewLine
    throw "The declaration satisfies its schema but is not executable:$([Environment]::NewLine)$detailText"
}

# Which validator ran is part of the result, not a footnote. The reduced 5.1 engine
# ignores pattern, minimum, minItems and the oneOf family, so a report from it carries
# less assurance about its own declaration than one from the full engine.
$schemaEngine = Get-GitHubAsCodeSchemaEngine
Write-ModuleLog "Schema and invariants: $($declaredNames.Count) repository declaration(s), $($knownClass.Count) class(es). Valid ($schemaEngine validation)."

if ($Command -eq 'validate') {
    Write-ModuleLog 'validate is offline and complete. Nothing was contacted.'
    return
}

# --- Live state -----------------------------------------------------------

# -Optional applies to the DEFAULT path only, and the distinction matters.
#
# A fresh clone has no .env, so treating the default as optional is right: the run then
# fails later with a message naming the variable that is missing, which is the useful
# error. But -Optional was passed unconditionally, so an -EnvFile the operator typed by
# hand was skipped in silence too.
#
# The benign version of that is a confusing error three steps later. The bad version is
# this: if the process already has the variables set - a .env loaded in an earlier
# session of the same console, or user-level variables pointing at another account -
# then a mistyped -EnvFile is skipped, Get-GitHubAsCodeRequiredValue finds values
# anyway, and the run completes successfully AGAINST THE WRONG ACCOUNT. It reports
# success, and the operator believes they inventoried what they asked for.
#
# This repository already refuses that pattern elsewhere: Resolve-GitHubAsCodeDeclaration
# returns UsedTemplate and the warning above shouts about it, precisely so a run never
# checks the template in silence while the operator believes it checked their
# declaration. Same reasoning, and it had not been applied here.
$usingDefaultEnvFile = -not $PSBoundParameters.ContainsKey('EnvFile')
$environmentFiles = if ($usingDefaultEnvFile) { @((Join-Path $repositoryRoot '.env')) } else { $EnvFile }
Import-GitHubAsCodeEnvironment -Path $environmentFiles -Optional:$usingDefaultEnvFile

$gitHubContext = Get-GitHubContext -Context $projectContext
Write-ModuleLog "Account: $($gitHubContext.Owner) at $($gitHubContext.BaseUrl), token from $($gitHubContext.TokenEnvironmentName)."

$listing = Get-GitHubOwnedRepository -GitHubContext $gitHubContext
$liveRepository = @($listing.Item)

# The token's own shape is part of the result. A fine-grained token expires on a
# fixed date, and the 401 that follows reads exactly like a revoked token, which
# sends the reader looking in the wrong place. A classic token is reported because
# it is the one that CAN hold delete_repo.
# Unconditional. This was guarded by "if any repository came back", which is always
# true by the time the listing has succeeded - a condition that read as caution and
# decided nothing. Worse, the one case it appeared to protect is the case that most
# needs the answer: an account listing that came back empty is exactly when the
# question "is this token expired, or scoped to nothing?" has to be asked.
$probe = Invoke-GitHubRequest -GitHubContext $gitHubContext -Path 'user'
$tokenShape = Get-GitHubTokenShape -Headers $probe.Headers

if ($tokenShape.IsClassic) {
    Write-ModuleLog "The token is a CLASSIC personal access token, with scopes: $($tokenShape.Scope -join ', '). Reading with it is fine. Before phase 3 adds a writer, replace it with a fine-grained token: there is no fine-grained permission equivalent to deleting a repository, so a fine-grained token cannot delete one at all." -Level warning
    if ($tokenShape.Scope -contains 'delete_repo') {
        Write-ModuleLog 'The token holds the delete_repo scope. Nothing here can use it, but it should not exist: revoke and reissue without it.' -Level warning
    }
}

if ($null -ne $tokenShape.DaysUntilExpiry) {
    $warningDays = 14
    if ($projectContext.defaults.PSObject.Properties['tokenExpiryWarningDays']) {
        $warningDays = [int] $projectContext.defaults.tokenExpiryWarningDays
    }
    if ($tokenShape.DaysUntilExpiry -le $warningDays) {
        Write-ModuleLog "The token expires in $($tokenShape.DaysUntilExpiry) day(s), on $($tokenShape.ExpiresUtc.ToString('yyyy-MM-dd')). Reissue it before a scheduled run starts failing with a 401 that looks like revocation." -Level warning
    }
}

$publicCount = @($liveRepository | Where-Object { -not $_.private }).Count
$privateCount = @($liveRepository | Where-Object { $_.private }).Count
Write-ModuleLog "Account listing: $($liveRepository.Count) repository/ies over $($listing.PageCount) page(s) - $publicCount public, $privateCount private."

if ($privateCount -gt 0) {
    Write-ModuleLog "$privateCount of those are private, and appear in NO unauthenticated view of this account. That is the difference this automation exists to close."
}

if ($listing.RateLimit -and $null -ne $listing.RateLimit.Remaining) {
    Write-ModuleLog "Rate limit: $($listing.RateLimit.Remaining) of $($listing.RateLimit.Limit) remaining on the $($listing.RateLimit.Resource) budget."
}

$snapshotByName = @{}
foreach ($repository in $liveRepository) {
    $snapshot = New-GitHubRepositorySnapshot -Repository $repository
    $snapshotByName[$snapshot.name] = $snapshot
}

# --- plan -----------------------------------------------------------------

$plan = New-Plan -Command $Command -Target $gitHubContext.Owner

# Truncation is the first operation in the plan, not a log line, because a plan that
# examined only the first N pages of an account cannot be read as complete. The exit
# code follows from it being blocked.
if ($listing.Truncated) {
    Add-PlanOperation -Plan $plan -Operation (New-PlanOperation -Resource 'accountListing' -Name $gitHubContext.Owner `
        -Action 'resolve' -Status 'blocked' `
        -Reason "The account has more pages of repositories than maximumPageCount ($($gitHubContext.MaximumPageCount)) allows following, so this inventory is incomplete and nothing below it can be read as a full picture. Raise defaults.maximumPageCount in the project context.") | Out-Null
}

$inScope = @($declaration.repositories)
if ($RepositoryName.Count -gt 0) {
    $inScope = @($inScope | Where-Object { $RepositoryName -contains $_.name })
}

foreach ($declared in $inScope) {
    $snapshot = if ($snapshotByName.ContainsKey($declared.name)) { $snapshotByName[$declared.name] } else { $null }
    $status = Get-GitHubRepositoryStatus -Declaration $declared -Snapshot $snapshot

    Add-PlanOperation -Plan $plan -Resource 'repository' -Name $declared.name -Status $status | Out-Null
}

# The undeclared half. On an account nobody has ever declared, every repository lands
# here - which is the finding of the first run, not a fault in it. Suppressed under
# -RepositoryName, because a filtered run asking about one repository should not
# answer with a verdict about twenty-three others.
if ($RepositoryName.Count -eq 0) {
    $declaredNameSet = @($declaration.repositories | ForEach-Object { $_.name })
    foreach ($name in @($snapshotByName.Keys | Sort-Object)) {
        if ($declaredNameSet -contains $name) { continue }
        $status = Get-GitHubUndeclaredStatus -Snapshot $snapshotByName[$name]
        Add-PlanOperation -Plan $plan -Resource 'repository' -Name $name -Status $status | Out-Null
    }
}

Write-PlanSummary -Plan $plan

# --- Evidence -------------------------------------------------------------

$reportPathResolved = Get-GitHubAsCodeReportPath -RepositoryRoot $repositoryRoot -Module $moduleName -Command $Command -ReportPath $ReportPath

$provenanceArgument = @{
    Command         = $Command
    DeclarationPath = $configurationPath
    DeclarationText = (Get-Content -LiteralPath $configurationPath -Raw)
    SchemaEngine    = $schemaEngine
    Scope           = if ($RepositoryName.Count -gt 0) { 'repositoryName=' + ($RepositoryName -join ',') } else { 'all' }
    RepositoryRoot  = $repositoryRoot
    ToolVersion     = "$($projectContext.version)"
    UsedTemplate    = $usedTemplate
}

# The account-level findings, computed once so the report and the checklist agree.
$withoutLicense = @($snapshotByName.Values | Where-Object { -not $_.license })
$withoutTopics = @($snapshotByName.Values | Where-Object { @($_.topics).Count -eq 0 })
$wikiEnabled = @($snapshotByName.Values | Where-Object { $_.has_wiki })
$projectsEnabled = @($snapshotByName.Values | Where-Object { $_.has_projects })

$detail = [ordered]@{
    provenance      = Get-GitHubAsCodeProvenance @provenanceArgument
    runLog          = $runLogPath
    apiBaseUrl      = $gitHubContext.BaseUrl
    owner           = $gitHubContext.Owner
    declarationPath = $configurationPath

    listing         = [ordered]@{
        endpoint  = 'GET /user/repos?affiliation=owner'
        rationale = 'Not GET /users/{owner}/repos, which returns public repositories only.'
        total        = $liveRepository.Count
        # publicCount / privateCount, not public / private. The absence guard in
        # tests/automations/Automations.Tests.ps1 forbids a hashtable key named
        # 'private' anywhere, because that is the shape of a PATCH /repos body field
        # that detaches the fork network. It cannot tell a report count from a
        # request field, and loosening it to allow one would weaken the real
        # protection - so the count gets the clearer name instead. These ARE counts.
        publicCount  = $publicCount
        privateCount = $privateCount
        pageCount    = $listing.PageCount
        truncated    = [bool] $listing.Truncated
    }

    # 'authentication', NOT 'token', and the name is the fix rather than a preference.
    #
    # Remove-SensitiveValue redacts by property NAME, and 'token' is one of the fragments
    # it matches - so this whole block used to be replaced by the string "[redacted]"
    # before the report was written. Not a block with redacted fields: the object was
    # gone. Verified in a real artefact, where detail.token was the four-character
    # string while listing, rateLimit and finding beside it survived intact.
    #
    # So the report promised the days remaining on the token - security-model.md says
    # "inventory reports the days remaining" - and then deleted the only place it was
    # recorded. The console warning is transient; the report is what gets attached to a
    # ticket.
    #
    # This module documents having made exactly this mistake once before: an unanchored
    # 'pat' rule redacted every Area Path out of every report, and the comment above
    # $script:SensitiveNameSegment concludes "Redaction that destroys evidence is not
    # failing safe; it is failing quietly, which is worse." It happened again, two
    # fragments further down the same list.
    #
    # 'authentication' is the one candidate that survives - measured, not assumed:
    # token, tokenShape, tokenInfo, credentialShape, auth and authorization are all
    # destroyed. The fragment list holds 'authorization' but not 'authentication', and
    # 'auth' matches only as a whole delimited segment. None of the four inner field
    # names match either, which is why they are worth keeping here rather than flattening
    # into a string.
    #
    # Nothing here is secret. isClassic is a fact about the token's TYPE, scope lists
    # names of permissions, and the two expiry fields are dates. The value never appears.
    authentication  = if ($tokenShape) {
        [ordered]@{
            isClassic       = [bool] $tokenShape.IsClassic
            scope           = @($tokenShape.Scope)
            expiresUtc      = if ($tokenShape.ExpiresUtc) { $tokenShape.ExpiresUtc.ToString('o') } else { '' }
            daysUntilExpiry = $tokenShape.DaysUntilExpiry
        }
    }
    else { $null }

    rateLimit       = if ($listing.RateLimit) {
        [ordered]@{
            limit     = $listing.RateLimit.Limit
            remaining = $listing.RateLimit.Remaining
            resetUtc  = if ($listing.RateLimit.ResetUtc) { $listing.RateLimit.ResetUtc.ToString('o') } else { '' }
            resource  = $listing.RateLimit.Resource
        }
    }
    else { $null }

    # The account-level summary. These four numbers are the reason to run this at
    # all: they are what a decision about the older repositories gets made from.
    finding         = [ordered]@{
        withoutLicense   = @($withoutLicense | ForEach-Object { $_.name } | Sort-Object)
        withoutTopics    = @($withoutTopics | ForEach-Object { $_.name } | Sort-Object)
        wikiEnabled      = $wikiEnabled.Count
        projectsEnabled  = $projectsEnabled.Count
        undeclaredCount  = @($snapshotByName.Keys | Where-Object { $declaredNames -notcontains $_ }).Count
    }

    # The snapshot the declaration is derived from. Everything needed to write
    # repositories.json is here, so nobody has to click through the web interface.
    repository      = @($snapshotByName.Keys | Sort-Object | ForEach-Object { $snapshotByName[$_] })
}

$written = Write-GitHubAsCodeReport -Plan $plan -Path $reportPathResolved -Module $moduleName -Detail ([pscustomobject] $detail)
Write-ModuleLog "Report: $($written.JsonPath)"
Write-ModuleLog "Summary: $($written.MarkdownPath)"

# --- smoke ----------------------------------------------------------------

if ($Command -eq 'smoke') {
    Write-ModuleLog 'Manual verification checklist:'
    Write-ModuleLog "  1. Compare the total against the account: gh repo list $($gitHubContext.Owner) --limit 200 | measure. A SMALLER number here than there means the listing was truncated or the token cannot see some repositories - both of which make this report incomplete rather than clean."
    Write-ModuleLog "  2. Confirm the private count is not zero if the account has private repositories. A zero there is the signature of reading the public endpoint by mistake."
    Write-ModuleLog '  3. Re-run plan. It must report the same operations as the first run; if it does not, the difference is not on GitHub.'
    Write-ModuleLog '  4. For every adopt/warning repository, decide: declare it, or leave it and accept that it stays unmanaged.'
    Write-ModuleLog '  5. Derive the declaration from the report, then run plan again. Zero pending is what finished means.'
}

# Exit code, because the result has a consumer that is not a person reading the
# screen.
#
#   0  the run completed and nothing is blocked
#   2  the run completed and at least one resource could not be determined - a
#      declared repository the API did not return, or a truncated account listing
#   1  the run itself failed (an uncaught throw: bad declaration, no credential,
#      API unreachable)
if (Test-PlanBlocked -Plan $plan) {
    Write-ModuleLog 'The plan contains blocked operation(s). Each one needs a person, not a retry.' -Level warning
    exit 2
}

exit 0
