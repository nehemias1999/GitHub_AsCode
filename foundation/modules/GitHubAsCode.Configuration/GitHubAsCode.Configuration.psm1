<#
    GitHubAsCode.Configuration - loading and validating declared state.

    Two jobs, both boring on purpose:

    * Read environment variables from a .env file into the process, so a value
      lives on a workstation or in a pipeline secret rather than in Git.
    * Read a JSON configuration file and validate it against the schema it points
      at, before any network call happens.

    The second job closes a gap worth naming. Shipping a JSON Schema next to a
    configuration file and never running it is common and worthless: the schema
    documents an intention while the loader accepts anything. Here `validate`
    actually validates, and it does so offline, so a malformed catalogue fails in
    a second instead of halfway through an apply.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Environment variables a .env file may never set. Each one changes where the
# interpreter finds code or executables, so allowing a configuration file to set it
# turns .env into a code execution path. Compared case-insensitively, because the
# Windows environment is case-insensitive.
$script:ProtectedEnvironmentNames = @(
    'PSModulePath'
    'PSExecutionPolicyPreference'
    'PSHOME'
    'Path'
    'PATHEXT'
    'ComSpec'
    'DOTNET_STARTUP_HOOKS'
    'DOTNET_ADDITIONAL_DEPS'
    'COREHOST_TRACE'
    'LD_PRELOAD'
    'LD_LIBRARY_PATH'
)

function Import-GitHubAsCodeEnvironment {
    <#
    .SYNOPSIS
        Loads KEY=VALUE pairs from one or more .env files into the process
        environment.

    .DESCRIPTION
        Accepts several paths, and splits each on commas, because the pipeline
        definitions pass a list as one parameter value. A missing file is an error
        rather than a silent skip: a run that quietly proceeds without its
        credentials fails later with a confusing message.

        Blank lines and lines starting with '#' are ignored. Surrounding single or
        double quotes are stripped so a value with trailing spaces can be expressed.

        A variable name must match ^[A-Za-z_][A-Za-z0-9_]*$, and a small set of names
        that steer the interpreter - PSModulePath, Path, PSExecutionPolicyPreference,
        DOTNET_STARTUP_HOOKS and similar - is refused outright. A .env file is
        operator-edited, unsigned and unhashed, and this function writes what it names
        into the process environment, so without that constraint the file is a code
        execution path: PSModulePath=\somewhere\share would be honoured by the next
        Import-Module. Both refusals throw rather than skip, because a silently ignored
        line in a credential file is how a run proceeds without the credential it
        needed.

    .PARAMETER Path
        One or more file paths. A single value may contain comma-separated paths.

    .PARAMETER Optional
        Skip a path that does not exist instead of failing. Intended for a members
        file that only some modules use.

    .EXAMPLE
        Import-GitHubAsCodeEnvironment -Path '.env'

    .EXAMPLE
        Import-GitHubAsCodeEnvironment -Path '.env,automations/team-provisioning/config/members.env'

    .OUTPUTS
        The names of the variables that were set.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)] [string[]] $Path,
        [switch] $Optional
    )

    $resolvedPaths = @(
        $Path |
            ForEach-Object { $_ -split ',' } |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )

    $names = New-Object System.Collections.Generic.List[string]

    foreach ($file in $resolvedPaths) {
        if (-not (Test-Path -LiteralPath $file)) {
            if ($Optional) {
                Write-Verbose "Environment file not found, skipped: $file"
                continue
            }
            throw "Environment file not found: $file. Run scripts/bootstrap.ps1 to create it from .env.example."
        }

        foreach ($line in (Get-Content -LiteralPath $file)) {
            if ($line -notmatch '^\s*([^#=\s][^=]*)=(.*)$') { continue }

            $name = $Matches[1].Trim()
            $value = $Matches[2].Trim()

            # A .env file is operator-edited, unsigned and unhashed, and this loop
            # writes whatever it names straight into the process environment. Without
            # a constraint on the name that is a code execution path, not just a
            # configuration one: a line reading
            #
            #     PSModulePath=\somewhere\share
            #
            # is applied verbatim, and the next Import-Module in
            # foundation/Import-Foundation.ps1 resolves modules from it.
            #
            # So the name has to look like an environment variable, and must not be
            # one that steers the interpreter.
            if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
                throw "Invalid variable name '$name' in '$file'. A name must start with a letter or underscore and contain only letters, digits and underscores."
            }
            if ($script:ProtectedEnvironmentNames -contains $name) {
                throw "Refusing to set '$name' from '$file'. That variable controls how PowerShell loads code or resolves executables, so a configuration file is not allowed to change it."
            }
            if ($value.Length -ge 2) {
                $first = $value[0]
                $last = $value[$value.Length - 1]
                if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
            }

            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
            $names.Add($name)
        }
    }

    return @($names.ToArray())
}

