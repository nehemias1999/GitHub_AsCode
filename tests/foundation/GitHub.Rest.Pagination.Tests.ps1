<#
    Pagination, rate limits and token shape.

    Every test here is named after the failure it prevents, not after the function it
    calls. Pagination gets the most of them because it is the one place on this API
    where a wrong implementation produces a plausible answer instead of an error: a
    short read reports fewer repositories and calls itself complete.
#>

Set-StrictMode -Version Latest

BeforeAll {
    . (Join-Path $PSScriptRoot '../TestHelpers.ps1')
    . (Join-Path (Get-RepositoryRoot) 'foundation/Import-Foundation.ps1')
}

Describe 'Get-HttpLinkHeaderTarget' {

    It 'finds the next page when the header carries more than one link' {
        # The regression this exists for: the comma separating one link from the next
        # landed at the end of the parameter segment, so rel="next", parsed as the
        # value 'next",' - matching no relation. The header then looked like a last
        # page, and a 24-repository account silently reported its first 3.
        $link = Get-FixtureText -Name 'link-header-two-links.txt'

        $next = Get-HttpLinkHeaderTarget -LinkHeader $link -Relation 'next'

        $next | Should -Be 'https://api.example.com/user/repos?per_page=100&affiliation=owner&page=2'
    }

    It 'does not truncate a URL that contains a comma in a query value' {
        # Segmentation is driven by the angle brackets for this reason. Splitting the
        # header on ',' cuts sort=full_name,asc in half and yields a URL that 404s.
        $link = Get-FixtureText -Name 'link-header-comma-in-url.txt'

        $next = Get-HttpLinkHeaderTarget -LinkHeader $link -Relation 'next'

        $next | Should -Be 'https://api.example.com/user/repos?per_page=100&sort=full_name,asc&page=3'
    }

    It 'finds a relation that is not the first one in the header' {
        $link = Get-FixtureText -Name 'link-header-comma-in-url.txt'

        Get-HttpLinkHeaderTarget -LinkHeader $link -Relation 'first' | Should -Be 'https://api.example.com/user/repos?page=1'
        Get-HttpLinkHeaderTarget -LinkHeader $link -Relation 'last' | Should -Be 'https://api.example.com/user/repos?page=9'
    }

    It 'returns nothing for a relation the header does not offer' {
        # This is the loop's only termination condition, so it has to be exact. A
        # function that returned something falsy-but-present here would loop forever;
        # one that threw would turn a finished collection into a failed run.
        $link = Get-FixtureText -Name 'link-header-last-page.txt'

        Get-HttpLinkHeaderTarget -LinkHeader $link -Relation 'next' | Should -BeNullOrEmpty
    }

    It 'treats an absent Link header as no next page rather than an error' {
        # A single-page collection sends no Link header at all. Throwing here would
        # make every small account a failed run.
        Get-HttpLinkHeaderTarget -LinkHeader '' -Relation 'next' | Should -BeNullOrEmpty
        Get-HttpLinkHeaderTarget -LinkHeader $null -Relation 'next' | Should -BeNullOrEmpty
    }

    It 'accepts a bare rel value and one carrying several relations' {
        # RFC 5988 allows rel=next unquoted, and rel="next last" for a link that is
        # both. Requiring quotes would silently stop paginating against a server that
        # omits them.
        Get-HttpLinkHeaderTarget -LinkHeader '<https://api.example.com/x?page=2>; rel=next' -Relation 'next' |
            Should -Be 'https://api.example.com/x?page=2'

        Get-HttpLinkHeaderTarget -LinkHeader '<https://api.example.com/x?page=9>; rel="next last"' -Relation 'last' |
            Should -Be 'https://api.example.com/x?page=9'
    }
}

Describe 'Get-GitHubRelativeTarget' {

    It 'splits an absolute next-page URL into the path and query the transport takes' {
        $target = Get-GitHubRelativeTarget -Url 'https://api.example.com/user/repos?per_page=100&page=2' -BaseUrl 'https://api.example.com'

        $target.Path | Should -Be 'user/repos'
        $target.Query['page'] | Should -Be '2'
        $target.Query['per_page'] | Should -Be '100'
    }

    It 'refuses to follow a Link header that points at another host' {
        # A Link header is server-controlled input. Following it to another host would
        # send the Authorization header there, which is a credential disclosure driven
        # by a response body.
        { Get-GitHubRelativeTarget -Url 'https://attacker.example.net/user/repos' -BaseUrl 'https://api.example.com' } |
            Should -Throw -ExpectedMessage '*Refusing to follow*'
    }

    It 'strips the base path so an Enterprise Server API root does not double up' {
        # On GitHub Enterprise Server the API root is https://host/api/v3, and the
        # next URL repeats that prefix. Keeping it produces /api/v3/api/v3/user/repos.
        $target = Get-GitHubRelativeTarget -Url 'https://ghes.example.com/api/v3/user/repos?page=2' -BaseUrl 'https://ghes.example.com/api/v3'

        $target.Path | Should -Be 'user/repos'
    }

    It 'unescapes a query value rather than passing the escaped form through' {
        # The transport escapes what it is given. Handing it an already-escaped value
        # double-escapes it, and the second page comes back filtered by a literal %20.
        $target = Get-GitHubRelativeTarget -Url 'https://api.example.com/search?q=a%20b' -BaseUrl 'https://api.example.com'

        $target.Query['q'] | Should -Be 'a b'
    }
}

