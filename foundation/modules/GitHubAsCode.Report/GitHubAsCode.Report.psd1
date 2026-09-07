@{
    RootModule        = 'GitHubAsCode.Report.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = 'ff6a88d0-6319-44db-b1f4-d14acbdc60e6'
    Author            = 'nehemias1999'
    CompanyName       = 'Unspecified'
    Copyright         = '(c) 2026 nehemias1999. Released under the MIT License.'
    Description       = 'Evidence writing: plan reports as JSON and Markdown, with redaction by property name and masking by value applied at the writer rather than at each call site.'
    PowerShellVersion = '5.1'

    RequiredModules   = @('GitHubAsCode.Plan', 'GitHubAsCode.Configuration')

    FunctionsToExport = @(
        'Protect-SecretInText',
        'Remove-SensitiveValue',
        'Get-GitHubAsCodeReportPath',
        'Start-GitHubAsCodeRunLog',
        'Add-GitHubAsCodeRunLogLine',
        'Get-GitHubAsCodeProvenance',
        'Write-GitHubAsCodeReport',
        'Format-GitHubAsCodeReportMarkdown'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData = @{
        PSData = @{
            Tags       = @('Reporting', 'Evidence', 'Redaction')
            LicenseUri = 'https://opensource.org/licenses/MIT'
        }
    }
}
