@{
    RootModule        = 'GitHub.Repository.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = '687a2932-2630-4ca4-ac87-b59889edd65d'
    Author            = 'TEMPLATE-AUTHOR'
    CompanyName       = 'Unspecified'
    Copyright         = '(c) TEMPLATE-AUTHOR. Released under the MIT License.'
    Description       = 'The repository rules as pure functions: the topic union that stops a replace-the-whole-collection API destroying undeclared topics, the snapshot the inventory reports, and the comparison that decides an action and a status. No network access, because drift is defined against the payload that would be sent.'
    PowerShellVersion = '5.1'

    RequiredModules   = @('GitHubAsCode.Plan')

    FunctionsToExport = @(
        'Get-GitHubSnapshotProperty',
        'Format-GitHubTopicName',
        'Get-GitHubTopicUnion',
        'New-GitHubRepositorySnapshot',
        'Get-GitHubRepositoryStatus',
        'Get-GitHubUndeclaredStatus'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData = @{
        PSData = @{
            Tags       = @('GitHub', 'Repository', 'PureFunctions')
            LicenseUri = 'https://opensource.org/licenses/MIT'
        }
    }
}
