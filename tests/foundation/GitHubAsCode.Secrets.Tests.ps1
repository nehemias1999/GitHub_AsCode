<#
    Credentials do not reach a message.

    docs/reference/security-model.md describes four layers that keep a token out of the
    output. Until this file existed, none of them had a test - which is why two of them
    were broken:

      - Protect-SecretInText carried a rule for Basic, a scheme this repository never
        sends, and had none for Bearer, the only scheme it does send.
      - Assert-HttpBaseUrl echoed the raw environment value in the branch a pasted
        credential actually reaches, while a comment fifteen lines below claimed the
        function deliberately did not.

    Both were found by reading, not by a failing test. Every case here is named after
    the leak it prevents.

    The token values below are invented, and they are ASSEMBLED AT RUN TIME from parts
    rather than written out. That is not obfuscation for its own sake: they have to be
    structurally valid or a masking rule with the wrong prefix would pass against a
    placeholder no rule was ever going to match - and a structurally valid literal in a
    committed file fails the sensitive data gate, correctly, because the gate cannot tell
    an invented token from a real one and should not try. Splitting the prefix keeps both
    properties: no line here is credential-shaped, and the value the tests see carries a
    real prefix at a real length.
#>

Set-StrictMode -Version Latest

BeforeAll {
    . (Join-Path $PSScriptRoot '../TestHelpers.ps1')
    . (Join-Path (Get-RepositoryRoot) 'foundation/Import-Foundation.ps1')

    # Assembled from parts - see the note at the top of this file.
    $script:FineGrained = ('github' + '_pat_') + ('EXAMPLE' * 6)
    $script:Classic = ('gh' + 'p_') + ('EXAMPLE' * 5)
}

Describe 'Protect-SecretInText' {

    It 'masks a Bearer credential, which is the scheme this repository actually sends' {
        # THE regression. Both inherited rules came from a project authenticating with
        # Basic, so the layer whose entire job is catching a token that arrived by an
        # unenumerated route was blind to the only scheme in use.
        $masked = Protect-SecretInText -Text "Authorization: Bearer $script:FineGrained"

        $masked | Should -Not -Match ([regex]::Escape($script:FineGrained))
        $masked | Should -Be 'Authorization: Bearer [redacted]'
    }

    It 'masks whatever follows Bearer, not only a known token shape' {
        # The rule is \S+ on purpose. Anything after Bearer is a credential regardless of
        # how it looks, and a rule matching only today's prefixes would miss the next
        # format GitHub introduces.
        (Protect-SecretInText -Text 'Bearer some-future-format.v9.abcdef') |
            Should -Be 'Bearer [redacted]'
    }

    It 'masks a bare fine-grained token in free text' {
        # The realistic route: an operator pastes the token into the wrong line of .env
        # and a validation message carries the value. No header, no property name - so
        # redaction by property name cannot see it and the userinfo rule does not fire.
        $masked = Protect-SecretInText -Text "GITHUB_OWNER is '$script:FineGrained', which is not a login"

        $masked | Should -Not -Match ([regex]::Escape($script:FineGrained))
        $masked | Should -Match '\[redacted-token\]'
    }

    It 'masks a bare classic token in free text' {
        $masked = Protect-SecretInText -Text "failed for $script:Classic"

        $masked | Should -Not -Match ([regex]::Escape($script:Classic))
        $masked | Should -Match '\[redacted-token\]'
    }

    It 'still masks userinfo in a URL, and keeps the host' {
        # The host is the diagnostically useful half. A message that says which endpoint
        # disagreed is the point; the credential in front of it never is.
        $masked = Protect-SecretInText -Text "cannot reach https://someone:$($script:Classic)@api.example.com/user/repos"

        $masked | Should -Not -Match ([regex]::Escape($script:Classic))
        $masked | Should -Match 'api\.example\.com'
    }

    It 'leaves text with no credential in it untouched' {
        # A masker that mangles ordinary output is the failure mode this module already
        # documents having had once, when an unanchored 'pat' rule replaced every Area
        # Path in every report. Redaction that destroys evidence fails quietly, which is
        # worse than not redacting.
        $ordinary = 'Account listing: 3 repository/ies over 1 page(s) - 2 public, 1 private.'

        Protect-SecretInText -Text $ordinary | Should -Be $ordinary
    }

    It 'returns null and empty unchanged rather than throwing' {
        Protect-SecretInText -Text '' | Should -Be ''
        Protect-SecretInText -Text $null | Should -BeNullOrEmpty
    }
}