function Resolve-GitHubAsCodePath {
    <#
    .SYNOPSIS
        Turns a repository-relative path into an absolute one.

    .DESCRIPTION
        Every entry point accepts path overrides, and a relative path has to mean
        the same thing whether the command was launched from the repository root, a
        module folder, or a build agent working directory.

    .PARAMETER Path
        Absolute or relative path.

    .PARAMETER RootPath
        Base for relative paths, normally the repository root.

    .EXAMPLE
        Resolve-GitHubAsCodePath -Path 'foundation/config/project-context.json' -RootPath $repoRoot
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $RootPath
    )

    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return [System.IO.Path]::GetFullPath((Join-Path $RootPath $Path))
}

function Resolve-GitHubAsCodeDeclaration {
    <#
    .SYNOPSIS
        Decides which declaration file a run will read.

    .DESCRIPTION
        The rule is the same for every automation: an explicit path wins; otherwise
        the active declaration if it exists; otherwise the versioned template. It was
        written out three times, character for character, in the three entry points -
        and this repository's own rule says that what all of them need belongs in the
        shared layer, because a rule implemented three times is a rule that drifts.

        The fallback to the template is what keeps validate runnable in a fresh clone,
        where the active file does not exist yet. It is returned rather than logged
        here, because this module knows nothing about how a caller reports - but a
        caller must report it, so that a run never silently checks the template while
        the operator believes it checked their own declaration. Callers say it out loud AND record
        it, since a plan built from the template describes an example and not an
        estate.

    .PARAMETER ProjectContext
        Parsed project context, whose automations section names both paths.

    .PARAMETER Module
        Automation name, used to look itself up in that section.

    .PARAMETER RepositoryRoot
        Root that relative paths resolve against.

    .PARAMETER ConfigurationPath
        Explicit override. When given, nothing else is consulted.

    .EXAMPLE
        $declaration = Resolve-GitHubAsCodeDeclaration -ProjectContext $ctx -Module 'job-inventory' -RepositoryRoot $root

    .OUTPUTS
        An object with Path and UsedTemplate.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $ProjectContext,
        [Parameter(Mandatory)] [string] $Module,
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [AllowEmptyString()] [string] $ConfigurationPath
    )

    if ($ConfigurationPath) {
        return [pscustomobject]@{
            Path         = Resolve-GitHubAsCodePath -Path $ConfigurationPath -RootPath $RepositoryRoot
            UsedTemplate = $false
            ActivePath   = ''
        }
    }

    $moduleContext = $ProjectContext.automations.$Module
    $active = Resolve-GitHubAsCodePath -Path $moduleContext.configuration -RootPath $RepositoryRoot
    if (Test-Path -LiteralPath $active) {
        return [pscustomobject]@{ Path = $active; UsedTemplate = $false; ActivePath = $active }
    }

    return [pscustomobject]@{
        Path         = Resolve-GitHubAsCodePath -Path $moduleContext.template -RootPath $RepositoryRoot
        UsedTemplate = $true
        ActivePath   = $active
    }
}

