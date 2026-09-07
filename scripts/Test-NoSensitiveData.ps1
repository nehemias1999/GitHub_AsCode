<#
.SYNOPSIS
    Fails when the working tree contains data that must not be published.

.DESCRIPTION
    A repository that automates a live platform accumulates sensitive material by
    accident: a token pasted into a comment, a host name copied from an inventory,
    an absolute path from someone's workstation. Reviews miss those; a mechanical
    gate does not.

    The gate has two layers.

    1. Structural rules (built in, always on). They match the *shape* of sensitive
       data - credential formats, private address ranges, internal DNS suffixes,
       workstation paths - so they keep working without anyone maintaining a list.

    2. An optional deny list of literal terms, read from a file that is excluded
       from version control. Organization names, host names and project code names
       are themselves sensitive, so they must not be committed inside the very
       script that looks for them. Keep them in .local/sensitive-terms.txt.

    Every rule may carry an allow list of expressions. A match that satisfies one
    of them is treated as an intentional placeholder rather than a finding.

.PARAMETER Path
    Root to scan. Defaults to the repository root.

.PARAMETER TermsFile
    File holding one literal deny term per line; blank lines and lines starting
    with '#' are ignored. A missing file means the layer is skipped, and that is
    reported so a silent pass is never mistaken for a clean scan.

.PARAMETER RequireTermsFile
    Treat an absent or empty deny list as a failure (exit 2) instead of running the
    structural rules alone. Use it where the deny-list layer is supposed to be in
    force. It is NOT the default on purpose: a check that cannot pass on a fresh
    clone is a check people learn to ignore, and this repository has already been
    bitten by exactly that.

.PARAMETER PassThru
    Emit the finding objects instead of only the summary, for use in a pipeline.

.EXAMPLE
    .\scripts\Test-NoSensitiveData.ps1

    Scans the repository and exits non-zero when anything is found.

.EXAMPLE
    .\scripts\Test-NoSensitiveData.ps1 -TermsFile .local\sensitive-terms.txt -PassThru

    Adds the local deny list and returns the findings as objects.

.OUTPUTS
    System.Management.Automation.PSCustomObject when -PassThru is supplied.
#>
[CmdletBinding()]
param(
    [string] $Path,
    [string] $TermsFile = '.local/sensitive-terms.txt',
    [switch] $RequireTermsFile,
    [switch] $PassThru
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Path) { $Path = $repoRoot }
$Path = (Resolve-Path -LiteralPath $Path).Path

# Directories that either are not ours to police or hold intentionally local data.
$excludedDirectories = @('.git', '.local', 'artifacts', 'node_modules')

# Extensions NOT worth reading, because they are binary. Everything else is read.
#
# This list used to be the other way round - an allowlist of text extensions - and that
# is a hole rather than an economy. A gate that reads only what it recognises does not
# scan .pem, .key, .p12, id_rsa, .netrc, .npmrc, .sh, .tfvars or .conf, so the
# PrivateKeyBlock rule below could never fire against the files most likely to contain
# a private key block. It reported "no findings" and meant "no findings in the files I
# chose to open".
#
# That matters more for a template than for a private tool: the files somebody adds
# while adapting this - a deploy key, a service account, a shell script with a token -
# are exactly the ones the allowlist omitted.
#
# Inverting it costs nothing, because the NUL-byte check further down already
# identifies a binary whatever its extension, and it always did. That check was written
# as a backstop for "a binary file that slips through"; with the allowlist gone it
# becomes the actual mechanism, which is where the reliable test belongs.
$binaryExtensions = @(
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.svgz',
    '.zip', '.gz', '.tgz', '.7z', '.rar', '.nupkg',
    '.exe', '.dll', '.pdb', '.so', '.dylib', '.msi', '.cab',
    '.pdf', '.docx', '.xlsx', '.pptx',
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    '.mp3', '.mp4', '.avi', '.mov', '.wav'
)

# Placeholder identities this repository uses on purpose. Keep the list short:
# every entry is a hole in the gate.
$allowedDomains = 'contoso\.com|contoso\.local|example\.com|example\.org|example\.net|users\.noreply\.github\.com|json-schema\.org|learn\.microsoft\.com|dev\.azure\.com|keepachangelog\.com|semver\.org'

