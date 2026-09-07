<#
    GitHubAsCode.Http - read-only HTTP, shared by every transport.

    This module exists because there are two API surfaces here, GitHub's REST v3
    and its GraphQL v4, and a retry policy implemented twice is a retry policy that
    drifts. It knows what a request, a retry and a credential are, and nothing about
    either surface: no URL, no endpoint, no permission name appears in it.

    The split is deliberate and load bearing. What REST and GraphQL share is pure
    transport. What differs is how a failure is recognised - REST fails with a status
    code, GraphQL answers HTTP 200 and puts the failure in an errors array - and that
    is a domain rule, so it lives in GitHub.Rest and GitHub.GraphQL, not here.

    READ ONLY BY CONSTRUCTION, FOR NOW. There is no -Method parameter and no code
    path that sends anything but GET, so in phases 1 and 2 this repository cannot
    write at all and the absence tests assert exactly that.

    Widening this is the single edit that turns GitHub_AsCode into a tool that can
    damage the account, so it is an ADR and not an edit. When phase 3 adds the first
    writer, -Method arrives here with [ValidateSet('GET','HEAD','POST','PATCH','PUT')]
    - never DELETE - and absence test 3 changes from "no write exists" to "every
    method is a literal from the allowlist". See docs/adr/0001-write-boundary.md.

    Because every request is a GET, and a GET is idempotent, retrying is always
    safe. A module that also wrote could not use this list: a POST retried after the
    server had already committed manufactures a duplicate.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RetryableStatusCode = @(408, 429, 500, 502, 503, 504)

# TLS floor, set once when the module loads. On Windows PowerShell 5.1 over .NET
# Framework the default protocol depends on how the host is patched: a host on an
# older framework, or with strong crypto disabled, can still negotiate TLS 1.0. A
# Basic credential travels on every request here, so the floor is not left to the
# machine. Existing flags are kept rather than replaced, and TLS 1.3 is added only
# where the runtime knows the value - naming it directly throws on 5.1.
$script:TlsFloor = [Net.SecurityProtocolType]::Tls12
if ([enum]::GetNames([Net.SecurityProtocolType]) -contains 'Tls13') {
    $script:TlsFloor = $script:TlsFloor -bor [Net.SecurityProtocolType]::Tls13
}
if (([Net.ServicePointManager]::SecurityProtocol -band $script:TlsFloor) -ne $script:TlsFloor) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor $script:TlsFloor
}

function Assert-HttpBaseUrl {
    <#
    .SYNOPSIS
        Validates and normalizes a base URL.

    .DESCRIPTION
        Pure function. Returns the URL with any trailing slash removed, so every
        caller concatenates against the same shape.

        A URL carrying a query or a fragment is refused. A base URL with a query
        string breaks every URL derived from it, because the query lands in the
        middle of the path and nothing complains.

    .PARAMETER Url
        Candidate base URL.

    .PARAMETER VariableName
        Environment variable the value came from, named in any failure so the
        message points at what to fix.

    .EXAMPLE
        Assert-HttpBaseUrl -Url 'https://example.com/' -VariableName 'GITHUB_API_URL'

    .OUTPUTS
        The normalized base URL.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Url,
        [Parameter(Mandatory)] [string] $VariableName
    )

    if ([string]::IsNullOrWhiteSpace($Url)) {
        throw "$VariableName is empty. Set it in .env to an absolute URL, for example https://example.com."
    }

    $trimmed = $Url.Trim().TrimEnd('/')

    $parsed = $null
    if (-not [Uri]::TryCreate($trimmed, [UriKind]::Absolute, [ref] $parsed)) {
        throw "$VariableName is '$trimmed', which is not an absolute URL. It must include the scheme, for example https://example.com."
    }
    if ($parsed.Scheme -notin @('http', 'https')) {
        throw "$VariableName uses scheme '$($parsed.Scheme)'. Only http and https are supported."
    }
    # http is accepted and announced, not accepted silently. Every request carries a
    # Basic header, and Base64 is an encoding, not encryption - so on plain http the
    # token is readable by anything on the path. It stays allowed because a controller
    # on a private network without a certificate is a real situation, and refusing it
    # outright would push people towards disabling TLS checks instead, which is worse.
    if ($parsed.Scheme -eq 'http') {
        Write-Warning "$VariableName uses http, so the API token travels unencrypted on every request. Use https unless this is a network you control end to end."
    }
    # Credentials in the base URL, rejected rather than carried. Two reasons, and the
    # second is the one that bites: the header is already how this authenticates, so
    # userinfo adds nothing - and every error message below, plus detail.controllerUrl
    # in every report, interpolates this value. One misconfigured .env would copy a
    # token into every artefact the tool writes. The message deliberately does not
    # echo the URL back.
    if ($parsed.UserInfo) {
        throw "$VariableName carries credentials in the URL (a user[:password]@ before the host). Remove them: authentication uses the token from the environment, and a URL with userinfo would be copied into reports and error messages."
    }
    if ($parsed.Query) {
        throw "$VariableName carries a query string. Remove it: the query of a derived URL would end up in the middle of the path."
    }
    if ($parsed.Fragment) {
        throw "$VariableName carries a fragment. Remove it."
    }

    return $trimmed
}