function Get-GitHubAsCodeDuplicateValue {
    <#
    .SYNOPSIS
        Returns the values that appear more than once.

    .DESCRIPTION
        Duplicate keys in a declaration are checked six times across the three entry
        points with the same three-stage pipeline each time. Two entries for one
        resource would each report their own verdict about it, which is why every
        automation checks - and why the check belongs in one place.

    .PARAMETER Value
        Values to inspect. An empty input yields nothing rather than failing.

    .EXAMPLE
        Get-GitHubAsCodeDuplicateValue -Value @('a', 'b', 'a')

        a

    .OUTPUTS
        Each duplicated value, once.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyCollection()] [object[]] $Value
    )

    if ($Value.Count -eq 0) { return @() }
    return @($Value | Group-Object | Where-Object { $_.Count -gt 1 } | ForEach-Object { $_.Name })
}
function Get-GitHubAsCodeSchemaEngine {
    <#
    .SYNOPSIS
        Names the schema validator this session will use.

    .DESCRIPTION
        Two engines exist and they are not equivalent. Test-Json -Schema is real
        JSON Schema validation and needs PowerShell 6.1 or later. On Windows
        PowerShell 5.1 - the declared support floor - a reduced validator written
        here runs instead, and it ignores pattern, minimum, minLength, minItems,
        uniqueItems, format and the oneOf family. A declaration that violates any of
        those passes validate on 5.1 and fails later, usually at the first request.

        Detected by capability rather than by version, so a future host that gains
        the parameter is not misclassified by a version comparison.

        Exported because the difference belongs in the report and in what an
        operator is told. Before this, a run on 5.1 said "Valid." with no
        qualification, which is the one place the difference actually mattered.

    .EXAMPLE
        Get-GitHubAsCodeSchemaEngine

        Test-Json

    .OUTPUTS
        'Test-Json' for the full engine, 'reduced' for the 5.1 one.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $supportsSchema = (Get-Command Test-Json -ErrorAction SilentlyContinue) -and
                      (Get-Command Test-Json).Parameters.ContainsKey('Schema')
    if ($supportsSchema) { return 'Test-Json' }
    return 'reduced'
}
function Test-GitHubAsCodeConfiguration {
    <#
    .SYNOPSIS
        Validates a JSON document against a JSON Schema file.

    .DESCRIPTION
        Uses Test-Json -Schema where the host provides it (PowerShell 6.1 and
        later). Windows PowerShell 5.1 has no schema support, so a reduced check
        runs instead - see Test-JsonAgainstSchemaNode for exactly what it covers.
        The reduced engine is weaker than real validation, and the return value
        names the engine that ran so a caller can report it honestly instead of
        implying full coverage.

    .PARAMETER Json
        The JSON document as text.

    .PARAMETER SchemaPath
        Path to the schema file.

    .EXAMPLE
        Test-GitHubAsCodeConfiguration -Json $raw -SchemaPath $schemaPath

    .OUTPUTS
        PSCustomObject with isValid, errors and engine.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)] [string] $Json,
        [Parameter(Mandatory)] [string] $SchemaPath
    )

    if (-not (Test-Path -LiteralPath $SchemaPath)) {
        return [pscustomobject]@{
            isValid = $false
            errors  = @("Schema file not found: $SchemaPath")
            engine  = 'none'
        }
    }

    $schemaText = Get-Content -LiteralPath $SchemaPath -Raw
    $supportsSchema = (Get-Command Test-Json -ErrorAction SilentlyContinue) -and
                      (Get-Command Test-Json).Parameters.ContainsKey('Schema')

    if ($supportsSchema) {
        $errors = New-Object System.Collections.Generic.List[string]
        try {
            Test-Json -Json $Json -Schema $schemaText -ErrorAction Stop | Out-Null
        }
        catch {
            $errors.Add("$($_.Exception.Message)")
        }
        return [pscustomobject]@{
            isValid = $errors.Count -eq 0
            errors  = @($errors.ToArray())
            engine  = 'Test-Json'
        }
    }

    $schema = $schemaText | ConvertFrom-Json
    $document = $Json | ConvertFrom-Json
    $errors = @(Test-JsonAgainstSchemaNode -Node $document -Schema $schema -RootSchema $schema -Path '$')

    return [pscustomobject]@{
        isValid = $errors.Count -eq 0
        errors  = @($errors)
        engine  = 'reduced'
    }
}

