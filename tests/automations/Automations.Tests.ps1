<#
    The automation contract, enforced.

    A contract nothing checks is a wish. These tests are the mechanical half of
    docs/reference/automation-contract.md: what a module must ship, what a command
    surface must expose, and - the rules this repository rests on - that no code path
    exists which could write to GitHub, delete anything, or read the account listing
    from the endpoint that hides private repositories.

    Read from the PARSE TREE, not from the text, wherever the question is about code.
    A grep for a forbidden word matches prose and misses a variable: -Method $verb is
    exactly how a write would actually arrive, and no amount of string matching sees
    it.
#>

# The automation table drives -ForEach, which Pester evaluates during discovery -
# before BeforeAll has run. Declared here for that reason; a table built in BeforeAll
# is still null when the cases are expanded, and Pester reports it as an empty ForEach.
BeforeDiscovery {
    . (Join-Path $PSScriptRoot '../TestHelpers.ps1')

    $script:Automation = @(
        @{ Name = 'repo-inventory'; EntryPoint = 'Invoke-RepositoryInventory.ps1'; Template = 'repositories.example.json'; Active = 'repositories.json' }
    )
}

BeforeAll {
    . (Join-Path $PSScriptRoot '../TestHelpers.ps1')
    $script:RepositoryRoot = Get-RepositoryRoot
    $script:ModuleSource = @(Get-ChildItem (Join-Path $script:RepositoryRoot 'foundation/modules') -Recurse -Filter '*.psm1')
    $script:EntryPointSource = @(Get-ChildItem (Join-Path $script:RepositoryRoot 'automations') -Recurse -Filter 'Invoke-*.ps1')
    $script:AllSource = @($script:ModuleSource + $script:EntryPointSource)

    function Get-SourceAst {
        <#
        .SYNOPSIS
            Parses one source file and fails the test if it does not parse.
        .PARAMETER Path
            File to parse.
        #>
        [CmdletBinding()]
        param([Parameter(Mandatory)] [string] $Path)

        $parseError = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref] $null, [ref] $parseError)
        if (@($parseError).Where({ $_ }).Count -gt 0) {
            throw "$(Split-Path -Leaf $Path) does not parse, so no guard below could have examined it."
        }
        return $ast
    }
}

Describe 'Every automation ships what the contract requires' {

    It 'has an entry point, a versioned template, a schema and a guide: <Name>' -ForEach $script:Automation {
        $root = Join-Path (Get-RepositoryRoot) "automations/$Name"
        Test-Path -LiteralPath (Join-Path $root $EntryPoint) | Should -BeTrue
        Test-Path -LiteralPath (Join-Path $root "config/$Template") | Should -BeTrue
        Test-Path -LiteralPath (Join-Path $root 'README.md') | Should -BeTrue
        @(Get-ChildItem (Join-Path $root 'schemas') -Filter '*.schema.json').Count | Should -BeGreaterThan 0
    }

    It 'excludes its active configuration from version control: <Name>' -ForEach $script:Automation {
        # The active file is produced by renaming the template. Renaming rather than
        # copying is not a style preference: the active name is the one .gitignore
        # excludes, and a copy invites a file with a new name, full of real values,
        # that Git happily tracks. On an account with private repositories the names
        # alone are worth excluding - that a given private repository exists is not
        # public.
        $ignored = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) '.gitignore') -Raw
        $ignored | Should -Match ([regex]::Escape($Active))
    }

    It 'declares the schema that governs its template: <Name>' -ForEach $script:Automation {
        # Shipping a schema and never running it is common and worthless: the schema
        # documents an intention while the loader accepts anything, and the two drift
        # apart with nobody noticing.
        $template = Join-Path (Get-RepositoryRoot) "automations/$Name/config/$Template"
        $document = Get-Content -LiteralPath $template -Raw | ConvertFrom-Json
        $document.PSObject.Properties.Name | Should -Contain '$schema'
    }

    It 'is registered in the project context: <Name>' -ForEach $script:Automation {
        $context = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) 'foundation/config/project-context.json') -Raw | ConvertFrom-Json
        $context.automations.PSObject.Properties.Name | Should -Contain $Name
    }

    It 'documents rollback in its guide: <Name>' -ForEach $script:Automation {
        # The section people skip and the one that matters at two in the morning. It
        # is allowed to say "nothing to reverse, and here is why" - it is not allowed
        # to be absent.
        $guide = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) "automations/$Name/README.md") -Raw
        $guide | Should -Match '(?i)rollback'
    }
}