$rules = @(
    [pscustomobject]@{
        Name        = 'AzureDevOpsPat'
        Description = 'String shaped like an Azure DevOps Personal Access Token.'
        Pattern     = '(?<![A-Za-z0-9])[a-z2-7]{52}(?![A-Za-z0-9])'
        Allow       = @()
    }
    [pscustomobject]@{
        Name        = 'LongOpaqueToken'
        Description = 'Long alphanumeric run with no word breaks: typical of a token or key.'
        Pattern     = '(?<![A-Za-z0-9+/=])[A-Za-z0-9]{72,}(?![A-Za-z0-9+/=])'
        Allow       = @('^[0-9a-f]+$')   # a long hex run is normally a checksum or fixture id
    }
    [pscustomobject]@{
        Name        = 'AtlassianApiToken'
        Description = 'Atlassian API token.'
        Pattern     = 'ATATT[A-Za-z0-9_\-]{20,}'
        Allow       = @()
    }
    [pscustomobject]@{
        Name        = 'GitHubToken'
        Description = 'GitHub classic personal access, OAuth, user-to-server, server-to-server or refresh token.'
        Pattern     = 'gh[pousr]_[A-Za-z0-9]{30,}'
        Allow       = @()
    }
    [pscustomobject]@{
        # As inherited from a sibling project, the rule above covered only the five gh*_
        # prefixes - the classic shapes. It does not match github_pat_, which is the
        # FINE-GRAINED prefix, and fine-grained is the token type this repository
        # recommends and documents (docs/reference/security-model.md). So the one
        # credential a reader of this repository is most likely to be holding was the
        # one the gate could not see.
        #
        # Separate from the rule above rather than folded into one alternation,
        # because the two have different shapes: the fine-grained value carries
        # underscores in its body, which [A-Za-z0-9]{30,} would stop at.
        Name        = 'GitHubFineGrainedToken'
        Description = 'GitHub fine-grained personal access token.'
        Pattern     = 'github_pat_[A-Za-z0-9_]{20,}'
        Allow       = @()
    }
    [pscustomobject]@{
        Name        = 'CloudAccessKey'
        Description = 'Cloud provider access key identifier.'
        Pattern     = '(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])'
        Allow       = @()
    }
    [pscustomobject]@{
        Name        = 'PrivateKeyBlock'
        Description = 'PEM or OpenSSH private key material.'
        Pattern     = '-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'
        Allow       = @()
    }
    [pscustomobject]@{
        Name        = 'SshPublicKey'
        Description = 'SSH public key body.'
        Pattern     = 'ssh-(?:rsa|dss|ed25519) AAAA[0-9A-Za-z+/]+'
        Allow       = @()
    }
    [pscustomobject]@{
        Name        = 'JsonWebToken'
        Description = 'Serialized JSON Web Token.'
        Pattern     = 'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}'
        Allow       = @()
    }
    [pscustomobject]@{
        Name        = 'AssignedSecret'
        Description = 'Credential-shaped key assigned a literal value.'
        Pattern     = '(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\b["'']?\s*[:=]\s*["'']?[^\s"''<>{}$,;#)]{6,}'
        Allow       = @(
            '(?i)[:=]\s*["'']?(?:PENDING_OWNER_CONFIGURATION|<[^>]+>|null|true|false)'
            '(?i)[:=]\s*["'']?[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*["'']?\s*$'   # points at an env var name, not a value
            '(?i)[:=]\s*["'']?\$'                                        # a PowerShell or macro expression
            '(?i)[:=]\s*(?:Get|New|Read|Resolve|Invoke|Test|Join|Split|Convert)[A-Za-z]*-'  # a command call, not a value
        )
    }
    [pscustomobject]@{
        Name        = 'PrivateIpAddress'
        Description = 'Address in a private or link-local range: identifies real infrastructure.'
        Pattern     = '(?<![\d.])(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|169\.254)\.\d{1,3}\.\d{1,3}(?![\d.])'
        Allow       = @()
    }
    [pscustomobject]@{
        Name        = 'InternalDnsSuffix'
        Description = 'Host name in an internal DNS zone.'
        Pattern     = '(?i)\b[a-z0-9][a-z0-9.-]{2,}\.(?:local|internal|intranet|corp|lan|loc|home|priv)\b'
        Allow       = @('(?i)(?:^|\.)contoso\.local$')
    }
    [pscustomobject]@{
        Name        = 'WorkstationPath'
        Description = 'Absolute path containing a real user profile name.'
        Pattern     = '(?i)[A-Za-z]:\\Users\\[A-Za-z0-9._-]+'
        Allow       = @('(?i)[A-Za-z]:\\Users\\(?:<[^>]+>|USERNAME|%USERNAME%)')
    }
    [pscustomobject]@{
        Name        = 'UncSharePath'
        Description = 'UNC path pointing at a named file server.'
        Pattern     = '\\\\[A-Za-z0-9][A-Za-z0-9._-]{2,}\\[A-Za-z0-9$._-]+'
        Allow       = @()
    }
    [pscustomobject]@{
        Name        = 'EmailAddress'
        Description = 'Email address outside the approved placeholder domains.'
        Pattern     = '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
        Allow       = @("(?i)@(?:$allowedDomains)$")
    }
)

