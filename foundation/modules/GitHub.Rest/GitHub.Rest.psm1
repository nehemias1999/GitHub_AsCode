<#
    GitHub.Rest - what is GitHub-specific about talking to the REST API.

    The generic transport half is in GitHubAsCode.Http, which owns Invoke-WebRequest
    and knows nothing about GitHub. This module owns the parts that are GitHub rules
    and would be wrong to share:

    Pagination. The next page is read from the Link header, never computed. A caller
    that builds ?page=N and stops on a short page will, when the collection changes
    underneath it, skip items and report the truncated list as complete.

    The account listing trap. GET /users/{user}/repos returns PUBLIC repositories
    only. The private ones exist solely behind GET /user/repos with an authenticated
    token. Every private repository is therefore missing in silence from an inventory
    built on the first endpoint - the inventory whose entire job is to be complete. So
    the owner's repositories are read from /user/repos.

    Rate limits, of which there are two. The primary budget is 5000 requests an hour
    and is visible in x-ratelimit-remaining. The secondary limits are undocumented
    ceilings on bursts of writes, and they answer 403 or 429 with retry-after. Reading
    those headers is a GitHub rule and lives here; deciding what to do about the
    numbers is arithmetic and lives in GitHubAsCode.Http.

    Token shape. A classic PAT answers with x-oauth-scopes; a fine-grained one does
    not. That single header is how this repository can refuse to run a write with a
    token whose blast radius includes deleting a repository.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Status code to guidance, passed to the transport as DATA. It is the mechanism that
# lets GitHubAsCode.Http stay free of GitHub knowledge while a 403 still says
# something useful.
$script:GitHubStatusMessage = @{
    401 = 'The token was rejected. Either it is not set, has been revoked, or has expired. A fine-grained token expires on a fixed date; inventory reports the days remaining.'
    403 = 'Forbidden. Three different causes share this code: the token lacks the fine-grained permission for this endpoint, the primary rate limit is exhausted, or a secondary rate limit was tripped by a burst. Check x-ratelimit-remaining to tell them apart.'
    404 = 'Not found - which on GitHub also means "exists, but this token cannot see it". Do not read this as absence unless absence was confirmed another way.'
    422 = 'Unprocessable. For a contents write this is the API refusing to overwrite an existing file, which is the intended outcome here rather than an error.'
    429 = 'Too many requests. A secondary rate limit; retry-after says how long to wait.'
}

function Get-GitHubStatusMessage {
    <#
    .SYNOPSIS
        Returns the status-code-to-guidance map handed to the transport.

    .DESCRIPTION
        Exported so a test can assert the map covers the codes the guides promise it
        covers, without reaching into module state.

    .EXAMPLE
        Get-GitHubStatusMessage

    .OUTPUTS
        A dictionary of integer status code to guidance string.
    #>
    [CmdletBinding()]
    [OutputType([System.Collections.IDictionary])]
    param()

    return $script:GitHubStatusMessage.Clone()
}

function Get-GitHubContext {
    <#
    .SYNOPSIS
        Resolves the API base URL, owner and token into one context object.

    .DESCRIPTION
        The configuration declares the NAME of every value; this turns those names
        into values, validates them, and returns the single object every request in a
        run is built from.

        The token never appears in the returned object as itself: it is already an
        Authorization header value by the time it is stored, so nothing downstream can
        log "the token" without also having decided to log a header.

    .PARAMETER Context
        The parsed project context.

    .PARAMETER TokenEnvironmentName
        Which declared token to resolve. Defaults to the read token, so an automation
        has to ask explicitly to hold a token that can write.

    .EXAMPLE
        Get-GitHubContext -Context $projectContext

    .OUTPUTS
        An object with BaseUrl, Owner, Headers and the bounds for a run.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [pscustomobject] $Context,
        [string] $TokenEnvironmentName
    )

    $github = $Context.github
    $defaults = $Context.defaults

    if (-not $TokenEnvironmentName) { $TokenEnvironmentName = $github.readTokenEnv }

    $baseUrl = Get-GitHubAsCodeRequiredValue -Name $github.apiBaseUrlEnv
    $baseUrl = Assert-HttpBaseUrl -Url $baseUrl -VariableName $github.apiBaseUrlEnv

    $owner = Get-GitHubAsCodeRequiredValue -Name $github.ownerEnv
    if ($owner -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$') {
        # Not echoed, for the same reason Assert-HttpBaseUrl stopped echoing. .env holds
        # the owner and the token a few lines apart, and a value that fails this check is
        # by definition not a login - which makes it more likely to be the thing that was
        # pasted by mistake. A login is at most 39 characters, so the length alone
        # usually identifies the error.
        throw "$($github.ownerEnv) is not a valid GitHub account name: $($owner.Length) characters, which does not match the allowed shape. The value is not shown here, because a value in the wrong line of .env is usually a credential. Expected the login only - not a URL, and not owner/repo."
    }

    $token = Get-GitHubAsCodeRequiredValue -Name $TokenEnvironmentName

    $headers = @{
        'Authorization'        = New-BearerAuthorizationHeader -Secret $token
        'Accept'               = 'application/vnd.github+json'
        'X-GitHub-Api-Version' = $github.apiVersion
        # GitHub rejects a request with no User-Agent outright, with a 403 whose body
        # explains it - but only if the body is read, which a status-code-only path
        # does not do.
        'User-Agent'           = 'GitHub_AsCode'
    }

    return [pscustomobject]@{
        BaseUrl              = $baseUrl
        Owner                = $owner
        Headers              = $headers
        TokenEnvironmentName = $TokenEnvironmentName
        TimeoutSeconds       = [int] $defaults.requestTimeoutSeconds
        MaximumRetryCount    = [int] $defaults.maximumRetryCount
        RetryAfterCapSeconds = [int] $defaults.retryAfterCapSeconds
        PageSize             = [int] $defaults.pageSize
        MaximumPageCount     = [int] $defaults.maximumPageCount
    }
}