function New-HttpUri {
    <#
    .SYNOPSIS
        Builds an absolute URL from a base URL, a path and an optional query.

    .DESCRIPTION
        Pure function. Query values are escaped here, so no caller has to remember
        to escape a JQL expression or a tree expression.

    .PARAMETER BaseUrl
        Normalized base URL.

    .PARAMETER Path
        Path with no leading slash.

    .PARAMETER Query
        Optional query values. A $null value is omitted rather than sent empty.

    .EXAMPLE
        New-HttpUri -BaseUrl 'https://example.com' -Path 'api/json' -Query @{ tree = 'jobs[name]' }

    .OUTPUTS
        The absolute URL.
    #>
    # Pure function: it computes a value and changes no system state. ShouldProcess
    # would offer a confirmation prompt for something there is nothing to confirm
    # about, and would train people to answer yes.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $BaseUrl,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Path,
        [System.Collections.IDictionary] $Query
    )

    $uri = $BaseUrl.TrimEnd('/')
    $cleanPath = $Path.Trim('/')
    if ($cleanPath) { $uri += '/' + $cleanPath }

    if ($Query -and $Query.Keys.Count -gt 0) {
        # Sorted, because the enumeration order of a .NET hashtable is not specified.
        # Query order does not change what a GET means, so nothing is broken today -
        # with one or two keys it is not even observable. It stops the same request
        # from producing two different URLs between runs, which is what a log line or
        # a cache key would disagree about later.
        $pairs = foreach ($key in (@($Query.Keys) | Sort-Object)) {
            $value = $Query[$key]
            if ($null -eq $value) { continue }
            '{0}={1}' -f [Uri]::EscapeDataString([string] $key), [Uri]::EscapeDataString([string] $value)
        }
        $joined = @($pairs) -join '&'
        if ($joined) { $uri += '?' + $joined }
    }

    return $uri
}

function New-BasicAuthorizationHeader {
    <#
    .SYNOPSIS
        Builds a Basic Authorization header value.

    .DESCRIPTION
        UTF-8 rather than ASCII. A user name or an email address is allowed to
        contain a non-ASCII character, and ASCII encoding turns it into a question
        mark - producing a 401 that reads like a wrong token and sends the reader
        looking in the wrong place.

    .PARAMETER UserName
        User name or email address.

    .PARAMETER Secret
        API token or password.

    .EXAMPLE
        New-BasicAuthorizationHeader -UserName $user -Secret $token

    .OUTPUTS
        The header value, beginning with 'Basic '.
    #>
    # Pure function: it computes a value and changes no system state. ShouldProcess
    # would offer a confirmation prompt for something there is nothing to confirm
    # about, and would train people to answer yes.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $UserName,
        [Parameter(Mandatory)] [string] $Secret
    )

    $bytes = [Text.Encoding]::UTF8.GetBytes($UserName + ':' + $Secret)
    return 'Basic ' + [Convert]::ToBase64String($bytes)
}