function Test-AllowedMatch {
    <#
    .SYNOPSIS
        Returns true when a matched string is an approved placeholder.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()]     [string]   $Value,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [string[]] $Allow
    )

    foreach ($expression in $Allow) {
        if ($Value -match $expression) { return $true }
    }
    return $false
}

function Get-GitIgnoredPath {
    <#
    .SYNOPSIS
        Returns the subset of paths that git ignores.

    .DESCRIPTION
        This gate exists to keep sensitive data out of a commit, so a file git ignores
        is out of scope by definition - and scanning it is worse than useless. A real
        installation has a filled-in .env, which is ignored, holds the credentials on
        purpose, and would make the gate fail on every single run. A gate that can never
        pass is a gate people learn to ignore, which is the exact failure this whole
        check exists to prevent.

        It asks git rather than keeping a second copy of the ignore rules, because a
        second copy drifts from .gitignore and nobody notices until it matters.

        One call: git status --porcelain --ignored, whose "!!" lines are the ignored
        entries. Not git check-ignore --stdin, which looks like the right tool and is
        not - given paths on stdin it matched nothing and exited 1, the same answer it
        gives for "not ignored", while the identical paths as arguments matched
        correctly. Measured against git 2.46.0.windows.1. A silent wrong answer from a
        security check is worse than no check, so the route that reports out loud wins.

        git reports an ignored directory as a single entry with a trailing slash, so a
        directory match is a prefix match.

        When git is unavailable, or the directory is not a working copy, or the command
        fails, nothing is reported as ignored and the scan covers everything - failing
        loud rather than silently skipping.

    .PARAMETER Root
        Working copy root.

    .PARAMETER Path
        Absolute paths to classify.

    .OUTPUTS
        The paths that git ignores.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [string[]] $Path
    )

    if ($Path.Count -eq 0) { return @() }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return @() }
    if (-not (Test-Path -LiteralPath (Join-Path $Root '.git'))) { return @() }

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'git'
    $startInfo.Arguments = 'status --porcelain --ignored'
    $startInfo.WorkingDirectory = $Root
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $standardOutput = ''
    try {
        $null = $process.Start()
        $reader = $process.StandardOutput.ReadToEndAsync()
        $null = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(60000)) {
            try { $process.Kill() } catch { Write-Verbose 'git had already exited.' }
            return @()
        }
        if ($process.ExitCode -ne 0) { return @() }
        $standardOutput = $reader.Result
    }
    finally {
        $process.Dispose()
    }

    # Entries are relative to the root, with forward slashes, and may be quoted when
    # the name needs it.
    $ignoredEntries = New-Object System.Collections.ArrayList
    foreach ($line in ($standardOutput -split "`r?`n")) {
        if ($line -notmatch '^!!\s+(.+)$') { continue }
        $entry = $Matches[1].Trim()
        if ($entry.Length -ge 2 -and $entry.StartsWith('"') -and $entry.EndsWith('"')) {
            $entry = $entry.Substring(1, $entry.Length - 2)
        }
        $null = $ignoredEntries.Add($entry)
    }
    if ($ignoredEntries.Count -eq 0) { return @() }

    $rootPrefix = $Root.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $matched = New-Object System.Collections.ArrayList
    foreach ($item in $Path) {
        $relative = $item
        if ($item.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            $relative = $item.Substring($rootPrefix.Length)
        }
        $relative = $relative.Replace('\', '/')

        foreach ($entry in $ignoredEntries) {
            $isDirectory = $entry.EndsWith('/')
            if ($isDirectory) {
                if ($relative.StartsWith($entry, [StringComparison]::OrdinalIgnoreCase)) {
                    $null = $matched.Add($item)
                    break
                }
            }
            elseif ([string]::Equals($relative, $entry, [StringComparison]::OrdinalIgnoreCase)) {
                $null = $matched.Add($item)
                break
            }
        }
    }

    return @($matched.ToArray())
}