function Get-GitHubRateLimitState {
    <#
    .SYNOPSIS
        Reads the rate limit budget out of response headers.

    .DESCRIPTION
        Pure function over a header dictionary, so the whole of rate limit reporting
        is testable from a fixture with no network.

        A response with none of these headers is not an error and not zero: it returns
        $null for the numbers, because "unknown" and "none left" must not look alike
        to a caller deciding whether to continue.

    .PARAMETER Headers
        Response headers.

    .EXAMPLE
        Get-GitHubRateLimitState -Headers $response.Headers

    .OUTPUTS
        An object with Limit, Remaining, ResetUtc and Resource; any may be $null.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [System.Collections.IDictionary] $Headers
    )

    $limit = $null
    $raw = Get-HttpResponseHeader -Headers $Headers -Name 'x-ratelimit-limit'
    $parsed = 0
    if ($raw -and [int]::TryParse($raw, [ref] $parsed)) { $limit = $parsed }

    $remaining = $null
    $raw = Get-HttpResponseHeader -Headers $Headers -Name 'x-ratelimit-remaining'
    $parsed = 0
    if ($raw -and [int]::TryParse($raw, [ref] $parsed)) { $remaining = $parsed }

    $resetUtc = $null
    $raw = Get-HttpResponseHeader -Headers $Headers -Name 'x-ratelimit-reset'
    $resetEpoch = [long] 0
    if ($raw -and [long]::TryParse($raw, [ref] $resetEpoch)) {
        # A UNIX epoch second, not an HTTP date. Treating it as a date silently
        # produces 1970 and a "reset 56 years ago" line in a report.
        $resetUtc = [DateTimeOffset]::FromUnixTimeSeconds($resetEpoch).UtcDateTime
    }

    return [pscustomobject]@{
        Limit     = $limit
        Remaining = $remaining
        ResetUtc  = $resetUtc
        Resource  = Get-HttpResponseHeader -Headers $Headers -Name 'x-ratelimit-resource'
    }
}

function Get-GitHubTokenShape {
    <#
    .SYNOPSIS
        Tells a classic personal access token from a fine-grained one.

    .DESCRIPTION
        A classic PAT answers with x-oauth-scopes listing its scopes. A fine-grained
        token has no scopes and sends no such header. That is the whole test, and it
        is worth having because the two have very different blast radii: there is no
        fine-grained permission equivalent to deleting a repository, so a fine-grained
        token CANNOT delete one, while a classic token with delete_repo can.

        Fine-grained tokens also send their expiry date, which is the only way to warn
        before a scheduled run starts failing with a 401 that reads like revocation.

    .PARAMETER Headers
        Response headers.

    .EXAMPLE
        Get-GitHubTokenShape -Headers $response.Headers

    .OUTPUTS
        An object with IsClassic, Scope, ExpiresUtc and DaysUntilExpiry.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [System.Collections.IDictionary] $Headers
    )

    $scopeHeader = Get-HttpResponseHeader -Headers $Headers -Name 'x-oauth-scopes'
    $isClassic = [bool] $scopeHeader

    $scope = @()
    if ($scopeHeader) {
        $scope = @($scopeHeader -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    }

    $expiresUtc = $null
    $daysUntilExpiry = $null
    $expiryRaw = Get-HttpResponseHeader -Headers $Headers -Name 'github-authentication-token-expiration'
    if ($expiryRaw) {
        # The header reads "2026-12-31 23:59:59 UTC", which no single standard parse
        # handles, so the zone label is dropped and the rest read as universal time.
        $normalized = ($expiryRaw -replace '\s+UTC$', '').Trim()
        $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
        $parsedDate = [datetime]::MinValue
        if ([datetime]::TryParse($normalized, [System.Globalization.CultureInfo]::InvariantCulture, $styles, [ref] $parsedDate)) {
            $expiresUtc = $parsedDate
            $daysUntilExpiry = [int] [Math]::Floor(($parsedDate - [datetime]::UtcNow).TotalDays)
        }
    }

    return [pscustomobject]@{
        IsClassic       = $isClassic
        Scope           = $scope
        ExpiresUtc      = $expiresUtc
        DaysUntilExpiry = $daysUntilExpiry
    }
}