function Get-HttpResponseHeader {
    <#
    .SYNOPSIS
        Reads one response header, tolerating the two shapes PowerShell returns.

    .DESCRIPTION
        Pure function. On Windows PowerShell 5.1 a header value is a string; on
        PowerShell 7 it is a string array. Code that assumes either one works on one
        engine and returns 'System.String[]' on the other.

    .PARAMETER Headers
        Response header dictionary.

    .PARAMETER Name
        Header name, matched case-insensitively.

    .EXAMPLE
        Get-HttpResponseHeader -Headers $response.Headers -Name 'Link'

    .OUTPUTS
        The header value, or an empty string when absent.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowNull()] [object] $Headers,
        [Parameter(Mandatory)] [string] $Name
    )

    if ($null -eq $Headers) { return '' }

    foreach ($key in $Headers.Keys) {
        if ([string]::Equals([string] $key, $Name, [StringComparison]::OrdinalIgnoreCase)) {
            $value = $Headers[$key]
            if ($value -is [string]) { return $value }
            return (@($value) -join ', ')
        }
    }

    return ''
}

function Get-HttpErrorStatusCode {
    <#
    .SYNOPSIS
        Extracts the HTTP status code from a terminating web error.

    .DESCRIPTION
        Returns 0 when the failure carried no HTTP response at all, which
        Get-HttpRetryDecision treats as retryable.

    .PARAMETER ErrorRecord
        The caught error.

    .EXAMPLE
        Get-HttpErrorStatusCode -ErrorRecord $_

    .OUTPUTS
        The status code, or 0.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory)] [object] $ErrorRecord
    )

    if (-not $ErrorRecord.Exception) { return 0 }
    if (-not $ErrorRecord.Exception.PSObject.Properties['Response']) { return 0 }

    $response = $ErrorRecord.Exception.Response
    if ($null -eq $response) { return 0 }
    if (-not $response.PSObject.Properties['StatusCode']) { return 0 }

    try { return [int] $response.StatusCode } catch { return 0 }
}

function Get-HttpRetryAfterSecond {
    <#
    .SYNOPSIS
        Reads Retry-After from a failed response, in seconds.

    .DESCRIPTION
        Retry-After may be a number of seconds or an HTTP date, and both shapes
        appear in practice behind a reverse proxy.

    .PARAMETER ErrorRecord
        The caught error.

    .EXAMPLE
        Get-HttpRetryAfterSecond -ErrorRecord $_

    .OUTPUTS
        Seconds to wait, or 0 when absent or unparsable.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory)] [object] $ErrorRecord
    )

    if (-not $ErrorRecord.Exception) { return 0 }
    if (-not $ErrorRecord.Exception.PSObject.Properties['Response']) { return 0 }
    $response = $ErrorRecord.Exception.Response
    if ($null -eq $response) { return 0 }
    if (-not $response.PSObject.Properties['Headers']) { return 0 }

    $raw = Get-HttpResponseHeader -Headers $response.Headers -Name 'Retry-After'
    if (-not $raw) { return 0 }

    # Both parses are pinned to the invariant culture. Retry-After is either a count
    # of seconds or an HTTP-date, and an HTTP-date is English by specification - so
    # the current culture is never the right reader for it. Parsed under, say, a
    # Spanish culture the date form simply fails, and the failure is silent: the
    # backoff the server asked for is dropped and the retry goes out immediately,
    # against a server that just said it was overloaded.
    $invariant = [Globalization.CultureInfo]::InvariantCulture
    $seconds = 0
    if ([int]::TryParse($raw, [Globalization.NumberStyles]::Integer, $invariant, [ref] $seconds)) { return [Math]::Max(0, $seconds) }

    $when = [datetime]::MinValue
    if ([datetime]::TryParse($raw, $invariant, [Globalization.DateTimeStyles]::AdjustToUniversal, [ref] $when)) {
        $delta = [int] ($when.ToUniversalTime() - [datetime]::UtcNow).TotalSeconds
        return [Math]::Max(0, $delta)
    }

    return 0
}

