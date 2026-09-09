<#
    GitHubAsCode.Plan - the plan model shared by every automation.

    A plan is a flat list of operations. Each one answers three questions about a
    single resource: what would be done to it (action), whether that is safe to do
    now (status), and why (reason). Nothing else. Keeping the model this small is
    what lets one reviewer read a plan for three different resource families
    without learning three vocabularies.

    The vocabulary is deliberately the same one a repository that DOES write would
    use, so that somebody reading a plan from either does not have to learn two
    languages. What differs is who executes: here, a person. Nothing in this module
    writes, and there is no apply for it to gate - see ADR 0001.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Status describes whether the operation may proceed.
#   ok        - the live state already matches; nothing to do.
#   pending   - a change is required and is safe to make.
#   warning   - the change will proceed but a human should read the reason.
#   protected - deliberately not changed, to avoid destroying something.
#   blocked   - nothing further about this resource could be determined.
$script:PlanStatus = @('ok', 'pending', 'warning', 'protected', 'blocked')

# Action describes what would happen to the resource.
$script:PlanAction = @(
    'create',    # the resource does not exist and will be created
    'exists',    # present and already correct
    'adopt',     # present, created outside this repository, brought under management as is
    'update',    # a property will be changed
    'set',       # a value will be written into an existing container
    'add',       # a member or child will be added
    'reconcile', # a collection will be rewritten to match the declaration
    'rename',    # the resource will be renamed
    'authorize', # a permission or access grant will be given
    'validate',  # a check with no possible write
    'resolve',   # a human must resolve an ambiguity before anything can proceed
    'manual',    # deliberately not automated; a human performs it
    'skip'       # intentionally out of scope for this run
)

function Get-PlanStatusName {
    <#
    .SYNOPSIS
        Returns the valid plan status values.

    .DESCRIPTION
        Exported so the test suite and the documentation can assert against the
        same list the module enforces, instead of restating it.

    .EXAMPLE
        Get-PlanStatusName
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    return @($script:PlanStatus)
}

function Get-PlanActionName {
    <#
    .SYNOPSIS
        Returns the valid plan action values.

    .EXAMPLE
        Get-PlanActionName
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()

    return @($script:PlanAction)
}

function New-Plan {
    <#
    .SYNOPSIS
        Creates an empty plan for a target.

    .PARAMETER Command
        Command that produced the plan, for example 'inventory' or 'plan'.

    .PARAMETER Target
        What the plan is about: an application key, an environment, or both.

    .PARAMETER GeneratedAt
        Timestamp recorded in the plan. Defaults to now in round-trip UTC format.
        Injectable so a test can assert on a fixed value.

    .EXAMPLE
        $plan = New-Plan -Command 'plan' -Target 'APP_ALPHA'

    .OUTPUTS
        PSCustomObject with command, target, generatedAt and operations.
    #>
    # Pure function: it computes a value and changes no system state. ShouldProcess
    # would offer a confirmation prompt for something there is nothing to confirm
    # about, and would train people to answer yes.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [string] $Command,
        [Parameter(Mandatory)] [string] $Target,
        [string] $GeneratedAt
    )

    if (-not $GeneratedAt) { $GeneratedAt = (Get-Date).ToUniversalTime().ToString('o') }

    # ArrayList rather than List[object] on purpose. In Windows PowerShell 5.1,
    # reading a generic List through a PSCustomObject property and wrapping it in
    # the array subexpression operator - @($plan.operations) - throws
    # "Argument types do not match". ArrayList round-trips correctly on both 5.1
    # and 7, and this object is passed across module boundaries constantly.
    return [pscustomobject]@{
        command     = $Command
        target      = $Target
        generatedAt = $GeneratedAt
        operations  = New-Object System.Collections.ArrayList
    }
}

