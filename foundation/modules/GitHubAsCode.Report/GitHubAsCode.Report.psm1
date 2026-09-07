<#
    GitHubAsCode.Report - evidence writing.

    Two artefacts, each answering a different question.

    * A plan report answers "what was about to happen", and is the thing a
      reviewer approves. It is written before any change.
    * A Markdown summary answers "can a person read this without a JSON viewer",
      and is what gets attached to a change ticket.

    Everything written here passes through Remove-SensitiveValue first, and that
    walk masks every string it copies with Protect-SecretInText. Two layers,
    because they see different things: one matches the NAME of a property, which
    catches a weak password whose value looks like nothing, and the other matches
    the VALUE, which catches a token embedded in a URL under a name like scmUrl
    that no pattern would ever flag.

    Both live at the writer rather than at each call site. A report is the artefact
    most likely to be pasted into a ticket or a chat window, and a call site added
    next year would otherwise reintroduce the leak without anybody noticing.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Property names whose values are replaced before a report is written. Matching is
# on the name, not the value, so a credential is redacted even when it does not
# look like one.
#
# The pattern is built in two halves, because a single unanchored alternation is
# wrong in both directions at once.
#
# Long, unambiguous tokens match anywhere in the name. Case-insensitivity is what
# makes these cover camelCase too: 'sshkey' matches 'sshKey'.
$script:SensitiveNameFragment = @(
    'password', 'passwd', 'pwd', 'passphrase'
    'secret', 'credential', 'token', 'authorization'
    'apikey', 'api_key', 'accesskey', 'privatekey', 'private_key', 'sshkey', 'signingkey', 'keymaterial'
    'connectionstring', 'connstr', 'signature'
) -join '|'

# Short tokens that are also common substrings of innocent words. These match only
# as a whole word or a whole underscore/dash-delimited segment.
#
# 'pat' is the reason this split exists. Unanchored, it matched 'areaPaths',
# 'iterationPaths', 'reportPath', 'patch' and 'compatible' - so every
# team-provisioning inventory report silently replaced its Area Path and Iteration
# Path inventory, the very data the report exists to carry, with the redaction
# marker. Redaction that destroys evidence is not failing safe; it is failing
# quietly, which is worse.
#
# IT HAPPENED AGAIN, with 'token' in the list below rather than 'pat' above.
# repo-inventory built a block of evidence about the token's SHAPE - classic or
# fine-grained, which permissions, how many days until it expires - named it
# 'token', and this function replaced the entire object with the string
# "[redacted]" on the way to the report. Nothing in it was secret; the value never
# went near it. The report promised the expiry and then deleted the only durable
# record of it.
#
# The lesson the 'pat' case taught was "anchor short fragments". The lesson this
# one adds is that a NAME can be sensitive-looking while the thing under it is
# evidence, and the mechanism cannot tell the difference. The caller carries the
# burden: name the field for what it holds rather than for the subject it concerns,
# and assert in a test that it survives - see
# tests/foundation/GitHubAsCode.Secrets.Tests.ps1.
$script:SensitiveNameSegment = @(
    'pat', 'key', 'sas', 'cert', 'auth', 'bearer'
) -join '|'

$script:SensitivePropertyPattern = "(?i)($($script:SensitiveNameFragment)|(?:^|[_-])(?:$($script:SensitiveNameSegment))(?:[_-]|$))"

