@{
    RootModule        = 'GitHubAsCode.Plan.psm1'
    ModuleVersion     = '0.1.0'
    GUID              = 'e88092ae-cd8f-45ab-9916-eba81cd763e1'
    Author            = 'TEMPLATE-AUTHOR'
    CompanyName       = 'Unspecified'
    Copyright         = '(c) TEMPLATE-AUTHOR. Released under the MIT License.'
    Description       = 'The shared plan model: one flat list of operations with a closed action and status vocabulary, and the test that says whether anything in a plan could not be determined.'
    PowerShellVersion = '5.1'

    FunctionsToExport = @(
        'Get-PlanStatusName',
        'Get-PlanActionName',
        'New-Plan',
        'New-PlanOperation',
        'Add-PlanOperation',
        'Get-PlanSummary',
        'Test-PlanBlocked',
        'Write-PlanSummary'
    )
    CmdletsToExport   = @()
    VariablesToExport = @()
    AliasesToExport   = @()

    PrivateData = @{
        PSData = @{
            Tags       = @('Plan', 'InfrastructureAsCode', 'DryRun')
            LicenseUri = 'https://opensource.org/licenses/MIT'
        }
    }
}
