@{
    RootModule        = 'GitHubAsCode.Configuration.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = '002a5020-3889-46fa-9e0d-225ccb314e78'
    Author            = 'nehemias1999'
    CompanyName       = 'Unspecified'
    Copyright         = '(c) 2026 nehemias1999. Released under the MIT License.'
    Description       = 'Loads declared state: .env files into the process environment, JSON configuration validated against the schema it declares, and membership lists resolved from environment variables rather than from Git.'
    PowerShellVersion = '5.1'

    FunctionsToExport = @(
        'Import-GitHubAsCodeEnvironment',
        'Resolve-GitHubAsCodePath',
        'Resolve-GitHubAsCodeDeclaration',
        'Get-GitHubAsCodeDuplicateValue',
        'Get-GitHubAsCodeSchemaEngine',
        'Test-GitHubAsCodeConfiguration',
        'Get-GitHubAsCodeConfiguration',
        'Get-GitHubAsCodeRequiredValue'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData = @{
        PSData = @{
            Tags       = @('Configuration', 'JsonSchema', 'DotEnv')
            LicenseUri = 'https://opensource.org/licenses/MIT'
        }
    }
}