function New-PlanOperation {
    <#
    .SYNOPSIS
        Creates one plan operation.

    .DESCRIPTION
        Action and status are validated against the closed vocabularies above. A
        free-text status is how a plan turns into prose that nothing can enforce -
        in particular, a caller cannot act on a status it does not recognise, and
        the exit code of a run is exactly such a caller.

    .PARAMETER Resource
        Resource family, for example 'Team', 'Board column', 'Variable Group'.

    .PARAMETER Name
        Name of the specific resource.

    .PARAMETER Action
        One of the values from Get-PlanActionName.

    .PARAMETER Status
        One of the values from Get-PlanStatusName.

    .PARAMETER Reason
        Why. Written for the person approving the plan, not for a log parser.

    .EXAMPLE
        New-PlanOperation -Resource 'Team' -Name 'APP_ALPHA_Team' -Action create -Status pending -Reason 'The Team does not exist yet.'

    .OUTPUTS
        PSCustomObject with resource, name, action, status and reason.
    #>
    # Pure function: it computes a value and changes no system state. ShouldProcess
    # would offer a confirmation prompt for something there is nothing to confirm
    # about, and would train people to answer yes.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [string] $Resource,
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Action,
        [Parameter(Mandatory)] [string] $Status,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Reason
    )

    if ($script:PlanAction -notcontains $Action) {
        throw "Unknown plan action '$Action'. Valid actions: $($script:PlanAction -join ', ')."
    }
    if ($script:PlanStatus -notcontains $Status) {
        throw "Unknown plan status '$Status'. Valid statuses: $($script:PlanStatus -join ', ')."
    }

    return [pscustomobject]@{
        resource = $Resource
        name     = $Name
        action   = $Action
        status   = $Status
        reason   = $Reason
    }
}