function Get-GitHubRelativeTarget {
    <#
    .SYNOPSIS
        Splits an absolute API URL into the path and query the transport takes.

    .DESCRIPTION
        The Link header gives the next page as an absolute URL. The transport builds a
        URL from a base plus a path plus a query dictionary, so this converts one into
        the other rather than having two places that construct URLs.

        Doing it this way, instead of computing ?page=N+1, is what makes pagination
        follow what the API actually said. Some endpoints paginate by an opaque cursor
        that cannot be computed at all.

        A URL whose host differs from the base URL is rejected. A Link header is
        server-controlled input, and following it to another host would send the
        Authorization header there.

    .PARAMETER Url
        The absolute URL from the Link header.

    .PARAMETER BaseUrl
        The normalized API base URL the run is bound to.

    .EXAMPLE
        Get-GitHubRelativeTarget -Url $next -BaseUrl $context.BaseUrl

    .OUTPUTS
        An object with Path and Query.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [string] $Url,
        [Parameter(Mandatory)] [string] $BaseUrl
    )

    $parsedUrl = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref] $parsedUrl)) {
        throw "The Link header offered a next page that is not an absolute URL: '$Url'."
    }
    $parsedBase = [Uri] $BaseUrl

    if ($parsedUrl.Scheme -ne $parsedBase.Scheme -or $parsedUrl.Authority -ne $parsedBase.Authority) {
        throw "The Link header pointed at $($parsedUrl.Scheme)://$($parsedUrl.Authority), which is not the configured API host $($parsedBase.Scheme)://$($parsedBase.Authority). Refusing to follow it: the Authorization header would go with the request."
    }

    $basePath = $parsedBase.AbsolutePath.TrimEnd('/')
    $path = $parsedUrl.AbsolutePath
    if ($basePath -and $path.StartsWith($basePath, [StringComparison]::Ordinal)) {
        $path = $path.Substring($basePath.Length)
    }
    $path = $path.Trim('/')

    $query = @{}
    if ($parsedUrl.Query) {
        foreach ($pair in $parsedUrl.Query.TrimStart('?') -split '&') {
            if (-not $pair) { continue }
            $separator = $pair.IndexOf('=')
            if ($separator -lt 1) { continue }
            $name = [Uri]::UnescapeDataString($pair.Substring(0, $separator))
            $value = [Uri]::UnescapeDataString($pair.Substring($separator + 1))
            $query[$name] = $value
        }
    }

    return [pscustomobject]@{
        Path  = $path
        Query = $query
    }
}