function Get-ScannableFile {
    <#
    .SYNOPSIS
        Enumerates candidate files under a root, skipping excluded trees and
        anything that does not look like text.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Root)

    $candidates = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Force | Where-Object {
        $relative = $_.FullName.Substring($Root.Length).TrimStart('\', '/')
        $inExcluded = $false
        foreach ($segment in ($relative -split '[\\/]')) {
            if ($excludedDirectories -contains $segment) { $inExcluded = $true; break }
        }
        if ($inExcluded) { return $false }

        # Read it unless the extension says it is binary. A file with no extension is
        # read too: LICENSE and Dockerfile are the innocent cases, but id_rsa, .netrc
        # and .npmrc are the ones that matter, and the previous allowlist of five known
        # names excluded every one of them.
        $extension = $_.Extension.ToLowerInvariant()
        return (-not ($binaryExtensions -contains $extension))
    })

    # Anything git ignores is out of scope: this gate is about what reaches a commit.
    # Reported, not silent - a file skipped without saying so is how a scan quietly
    # stops covering something.
    $ignored = @(Get-GitIgnoredPath -Root $Root -Path @($candidates | ForEach-Object { $_.FullName }))
    if ($ignored.Count -gt 0) {
        Write-Information "[secrets] $($ignored.Count) file(s) skipped because git ignores them (a filled-in .env is the usual one)." -InformationAction Continue
    }

    return @($candidates | Where-Object { $ignored -notcontains $_.FullName })
}

$denyTerms = @()
$termsPath = if ([System.IO.Path]::IsPathRooted($TermsFile)) { $TermsFile } else { Join-Path $repoRoot $TermsFile }
if (Test-Path -LiteralPath $termsPath) {
    $denyTerms = @(
        Get-Content -LiteralPath $termsPath |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and -not $_.StartsWith('#') }
    )
    Write-Verbose "Loaded $($denyTerms.Count) deny term(s) from $termsPath."
    if ($denyTerms.Count -eq 0) {
        # Present but with no usable line in it. Worth saying out loud: the file
        # existing is what silences the warning below, so an empty one would
        # otherwise buy false confidence rather than coverage.
        Write-Warning "Deny-term file $termsPath has no terms in it. Structural rules only - this is not a full scan."
    }
}
else {
    Write-Warning "Deny-term file not found: $termsPath. Structural rules only - this is not a full scan."
}

$findings = New-Object System.Collections.Generic.List[object]
$scanned = 0
$skippedBinary = 0
$unreadable = New-Object System.Collections.Generic.List[string]