Describe 'The command surface is the ladder the contract describes' {

    It 'exposes validate, inventory, plan and smoke: <Name>' -ForEach $script:Automation {
        $source = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) "automations/$Name/$EntryPoint") -Raw
        foreach ($verb in @('validate', 'inventory', 'plan', 'smoke')) {
            $source | Should -Match ("ValidateSet\([^)]*'" + $verb + "'")
        }
    }

    It 'exposes no verb that writes: <Name>' -ForEach $script:Automation {
        # apply, reconcile and rename are absent by construction, not by convention.
        # Phase 3 adds apply to repo-metadata, and when it does this case moves to
        # asserting the confirmation switch instead of the verb's absence - which is
        # a deliberate, reviewed change to this file, not a quiet edit somewhere else.
        $source = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) "automations/$Name/$EntryPoint") -Raw
        $validateSet = [regex]::Match($source, "ValidateSet\((?<set>[^)]*)\)").Groups['set'].Value
        foreach ($verb in @('apply', 'reconcile', 'rename', 'delete', 'remove', 'archive')) {
            $validateSet | Should -Not -Match ("'" + $verb + "'")
        }
    }

    It 'has no confirmation switch, because nothing here needs confirming: <Name>' -ForEach $script:Automation {
        # A -ConfirmApply parameter on a module that cannot write would be a promise
        # of a capability that does not exist, and the first thing somebody reaches
        # for when they want one.
        $source = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) "automations/$Name/$EntryPoint") -Raw
        $source | Should -Not -Match '\$ConfirmApply'
    }
}