function Test-JsonAgainstSchemaNode {
    <#
    .SYNOPSIS
        Reduced schema check for hosts without Test-Json -Schema.

    .DESCRIPTION
        Recursive, and deliberately partial: it covers type, required, properties,
        additionalProperties (both the boolean and the schema form), items, enum,
        const, and local $ref pointers into $defs. It does not implement composition
        keywords, string patterns or numeric bounds. Anything it cannot check it
        ignores rather than guessing, so it never rejects a valid document.

    .PARAMETER Node
        Value being validated.

    .PARAMETER Schema
        Schema node.

    .PARAMETER RootSchema
        The whole schema document, used to resolve local $ref pointers.

    .PARAMETER Path
        JSON pointer-ish path used in messages.

    .EXAMPLE
        Test-JsonAgainstSchemaNode -Node $document -Schema $schema -RootSchema $schema -Path '$'
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)] [AllowNull()] [object] $Node,
        [Parameter(Mandatory)] [AllowNull()] [object] $Schema,
        [Parameter(Mandatory)] [AllowNull()] [object] $RootSchema,
        [Parameter(Mandatory)] [string] $Path
    )

    $errors = New-Object System.Collections.Generic.List[string]
    if ($null -eq $Schema) { return @() }

    # Local $ref support. Without it, a schema that factors its item definitions
    # into $defs - which every schema in this repository does - would validate
    # nothing below the reference, and a reduced validator that silently checks
    # less than it appears to is worse than none.
    $schemaProperties = @($Schema.PSObject.Properties.Name)
    if ($schemaProperties -contains '$ref') {
        $resolved = Resolve-JsonSchemaReference -Reference "$($Schema.'$ref')" -RootSchema $RootSchema
        if ($null -eq $resolved) {
            return @("$Path references '$($Schema.'$ref')', which could not be resolved in this schema")
        }
        return @(Test-JsonAgainstSchemaNode -Node $Node -Schema $resolved -RootSchema $RootSchema -Path $Path)
    }

    if ($schemaProperties -contains 'type') {
        $expectedTypes = @($Schema.type)
        $actualType = Get-JsonNodeType -Node $Node
        if ($actualType -and ($expectedTypes -notcontains $actualType)) {
            # 'integer' satisfies a schema asking for 'number'.
            $numberIsFine = ($actualType -eq 'integer' -and $expectedTypes -contains 'number')
            if (-not $numberIsFine) {
                $errors.Add("$Path expected type $($expectedTypes -join '|') but found $actualType")
                return @($errors.ToArray())
            }
        }
    }

    if ($schemaProperties -contains 'const') {
        if ("$Node" -cne "$($Schema.const)") {
            $errors.Add("$Path must be '$($Schema.const)' but is '$Node'")
        }
    }

    if ($schemaProperties -contains 'enum') {
        $allowed = @($Schema.enum)
        if ($null -ne $Node -and $allowed -notcontains $Node) {
            $errors.Add("$Path value '$Node' is not one of: $($allowed -join ', ')")
        }
    }

    if ($schemaProperties -contains 'required' -and $Node -is [pscustomobject]) {
        $present = @($Node.PSObject.Properties.Name)
        foreach ($required in @($Schema.required)) {
            if ($present -notcontains "$required") {
                $errors.Add("$Path is missing required property '$required'")
            }
        }
    }

    if ($schemaProperties -contains 'properties' -and $Node -is [pscustomobject]) {
        $declared = @($Schema.properties.PSObject.Properties.Name)
        foreach ($property in $Node.PSObject.Properties) {
            if ($declared -notcontains $property.Name) {
                if ($schemaProperties -contains 'additionalProperties') {
                    if ($Schema.additionalProperties -is [bool]) {
                        if (-not $Schema.additionalProperties) {
                            $errors.Add("$Path has undeclared property '$($property.Name)'")
                        }
                    }
                    else {
                        $errors.AddRange([string[]]@(Test-JsonAgainstSchemaNode `
                            -Node $property.Value `
                            -Schema $Schema.additionalProperties `
                            -RootSchema $RootSchema `
                            -Path "$Path.$($property.Name)"))
                    }
                }
                continue
            }
            $errors.AddRange([string[]]@(Test-JsonAgainstSchemaNode `
                -Node $property.Value `
                -Schema $Schema.properties.($property.Name) `
                -RootSchema $RootSchema `
                -Path "$Path.$($property.Name)"))
        }
    }

    if ($schemaProperties -contains 'items' -and $Node -is [System.Collections.IEnumerable] -and $Node -isnot [string]) {
        $index = 0
        foreach ($item in $Node) {
            $errors.AddRange([string[]]@(Test-JsonAgainstSchemaNode -Node $item -Schema $Schema.items -RootSchema $RootSchema -Path "$Path[$index]"))
            $index++
        }
    }

    return @($errors.ToArray())
}

function Resolve-JsonSchemaReference {
    <#
    .SYNOPSIS
        Resolves a local JSON Schema $ref pointer against the schema document.

    .DESCRIPTION
        Handles the one form this repository uses: '#/$defs/name', optionally
        nested. An external or remote reference returns $null, and the caller
        reports that rather than pretending to have validated it.

    .PARAMETER Reference
        The $ref value.

    .PARAMETER RootSchema
        The whole schema document.

    .EXAMPLE
        Resolve-JsonSchemaReference -Reference '#/$defs/application' -RootSchema $schema
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Reference,
        [Parameter(Mandatory)] [AllowNull()] [object] $RootSchema
    )

    if ($null -eq $RootSchema) { return $null }
    if (-not $Reference.StartsWith('#/')) { return $null }

    $node = $RootSchema
    foreach ($segment in @($Reference.Substring(2) -split '/' | Where-Object { $_ })) {
        $name = $segment.Replace('~1', '/').Replace('~0', '~')
        if ($null -eq $node -or $node.PSObject.Properties.Name -notcontains $name) { return $null }
        $node = $node.$name
    }
    return $node
}