# Value shapes that carry a secret regardless of the property name holding them.
#
# Name-based redaction cannot see these, and that is not a hypothetical gap. A job
# whose SCM URL is https://user:TOKEN@host/org/repo.git stores that string under the
# name 'scmUrl', which looks innocent - while 'credentialsId', which is only a
# reference and no secret at all, IS redacted because its name contains
# 'credential'. The name layer was protecting the harmless field and passing the
# dangerous one.
#
# Free text is the other half of the same hole: a reason, a failure message or git's
# stderr ("Authentication failed for 'https://user:TOKEN@host'") is a string under a
# name no pattern would ever flag.
$script:SecretValuePattern = @(
    # URL userinfo. The host is kept: it is the diagnostically useful part, and a
    # reason that says which repository disagrees is the point of the message.
    @{ Pattern = '(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s@"'']+@'; Replacement = '$1[redacted]@' }

    # Bearer, which is the scheme THIS repository authenticates with - see
    # New-BearerAuthorizationHeader. Its absence was the gap that mattered: both rules
    # here arrived with the port from a project that uses Basic, so the layer whose
    # entire purpose is catching "a token that reached a message by a route nobody
    # enumerated" was blind to the only scheme in use, while carrying a rule for one
    # that is never sent.
    #
    # \S+ rather than a token-shaped pattern, on purpose. Whatever follows Bearer is a
    # credential regardless of how it looks, and a rule matching only known prefixes
    # would miss the next format GitHub introduces.
    @{ Pattern = '(?i)\bBearer\s+\S+'; Replacement = 'Bearer [redacted]' }

    # A bare GitHub token, under no header and no property name. Both other layers miss
    # this: redaction by property name cannot see a value in free text, and the userinfo
    # rule only fires inside a URL.
    #
    # Two shapes. The five classic gh*_ prefixes, and github_pat_ for fine-grained -
    # which is the type this repository recommends, and which carries underscores in its
    # body, so it needs its own character class rather than an alternation.
    @{ Pattern = '(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}'; Replacement = '[redacted-token]' }
    @{ Pattern = '(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,}'; Replacement = '[redacted-token]' }

    # A Basic credential. Kept even though nothing here sends one: a proxy or an SSO
    # gateway in front of the API can put one in a message, and the rule costs nothing.
    @{ Pattern = '(?i)\bBasic\s+[A-Za-z0-9+/]{8,}={0,2}'; Replacement = 'Basic [redacted]' }
)

function Protect-SecretInText {
    <#
    .SYNOPSIS
        Returns text with credential-shaped values masked.

    .DESCRIPTION
        Masks by VALUE, which is the complement of Remove-SensitiveValue masking by
        property name. Use it for any string that reaches a report, a Markdown
        summary or the console: a reason, an exception message, a URL read from a
        controller or from .git/config.

        The host of a URL is preserved deliberately. A message that says which
        repository was contacted is worth having; the userinfo in front of it never
        is.

        Pure function, so it is tested offline against strings.

    .PARAMETER Text
        Text to mask. Null or empty is returned unchanged.

    .EXAMPLE
        Protect-SecretInText -Text "failed for 'https://me:ghp_x@example.com/a.git'"

        failed for 'https://[redacted]@example.com/a.git'

    .OUTPUTS
        The masked string.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowNull()] [AllowEmptyString()] [string] $Text
    )

    if ([string]::IsNullOrEmpty($Text)) { return $Text }

    $masked = $Text
    foreach ($rule in $script:SecretValuePattern) {
        $masked = [regex]::Replace($masked, $rule.Pattern, $rule.Replacement)
    }
    return $masked
}