function Add-PlanOperation {
    <#
    .SYNOPSIS
        Appends an operation to a plan.

    .DESCRIPTION
        Accepts either an already-built operation or the individual fields, so a
        caller that already has an action/status/reason triple from a status
        function does not have to unpack it.

    .PARAMETER Plan
        Plan from New-Plan.

    .PARAMETER Operation
        Operation from New-PlanOperation.

    .PARAMETER Resource
        Resource family.

    .PARAMETER Name
        Resource name.

    .PARAMETER Status
        Status object carrying action, status and reason - typically the return of a
        Get-*Status function.

    .EXAMPLE
        Add-PlanOperation -Plan $plan -Resource 'Board column' -Name 'Issues' -Status $columnStatus

    .EXAMPLE
        Add-PlanOperation -Plan $plan -Operation (New-PlanOperation -Resource 'Team' -Name $team -Action exists -Status ok -Reason 'Already present.')
    #>
    [CmdletBinding(DefaultParameterSetName = 'FromStatus')]
    param(
        [Parameter(Mandatory)] [object] $Plan,

        [Parameter(ParameterSetName = 'FromOperation', Mandatory)]
        [object] $Operation,

        [Parameter(ParameterSetName = 'FromStatus', Mandatory)]
        [string] $Resource,

        [Parameter(ParameterSetName = 'FromStatus', Mandatory)]
        [string] $Name,

        [Parameter(ParameterSetName = 'FromStatus', Mandatory)]
        [object] $Status
    )

    if ($PSCmdlet.ParameterSetName -eq 'FromOperation') {
        $Plan.operations.Add($Operation) | Out-Null
        return
    }

    $Plan.operations.Add((New-PlanOperation `
        -Resource $Resource `
        -Name $Name `
        -Action "$($Status.action)" `
        -Status "$($Status.status)" `
        -Reason "$($Status.reason)")) | Out-Null
}

function Get-PlanSummary {
    <#
    .SYNOPSIS
        Counts the operations of a plan by status.

    .PARAMETER Plan
        Plan from New-Plan.

    .EXAMPLE
        (Get-PlanSummary -Plan $plan).pending

    .OUTPUTS
        PSCustomObject with total and one count per status.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [object] $Plan
    )

    $operations = @($Plan.operations)
    $summary = [ordered]@{ total = $operations.Count }
    foreach ($status in $script:PlanStatus) {
        $summary[$status] = @($operations | Where-Object { $_.status -eq $status }).Count
    }
    return [pscustomobject]$summary
}

function Test-PlanBlocked {
    <#
    .SYNOPSIS
        Returns true when any operation in the plan is blocked.

    .DESCRIPTION
        Says whether anything in the plan could not be determined. A single function
        rather than an inline check so that the question is one testable statement
        instead of a convention each module re-implements.

        Its answer is what the entry points turn into an exit code: a run whose plan
        holds a blocked operation exits 2, not 0. Before that it was called and its
        answer thrown away in a log line, so a scheduler saw success.

    .PARAMETER Plan
        Plan from New-Plan.

    .EXAMPLE
        if (Test-PlanBlocked -Plan $plan) { throw 'Resolve the blocked operations first.' }
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [object] $Plan
    )

    return @($Plan.operations | Where-Object { $_.status -eq 'blocked' }).Count -gt 0
}

function Write-PlanSummary {
    <#
    .SYNOPSIS
        Writes a readable plan summary to the information stream.

    .DESCRIPTION
        Prints the counts, then the operations that need attention - blocked first,
        then warnings, then pending. Operations that are already `ok` are counted but
        not listed: on an idempotent re-run they are almost the entire plan, and
        printing hundreds of "already correct" lines is what trains people to stop
        reading the output.

    .PARAMETER Plan
        Plan from New-Plan.

    .PARAMETER MaximumItem
        Maximum number of operations to list per status group.

    .EXAMPLE
        Write-PlanSummary -Plan $plan
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Plan,
        [ValidateRange(1, 1000)] [int] $MaximumItem = 15
    )

    $summary = Get-PlanSummary -Plan $Plan
    Write-Information "Plan for '$($Plan.target)' ($($Plan.command)): $($summary.total) operation(s) - ok $($summary.ok), pending $($summary.pending), warning $($summary.warning), protected $($summary.protected), blocked $($summary.blocked)." -InformationAction Continue

    foreach ($status in @('blocked', 'warning', 'pending', 'protected')) {
        $items = @($Plan.operations | Where-Object { $_.status -eq $status })
        if ($items.Count -eq 0) { continue }

        Write-Information "  [$status]" -InformationAction Continue
        foreach ($item in ($items | Select-Object -First $MaximumItem)) {
            Write-Information "    $($item.action.PadRight(10)) $($item.resource): $($item.name) - $($item.reason)" -InformationAction Continue
        }
        if ($items.Count -gt $MaximumItem) {
            # Never hide a truncation. A silent cap reads as full coverage.
            Write-Information "    ... $($items.Count - $MaximumItem) more $status operation(s) not listed; see the report file." -InformationAction Continue
        }
    }

    if ($summary.pending -eq 0 -and $summary.blocked -eq 0) {
        # Worded by command, because inventory does not read the declaration at all - it
        # reports what is live. The single sentence this used to print claimed the live
        # state "already matches the declaration", which after an inventory announces the
        # result of a comparison that never ran, and contradicts the per-operation reasons
        # in the same run ("Present on the account. Notable: no topics."). It is the same
        # failure as a silent truncation above: a closing line that overstates what the
        # command actually did.
        #
        # This branches on the ladder vocabulary, not on any resource, so the shared layer
        # stays free of domain rules: validate/inventory/plan/apply is the contract every
        # automation implements.
        if ($Plan.command -eq 'inventory') {
            Write-Information '  Nothing outstanding: inventory reports live state and compares nothing.' -InformationAction Continue
        }
        else {
            Write-Information '  Nothing to change: the live state already matches the declaration.' -InformationAction Continue
        }
    }
}

Export-ModuleMember -Function @(
    'Get-PlanStatusName',
    'Get-PlanActionName',
    'New-Plan',
    'New-PlanOperation',
    'Add-PlanOperation',
    'Get-PlanSummary',
    'Test-PlanBlocked',
    'Write-PlanSummary'
)
