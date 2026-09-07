@{
    RootModule        = 'GitHub.Rest.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = '89eedb1d-0ea0-4a99-a914-a8df7c0ca35d'
    Author            = 'TEMPLATE-AUTHOR'
    CompanyName       = 'Unspecified'
    Copyright         = '(c) TEMPLATE-AUTHOR. Released under the MIT License.'
    Description       = 'What is GitHub-specific about talking to the REST API: pagination read from the Link header rather than computed, the account listing that includes private repositories, the two kinds of rate limit, and telling a classic token from a fine-grained one. The generic HTTP half is in GitHubAsCode.Http.'
    PowerShellVersion = '5.1'

    RequiredModules   = @('GitHubAsCode.Configuration', 'GitHubAsCode.Http')

    FunctionsToExport = @(
        'Get-GitHubStatusMessage',
        'Get-GitHubContext',
        'Get-GitHubRateLimitState',
        'Get-GitHubTokenShape',
        'Get-GitHubRelativeTarget',
        'Invoke-GitHubRequest',
        'Get-GitHubPagedResult',
        'Get-GitHubOwnedRepository'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData = @{
        PSData = @{
            Tags       = @('GitHub', 'REST', 'ReadOnly')
            LicenseUri = 'https://opensource.org/licenses/MIT'
        }
    }
}