function Remove-SensitiveValue {
    <#
    .SYNOPSIS
        Returns a copy of an object with sensitive property values replaced.

    .DESCRIPTION
        Walks objects, dictionaries and arrays. A property whose NAME looks like a
        credential is replaced with a fixed marker; everything else is copied. Name
        matching rather than value matching is deliberate: a weak password does not
        look like a secret, but its property name always does.

        Depth is capped so a cyclic or pathologically nested structure cannot hang
        the writer.

    .PARAMETER InputObject
        Object to sanitize.

    .PARAMETER Replacement
        Marker written in place of a sensitive value.

    .PARAMETER Depth
        Remaining recursion depth.

    .EXAMPLE
        Remove-SensitiveValue -InputObject $operation

    .OUTPUTS
        A sanitized copy. The input is not modified.
    #>
    # Pure function: it computes a value and changes no system state. ShouldProcess
    # would offer a confirmation prompt for something there is nothing to confirm
    # about, and would train people to answer yes.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowNull()] [object] $InputObject,
        [string] $Replacement = '[redacted]',
        [ValidateRange(0, 32)] [int] $Depth = 12
    )

    if ($null -eq $InputObject) { return $null }
    if ($Depth -le 0) { return '[depth limit reached]' }

    # Every string in the evidence passes the value masker. Doing it here rather than
    # at each call site is the same reasoning that put name-based redaction at the
    # writer: a call site added later would otherwise reintroduce the leak silently.
    if ($InputObject -is [string]) {
        return (Protect-SecretInText -Text $InputObject)
    }

    if ($InputObject -is [bool] -or $InputObject -is [int] -or
        $InputObject -is [long] -or $InputObject -is [double] -or $InputObject -is [decimal] -or
        $InputObject -is [datetime]) {
        return $InputObject
    }

    if ($InputObject -is [System.Collections.IDictionary]) {
        $copy = [ordered]@{}
        foreach ($key in $InputObject.Keys) {
            if ("$key" -match $script:SensitivePropertyPattern) {
                $copy["$key"] = $Replacement
                continue
            }
            $copy["$key"] = Remove-SensitiveValue -InputObject $InputObject[$key] -Replacement $Replacement -Depth ($Depth - 1)
        }
        return [pscustomobject]$copy
    }

    if ($InputObject -is [System.Collections.IEnumerable]) {
        $items = @($InputObject | ForEach-Object {
            Remove-SensitiveValue -InputObject $_ -Replacement $Replacement -Depth ($Depth - 1)
        })
        # The leading comma is load-bearing. PowerShell enumerates a function's
        # output, so `return @($items)` hands a single-element array back to the
        # caller as the bare element - and a one-item list in an inventory then
        # serialised as a string instead of an array, silently changing the shape of
        # the evidence file. The comma wraps the array so enumeration yields it
        # whole.
        return , $items
    }

    $properties = @($InputObject.PSObject.Properties)
    if ($properties.Count -eq 0) { return $InputObject }

    $copy = [ordered]@{}
    foreach ($property in $properties) {
        if ($property.Name -match $script:SensitivePropertyPattern) {
            $copy[$property.Name] = $Replacement
            continue
        }
        $copy[$property.Name] = Remove-SensitiveValue -InputObject $property.Value -Replacement $Replacement -Depth ($Depth - 1)
    }
    return [pscustomobject]$copy
}

function Write-Utf8NoBom {
    <#
    .SYNOPSIS
        Writes text as UTF-8 with no byte order mark.

    .DESCRIPTION
        Set-Content -Encoding UTF8 writes a BOM on Windows PowerShell 5.1, and a JSON
        document that starts with a BOM is rejected by strict parsers - Python answers
        "Unexpected UTF-8 BOM", and several CI tools do the same. A report nobody can
        parse is not evidence.

        PowerShell 7 has -Encoding utf8NoBOM, but 5.1 is the support floor, so the
        write goes through .NET where both engines behave identically.

    .PARAMETER Path
        File to write.

    .PARAMETER Content
        Text to write.

    .EXAMPLE
        Write-Utf8NoBom -Path $path -Content $json
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Content
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)

    # A relative path is resolved against PowerShell's current location, because .NET
    # would resolve it against the process working directory, which is not the same
    # thing. An absolute path is used as it is: joining a rooted path onto the current
    # location puts a drive letter in the middle of it, and the write then throws
    # "The given path's format is not supported". That is how the first version of
    # this silently produced no report at all.
    $fullPath = if ([System.IO.Path]::IsPathRooted($Path)) {
        $Path
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path (Get-Location).ProviderPath $Path))
    }

    [System.IO.File]::WriteAllText($fullPath, $Content, $encoding)
}