Describe 'No code path can write to GitHub' {

    It 'performs network I/O in exactly one place' {
        # One function does the requesting, so there is one place to audit and one
        # place a write could ever be added. This matters more here than in the
        # read-only sibling: this repository is going to grow a writer, and when it
        # does, the audit surface must still be one file.
        #
        # Read from the parse tree, not from the raw text. The inherited version of
        # this test matched the file CONTENT, so it fired on any file that merely
        # mentioned Invoke-WebRequest - including GitHub.Rest.psm1, whose header
        # comment explains that the generic transport half lives elsewhere. A guard
        # that fails on prose gets "fixed" by deleting the prose, which is the
        # opposite of what is wanted here: the comment explaining the boundary is
        # part of how the boundary survives.
        $callers = @($script:AllSource | Where-Object {
            $ast = Get-SourceAst -Path $_.FullName
            @($ast.FindAll({
                $args[0] -is [System.Management.Automation.Language.CommandAst] -and
                "$($args[0].GetCommandName())" -in 'Invoke-WebRequest', 'Invoke-RestMethod'
            }, $true)).Count -gt 0
        })
        @($callers | ForEach-Object { $_.Name }) | Should -Be @('GitHubAsCode.Http.psm1')
    }

    It 'sends no HTTP method other than GET' {
        # Asks whether the word Method appears as a parameter or as a hashtable key at
        # all. In a repository whose HTTP layer has no -Method parameter by design,
        # the answer has to be no, whatever the value would have been. A text search
        # for '-Method Post' cannot see -Method $verb, which is how this would
        # actually arrive.
        foreach ($file in $script:AllSource) {
            $ast = Get-SourceAst -Path $file.FullName

            $asParameter = @($ast.FindAll({
                $args[0] -is [System.Management.Automation.Language.CommandParameterAst] -and
                $args[0].ParameterName -eq 'Method'
            }, $true))
            $asParameter.Count | Should -Be 0 -Because "$($file.Name) must not pass -Method to anything"

            # Method = ... inside a splat or a request hashtable. The key is allowed
            # to exist, because the HTTP layer sets it to Get explicitly rather than
            # relying on a default - but only ever to that, as a literal. A variable
            # there is the failure this guard exists for.
            $methodKeys = @($ast.FindAll({
                $args[0] -is [System.Management.Automation.Language.HashtableAst]
            }, $true) | ForEach-Object { $_.KeyValuePairs } |
                Where-Object { "$($_.Item1.Extent.Text)".Trim("'", '"') -eq 'Method' })
            foreach ($pair in $methodKeys) {
                $value = $pair.Item2
                while ($true) {
                    if ($value -is [System.Management.Automation.Language.PipelineAst]) { $value = @($value.PipelineElements)[0]; continue }
                    if ($value -is [System.Management.Automation.Language.CommandExpressionAst]) { $value = $value.Expression; continue }
                    break
                }
                $value | Should -BeOfType [System.Management.Automation.Language.StringConstantExpressionAst] `
                    -Because "$($file.Name) line $($pair.Item1.Extent.StartLineNumber) sets Method to something that is not a literal"
                $value.Value | Should -Be 'Get' -Because "$($file.Name) line $($pair.Item1.Extent.StartLineNumber) sets Method to a verb other than Get"
            }
        }
    }

    It 'contains no DELETE, in any spelling, anywhere' {
        # Held separately from the method guard above and kept after phase 3 widens
        # that one. DELETE is the method this repository never acquires: not for a
        # repository, not for a label, not for a topic, not for a project field.
        # Every one of those destroys something whose blast radius is not in the plan.
        foreach ($file in $script:AllSource) {
            $ast = Get-SourceAst -Path $file.FullName

            $literals = @($ast.FindAll({
                $args[0] -is [System.Management.Automation.Language.StringConstantExpressionAst]
            }, $true) | ForEach-Object { $_.Value })

            foreach ($literal in $literals) {
                $literal | Should -Not -Match '^(?i)delete$' -Because "$($file.Name) contains the literal string 'delete'"
            }
        }
    }

    It 'never names the delete_repo scope, because that token must not exist' {
        # The recommendation is a fine-grained token, and the reason is structural:
        # there is NO fine-grained permission equivalent to deleting a repository, so
        # a fine-grained token cannot delete one at all. The only place this string
        # is allowed is documentation explaining that, and a warning that refuses to
        # proceed when a classic token holds it.
        foreach ($file in $script:AllSource) {
            $ast = Get-SourceAst -Path $file.FullName

            $used = @($ast.FindAll({
                $args[0] -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
                $args[0].Value -eq 'delete_repo'
            }, $true))

            # The entry point compares against it to warn. Nothing else may mention it.
            if ($file.Name -ne 'Invoke-RepositoryInventory.ps1') {
                $used.Count | Should -Be 0 -Because "$($file.Name) names the delete_repo scope"
            }
        }
    }

    It 'never mutates visibility, archived state or template status' {
        # These are the PATCH /repos fields that look like any other field and are
        # not. private detaches the fork network and disables Pages, irreversibly in
        # the sense that matters; archived makes every later write fail. A generic
        # writer that PATCHes whatever the configuration hands it would reach them.
        $forbidden = @('private', 'visibility', 'archived', 'is_template', 'default_branch')

        foreach ($file in $script:AllSource) {
            $ast = Get-SourceAst -Path $file.FullName

            # A body hashtable is what a PATCH is built from, so a forbidden field as
            # a hashtable KEY is the shape to catch. Reading one is fine - the
            # snapshot reads archived to decide to skip - so this looks only at
            # assignment into a hashtable, never at property access.
            $keys = @($ast.FindAll({
                $args[0] -is [System.Management.Automation.Language.HashtableAst]
            }, $true) | ForEach-Object { $_.KeyValuePairs } |
                ForEach-Object { "$($_.Item1.Extent.Text)".Trim("'", '"') })

            foreach ($field in $forbidden) {
                $keys | Should -Not -Contain $field -Because "$($file.Name) builds a hashtable with a '$field' key, which is what a PATCH body looks like"
            }
        }
    }

    It 'reads the account listing from the endpoint that includes private repositories' {
        # THE endpoint guard, and the measured reason this repository exists.
        # GET /users/{user}/repos returns public repositories only. On this account
        # the public listing reports 15 and the authenticated one reports 24: nine
        # private repositories, absent in silence, from the inventory whose whole
        # purpose is to be complete enough to decide from.
        foreach ($file in $script:AllSource) {
            $ast = Get-SourceAst -Path $file.FullName

            $literals = @($ast.FindAll({
                $args[0] -is [System.Management.Automation.Language.StringConstantExpressionAst]
            }, $true) | ForEach-Object { $_.Value })

            foreach ($literal in $literals) {
                $literal | Should -Not -Match '^users/' -Because "$($file.Name) builds a path under users/, which returns public repositories only. Use user/repos with an authenticated token."
            }
        }
    }

    It 'always passes -Depth to ConvertTo-Json' {
        # A Windows PowerShell 5.1 trap with no error attached to it: ConvertTo-Json
        # defaults to -Depth 2, so anything nested serializes as the NAME of its type.
        # The request is then sent malformed, and GraphQL rejects it with an HTTP 200
        # - so nothing in the chain reports a problem the reader can act on.
        foreach ($file in $script:AllSource) {
            $ast = Get-SourceAst -Path $file.FullName

            $calls = @($ast.FindAll({
                $args[0] -is [System.Management.Automation.Language.CommandAst] -and
                "$($args[0].GetCommandName())" -eq 'ConvertTo-Json'
            }, $true))

            foreach ($call in $calls) {
                $hasDepth = @($call.CommandElements | Where-Object {
                    $_ -is [System.Management.Automation.Language.CommandParameterAst] -and
                    $_.ParameterName -eq 'Depth'
                }).Count -gt 0

                $hasDepth | Should -BeTrue -Because "$($file.Name) line $($call.Extent.StartLineNumber) calls ConvertTo-Json without -Depth, which silently truncates at 2 on PowerShell 5.1"
            }
        }
    }
}

Describe 'The shipped template is executable, and contains nothing real' {

    It 'passes its own validate command offline: <Name>' -ForEach $script:Automation {
        # validate is the promise that a malformed declaration fails in a second
        # rather than halfway through a run. This is that promise, exercised - and it
        # runs under whichever host started the suite, so running the suite on
        # PowerShell 7 actually tests PowerShell 7.
        $entryPoint = Join-Path (Get-RepositoryRoot) "automations/$Name/$EntryPoint"
        $template = Join-Path (Get-RepositoryRoot) "automations/$Name/config/$Template"
        $output = & (Get-PowerShellHostPath) -NoProfile -ExecutionPolicy Bypass -File $entryPoint -Command validate -ConfigurationPath $template 2>&1
        $LASTEXITCODE | Should -Be 0 -Because ($output -join [Environment]::NewLine)
    }

    It 'names only reserved example hosts: <Name>' -ForEach $script:Automation {
        # Every fixture and template is invented. A template that borrowed a real host
        # name would turn this repository into another place to leak one, and template
        # files are the last place anyone thinks to look.
        $template = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) "automations/$Name/config/$Template") -Raw
        foreach ($url in [regex]::Matches($template, 'https?://(?<host>[^/"]+)')) {
            $url.Groups['host'].Value | Should -Match '(^|\.)example\.(com|org|net)$'
        }
    }

    It 'names no repository of the account it was written for: <Name>' -ForEach $script:Automation {
        # The template ships with the repository, so every name in it is published.
        # Prefixing them EXAMPLE- is the convention; this is what makes it a rule.
        $document = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) "automations/$Name/config/$Template") -Raw | ConvertFrom-Json
        foreach ($repository in $document.repositories) {
            $repository.name | Should -Match '^EXAMPLE-' -Because 'a committed template must name only invented repositories'
        }
    }
}

Describe 'The environment template declares names and holds no value' {

    It 'leaves every token empty' {
        # The file exists to say which variables are needed and what each one has to
        # be allowed to do. A value in it is a committed secret, and this is the file
        # somebody fills in fastest and reviews least.
        $template = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) '.env.example')

        foreach ($line in $template) {
            if ($line -notmatch '^(?<name>[A-Z][A-Z0-9_]*)=(?<value>.*)$') { continue }
            $name = $Matches['name']
            $value = $Matches['value'].Trim()

            if ($name -match 'TOKEN|SECRET|PASSWORD|KEY') {
                $value | Should -BeNullOrEmpty -Because "$name carries a value in .env.example"
            }
        }
    }

    It 'declares every token the project context names' {
        # A context naming a variable that .env.example does not mention sends the
        # reader looking for documentation that does not exist. The two drift apart
        # exactly when a new automation is added, which is when it matters.
        $context = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) 'foundation/config/project-context.json') -Raw | ConvertFrom-Json
        $template = Get-Content -LiteralPath (Join-Path (Get-RepositoryRoot) '.env.example') -Raw

        foreach ($property in $context.github.PSObject.Properties) {
            if ($property.Name -notmatch 'Env$') { continue }
            $template | Should -Match ([regex]::Escape($property.Value)) -Because "$($property.Value) is declared in the project context but absent from .env.example"
        }
    }
}

Describe 'Documentation is indexed' {

    It 'links every document from the documentation index' {
        # A document nobody can reach from the index is a document nobody reads, and
        # it drifts from the code silently. Continuous integration enforces this, so
        # an unindexed document is treated as incomplete rather than as a nice extra.
        $documentationRoot = Join-Path (Get-RepositoryRoot) 'docs'
        $index = Get-Content -LiteralPath (Join-Path $documentationRoot 'README.md') -Raw

        $unlinked = New-Object System.Collections.ArrayList
        foreach ($document in (Get-ChildItem $documentationRoot -Recurse -Filter '*.md')) {
            if ($document.Name -eq 'README.md' -and $document.DirectoryName -eq (Resolve-Path -LiteralPath $documentationRoot).Path) { continue }
            if ($index -notmatch [regex]::Escape($document.Name)) { $null = $unlinked.Add($document.Name) }
        }

        $unlinked | Should -BeNullOrEmpty -Because 'every document under docs/ must be reachable from docs/README.md'
    }
}