function Get-HttpRetryDecision {
    <#
    .SYNOPSIS
        Decides whether a failed request should be retried, and after how long.

    .DESCRIPTION
        Pure function, so the retry policy is testable offline instead of only
        observable during an outage.

        Two rules are worth stating because the obvious implementation gets them
        backwards:

        A failure carrying NO status code - a DNS failure, a TLS reset, a timeout -
        is the most transient failure there is, and is retried. Classifying it as
        non-retryable because there is no code to match against is a common mistake,
        and it makes the tool fail on exactly the conditions retry exists for.

        An honoured Retry-After is capped. A service or proxy answering
        'Retry-After: 999999' would otherwise park the run in Start-Sleep for days.

    .PARAMETER StatusCode
        HTTP status code, or 0 when the failure carried none.

    .PARAMETER Attempt
        1-based number of the attempt that just failed.

    .PARAMETER MaximumRetryCount
        Total attempts allowed.

    .PARAMETER RetryAfterSeconds
        Value of the Retry-After header, or 0 when absent.

    .PARAMETER RetryAfterCapSeconds
        Upper bound applied to RetryAfterSeconds.

    .EXAMPLE
        Get-HttpRetryDecision -StatusCode 503 -Attempt 1 -MaximumRetryCount 3

    .OUTPUTS
        An object with ShouldRetry and DelaySeconds.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [int] $StatusCode,
        [Parameter(Mandatory)] [int] $Attempt,
        [Parameter(Mandatory)] [int] $MaximumRetryCount,
        [int] $RetryAfterSeconds = 0,
        [int] $RetryAfterCapSeconds = 120
    )

    $retryable = ($StatusCode -eq 0) -or ($script:RetryableStatusCode -contains $StatusCode)

    if (($Attempt -ge $MaximumRetryCount) -or -not $retryable) {
        return [pscustomobject]@{ ShouldRetry = $false; DelaySeconds = 0 }
    }

    if ($RetryAfterSeconds -gt 0) {
        $delay = [Math]::Min($RetryAfterSeconds, $RetryAfterCapSeconds)
    }
    else {
        $delay = [Math]::Min([Math]::Pow(2, $Attempt), $RetryAfterCapSeconds)
    }

    return [pscustomobject]@{ ShouldRetry = $true; DelaySeconds = [int] $delay }
}

function Get-HttpRetryableStatusCode {
    <#
    .SYNOPSIS
        Returns the status codes this module retries.

    .DESCRIPTION
        Exported so the test suite asserts against the same list the module
        enforces, rather than restating it and drifting from it.

    .EXAMPLE
        Get-HttpRetryableStatusCode

    .OUTPUTS
        The status codes.
    #>
    [CmdletBinding()]
    [OutputType([int[]])]
    param()

    return @($script:RetryableStatusCode)
}