function Get-GitHubAsCodeReportPath {
    <#
    .SYNOPSIS
        Decides where a run writes its report.

    .DESCRIPTION
        An explicit path wins; otherwise a generated one under artifacts/reports. The
        generated name is UTC and unique, not merely precise: local time in the name
        while the content records UTC meant a directory's lexicographic order was not
        its chronological one, and in the autumn clock change two runs an hour apart
        landed on the same name. One second of granularity collided on its own anyway
        - two runs of the same command in the same second overwrote each other, and
        the writer truncates without a word.

        Written out three times in the three entry points before this.

    .PARAMETER RepositoryRoot
        Root that the generated path is relative to.

    .PARAMETER Module
        Automation name, used in the file name.

    .PARAMETER Command
        Verb, used in the file name.

    .PARAMETER ReportPath
        Explicit override. When given, nothing is generated.

    .EXAMPLE
        Get-GitHubAsCodeReportPath -RepositoryRoot $root -Module 'job-inventory' -Command 'plan'

    .OUTPUTS
        The path to write to.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [Parameter(Mandatory)] [string] $Module,
        [Parameter(Mandatory)] [string] $Command,
        [AllowEmptyString()] [string] $ReportPath
    )

    if ($ReportPath) {
        return (Resolve-GitHubAsCodePath -Path $ReportPath -RootPath $RepositoryRoot)
    }
    $stamp = [datetime]::UtcNow.ToString('yyyyMMdd-HHmmss', [Globalization.CultureInfo]::InvariantCulture)
    $unique = [guid]::NewGuid().ToString('N').Substring(0, 6)
    return (Join-Path $RepositoryRoot ("artifacts/reports/{0}-{1}-{2}Z-{3}.json" -f $Module, $Command, $stamp, $unique))
}
function Start-GitHubAsCodeRunLog {
    <#
    .SYNOPSIS
        Opens the transcript for one run and returns its path.

    .DESCRIPTION
        Progress used to exist only on the console, so closing the window ended the
        only account of what a run did. The report holds the conclusions - which jobs
        were compared, what each verdict was - and nothing about how they were
        reached. What was lost between the two: which folders were walked and how
        many items each returned, the message behind every unreadable job, the notice
        that a walk was truncated at maximumDepth, and the one that matters most,
        that the run used the versioned TEMPLATE rather than the active declaration -
        which means the report describes an example rather than an estate.

        .gitignore has promised that artifacts/ holds "logs" since the first commit.
        This is the first thing to put one there.

        Called once per run, near the start. Returns the path so the log funnel can
        append to it; failing to open it is not fatal, because a run that reports
        correctly without a transcript is better than no run at all.

    .PARAMETER RepositoryRoot
        Root under which artifacts/logs lives.

    .PARAMETER Module
        Automation name, used in the file name.

    .PARAMETER Command
        Verb, used in the file name.

    .EXAMPLE
        $log = Start-GitHubAsCodeRunLog -RepositoryRoot $root -Module 'job-inventory' -Command 'plan'

    .OUTPUTS
        The transcript path, or an empty string when one could not be opened.
    #>
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [Parameter(Mandatory)] [string] $Module,
        [Parameter(Mandatory)] [string] $Command
    )

    try {
        $directory = Join-Path $RepositoryRoot 'artifacts/logs'
        if (-not (Test-Path -LiteralPath $directory)) {
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
        }
        # UTC and unique, for the same reasons the report file name is.
        $stamp = [datetime]::UtcNow.ToString('yyyyMMdd-HHmmss', [Globalization.CultureInfo]::InvariantCulture)
        $unique = [guid]::NewGuid().ToString('N').Substring(0, 6)
        $path = Join-Path $directory ("{0}-{1}-{2}Z-{3}.log" -f $Module, $Command, $stamp, $unique)
        Add-GitHubAsCodeRunLogLine -Path $path -Level 'info' -Message "run started: $Module $Command"
        return $path
    }
    catch {
        Write-Warning "Could not open a run transcript, so this run will not leave one: $($_.Exception.Message)"
        return ''
    }
}