function Invoke-GitHubRequest {
    <#
    .SYNOPSIS
        Sends one authenticated GET to the API and returns the parsed body.

    .DESCRIPTION
        Adds the two things a GitHub caller needs on top of the generic transport: the
        status guidance map, and the response headers, which on this API are sometimes
        the answer rather than metadata - Link carries pagination and
        x-ratelimit-remaining carries the budget.

    .PARAMETER GitHubContext
        The context from Get-GitHubContext.

    .PARAMETER Path
        API path with no leading slash, such as 'user/repos'.

    .PARAMETER Query
        Optional query values.

    .PARAMETER AllowNotFound
        Return $null instead of throwing on 404. Use only where absence has been
        established another way: on GitHub a 404 also means "no permission".

    .EXAMPLE
        Invoke-GitHubRequest -GitHubContext $gh -Path 'user/repos' -Query @{ per_page = 100 }

    .OUTPUTS
        An object with Content (parsed), Headers, StatusCode, RateLimit and Link.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [pscustomobject] $GitHubContext,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Path,
        [System.Collections.IDictionary] $Query,
        [switch] $AllowNotFound
    )

    $response = Invoke-ReadOnlyRequest `
        -BaseUrl $GitHubContext.BaseUrl `
        -Path $Path `
        -Headers $GitHubContext.Headers `
        -Query $Query `
        -TimeoutSeconds $GitHubContext.TimeoutSeconds `
        -MaximumRetryCount $GitHubContext.MaximumRetryCount `
        -RetryAfterCapSeconds $GitHubContext.RetryAfterCapSeconds `
        -StatusMessage $script:GitHubStatusMessage `
        -AllowNotFound:$AllowNotFound

    if ($null -eq $response) { return $null }

    return [pscustomobject]@{
        Content    = ConvertFrom-JsonResponse -Content $response.Content -Uri "GET $Path"
        Headers    = $response.Headers
        StatusCode = $response.StatusCode
        RateLimit  = Get-GitHubRateLimitState -Headers $response.Headers
        Link       = Get-HttpResponseHeader -Headers $response.Headers -Name 'Link'
    }
}

function Get-GitHubPagedResult {
    <#
    .SYNOPSIS
        Follows Link rel="next" and returns every item in a collection.

    .DESCRIPTION
        The loop terminates on one condition only: the Link header stopped offering a
        next page. It does not stop on a short page, because a short page is not the
        end of a collection that is changing underneath the reader.

        Reaching MaximumPageCount is NOT a quiet truncation. The result says so, and
        the caller is expected to turn that into a blocked plan. An inventory that
        reports fewer repositories than exist, while claiming to be complete, is worse
        than one that fails: the whole point of the phase-1 inventory is to be the
        input to a decision.

    .PARAMETER GitHubContext
        The context from Get-GitHubContext.

    .PARAMETER Path
        API path with no leading slash.

    .PARAMETER Query
        Query values applied to the FIRST request. Later pages use the query the Link
        header supplies, so per_page does not have to be re-sent or re-derived.

    .EXAMPLE
        Get-GitHubPagedResult -GitHubContext $gh -Path 'user/repos' -Query @{ affiliation = 'owner' }

    .OUTPUTS
        An object with Item, PageCount, Truncated and RateLimit.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [pscustomobject] $GitHubContext,
        [Parameter(Mandatory)] [string] $Path,
        [System.Collections.IDictionary] $Query
    )

    $effectiveQuery = @{ per_page = $GitHubContext.PageSize }
    if ($Query) {
        foreach ($key in $Query.Keys) { $effectiveQuery[$key] = $Query[$key] }
    }

    $items = New-Object System.Collections.Generic.List[object]
    $currentPath = $Path
    $currentQuery = $effectiveQuery
    $pageCount = 0
    $truncated = $false
    $rateLimit = $null

    while ($true) {
        $response = Invoke-GitHubRequest -GitHubContext $GitHubContext -Path $currentPath -Query $currentQuery
        $pageCount++
        $rateLimit = $response.RateLimit

        # A collection endpoint returns an array. PowerShell unrolls a single-element
        # array on assignment, and an empty one to $null, so the body is wrapped
        # rather than counted.
        foreach ($item in @($response.Content)) {
            if ($null -ne $item) { $items.Add($item) }
        }

        $next = Get-HttpLinkHeaderTarget -LinkHeader $response.Link -Relation 'next'
        if (-not $next) { break }

        if ($pageCount -ge $GitHubContext.MaximumPageCount) {
            $truncated = $true
            break
        }

        $target = Get-GitHubRelativeTarget -Url $next -BaseUrl $GitHubContext.BaseUrl
        $currentPath = $target.Path
        $currentQuery = $target.Query
    }

    return [pscustomobject]@{
        Item      = $items.ToArray()
        PageCount = $pageCount
        Truncated = $truncated
        RateLimit = $rateLimit
    }
}

function Get-GitHubOwnedRepository {
    <#
    .SYNOPSIS
        Returns every repository the authenticated account owns, public and private.

    .DESCRIPTION
        Uses GET /user/repos with affiliation=owner, and NOT
        GET /users/{owner}/repos, which returns public repositories only.

        That distinction is the reason this function exists rather than each caller
        building a path: the public endpoint omits every private repository, so an
        inventory built on it reports itself complete while being short by however many
        the account has.

        affiliation=owner rather than the default, because the default also brings in
        repositories the account collaborates on or reaches through an organization,
        which are not the account's to configure.

    .PARAMETER GitHubContext
        The context from Get-GitHubContext.

    .EXAMPLE
        Get-GitHubOwnedRepository -GitHubContext $gh

    .OUTPUTS
        The paged result: Item, PageCount, Truncated and RateLimit.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [pscustomobject] $GitHubContext
    )

    return Get-GitHubPagedResult -GitHubContext $GitHubContext -Path 'user/repos' -Query @{
        affiliation = 'owner'
        sort        = 'full_name'
        direction   = 'asc'
    }
}

Export-ModuleMember -Function @(
    'Get-GitHubStatusMessage',
    'Get-GitHubContext',
    'Get-GitHubRateLimitState',
    'Get-GitHubTokenShape',
    'Get-GitHubRelativeTarget',
    'Invoke-GitHubRequest',
    'Get-GitHubPagedResult',
    'Get-GitHubOwnedRepository'
)