foreach ($file in Get-ScannableFile -Root $Path) {
    $relativePathForReport = ($file.FullName.Substring($Path.Length).TrimStart('\', '/')) -replace '\\', '/'

    # Unreadable is a FINDING, not a skip.
    #
    # This loop used to read with -ErrorAction SilentlyContinue and then `continue` on
    # empty content, having already incremented $scanned. So a file locked by another
    # process, or one whose ACL denies a read, was counted as clean - and the gate
    # printed "no findings across N file(s)" having opened fewer than N. The one guard
    # the whole secret-hygiene claim of this repository rests on failed in the
    # insecure direction, and silently, which is what this file's own comment about
    # redaction calls worse than failing.
    #
    # It is also indistinguishable from a genuinely empty file, which is why the two
    # are now separated: empty is fine and uninteresting, unreadable is reported.
    $content = $null
    $readFailure = ''
    try {
        $content = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop
    }
    catch {
        $readFailure = $_.Exception.Message
    }

    if ($readFailure) {
        $unreadable.Add($relativePathForReport)
        $null = $findings.Add([pscustomobject]@{
            Rule = 'UnreadableFile'
            File = $relativePathForReport
            Line = 0
            Match = 'could not be read, so it was not scanned'
        })
        continue
    }

    # Binary despite the extension, or genuinely empty. Neither is a finding, but they
    # are not "scanned" either, so neither counts towards the total.
    if ($null -eq $content -or $content.Length -eq 0) { continue }
    if ($content.IndexOf([char]0) -ge 0) { $skippedBinary++; continue }

    # Counted here, after the file has actually been read, so the number in the summary
    # is a count of files examined rather than of files considered.
    $scanned++

    $relativePath = $relativePathForReport
    $lines = $content -split "\r?\n"

    # JSON escapes a backslash as two, so an ordinary path such as
    # "Platform\\APP_ALPHA_Team" reads as a UNC share to a pattern that knows nothing
    # about the encoding. Unescaping before scanning removes that false positive
    # without creating a blind spot: a genuine UNC path is written with four
    # backslashes in JSON, which unescapes to two and is still matched. The
    # replacement is character for character, so reported line numbers stay correct.
    $scanText = $content
    if ($file.Extension -eq '.json') { $scanText = $content.Replace('\\', '\') }

    foreach ($rule in $rules) {
        foreach ($match in [regex]::Matches($scanText, $rule.Pattern)) {
            if (Test-AllowedMatch -Value $match.Value -Allow $rule.Allow) { continue }
            $findings.Add([pscustomobject]@{
                Rule   = $rule.Name
                File   = $relativePath
                Line   = ($scanText.Substring(0, $match.Index) -split "\r?\n").Count
                Match  = if ($match.Value.Length -gt 40) { $match.Value.Substring(0, 12) + '...[redacted]' } else { $match.Value }
                Reason = $rule.Description
            })
        }
    }

    for ($index = 0; $index -lt $lines.Count; $index++) {
        foreach ($term in $denyTerms) {
            if ($lines[$index].IndexOf($term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $findings.Add([pscustomobject]@{
                    Rule   = 'DenyTerm'
                    File   = $relativePath
                    Line   = $index + 1
                    Match  = '[deny term matched]'
                    Reason = 'Literal term from the local deny list.'
                })
            }
        }
    }
}

# Which layers actually ran is part of the result, not a footnote. "No findings"
# on its own reads as a clean bill of health, and the deny-list layer is the only
# one that can match an internal identifier with no recognisable shape - a folder
# name, a job path, an opaque field id. Saying so in the success line is what stops
# a structural-only pass from being mistaken for a full one.
$coverage = if ($denyTerms.Count -gt 0) {
    "structural rules + $($denyTerms.Count) deny term(s)"
} else {
    'structural rules only, no deny terms loaded'
}

if ($findings.Count -eq 0) {
    Write-Information "Sensitive data gate: no findings across $scanned file(s) ($coverage)." -InformationAction Continue
    if ($RequireTermsFile -and $denyTerms.Count -eq 0) {
        Write-Warning "The deny-list layer was required and did not run. Add terms to $termsPath, or drop -RequireTermsFile to accept structural coverage only."
        if ($PassThru) { return @() }
        exit 2
    }
    if ($PassThru) { return @() }
    exit 0
}

Write-Warning "Sensitive data gate: $($findings.Count) finding(s) across $scanned file(s) ($coverage)."
$table = $findings | Sort-Object File, Line | Format-Table -AutoSize Rule, File, Line, Match | Out-String
Write-Information $table -InformationAction Continue
foreach ($group in ($findings | Group-Object Rule | Sort-Object Count -Descending)) {
    Write-Information ("  {0,-20} {1,4}  {2}" -f $group.Name, $group.Count, $group.Group[0].Reason) -InformationAction Continue
}

# -PassThru returns the objects AND still exits non-zero. Returning findings with a
# success code invites a pipeline that is green while holding them, which is the
# failure this whole check exists to prevent.
if ($PassThru) { $findings; exit 1 }
exit 1