function Add-GitHubAsCodeRunLogLine {
    <#
    .SYNOPSIS
        Appends one line to a run transcript.

    .DESCRIPTION
        Every line carries a UTC timestamp and a level, so the transcript can be read
        long after the console it was echoed to is gone, and so the failures can be
        picked out of a long run without reading all of it.

        Masks the line the same way the console funnel does. A transcript is written
        to disk and lives longer than a scrollback buffer, so it is the last place a
        credential should be allowed to settle.

        Never throws. A transcript that cannot be written must not take the run down
        with it - the report is the artefact that matters.

    .PARAMETER Path
        Transcript path. An empty path is ignored, which is how a run with no
        transcript keeps working.

    .PARAMETER Message
        Line to write.

    .PARAMETER Level
        info or warning, matching the console funnel.

    .EXAMPLE
        Add-GitHubAsCodeRunLogLine -Path $log -Level 'warning' -Message 'could not read job/x'
    #>
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Path,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Message,
        [ValidateSet('info', 'warning')] [string] $Level = 'info'
    )

    if ([string]::IsNullOrEmpty($Path)) { return }
    try {
        $stamp = [datetime]::UtcNow.ToString('o', [Globalization.CultureInfo]::InvariantCulture)
        $line = '{0} {1,-7} {2}' -f $stamp, $Level, (Protect-SecretInText -Text $Message)
        [System.IO.File]::AppendAllText($Path, $line + [Environment]::NewLine)
    }
    catch {
        # Verbose, not a warning. A run whose disk filled up would otherwise emit one
        # warning per line and bury its own output in noise about the transcript -
        # but swallowing the reason entirely leaves nothing to diagnose it with.
        Write-Verbose "Could not append to the run transcript '$Path': $($_.Exception.Message)"
    }
}
function Get-GitHubAsCodeProvenance {
    <#
    .SYNOPSIS
        Returns the facts a report needs for somebody to reproduce the run.

    .DESCRIPTION
        A report used to record its conclusions and almost nothing about where they
        came from. Four things were missing and each one made a specific question
        unanswerable.

        Who and where: a report attached to a ticket did not say who generated it or
        from which machine.

        Which code: no tool version and no repository commit, so "this report says
        the branch specifier was X" could not be tied to the logic that read it.

        Which declaration: the path was recorded, but the active declaration is
        excluded from version control - so the path identifies a file that may have
        changed since. A fingerprint of the content does identify it.

        Which scope: the key filters restrict what is looked at and were not
        recorded, so a report of two jobs and a report of forty differed only in a
        total. That one is the dangerous one: "pending 0" reads as "everything is
        aligned" when it can equally mean "only one job was examined". It is the same
        mistake this repository refuses elsewhere - asserting something about what
        was never compared.

        The correlation id exists so an inventory and the plan from the same session
        can be tied together by something better than adjacent timestamps.

        Every value is read from the environment or passed in; nothing here contacts
        anything.

    .PARAMETER Command
        Verb that produced the report.

    .PARAMETER DeclarationPath
        Path of the declaration that was read.

    .PARAMETER DeclarationText
        Content of that declaration, fingerprinted rather than stored. The
        declaration holds no secrets by design - it names environment variables -
        but a fingerprint is what identifies it, and storing the whole file would
        make the report bulky for no gain.

    .PARAMETER SchemaEngine
        Which validator ran: the full one or the reduced 5.1 one. Recorded because
        the reduced engine ignores several keywords, so a report from it carries
        less assurance about its own declaration than one from the full engine - and
        until now the only place that difference appeared was a message on screen.

    .PARAMETER Scope
        What the run was restricted to, or 'all' when it was not.

    .PARAMETER RepositoryRoot
        Root used to resolve the repository commit.

    .PARAMETER UsedTemplate
        True when the run read the versioned TEMPLATE rather than the active
        declaration. It belongs in the report because it changes what the report is
        ABOUT: a plan built from the template describes an example, not an estate,
        and the only trace of that used to be one line on a console nobody keeps.

    .PARAMETER ToolVersion
        Version this product states for itself, from the project context. Recorded
        because a report is read long after the run, and "the branch specifier was
        X" is only checkable against the code that read it.

    .EXAMPLE
        Get-GitHubAsCodeProvenance -Command plan -DeclarationPath $path -DeclarationText $text -SchemaEngine reduced -Scope all -RepositoryRoot $root

    .OUTPUTS
        An ordered dictionary, ready to merge into a report detail.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Command,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $DeclarationPath,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $DeclarationText,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $SchemaEngine,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Scope,
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $ToolVersion,
        [switch] $UsedTemplate
    )

    $declarationFingerprint = ''
    if ($DeclarationText) {
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [Text.Encoding]::UTF8.GetBytes(($DeclarationText -replace "`r`n", "`n").TrimEnd("`n"))
            $declarationFingerprint = 'sha256:' + ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant()
        }
        finally { $sha.Dispose() }
    }

    # Best effort, and silent when it fails. A checkout with no .git - a tarball, a
    # vendored copy - is a legitimate way to run this, and a report is more useful
    # with an empty commit field than not written at all.
    $commit = ''
    try {
        $head = Join-Path $RepositoryRoot '.git/HEAD'
        if (Test-Path -LiteralPath $head) {
            $headText = (Get-Content -LiteralPath $head -Raw).Trim()
            if ($headText -like 'ref: *') {
                $refPath = Join-Path $RepositoryRoot ('.git/' + $headText.Substring(5).Trim())
                if (Test-Path -LiteralPath $refPath) { $commit = (Get-Content -LiteralPath $refPath -Raw).Trim() }
            }
            else { $commit = $headText }
        }
    }
    catch { $commit = '' }

    return [ordered]@{
        command                = $Command
        toolVersion            = $ToolVersion
        correlationId          = [guid]::NewGuid().ToString()
        runBy                  = "$($env:USERNAME)"
        runOn                  = "$($env:COMPUTERNAME)"
        repositoryCommit       = $commit
        declarationPath        = $DeclarationPath
        declarationFingerprint = $declarationFingerprint
        declarationIsTemplate  = [bool] $UsedTemplate
        schemaEngine           = $SchemaEngine
        scope                  = if ($Scope) { $Scope } else { 'all' }
    }
}
function Write-GitHubAsCodeReport {
    <#
    .SYNOPSIS
        Writes a plan or result report as JSON, plus a Markdown sibling.

    .DESCRIPTION
        The JSON file is the machine-readable record; the Markdown file next to it
        is what a person reads or attaches to a ticket. Both are written from the
        same sanitized object, so they cannot disagree.

        The parent directory is created if needed. Reports belong under `artifacts/`,
        which is excluded from version control: they describe one run of one
        environment and are not part of the declared state.

    .PARAMETER Plan
        Plan object from New-Plan.

    .PARAMETER Path
        Destination path for the JSON report. The Markdown file replaces the
        extension with .md.

    .PARAMETER Module
        Name of the automation that produced the report.

    .PARAMETER Detail
        Optional extra object to embed, for example the inventory counts observed.

    .EXAMPLE
        Write-GitHubAsCodeReport -Plan $plan -Path 'artifacts/plans/team-provisioning-APP_ALPHA.json' -Module 'team-provisioning'

    .OUTPUTS
        PSCustomObject with JsonPath and MarkdownPath.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [object] $Plan,
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Module,
        [object] $Detail
    )

    $directory = Split-Path -Parent $Path
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }

    $summary = Get-PlanSummary -Plan $Plan
    $report = [ordered]@{
        module      = $Module
        command     = $Plan.command
        target      = $Plan.target
        generatedAt = $Plan.generatedAt
        summary     = $summary
        operations  = @($Plan.operations)
    }
    if ($PSBoundParameters.ContainsKey('Detail') -and $null -ne $Detail) {
        $report.detail = $Detail
    }

    $sanitized = Remove-SensitiveValue -InputObject ([pscustomobject]$report)
    Write-Utf8NoBom -Path $Path -Content ($sanitized | ConvertTo-Json -Depth 12)

    $markdownPath = [System.IO.Path]::ChangeExtension($Path, '.md')
    Write-Utf8NoBom -Path $markdownPath -Content (Format-GitHubAsCodeReportMarkdown -Report $sanitized)

    Write-Verbose "Wrote report '$Path' and summary '$markdownPath'."
    return [pscustomobject]@{ JsonPath = $Path; MarkdownPath = $markdownPath }
}