function ConvertFrom-JsonResponse {
    <#
    .SYNOPSIS
        Parses a response body as JSON, and explains a non-JSON body instead of
        failing on it obscurely.

    .DESCRIPTION
        A raw ConvertFrom-Json failure reads "Invalid JSON primitive: <" and names a
        line inside a transport module, which tells the reader nothing about the cause.

        The cause is nearly always one of two things, and both are configuration:

        The base URL points at a web UI rather than at the API root. Pasting the URL
        out of a browser address bar is how this happens - https://github.com/owner/repo
        copied from the address bar is not https://api.github.com, and every request
        built from it lands on an HTML page that answers 200.

        Or a reverse proxy or SSO gateway is answering with a login page instead of
        passing the request through, which also answers 200 with HTML.

        Either way the status code is fine and the body is a document, so nothing
        upstream notices. This function names both possibilities.

    .PARAMETER Content
        The response body.

    .PARAMETER Uri
        The URL that was requested, quoted in any failure.

    .EXAMPLE
        ConvertFrom-JsonResponse -Content $response.Content -Uri $uri

    .OUTPUTS
        The parsed object.
    #>
    [CmdletBinding()]
    [OutputType([object])]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Content,
        [Parameter(Mandatory)] [string] $Uri
    )

    if ([string]::IsNullOrWhiteSpace($Content)) {
        throw "GET $Uri answered with an empty body where JSON was expected. A 200 with no body usually means a proxy in front of the service handled the request itself."
    }

    $looksLikeMarkup = $Content.TrimStart().StartsWith('<')

    try {
        return ($Content | ConvertFrom-Json)
    }
    catch {
        if ($looksLikeMarkup) {
            throw "GET $Uri answered with HTML or XML where JSON was expected. Two usual causes: the configured base URL points at a web UI rather than the API root - a URL copied from a browser address bar carries a UI path - or a proxy or SSO gateway returned a login page. Check the base URL in .env and remove any path that belongs to the UI."
        }
        throw "GET $Uri answered with a body that is not JSON: $($_.Exception.Message)"
    }
}

function New-BearerAuthorizationHeader {
    <#
    .SYNOPSIS
        Builds a Bearer Authorization header value.

    .DESCRIPTION
        A token is sent as-is, with no encoding step. That is the whole function, and
        it exists anyway for one reason: so that no transport module builds the string
        itself. A caller writing "Bearer $token" by hand is a caller that can write
        "Bearer  $token", and the resulting 401 reads like a revoked token.

        The token is trimmed, because a value pasted into a .env file arrives with a
        trailing newline more often than not, and a header value containing a newline
        throws a very unhelpful ArgumentException from the header collection rather
        than failing as authentication.

    .PARAMETER Secret
        The personal access token.

    .EXAMPLE
        New-BearerAuthorizationHeader -Secret $token

    .OUTPUTS
        The header value, beginning with 'Bearer '.
    #>
    # Pure function: it computes a value and changes no system state.
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '')]
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $Secret
    )

    $trimmed = $Secret.Trim()
    if (-not $trimmed) {
        throw 'The token is empty. The configuration names the environment variable that should hold it; check that the variable is set in .env and that bootstrap.ps1 loaded it.'
    }
    if ($trimmed -match '\s') {
        throw 'The token contains whitespace, which cannot be sent in a header. This is usually a value that was pasted across a line break.'
    }

    return 'Bearer ' + $trimmed
}