Describe 'Get-GitHubRateLimitState' {

    It 'reads the budget out of the response headers' {
        $headers = New-FixtureHeader -Header @{
            'x-ratelimit-limit'     = '5000'
            'x-ratelimit-remaining' = '4987'
            'x-ratelimit-reset'     = '1788800000'
            'x-ratelimit-resource'  = 'core'
        }

        $state = Get-GitHubRateLimitState -Headers $headers

        $state.Limit | Should -Be 5000
        $state.Remaining | Should -Be 4987
        $state.Resource | Should -Be 'core'
    }

    It 'reads the reset as an epoch second, not as a date' {
        # x-ratelimit-reset is a UNIX timestamp. Parsing it as a date yields 1970 and
        # a report line claiming the limit reset fifty-six years ago.
        $headers = New-FixtureHeader -Header @{ 'x-ratelimit-reset' = '1788800000' }

        $state = Get-GitHubRateLimitState -Headers $headers

        $state.ResetUtc.Year | Should -BeGreaterThan 2020
    }

    It 'reports an absent budget as unknown rather than as zero' {
        # "Unknown" and "none left" must not look alike to a caller deciding whether
        # to keep going: zero would stop a run that had a full budget.
        $state = Get-GitHubRateLimitState -Headers (New-FixtureHeader)

        $state.Remaining | Should -BeNullOrEmpty
        $state.Limit | Should -BeNullOrEmpty
    }
}

Describe 'Get-GitHubTokenShape' {

    It 'recognises a classic token by the scope header it sends' {
        # This is the whole basis of refusing to write with a classic token, so if it
        # is wrong the guarantee is decorative.
        $headers = New-FixtureHeader -Header @{ 'x-oauth-scopes' = 'gist, read:org, repo, workflow' }

        $shape = Get-GitHubTokenShape -Headers $headers

        $shape.IsClassic | Should -BeTrue
        $shape.Scope | Should -Contain 'repo'
        $shape.Scope | Should -Contain 'read:org'
    }

    It 'recognises a fine-grained token by the absence of that header' {
        $shape = Get-GitHubTokenShape -Headers (New-FixtureHeader -Header @{ 'x-ratelimit-limit' = '5000' })

        $shape.IsClassic | Should -BeFalse
        $shape.Scope | Should -BeNullOrEmpty
    }

    It 'reads the expiry header GitHub actually sends, trailing UTC label and all' {
        # The header reads "2026-12-31 23:59:59 UTC", which no single standard parse
        # handles. Getting this wrong loses the only warning available before a
        # scheduled run starts failing with a 401 that looks like revocation.
        $headers = New-FixtureHeader -Header @{ 'github-authentication-token-expiration' = '2099-12-31 23:59:59 UTC' }

        $shape = Get-GitHubTokenShape -Headers $headers

        $shape.ExpiresUtc.Year | Should -Be 2099
        $shape.DaysUntilExpiry | Should -BeGreaterThan 0
    }

    It 'reports a past expiry as a negative number of days rather than clamping to zero' {
        # A token that expired yesterday and one expiring today need different
        # messages, and clamping makes them identical.
        $headers = New-FixtureHeader -Header @{ 'github-authentication-token-expiration' = '2020-01-01 00:00:00 UTC' }

        (Get-GitHubTokenShape -Headers $headers).DaysUntilExpiry | Should -BeLessThan 0
    }
}

Describe 'Get-GitHubStatusMessage' {

    It 'explains that a GitHub 404 is ambiguous' {
        # The guides and Get-GitHubRepositoryStatus both rest on this: absence and
        # no-permission are the same status code, so a 404 is never read as absence.
        (Get-GitHubStatusMessage)[404] | Should -Match 'cannot see it'
    }

    It 'separates the three causes that share a 403' {
        # Missing permission, exhausted primary limit and tripped secondary limit all
        # answer 403. A message naming one of them sends the reader to the wrong fix.
        $message = (Get-GitHubStatusMessage)[403]

        $message | Should -Match 'permission'
        $message | Should -Match 'rate limit'
        $message | Should -Match 'secondary'
    }

    It 'returns a copy, so a caller cannot edit the shared map' {
        $first = Get-GitHubStatusMessage
        $first[404] = 'mutated'

        (Get-GitHubStatusMessage)[404] | Should -Not -Be 'mutated'
    }
}