function Format-MarkdownCell {
    <#
    .SYNOPSIS
        Returns text safe to place in one cell of a Markdown table.

    .DESCRIPTION
        A plan carries text this tool did not write: a job path, a script path and a
        reason all come from the controller being inspected or from git. Three things
        go wrong when that text is pasted into a table unescaped.

        A pipe ends the cell, so the row grows a column and the table stops lining
        up. A newline ends the row, so one value silently becomes two rows. And a
        bracket pair followed by a parenthesis is a link, so a value can inject one
        into whatever renders the summary on a ticket.

        None of that is a route to a secret - it is a route to a report that is wrong
        or that carries something nobody wrote. Pure function, so it is tested through
        the renderer without touching the file system.

    .PARAMETER Text
        Cell text. Null becomes an empty cell rather than the word null.

    .EXAMPLE
        Format-MarkdownCell -Text 'a | b'

        a \| b

    .OUTPUTS
        The escaped single-line string.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowNull()] [AllowEmptyString()] [object] $Text
    )

    if ($null -eq $Text) { return '' }
    $cell = [string] $Text

    # Ordinal string replacement, not -replace. That operator takes a regex on the
    # left and a substitution string on the right, and both give a backslash its own
    # meaning - so escaping a backslash there is a pattern that reads as an escape
    # and a replacement that reads as an escape. The first version of this function
    # shipped a bare backslash as a pattern and threw on every row it rendered.
    # Replace() has no such semantics: it takes the characters it is given.
    #
    # Backslash goes first, because doing it later would escape the backslashes the
    # other steps just added.
    $cell = $cell.Replace('\', '\\')
    $cell = $cell.Replace('|', '\|')
    $cell = $cell.Replace('[', '\[').Replace(']', '\]')
    $cell = $cell.Replace('<', '&lt;').Replace('>', '&gt;')

    # A cell is one line. CRLF first, so a Windows newline does not leave a stray
    # carriage return behind for the next step to turn into a second space.
    $cell = $cell.Replace("`r`n", ' ').Replace("`n", ' ').Replace("`r", ' ')
    return $cell
}