function Get-HttpLinkHeaderTarget {
    <#
    .SYNOPSIS
        Returns the URL for one relation from an RFC 5988 Link header.

    .DESCRIPTION
        Pagination is the one place where guessing quietly produces a wrong answer
        instead of an error. A caller that builds ?page=N itself and stops when a page
        comes back short will, if the collection changes underneath it, skip items and
        report the truncated list as complete. So the next page is never computed: it
        is read from the Link header, and when the header stops offering one, the
        collection is finished.

        The parser is deliberately literal about the grammar:

        Multiple links are comma separated, and each is <url>; rel="name". The URL is
        taken from between the angle brackets and NOT unescaped - it is already a
        valid absolute URL and contains an opaque cursor in some APIs, which
        unescaping would corrupt.

        A comma may appear inside the URL, in a query value. Splitting the header on
        ',' therefore truncates the URL, so segmentation is driven by the angle
        brackets instead.

        rel values may be quoted or bare, and are matched case-insensitively, because
        the relation name is a registered token and not user data.

    .PARAMETER LinkHeader
        The raw Link header value. An empty or absent header is not an error: it means
        there is no next page.

    .PARAMETER Relation
        The relation to look for, such as 'next', 'last', 'prev' or 'first'.

    .EXAMPLE
        Get-HttpLinkHeaderTarget -LinkHeader $link -Relation 'next'

    .OUTPUTS
        The absolute URL, or $null when the header offers no such relation.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [AllowEmptyString()] [AllowNull()] [string] $LinkHeader,
        [Parameter(Mandatory)] [string] $Relation
    )

    if (-not $LinkHeader) { return $null }

    # Segment on the angle brackets rather than on ',' so a comma inside a query
    # value cannot split one link into two.
    $pattern = '<(?<target>[^>]*)>(?<parameters>[^<]*)'
    foreach ($match in [regex]::Matches($LinkHeader, $pattern)) {
        $target = $match.Groups['target'].Value.Trim()
        if (-not $target) { continue }

        foreach ($parameter in $match.Groups['parameters'].Value -split ';') {
            $parameter = $parameter.Trim()
            if (-not $parameter) { continue }

            $separator = $parameter.IndexOf('=')
            if ($separator -lt 1) { continue }

            $name = $parameter.Substring(0, $separator).Trim().Trim(',').Trim()
            if ($name -ne 'rel') { continue }

            # The comma that separates one link from the next lands at the end of
            # this segment, because segmentation is driven by the angle brackets and
            # the parameters of a link run up to the following '<'. So it is stripped
            # BEFORE the quotes: trimming quotes first leaves rel="next", as
            # 'next",' - a value that matches no relation, which is how a
            # Link header with more than one link silently reported "no next page"
            # and truncated a collection to its first page.
            $value = $parameter.Substring($separator + 1).Trim()
            $value = $value.TrimEnd(',').Trim()
            $value = $value.Trim('"').Trim("'")

            # A single link may carry several relations: rel="next last".
            foreach ($candidate in $value -split '\s+') {
                if ($candidate -and $candidate -eq $Relation) { return $target }
            }
        }
    }

    return $null
}