Describe 'Assert-HttpBaseUrl' {

    It 'does not echo the value when it is not a URL' {
        # THE other half of the same leak, and the one with a constructible scenario:
        # .env holds the API URL, the owner and the token within a few lines of each
        # other. A token is not an absolute URL, so it lands in this branch - and this
        # throw is terminating, so it never passes through the masking funnel that
        # console output goes through. In a workflow the value goes to the run log.
        $message = ''
        try { Assert-HttpBaseUrl -Url $script:FineGrained -VariableName 'GITHUB_API_URL' | Out-Null }
        catch { $message = $_.Exception.Message }

        $message | Should -Not -BeNullOrEmpty
        $message | Should -Not -Match ([regex]::Escape($script:FineGrained))
    }

    It 'still says enough to identify the mistake' {
        # Not echoing must not mean not diagnosing. The length and whether the value
        # contains a scheme separator distinguish a typo'd URL from something that is not
        # a URL at all, which is what the reader needs in order to fix it.
        $message = ''
        try { Assert-HttpBaseUrl -Url $script:FineGrained -VariableName 'GITHUB_API_URL' | Out-Null }
        catch { $message = $_.Exception.Message }

        $message | Should -Match 'GITHUB_API_URL'
        $message | Should -Match "$($script:FineGrained.Length) characters"
        $message | Should -Match "does not contain"
    }

    It 'does not echo the value when it has a scheme but will not parse' {
        $broken = "https://$($script:Classic)@ api.example.com"
        $message = ''
        try { Assert-HttpBaseUrl -Url $broken -VariableName 'GITHUB_API_URL' | Out-Null }
        catch { $message = $_.Exception.Message }

        $message | Should -Not -Match ([regex]::Escape($script:Classic))
    }

    It 'rejects a credential in the URL without echoing it' {
        # This branch was already correct - it is the one the old comment described. The
        # case exists so a future edit cannot regress it while nothing watches.
        $message = ''
        try { Assert-HttpBaseUrl -Url "https://someone:$($script:Classic)@api.example.com" -VariableName 'GITHUB_API_URL' | Out-Null }
        catch { $message = $_.Exception.Message }

        $message | Should -Match 'carries credentials in the URL'
        $message | Should -Not -Match ([regex]::Escape($script:Classic))
    }

    It 'accepts a normal URL and normalises the trailing slash' {
        Assert-HttpBaseUrl -Url 'https://api.example.com/' -VariableName 'GITHUB_API_URL' |
            Should -Be 'https://api.example.com'
    }

    It 'rejects a scheme that is not http or https' {
        { Assert-HttpBaseUrl -Url 'ftp://api.example.com' -VariableName 'GITHUB_API_URL' } |
            Should -Throw -ExpectedMessage '*Only http and https*'
    }
}

Describe 'New-BearerAuthorizationHeader' {

    It 'never puts the token in its own error messages' {
        # A function whose only argument is a credential is the last place to interpolate
        # that argument. Both rejection paths are checked because both are reachable from
        # a .env a person typed.
        foreach ($bad in @("$script:FineGrained`n$script:Classic", "  ")) {
            $message = ''
            try { New-BearerAuthorizationHeader -Secret $bad | Out-Null }
            catch { $message = $_.Exception.Message }

            $message | Should -Not -BeNullOrEmpty
            $message | Should -Not -Match ([regex]::Escape($script:FineGrained))
        }
    }

    It 'rejects a token containing whitespace, which is a value pasted across a line break' {
        { New-BearerAuthorizationHeader -Secret "$script:Classic extra" } |
            Should -Throw -ExpectedMessage '*whitespace*'
    }

    It 'trims a trailing newline rather than sending an unusable header' {
        # A value read from a file arrives with a newline more often than not, and a
        # header value containing one throws an ArgumentException from the header
        # collection - an error that reads like anything but authentication.
        New-BearerAuthorizationHeader -Secret "  $script:Classic  " |
            Should -Be "Bearer $script:Classic"
    }
}

Describe 'Remove-SensitiveValue' {

    It 'does not destroy the token evidence the report exists to carry' {
        # THE regression, and it was live: repo-inventory named its block of evidence
        # about the token's shape 'token', and this function - which matches by property
        # NAME - replaced the whole object with the string "[redacted]" on the way to the
        # report. Confirmed in a real artefact, where detail.token was that string while
        # listing, rateLimit and finding beside it survived.
        #
        # Nothing in the block is secret. isClassic is a fact about the token's TYPE,
        # scope lists permission names, and the two expiry fields are dates. The value
        # never goes near it. Meanwhile security-model.md promises that inventory reports
        # the days remaining - so the report promised the evidence and deleted it.
        $detail = [pscustomobject]@{
            authentication = [pscustomobject]@{
                isClassic       = $true
                scope           = @('metadata:read')
                expiresUtc      = '2099-12-31T23:59:59.0000000Z'
                daysUntilExpiry = 5
            }
        }

        $clean = Remove-SensitiveValue -InputObject $detail

        $clean.authentication | Should -Not -BeOfType [string]
        $clean.authentication.daysUntilExpiry | Should -Be 5
        $clean.authentication.isClassic | Should -BeTrue
        @($clean.authentication.scope) | Should -Be @('metadata:read')
    }

    It 'still destroys a block whose name really is credential-shaped' {
        # The complement, so the fix above cannot be mistaken for weakening redaction.
        # A property actually named token, authorization or password must still go.
        foreach ($name in @('token', 'authorization', 'password', 'apiKey')) {
            $probe = [pscustomobject]@{ $name = 'some-real-value' }

            (Remove-SensitiveValue -InputObject $probe).$name | Should -Be '[redacted]' -Because "$name must still be redacted"
        }
    }

    It 'redacts a name this repository might reasonably have chosen instead' {
        # Measured, not assumed. These were the candidates for the rename, and all of
        # them are destroyed - which is why the field is called 'authentication' and why
        # renaming it back would silently delete the evidence again.
        foreach ($name in @('tokenShape', 'tokenInfo', 'credentialShape', 'auth')) {
            $probe = [pscustomobject]@{ $name = [pscustomobject]@{ daysUntilExpiry = 5 } }

            (Remove-SensitiveValue -InputObject $probe).$name | Should -Be '[redacted]' -Because "$name is destroyed, so it is not a usable name for evidence"
        }
    }
}