function Format-GitHubAsCodeReportMarkdown {
    <#
    .SYNOPSIS
        Renders a report object as Markdown.

    .DESCRIPTION
        Pure function, so the rendering is covered by a test without touching the
        file system. Operations are grouped by status, with the ones needing
        attention first, because that is the order a reviewer reads in.

    .PARAMETER Report
        Sanitized report object.

    .EXAMPLE
        Format-GitHubAsCodeReportMarkdown -Report $report

    .OUTPUTS
        The Markdown document as a single string.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [object] $Report
    )

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# $($Report.module): $($Report.command) - $($Report.target)")
    $lines.Add('')
    $lines.Add("Generated at $($Report.generatedAt) (UTC).")
    $lines.Add('')
    $lines.Add('## Summary')
    $lines.Add('')
    $lines.Add('| Status | Count |')
    $lines.Add('| --- | --- |')
    foreach ($property in $Report.summary.PSObject.Properties) {
        $lines.Add("| $($property.Name) | $($property.Value) |")
    }
    $lines.Add('')
    $lines.Add('## Operations')
    $lines.Add('')

    $operations = @($Report.operations)
    if ($operations.Count -eq 0) {
        $lines.Add('No operations were produced.')
    }
    else {
        foreach ($status in @('blocked', 'warning', 'pending', 'protected', 'ok')) {
            $items = @($operations | Where-Object { $_.status -eq $status })
            if ($items.Count -eq 0) { continue }

            $lines.Add("### $status ($($items.Count))")
            $lines.Add('')
            $lines.Add('| Resource | Name | Action | Reason |')
            $lines.Add('| --- | --- | --- | --- |')
            foreach ($item in $items) {
                # All four cells escaped, not just the reason. Three of them carry text
                # the inspected controller chooses - a job path, a script path - so a
                # pipe or a newline in any of them breaks the table, and a bracket pair
                # injects a link into whatever renders this Markdown on a ticket.
                $cellResource = Format-MarkdownCell -Text $item.resource
                $cellName     = Format-MarkdownCell -Text $item.name
                $cellAction   = Format-MarkdownCell -Text $item.action
                $cellReason   = Format-MarkdownCell -Text $item.reason
                $lines.Add("| $cellResource | $cellName | $cellAction | $cellReason |")
            }
            $lines.Add('')
        }
    }

    return ($lines -join [Environment]::NewLine)
}

Export-ModuleMember -Function @(
    'Protect-SecretInText',
    'Remove-SensitiveValue',
    'Get-GitHubAsCodeReportPath',
    'Start-GitHubAsCodeRunLog',
    'Add-GitHubAsCodeRunLogLine',
    'Get-GitHubAsCodeProvenance',
    'Write-GitHubAsCodeReport',
    'Format-GitHubAsCodeReportMarkdown'
)
