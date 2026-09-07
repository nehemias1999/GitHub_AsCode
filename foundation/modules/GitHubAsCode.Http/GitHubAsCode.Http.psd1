@{
    RootModule        = 'GitHubAsCode.Http.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = '1c8f68e7-cf71-433b-b06f-8ffac2fbae0a'
    Author            = 'nehemias1999'
    CompanyName       = 'Unspecified'
    Copyright         = '(c) 2026 nehemias1999. Released under the MIT License.'
    Description       = 'Read-only HTTP shared by every transport: URL construction, credentials, retry and error translation. It knows nothing about GitHub, and exposes no way to send anything but GET.'
    PowerShellVersion = '5.1'

    FunctionsToExport = @(
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
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData = @{
        PSData = @{
            Tags       = @('Http', 'ReadOnly', 'Retry')
            LicenseUri = 'https://opensource.org/licenses/MIT'
        }
    }
}