function Invoke-ReadOnlyRequest {
    <#
    .SYNOPSIS
        Sends one authenticated GET and returns body, headers and status.

    .DESCRIPTION
        The only function in this repository that performs network I/O. It sends GET
        and nothing else: no parameter exists that could make it write.

        Three decisions worth knowing:

        Invoke-WebRequest rather than Invoke-RestMethod, because a response header is
        sometimes the answer - the next page of a paginated result arrives only in
        the Link header, and the rate limit budget only in x-ratelimit-remaining - and
        Windows PowerShell 5.1 has no -ResponseHeadersVariable. One code path on both
        engines instead of a version check.

        The body is decoded from the raw bytes as UTF-8 rather than trusting the
        response charset. Where a response arrives with no charset parameter, 5.1
        falls back to ISO-8859-1, which corrupts any non-ASCII character in a
        repository description or a topic.

        Redirects are not followed. An Authorization header sent to whatever host a
        30x points at is a credential disclosure. The usual cause is a wrong base
        URL, so a redirect is reported as the configuration problem it is.

    .PARAMETER BaseUrl
        Normalized base URL.

    .PARAMETER Path
        Path with no leading slash.

    .PARAMETER Headers
        Request headers, including Authorization.

    .PARAMETER Query
        Optional query values.

    .PARAMETER TimeoutSeconds
        Per-attempt timeout.

    .PARAMETER MaximumRetryCount
        Total attempts allowed.

    .PARAMETER RetryAfterCapSeconds
        Upper bound on an honoured Retry-After.

    .PARAMETER StatusMessage
        Status code to guidance, supplied by the calling transport. It is data, so
        this module stays free of any knowledge about GitHub while a 403 can still
        name the fine-grained permission that is missing.

    .PARAMETER AllowNotFound
        Return $null instead of throwing on 404, where absence is itself an answer.

    .EXAMPLE
        Invoke-ReadOnlyRequest -BaseUrl $url -Path 'api/json' -Headers $headers -TimeoutSeconds 60 -MaximumRetryCount 3

    .OUTPUTS
        An object with Content, Headers and StatusCode, or $null on an allowed 404.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [string] $BaseUrl,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Path,
        [Parameter(Mandatory)] [System.Collections.IDictionary] $Headers,
        [System.Collections.IDictionary] $Query,
        [int] $TimeoutSeconds = 60,
        [int] $MaximumRetryCount = 3,
        [int] $RetryAfterCapSeconds = 120,
        [System.Collections.IDictionary] $StatusMessage,
        [switch] $AllowNotFound
    )

    $uri = New-HttpUri -BaseUrl $BaseUrl -Path $Path -Query $Query

    $attempt = 0
    while ($true) {
        $attempt++
        try {
            $requestParameters = @{
                Uri                = $uri
                Method             = 'Get'
                Headers            = $Headers
                TimeoutSec         = $TimeoutSeconds
                MaximumRedirection = 0
                UseBasicParsing    = $true
                ErrorAction        = 'Stop'
            }
            $response = Invoke-WebRequest @requestParameters

            if ($response.PSObject.Properties['RawContentStream'] -and $response.RawContentStream) {
                $content = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
            }
            else {
                $content = [string] $response.Content
            }

            # A UTF-8 byte order mark ahead of an XML declaration makes an [xml] cast
            # throw "Data at the root level is invalid", three layers from here.
            $content = $content.TrimStart([char] 0xFEFF)

            return [pscustomobject]@{
                Content    = $content
                Headers    = $response.Headers
                StatusCode = [int] $response.StatusCode
            }
        }
        catch {
            $statusCode = Get-HttpErrorStatusCode -ErrorRecord $_

            if ($statusCode -eq 404 -and $AllowNotFound) { return $null }

            if ($statusCode -ge 300 -and $statusCode -lt 400) {
                throw "GET $uri was redirected (HTTP $statusCode), and redirects are not followed because forwarding the Authorization header to another host would disclose the credential. Check the base URL: http against https, or a missing or extra path prefix."
            }

            if ($StatusMessage -and $StatusMessage.Contains($statusCode)) {
                throw "GET $uri failed with HTTP $statusCode. $($StatusMessage[$statusCode])"
            }

            $decisionParameters = @{
                StatusCode           = $statusCode
                Attempt              = $attempt
                MaximumRetryCount    = $MaximumRetryCount
                RetryAfterSeconds    = (Get-HttpRetryAfterSecond -ErrorRecord $_)
                RetryAfterCapSeconds = $RetryAfterCapSeconds
            }
            $decision = Get-HttpRetryDecision @decisionParameters

            if (-not $decision.ShouldRetry) {
                $detail = if ($statusCode -gt 0) { "HTTP $statusCode" } else { 'no HTTP response' }
                throw "GET $uri failed after $attempt attempt(s) ($detail): $($_.Exception.Message)"
            }

            Write-Verbose "GET $uri failed on attempt $attempt. Retrying in $($decision.DelaySeconds)s."
            Start-Sleep -Seconds $decision.DelaySeconds
        }
    }
}

Export-ModuleMember -Function @(
    'Assert-HttpBaseUrl',
    'ConvertFrom-JsonResponse',
    'New-HttpUri',
    'New-BasicAuthorizationHeader',
    'New-BearerAuthorizationHeader',
    'Get-HttpLinkHeaderTarget',
    'Get-HttpResponseHeader',
    'Get-HttpErrorStatusCode',
    'Get-HttpRetryAfterSecond',
    'Get-HttpRetryDecision',
    'Get-HttpRetryableStatusCode',
    'Invoke-ReadOnlyRequest'
)