function Get-JsonNodeType {
    <#
    .SYNOPSIS
        Maps a deserialized value to its JSON Schema type name.

    .PARAMETER Node
        Value to classify.

    .EXAMPLE
        Get-JsonNodeType -Node 42
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [AllowNull()] [object] $Node
    )

    if ($null -eq $Node) { return 'null' }
    if ($Node -is [bool]) { return 'boolean' }
    if ($Node -is [string]) { return 'string' }
    if ($Node -is [int] -or $Node -is [long]) { return 'integer' }
    if ($Node -is [double] -or $Node -is [decimal]) { return 'number' }
    if ($Node -is [System.Collections.IEnumerable]) { return 'array' }
    if ($Node -is [pscustomobject]) { return 'object' }
    return $null
}

function Get-GitHubAsCodeConfiguration {
    <#
    .SYNOPSIS
        Reads a JSON configuration file and validates it against its schema.

    .DESCRIPTION
        The configuration files declare their own schema through a relative
        `$schema` property, which keeps the pairing next to the data instead of in a
        lookup table that drifts. This function resolves that relative reference
        against the file's own folder, validates, and throws with every error
        listed - not just the first, since a half-corrected file costs another round
        trip.

    .PARAMETER Path
        Path to the configuration file.

    .PARAMETER SchemaPath
        Explicit schema path, overriding the `$schema` property.

    .PARAMETER SkipValidation
        Return the parsed document without validating. Intended for the reader in a
        test fixture, not for production paths.

    .EXAMPLE
        $projectContext = Get-GitHubAsCodeConfiguration -Path 'foundation/config/project-context.json'

    .OUTPUTS
        The parsed configuration object.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [string] $SchemaPath,
        [switch] $SkipValidation
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Configuration file not found: $Path. If the repository ships a template, copy it by renaming the .example file."
    }

    $raw = Get-Content -LiteralPath $Path -Raw
    try {
        $document = $raw | ConvertFrom-Json
    }
    catch {
        throw "Configuration file '$Path' is not valid JSON: $($_.Exception.Message)"
    }

    if ($SkipValidation) { return $document }

    if (-not $SchemaPath) {
        if ($document.PSObject.Properties.Name -notcontains '$schema') {
            throw "Configuration file '$Path' does not declare a `$schema property, and no -SchemaPath was supplied. Every configuration file must point at the schema that governs it."
        }
        $reference = "$($document.'$schema')"
        $SchemaPath = Resolve-GitHubAsCodePath -Path $reference -RootPath (Split-Path -Parent (Resolve-Path -LiteralPath $Path).Path)
    }

    $result = Test-GitHubAsCodeConfiguration -Json $raw -SchemaPath $SchemaPath
    if (-not $result.isValid) {
        $detail = ($result.errors | ForEach-Object { "  - $_" }) -join [Environment]::NewLine
        throw "Configuration file '$Path' does not satisfy its schema ($($result.engine) validation):$([Environment]::NewLine)$detail"
    }

    Write-Verbose "Validated '$Path' against '$SchemaPath' using $($result.engine) validation."
    return $document
}

function Get-GitHubAsCodeRequiredValue {
    <#
    .SYNOPSIS
        Reads a process environment variable named by the configuration, and fails
        with a usable message when it is not set.

    .DESCRIPTION
        The configuration declares the NAME of every value the automations need;
        this function turns a name into the value. It lives in the cross-cutting
        layer rather than in a transport module on purpose: there are two
        transports here (REST and GraphQL), and a rule implemented twice is a rule
        that drifts.

        It knows nothing about what the value is for, so it can never leak one into
        a message: the failure names the variable, never its content.

    .PARAMETER Name
        Name of the environment variable to read.

    .EXAMPLE
        Get-GitHubAsCodeRequiredValue -Name 'GITHUB_TOKEN_READ'

    .OUTPUTS
        The value, with surrounding whitespace removed.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)] [string] $Name
    )

    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Required environment variable '$Name' is not set. Add it to .env (see .env.example), or export it before running."
    }

    # Trimmed because these values are typed by hand into a local file, and a
    # trailing space on a base URL or a token produces a 401 that reads like bad
    # credentials rather than like a typo.
    return $value.Trim()
}

Export-ModuleMember -Function @(
    'Import-GitHubAsCodeEnvironment',
    'Resolve-GitHubAsCodePath',
    'Resolve-GitHubAsCodeDeclaration',
    'Get-GitHubAsCodeDuplicateValue',
    'Get-GitHubAsCodeSchemaEngine',
    'Test-GitHubAsCodeConfiguration',
    'Get-GitHubAsCodeConfiguration',
    'Get-GitHubAsCodeRequiredValue'
)
